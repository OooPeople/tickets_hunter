import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from nodriver_common import (  # noqa: E402
    detect_cloudflare_challenge,
    handle_cloudflare_challenge,
    wait_for_challenge_cleared,
)
from nodriver_tixcraft import (  # noqa: E402
    _advance_cloudflare_gate,
    _is_kktix_login_page,
)
from platforms.cityline import is_cityline_login_page  # noqa: E402


class ProofTab:
    def __init__(self):
        self.sleeps = []

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


class EvaluateTab(ProofTab):
    def __init__(self, evaluate_results):
        super().__init__()
        self.evaluate_results = list(evaluate_results)

    async def evaluate(self, _script):
        return self.evaluate_results.pop(0)


class NoReloadTab(ProofTab):
    def __init__(self, send_results=None):
        super().__init__()
        self.reload_count = 0
        self.send_results = list(send_results or [])

    async def send(self, _command):
        if self.send_results:
            return self.send_results.pop(0)
        return types.SimpleNamespace()

    async def evaluate(self, _script):
        return None

    async def reload(self):
        self.reload_count += 1


class ForeignTargetTab(ProofTab):
    async def send(self, _command):
        # This models a browser-wide target list containing a challenge owned
        # by another tab. It must not be evidence for the current tab.
        return [types.SimpleNamespace(url="https://challenges.cloudflare.com/widget")]

    async def evaluate(self, _script):
        return False

    async def get_content(self):
        return "<html><body>ordinary ticket page</body></html>"


class CloudflareSuccessProofTest(unittest.IsolatedAsyncioTestCase):
    async def test_detector_ignores_a_foreign_tabs_challenge_target(self):
        self.assertFalse(await detect_cloudflare_challenge(ForeignTargetTab()))

    async def test_token_proves_embedded_challenge_success(self):
        tab = ProofTab()
        responses = AsyncMock(side_effect=[
            (True, ""),
            (True, "x" * 21),
        ])
        with patch("nodriver_common.read_turnstile_response", responses), patch(
            "nodriver_common.nodriver_current_url_safe",
            AsyncMock(return_value="https://example.test/challenge"),
        ):
            self.assertTrue(
                await wait_for_challenge_cleared(tab, timeout=0, interval=0.01)
            )

    async def test_field_disappearance_proves_transit_success(self):
        tab = ProofTab()
        responses = AsyncMock(side_effect=[
            (True, ""),
            (False, ""),
        ])
        with patch("nodriver_common.read_turnstile_response", responses), patch(
            "nodriver_common.nodriver_current_url_safe",
            AsyncMock(return_value="https://example.test/challenge"),
        ):
            self.assertTrue(
                await wait_for_challenge_cleared(tab, timeout=0, interval=0.01)
            )

    async def test_url_change_proves_transit_success(self):
        tab = ProofTab()
        responses = AsyncMock(side_effect=[
            (False, ""),
            (False, ""),
        ])
        urls = AsyncMock(side_effect=[
            "https://example.test/challenge",
            "https://example.test/tickets",
        ])
        with patch("nodriver_common.read_turnstile_response", responses), patch(
            "nodriver_common.nodriver_current_url_safe", urls
        ):
            self.assertTrue(
                await wait_for_challenge_cleared(tab, timeout=0, interval=0.01)
            )

    async def test_no_proof_times_out(self):
        tab = ProofTab()
        responses = AsyncMock(side_effect=[
            (False, ""),
            (False, ""),
        ])
        with patch("nodriver_common.read_turnstile_response", responses), patch(
            "nodriver_common.nodriver_current_url_safe",
            AsyncMock(return_value="https://example.test/challenge"),
        ):
            self.assertFalse(
                await wait_for_challenge_cleared(tab, timeout=0, interval=0.01)
            )

    async def test_handler_never_reloads_after_exhausting_attempts(self):
        tab = NoReloadTab()
        with patch(
            "nodriver_common._find_cf_iframe_in_dom", return_value=(None, None)
        ), patch(
            "nodriver_common.util.verify_cf_with_templates",
            AsyncMock(return_value=False),
        ), patch(
            "nodriver_common.detect_cloudflare_challenge",
            AsyncMock(return_value=True),
        ):
            self.assertFalse(
                await handle_cloudflare_challenge(
                    tab, {"advanced": {"verbose": False}}, max_retry=1
                )
            )
        self.assertEqual(0, tab.reload_count)

    async def test_clicked_challenge_requires_success_proof(self):
        tab = NoReloadTab()
        proof = AsyncMock(return_value=True)
        with patch(
            "nodriver_common.solve_turnstile_checkbox", AsyncMock(return_value=True)
        ), patch("nodriver_common.wait_for_challenge_cleared", proof):
            self.assertTrue(
                await handle_cloudflare_challenge(
                    tab, {"advanced": {"verbose": False}}, max_retry=1
                )
            )
        proof.assert_awaited_once_with(tab, show_debug=False)
        self.assertEqual(0, tab.reload_count)


class CloudflareMainGateTest(unittest.IsolatedAsyncioTestCase):
    def test_kktix_login_page_ownership_is_path_scoped(self):
        self.assertTrue(
            _is_kktix_login_page(
                "https://kktix.com/users/sign_in?back_to=https%3A%2F%2Fkktix.cc"
            )
        )
        self.assertTrue(_is_kktix_login_page("https://foo.kktix.cc/users/sign_in"))
        self.assertFalse(_is_kktix_login_page("https://kktix.com/events/demo"))
        self.assertFalse(_is_kktix_login_page("https://notkktix.com/users/sign_in"))
        self.assertFalse(_is_kktix_login_page(
            "https://evil.example/?next=https://kktix.com/users/sign_in"
        ))

    def test_cityline_login_ownership_requires_a_real_cityline_host(self):
        self.assertTrue(is_cityline_login_page(
            "https://www.cityline.com.hk/en_US/Login.html?targetUrl=%2FEvents.html"
        ))
        self.assertFalse(is_cityline_login_page(
            "https://notcityline.com/Login.html"
        ))
        self.assertFalse(is_cityline_login_page(
            "https://evil.example/?next=https://cityline.com/Login.html"
        ))

    async def test_positive_detection_gates_on_solve_success_or_failure(self):
        config = {"advanced": {"verbose": False}}
        for solved, expected_failures in ((True, 0), (False, 1)):
            with self.subTest(solved=solved), patch(
                "nodriver_tixcraft.detect_cloudflare_challenge",
                AsyncMock(return_value=True),
            ), patch(
                "nodriver_tixcraft.handle_cloudflare_challenge",
                AsyncMock(return_value=solved),
            ):
                gated, checked, failures, active, confirmations = (
                    await _advance_cloudflare_gate(
                    object(),
                    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                    config,
                    False,
                    0,
                    )
                )
            self.assertTrue(gated)
            self.assertEqual(solved, checked)
            self.assertEqual(expected_failures, failures)
            self.assertEqual(not solved, active)
            self.assertEqual(0, confirmations)

    async def test_three_failures_keep_gating_without_more_solve_attempts(self):
        detect = AsyncMock(return_value=True)
        solve = AsyncMock()
        with patch(
            "nodriver_tixcraft._current_cloudflare_clearance",
            AsyncMock(return_value="active"),
        ), patch(
            "nodriver_tixcraft.detect_cloudflare_challenge", detect
        ), patch("nodriver_tixcraft.handle_cloudflare_challenge", solve):
            gated, checked, failures, active, confirmations = (
                await _advance_cloudflare_gate(
                object(),
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                False,
                3,
                True,
                0,
                )
            )
        self.assertTrue(gated)
        self.assertFalse(checked)
        self.assertEqual(3, failures)
        self.assertTrue(active)
        self.assertEqual(0, confirmations)
        detect.assert_awaited_once()
        solve.assert_not_awaited()

    async def test_same_url_manual_token_solve_releases_immediately(self):
        tab = EvaluateTab([{"present": True, "value": "x" * 21}])
        with patch(
            "nodriver_tixcraft.detect_cloudflare_challenge", AsyncMock()
        ) as detect:
            result = await _advance_cloudflare_gate(
                tab,
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                False,
                3,
                True,
                0,
            )
        self.assertEqual((False, True, 0, False, 0), result)
        detect.assert_not_awaited()

    async def test_same_url_dom_clearance_requires_two_observations(self):
        detect = AsyncMock(return_value=False)
        with patch(
            "nodriver_tixcraft._current_cloudflare_clearance",
            AsyncMock(return_value="absent"),
        ), patch("nodriver_tixcraft.detect_cloudflare_challenge", detect):
            first = await _advance_cloudflare_gate(
                object(),
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                False,
                3,
                True,
                0,
            )
            second = await _advance_cloudflare_gate(
                object(),
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                first[1],
                first[2],
                first[3],
                first[4],
            )
        self.assertEqual((True, False, 3, True, 1), first)
        self.assertEqual((False, True, 0, False, 0), second)

    async def test_real_clearance_helper_releases_utk_normal_root_twice(self):
        state = {
            "ready": True,
            "body_ready": True,
            "challenge": False,
            "normal_root": True,
        }
        tab = EvaluateTab([
            {"present": False, "value": ""}, state,
            {"present": False, "value": ""}, state,
        ])
        with patch(
            "nodriver_tixcraft.detect_cloudflare_challenge",
            AsyncMock(return_value=True),
        ):
            first = await _advance_cloudflare_gate(
                tab,
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                False,
                3,
                True,
                0,
            )
            second = await _advance_cloudflare_gate(
                tab,
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                first[1],
                first[2],
                first[3],
                first[4],
            )
        self.assertEqual((True, False, 3, True, 1), first)
        self.assertEqual((False, True, 0, False, 0), second)

    async def test_inconclusive_dom_does_not_count_as_clearance(self):
        with patch(
            "nodriver_tixcraft._current_cloudflare_clearance",
            AsyncMock(return_value="unknown"),
        ), patch(
            "nodriver_tixcraft.detect_cloudflare_challenge",
            AsyncMock(return_value=False),
        ):
            result = await _advance_cloudflare_gate(
                object(),
                "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1",
                {"advanced": {"verbose": False}},
                False,
                3,
                True,
                1,
            )
        self.assertEqual((True, False, 3, True, 0), result)

    async def test_login_form_turnstile_is_left_to_platform_owner(self):
        detect = AsyncMock()
        with patch("nodriver_tixcraft.detect_cloudflare_challenge", detect):
            kktix = await _advance_cloudflare_gate(
                object(),
                "https://kktix.com/users/sign_in?back_to=%2Fevents%2Fdemo",
                {},
                False,
                0,
            )
            cityline = await _advance_cloudflare_gate(
                object(),
                "https://www.cityline.com.hk/Login.html?targetUrl=%2FEvents.html",
                {},
                False,
                0,
            )
        self.assertEqual((False, True, 0, False, 0), kktix)
        self.assertEqual((False, True, 0, False, 0), cityline)
        detect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
