import asyncio
import base64
import gc
import json
import time
import types
import unittest
import weakref
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from tests.ctbc_support import (
    CTBC_TEST_AREA_URL,
    CTBC_TEST_SEAT_URL,
    CtbcStateIsolationMixin,
    FakeElement,
    FakeSubmitTab,
    FakeTab,
    ctbc_identity_runtime,
    ctbc_tab_runtime,
    config,
    make_ctbc_area_snapshot,
    make_ctbc_seat_snapshot,
    make_ctbc_submit_plan,
)

from platforms.utiki.dom import CommitResult, SubmitDispatchResult
from platforms.utiki.selection import (
    QuantityPlan,
    build_submit_plan,
    parse_seat_snapshot,
)

from platforms.utiki.workflow import (
    CTBC_AREA_ROW_SELECTOR,
    _ctbc_attest_fast_destination,
    _ctbc_captcha_and_submit as _workflow_ctbc_captcha_and_submit,
    _ctbc_fast_marker_matches_pending,
    _fill_ctbc_inline_login,
    _ctbc_pending_from_fast_marker,
    _ctbc_purchase_state_key,
    _ctbc_reconcile_area_navigation,
    _reconcile_utiki_purchase,
    _ctbc_selection_config_fingerprint,
    _ctbc_tab_identity,
    _ctbc_tab_state_key,
    _get_ctbc_captcha_image_bytes,
    _handle_ctbc_submit_dialog,
    _recover_ctbc_captcha_dialog,
    _recover_ctbc_seat_conflict,
    _reset_ctbc_captcha,
    _state,
    _utk_refresh_identity,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_general_admission_main,
    nodriver_ctbc_inject_cookies,
    nodriver_ctbc_main,
    nodriver_ctbc_seat_main,
)


async def _ctbc_captcha_and_submit(
    tab, config_dict, ocr, *, submit_plan=None,
):
    """Keep legacy dialog tests focused while supplying the new final intent."""
    if submit_plan is None:
        submit_plan = make_ctbc_submit_plan(str(tab.target.url))
    return await _workflow_ctbc_captcha_and_submit(
        tab, config_dict, ocr, submit_plan=submit_plan
    )


CTBC_DATE_ROW_SELECTOR = "#PerformanceListTable table > tbody > tr"
CTBC_DATE_PURCHASE_CONTROL_SELECTOR = (
    'td:last-child button, td:last-child a[href], '
    'td:last-child [role="button"]'
)


class CtbcDomFlowTest(CtbcStateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    ctbc_state = _state

    def _quantity_plan(self, page_url):
        query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
        return QuantityPlan(
            page_url=page_url,
            document_id="fixture-document",
            performance_id=query["PERFORMANCE_ID"][0],
            group_id=query["GROUP_ID"][0],
            price_area_id=query["PERFORMANCE_PRICE_AREA_ID"][0],
            quantity_limit=4,
            target_key="FULL",
            target_name="全票",
            ticket_number=2,
        )

    def _track_submit_state(self, tab):
        submit_key = _ctbc_purchase_state_key(tab)
        purchase = ctbc_tab_runtime(tab)["purchase"]
        def cleanup():
            active = purchase.get("active_submit")
            if isinstance(active, dict) and active.get("purchase_key") == submit_key:
                purchase["active_submit"] = None
            purchase["outcomes"].pop(submit_key, None)
            pointer = purchase.get("last_submit")
            if isinstance(pointer, dict) and pointer.get("purchase_key") == submit_key:
                purchase["last_submit"] = None
        self.addCleanup(cleanup)
        return submit_key

    def _set_active_submit(self, tab, submit_key, value):
        active = value if isinstance(value, dict) else {"dispatched_at": value}
        active.setdefault("purchase_key", submit_key)
        active.setdefault("nonce", "f" * 32)
        active.setdefault("source_url", str(tab.target.url))
        ctbc_tab_runtime(tab)["purchase"]["active_submit"] = active
        return active

    def _active_submit_for(self, tab, submit_key):
        active = ctbc_tab_runtime(tab)["purchase"].get("active_submit")
        if isinstance(active, dict) and active.get("purchase_key") == submit_key:
            return active
        return None

    def _outcome_for(self, tab, submit_key):
        return ctbc_tab_runtime(tab)["purchase"]["outcomes"].get(submit_key)

    def test_tab_runtime_isolates_two_tabs(self):
        first = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        second = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        first.target.target_id = "target-one"
        second.target.target_id = "target-two"
        first_identity = _ctbc_tab_identity(first)
        second_identity = _ctbc_tab_identity(second)

        first_runtime = ctbc_tab_runtime(first)
        second_runtime = ctbc_tab_runtime(second)
        first_runtime["transition"]["pending_navigation"] = {"nonce": "one"}

        self.assertEqual("utiki-target-target-one", first_identity)
        self.assertNotEqual(first_identity, second_identity)
        self.assertIsNone(
            second_runtime["transition"]["pending_navigation"]
        )
        self.assertIs(first_runtime, _state["tabs"][first_identity])
        self.assertIs(second_runtime, _state["tabs"][second_identity])

    def test_tab_identity_does_not_retain_tab_object(self):
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        identity = _ctbc_tab_identity(tab)
        reference = weakref.ref(tab)
        ctbc_tab_runtime(tab)["document"]["last_document"] = "document-1"

        del tab
        gc.collect()

        self.assertIsNone(reference())
        self.assertEqual(
            "document-1",
            ctbc_identity_runtime(identity)["document"]["last_document"],
        )

    def test_tab_identity_is_pinned_when_target_id_appears_later(self):
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["transition"]["pending_navigation"] = {"nonce": "pending"}

        tab.target.target_id = "late-target"

        self.assertEqual(identity, _ctbc_tab_identity(tab))
        self.assertIs(runtime, ctbc_tab_runtime(tab))
        self.assertEqual(
            "pending", runtime["transition"]["pending_navigation"]["nonce"]
        )

    def test_same_target_wrappers_share_refresh_coordinator_identity(self):
        first = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        second = FakeTab("", [], url=CTBC_TEST_AREA_URL)
        first.target.target_id = "shared-target"
        second.target.target_id = "shared-target"

        self.assertEqual(_ctbc_tab_identity(first), _ctbc_tab_identity(second))
        self.assertEqual(_utk_refresh_identity(first), _utk_refresh_identity(second))
        self.assertIs(ctbc_tab_runtime(first), ctbc_tab_runtime(second))

    async def test_inline_login_never_reuses_ctbc_credentials_for_dragons(self):
        class LoginTab:
            def __init__(self, url, navigate_before_commit=False):
                self.target = types.SimpleNamespace(url=url)
                self.scripts = []
                self.navigate_before_commit = navigate_before_commit
                self.credentials_written = False

            async def evaluate(self, script):
                self.scripts.append(script)
                if len(self.scripts) == 1:
                    return {"required": True, "account": "", "password": ""}
                if self.navigate_before_commit:
                    self.target.url = (
                        "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1"
                    )
                has_atomic_origin_gate = (
                    'const expectedOrigin = "https://tix.ctbcsports.com";' in script
                    and "const currentUrl = new URL(location.href);" in script
                    and "currentUrl.origin !== expectedOrigin" in script
                    and "currentUrl.username" in script
                    and "currentUrl.password" in script
                )
                if (
                    has_atomic_origin_gate
                    and not self.target.url.startswith(
                        "https://tix.ctbcsports.com/BROTHERS/"
                    )
                ):
                    return False
                self.credentials_written = True
                return True

        cfg = config()
        cfg["accounts"]["ctbc_account"] = "ctbc-test-account"
        cfg["accounts"]["ctbc_password"] = "ctbc-test-password"
        dragons = LoginTab(
            "https://tix.wdragons.com/UTK0205_?PERFORMANCE_ID=P1"
        )
        ctbc = LoginTab(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1"
        )

        self.assertFalse(await _fill_ctbc_inline_login(dragons, cfg))
        self.assertEqual(1, len(dragons.scripts))
        self.assertNotIn("ctbc-test-account", dragons.scripts[0])
        self.assertNotIn("ctbc-test-password", dragons.scripts[0])

        self.assertTrue(await _fill_ctbc_inline_login(ctbc, cfg))
        self.assertEqual(2, len(ctbc.scripts))
        self.assertIn("ctbc-test-account", ctbc.scripts[1])
        self.assertIn("ctbc-test-password", ctbc.scripts[1])
        self.assertTrue(ctbc.credentials_written)

        raced = LoginTab(
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1",
            navigate_before_commit=True,
        )
        self.assertFalse(await _fill_ctbc_inline_login(raced, cfg))
        self.assertFalse(raced.credentials_written)

    async def test_cookie_injection_rechecks_the_exact_tenant(self):
        get_all = AsyncMock(return_value=[])
        send = AsyncMock(return_value=True)
        tab = types.SimpleNamespace(
            target=types.SimpleNamespace(
                url="https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
            ),
            browser=types.SimpleNamespace(
                cookies=types.SimpleNamespace(get_all=get_all)
            ),
            send=send,
        )
        cfg = config()
        cfg["accounts"]["ctbc_cookie"] = "CTBC_IMPORTED|BROTHERS|_1=session"

        self.assertFalse(await nodriver_ctbc_inject_cookies(tab, cfg))
        get_all.assert_not_awaited()
        send.assert_not_awaited()

    async def test_cookie_cleanup_requires_ctbc_dns_label_boundary(self):
        cookies = [
            types.SimpleNamespace(
                name="CTBC_SESSION", domain=".ctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_SUBDOMAIN", domain="login.ctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_EVIL", domain="evilctbcsports.com", path="/"
            ),
            types.SimpleNamespace(
                name="CTBC_SUFFIX", domain="ctbcsports.com.evil.test", path="/"
            ),
        ]
        tab = types.SimpleNamespace(
            target=types.SimpleNamespace(
                url="https://tix.ctbcsports.com/BROTHERS/"
            ),
            browser=types.SimpleNamespace(
                cookies=types.SimpleNamespace(
                    get_all=AsyncMock(return_value=cookies)
                )
            ),
            send=AsyncMock(return_value=True),
        )
        cfg = config()
        cfg["accounts"]["ctbc_cookie"] = "CTBC_IMPORTED|BROTHERS|_1=session"

        with patch(
            "platforms.utiki.workflow.cdp.network.delete_cookies",
            side_effect=lambda **kwargs: ("delete", kwargs),
        ), patch(
            "platforms.utiki.workflow.cdp.network.set_cookie",
            side_effect=lambda **kwargs: ("set", kwargs),
        ):
            self.assertTrue(await nodriver_ctbc_inject_cookies(tab, cfg))

        commands = [call.args[0] for call in tab.send.await_args_list]
        deleted_domains = [
            command[1]["domain"] for command in commands if command[0] == "delete"
        ]
        self.assertEqual(
            [".ctbcsports.com", "login.ctbcsports.com"], deleted_domains
        )
        set_commands = [command for command in commands if command[0] == "set"]
        self.assertEqual(1, len(set_commands))
        self.assertEqual("tix.ctbcsports.com", set_commands[0][1]["domain"])

    async def test_captcha_capture_prefers_native_canvas_bytes(self):
        expected = b"native-png"
        data_url = "data:image/png;base64," + base64.b64encode(expected).decode("ascii")
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=data_url))
        with patch(
            "platforms.utiki.workflow.nodriver_get_captcha_image_from_dom_snapshot",
            new=AsyncMock(return_value=b"fallback"),
        ) as fallback:
            self.assertEqual(expected, await _get_ctbc_captcha_image_bytes(tab, config()))
            fallback.assert_not_awaited()

    async def test_captcha_capture_falls_back_when_canvas_is_unavailable(self):
        tab = types.SimpleNamespace(evaluate=AsyncMock(return_value=""))
        with patch(
            "platforms.utiki.workflow.nodriver_get_captcha_image_from_dom_snapshot",
            new=AsyncMock(return_value=b"fallback"),
        ) as fallback:
            self.assertEqual(b"fallback", await _get_ctbc_captcha_image_bytes(tab, config()))
            fallback.assert_awaited_once_with(tab, config(), preferred_image_id="chk_pic")

    async def test_date_uses_visible_main_table_and_skips_disabled_sold_out(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        available_button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        sold_button = FakeElement(
            "<button id='buy_btn' disabled>已售完</button>",
            disabled=True,
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        available_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": available_button},
        )
        sold_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>家庭席</td><td><button disabled>已售完</button></td></tr>",
            children={"button#buy_btn": sold_button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [available_row, sold_row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertEqual(CTBC_DATE_ROW_SELECTOR, tab.last_selector)
        self.assertTrue(available_button.clicked)
        self.assertFalse(sold_button.clicked)

    async def test_date_fast_path_accepts_available_area_status_skew(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>立即選位》</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>立即選位》</button></td></tr>",
            children={"button#buy_btn": button},
        )
        snapshot = make_ctbc_area_snapshot(include_back=False)
        snapshot.update({
            "elapsed_ms": 120,
            "map_url": "https://imgs2.utiki.com.tw/Data/CTBC_SPORTS/Images/Temp/P16D0B8Z/1_P16D0B8Z_live.map?v=12345678",
        })
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            evaluate_results=[json.dumps(snapshot), True],
        )
        try:
            self.assertTrue(await nodriver_ctbc_date_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))
            self.assertFalse(button.clicked)
            expected = ctbc_tab_runtime(tab)["transition"]["pending_navigation"]
            self.assertEqual("PRICE_FRONT", expected["price_area_id"])
        finally:
            ctbc_tab_runtime(tab)["transition"]["pending_navigation"] = None

    async def test_date_fast_path_failure_falls_back_to_the_native_button(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            evaluate_results=[json.dumps({"ok": False, "reason": "special-mode"})],
        )
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertTrue(button.clicked)
        self.assertEqual(1, tab.evaluate_count)

    async def test_computer_mode_skips_date_fast_path(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L"
        )
        onclick = (
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&"
            "PRODUCT_ID=P16ANF0L'"
        )
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td>"
            "<td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config()
        cfg["utiki"]["seat_selection_mode"] = "computer"
        cfg["ctbc"] = dict(cfg["utiki"])

        with (
            patch(
                "platforms.utiki.workflow._get_utk_sports_profile",
                return_value={
                    "name": "CTBC Sports",
                    "host": "tix.ctbcsports.com",
                    "tenant_path": "/BROTHERS",
                    "map_namespace": "CTBC_SPORTS",
                    "fast_area_prefetch": True,
                    "supports_server_assignment": True,
                },
            ),
            patch(
                "platforms.utiki.workflow._ctbc_try_fast_area_navigation",
                AsyncMock(),
            ) as fast_path,
        ):
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        fast_path.assert_not_awaited()
        self.assertTrue(button.clicked)

    async def test_computer_mode_without_capability_does_not_select_date(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L"
        )
        onclick = (
            "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&"
            "PRODUCT_ID=P16ANF0L'"
        )
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td>"
            "<td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config()
        cfg["utiki"]["seat_selection_mode"] = "computer"
        cfg["ctbc"] = dict(cfg["utiki"])

        with (
            patch(
                "platforms.utiki.workflow._ctbc_try_fast_area_navigation",
                AsyncMock(),
            ) as fast_path,
            patch(
                "platforms.utiki.workflow.commit_date_action",
                AsyncMock(),
            ) as native_commit,
        ):
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))

        fast_path.assert_not_awaited()
        native_commit.assert_not_awaited()
        self.assertFalse(button.clicked)

    def test_seat_selection_mode_changes_navigation_fingerprint(self):
        manual = config()
        computer = config()
        computer["utiki"]["seat_selection_mode"] = "computer"
        computer["ctbc"] = dict(computer["utiki"])

        self.assertNotEqual(
            _ctbc_selection_config_fingerprint(manual),
            _ctbc_selection_config_fingerprint(computer),
        )

    async def test_fast_navigation_installs_pending_before_dispatch_exception(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        source_document = f"{source}|1000"
        destination_document = f"{destination}|2000"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        observed = []
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="throw_after_dispatch",
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
                "document_id": destination_document,
            },
            on_fast_commit=lambda: observed.append(
                ctbc_tab_runtime(tab)["transition"]["pending_navigation"]["kind"]
            ),
        )

        cfg = config(area_keyword='"109區前排"')
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = source_document
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertEqual(["FAST"], observed)
        self.assertFalse(button.clicked)
        self.assertEqual(destination, tab.target.url)
        pending = ctbc_tab_runtime(tab)["transition"]["pending_navigation"]
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])
        self.assertEqual(pending["nonce"], tab.session_marker["nonce"])
        self.assertEqual(source, tab.session_marker["source"])
        self.assertEqual(destination, tab.session_marker["destination"])

        ctbc_identity_runtime(identity)["document"]["last_document"] = destination_document
        self.assertTrue(await _ctbc_attest_fast_destination(tab, "seat", cfg))
        self.assertIsNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertIsNone(tab.session_marker)

    async def test_rejected_fast_commit_compare_pops_pending_before_native_fallback(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="rejected",
            marker_delete_allowed=False,
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertTrue(button.clicked)
        pending = ctbc_tab_runtime(tab)["transition"]["pending_navigation"]
        self.assertEqual("DATE_NATIVE", pending["kind"])
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])
        self.assertIsNone(tab.session_marker)

    async def test_rejected_old_nonce_does_not_clear_newer_pending(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        replacement = {"kind": "FAST", "nonce": "9" * 32}
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="rejected",
            on_fast_commit=lambda: ctbc_tab_runtime(tab)[
                "transition"
            ].__setitem__(
                "pending_navigation", replacement
            ),
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertIs(replacement, ctbc_tab_runtime(tab)["transition"]["pending_navigation"])
        self.assertFalse(button.clicked)

    async def test_date_commit_guard_recovers_only_after_reload_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            fast_commit_outcome="pending",
        )
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        cfg = config(area_keyword='"109區前排"')

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertIsNotNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertFalse(button.clicked)
        ctbc_identity_runtime(identity)["transition"]["pending_navigation"]["created_at"] -= 30

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(side_effect=[False, True]),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertNotIn(
                "reload_requested_at", ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
            )
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            pending = ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
            self.assertEqual(2, reload_page.await_count)
            self.assertIn("reload_requested_at", pending)

            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertEqual(2, reload_page.await_count)

            ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
            cfg["area_auto_select"]["enable"] = False
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        self.assertTrue(button.clicked)
        pending = ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
        self.assertEqual("DATE_NATIVE", pending["kind"])
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])

    async def test_storage_unavailable_clean_date_uses_native_fallback(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        button = FakeElement("<button id='buy_btn'>購買</button>")
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, storage_available=False,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
        )

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertTrue(button.clicked)
        pending = ctbc_tab_runtime(tab)["transition"]["pending_navigation"]
        self.assertEqual("DATE_NATIVE", pending["kind"])
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])

    async def test_storage_unavailable_native_attests_but_fast_returns(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        expected = {
            "phase": "DISPATCH_UNKNOWN", "nonce": "4" * 32,
            "source_url": source, "area_url": CTBC_TEST_AREA_URL,
            "destination_url": destination, "route": "0205",
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
            "source_document_id": f"{source}|1000",
            "created_at": time.monotonic(),
        }
        destination_document = f"{destination}|2000"
        dom_ids = {
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
            "document_id": destination_document,
        }
        native = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            storage_available=False, dom_ids=dom_ids,
        )
        native_pending = {**expected, "kind": "NATIVE"}
        native_identity = _ctbc_tab_identity(native)
        ctbc_identity_runtime(native_identity)["transition"]["pending_navigation"] = native_pending
        ctbc_identity_runtime(native_identity)["document"][
            "last_document"
        ] = destination_document
        self.assertTrue(await _ctbc_attest_fast_destination(
            native, "seat", config()
        ))
        self.assertIsNone(
            ctbc_identity_runtime(native_identity)["transition"][
                "pending_navigation"
            ]
        )

        fast = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            storage_available=False, dom_ids=dom_ids,
        )
        fast_pending = {**expected, "kind": "FAST", "nonce": "5" * 32}
        fast_identity = _ctbc_tab_identity(fast)
        ctbc_identity_runtime(fast_identity)["transition"]["pending_navigation"] = fast_pending
        ctbc_identity_runtime(fast_identity)["document"][
            "last_document"
        ] = destination_document
        self.assertFalse(await _ctbc_attest_fast_destination(
            fast, "seat", config()
        ))
        self.assertEqual(CTBC_TEST_AREA_URL, fast.target.url)
        self.assertEqual("RETURNING", fast_pending["phase"])
        return_script = next(
            script for script in fast.evaluate_scripts
            if "__ticketsHunterCtbcReturn" in script
        )
        self.assertIn("location.href !== sourceUrl", return_script)

    async def test_async_old_navigation_observation_cannot_replace_new_nonce(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        replacement = {
            "kind": "NATIVE", "phase": "DISPATCH_UNKNOWN",
            "nonce": "6" * 32, "area_url": CTBC_TEST_AREA_URL,
        }
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1, "nonce": "7" * 32, "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )
        identity = _ctbc_tab_identity(tab)
        tab.on_marker_read = lambda: ctbc_identity_runtime(identity)[
            "transition"
        ].__setitem__(
            "pending_navigation", replacement
        )

        self.assertFalse(await _ctbc_attest_fast_destination(
            tab, "seat", config()
        ))
        self.assertIs(replacement, ctbc_identity_runtime(identity)["transition"]["pending_navigation"])
        self.assertEqual(destination, tab.target.url)

        other_destination = destination.replace("GROUP_FRONT", "GROUP_OTHER")
        native = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            dom_ids={
                "performance_id": "WRONG", "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
            },
        )
        native_identity = _ctbc_tab_identity(native)
        old_pending = {
            "kind": "NATIVE", "phase": "DISPATCH_UNKNOWN",
            "nonce": "a" * 32, "area_url": CTBC_TEST_AREA_URL,
            "destination_url": destination, "route": "0205",
            "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "GROUP_FRONT", "price_area_id": "PRICE_FRONT",
        }
        new_pending = {**old_pending, "nonce": "b" * 32}
        ctbc_identity_runtime(native_identity)["transition"]["pending_navigation"] = old_pending

        def replace_during_destination_read():
            ctbc_identity_runtime(native_identity)["transition"]["pending_navigation"] = new_pending
            native.target.url = other_destination

        native.on_destination_state = replace_during_destination_read
        self.assertFalse(await _ctbc_attest_fast_destination(
            native, "seat", config()
        ))
        self.assertIs(new_pending, ctbc_identity_runtime(native_identity)["transition"]["pending_navigation"])
        self.assertEqual(other_destination, native.target.url)

    async def test_browser_marker_blocks_duplicate_fast_dispatch_after_state_loss(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        marker = {
            "version": 1,
            "nonce": "f" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        }
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [row],
            url=source,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            marker=marker,
        )

        cfg = config(area_keyword='"109區前排"')
        cfg["area_auto_select"]["enable"] = False
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertEqual(source, tab.target.url)
        self.assertEqual(marker, tab.session_marker)
        self.assertFalse(button.clicked)
        self.assertIsNotNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertFalse(any("fetch(areaUrl" in script for script in tab.evaluate_scripts))

        tab.session_marker["expires_at"] = time.time() * 1000 - 1
        ctbc_identity_runtime(identity)["transition"]["pending_navigation"]["created_at"] -= 30
        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
            reload_page.assert_awaited_once()

            ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
            self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))

        self.assertTrue(button.clicked)
        self.assertIsNone(tab.session_marker)
        pending = ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
        self.assertEqual("DATE_NATIVE", pending["kind"])
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])

    async def test_marker_only_fast_destination_returns_then_allows_native_retry(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        area_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "a" * 32,
                "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "destination-document"

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        pending = ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
        self.assertEqual(("FAST", "RETURNING"), (pending["kind"], pending["phase"]))
        self.assertEqual(area_url, tab.target.url)
        self.assertIsNotNone(tab.session_marker)

        ctbc_identity_runtime(identity)["document"]["last_document"] = "area-document"
        self.assertTrue(await _ctbc_reconcile_area_navigation(tab, area_url))
        self.assertIsNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertIsNone(tab.session_marker)

    async def test_active_fast_marker_blocks_native_area_snapshot(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR,
            [],
            url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            native_clears_marker=False,
            native_destination=destination,
            marker={
                "version": 1,
                "nonce": "b" * 32,
                "source": "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L",
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            },
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
            },
        )

        self.assertFalse(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertFalse(any(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))
        self.assertFalse(any(
            "new MutationObserver(collect)" in script
            for script in tab.evaluate_scripts
        ))
        self.assertIsNotNone(tab.session_marker)
        pending = ctbc_tab_runtime(tab)["transition"]["pending_navigation"]
        self.assertEqual("FAST", pending["kind"])

    def test_fast_marker_requires_exact_pending_identity(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        marker_pending = _ctbc_pending_from_fast_marker({
            "version": 1,
            "nonce": "1" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        })
        pending = dict(marker_pending)
        self.assertTrue(_ctbc_fast_marker_matches_pending(marker_pending, pending))

        changes = {
            "source_url": source + "&TRACKING=1",
            "destination_url": destination.replace("GROUP_FRONT", "GROUP_OTHER"),
            "route": "0202",
            "performance_id": "OTHER_PERFORMANCE",
            "product_id": "OTHER_PRODUCT",
            "group_id": "OTHER_GROUP",
            "price_area_id": "OTHER_PRICE",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                changed = dict(pending)
                changed[key] = value
                self.assertFalse(
                    _ctbc_fast_marker_matches_pending(marker_pending, changed)
                )

    async def test_malformed_marker_clears_once_and_preserves_racing_replacement(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        button = FakeElement("<button id='buy_btn'>購買</button>")
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>一般席</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        cfg = config()
        cfg["area_auto_select"]["enable"] = False
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, marker_raw="{broken-json",
        )

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertIsNone(tab._marker_raw())
        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertTrue(button.clicked)

        replacement = {
            "version": 1,
            "nonce": "2" * 32,
            "source": source,
            "destination": destination,
            "expires_at": time.time() * 1000 + 15000,
        }
        racing = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True, marker_raw="{old-broken-json",
        )

        def replace_marker():
            racing.session_marker_raw = None
            racing.session_marker = replacement

        racing.on_raw_marker_delete = replace_marker
        self.assertFalse(await nodriver_ctbc_date_auto_select(racing, cfg))
        self.assertEqual(replacement, racing.session_marker)

        far_future = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [row], url=source,
            semantic_navigation=True,
            marker={**replacement, "expires_at": time.time() * 1000 + 60000},
        )
        self.assertFalse(await nodriver_ctbc_date_auto_select(far_future, cfg))
        self.assertIsNone(far_future.session_marker)

    async def test_malformed_destination_marker_fails_one_tick_then_recovers(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker_raw="not-json",
        )

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        self.assertIsNone(tab._marker_raw())
        self.assertTrue(await _ctbc_attest_fast_destination(tab, "seat", config()))

    async def test_native_exception_reloads_before_same_area_retry(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        snapshot = json.dumps(make_ctbc_area_snapshot(include_back=False))
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR, [], url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[snapshot],
            native_commit_outcome="throw_after_dispatch",
        )
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        cfg = config(area_keyword='"109區前排"')

        self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
        pending = ctbc_identity_runtime(identity)["transition"]["pending_navigation"]
        self.assertEqual("NATIVE", pending["kind"])
        self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
        self.assertEqual(1, sum(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))

        pending["created_at"] -= 30
        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
            self.assertFalse(await nodriver_ctbc_area_auto_select(tab, cfg))
            reload_page.assert_awaited_once()

            ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
            tab.native_commit_outcome = True
            tab.native_destination = destination
            tab.evaluate_results.append(snapshot)
            self.assertTrue(await nodriver_ctbc_area_auto_select(tab, cfg))

        self.assertEqual(2, sum(
            "__ticketsHunterCtbcNativeAreaCommit" in script
            for script in tab.evaluate_scripts
        ))

    async def test_native_exception_after_navigation_is_consumed_at_destination(self):
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        source_document = f"{CTBC_TEST_AREA_URL}|1000"
        destination_document = f"{destination}|2000"
        tab = FakeTab(
            CTBC_AREA_ROW_SELECTOR, [], url=CTBC_TEST_AREA_URL,
            semantic_navigation=True,
            evaluate_results=[json.dumps(make_ctbc_area_snapshot(include_back=False))],
            native_commit_outcome="throw_after_dispatch",
            native_clears_marker=False,
            native_destination=destination,
            dom_ids={
                "performance_id": "P16D0B8Z",
                "product_id": "P16ANF0L",
                "group_id": "GROUP_FRONT",
                "price_area_id": "PRICE_FRONT",
                "document_id": destination_document,
            },
            on_native_commit=lambda: setattr(tab, "session_marker", {
                "version": 1,
                "nonce": "8" * 32,
                "source": (
                    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
                    "PRODUCT_ID=P16ANF0L"
                ),
                "destination": destination,
                "expires_at": time.time() * 1000 + 15000,
            }),
        )

        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = source_document
        self.assertFalse(await nodriver_ctbc_area_auto_select(
            tab, config(area_keyword='"109區前排"')
        ))
        self.assertIsNotNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertIsNotNone(tab.session_marker)
        ctbc_identity_runtime(identity)["document"]["last_document"] = destination_document
        self.assertTrue(await _ctbc_attest_fast_destination(
            tab, "seat", config(area_keyword='"109區前排"')
        ))
        self.assertIsNone(
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ]
        )
        self.assertIsNone(tab.session_marker)

    async def test_expired_exact_marker_fails_closed_and_manual_arrival_passes(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = FakeTab(
            "", [], url=destination, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "c" * 32,
                "source": source,
                "destination": destination,
                "expires_at": time.time() * 1000 - 1,
            },
        )
        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))

        manual = FakeTab("", [], url=destination, semantic_navigation=True)
        self.assertTrue(await _ctbc_attest_fast_destination(manual, "seat", config()))

    async def test_marker_destination_mismatch_fails_closed(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
        marker_destination = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        current_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P16D0B8Z&GROUP_ID=OTHER&"
            "PERFORMANCE_PRICE_AREA_ID=OTHER_PRICE"
        )
        tab = FakeTab(
            "", [], url=current_url, semantic_navigation=True,
            marker={
                "version": 1,
                "nonce": "e" * 32,
                "source": source,
                "destination": marker_destination,
                "expires_at": time.time() * 1000 + 15000,
            },
        )

        self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
        self.assertEqual(CTBC_TEST_AREA_URL, tab.target.url)

    async def test_vip_presale_uses_native_click_without_fast_prefetch(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0P&STARTDATE=2026/09/18"
        )
        button = FakeElement(
            "<button id='buy_btn'>2026季票<br>優先訂購》</button>",
            attrs={"onclick": "VipSellCheck('P1DN7WJI');"},
        )
        row = FakeElement(
            "<tr><td>2026/09/18(五)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>2026季票 優先訂購》</button></td></tr>",
            children={"button#buy_btn": button},
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/18"')
        ))
        self.assertEqual(1, button.click_count)
        self.assertEqual(0, tab.evaluate_count)

    async def test_unknown_single_handler_uses_scoped_structural_native_fallback(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement(
            "<button>會員立即選位</button>",
            attrs={"onclick": "DragonMemberCheck('P1DRAGON')"},
        )
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>會員立即選位</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertEqual(1, button.click_count)
        self.assertEqual(0, tab.evaluate_count)

    async def test_empty_button_keeps_structural_native_fallback(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement("<button></button>")
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button></button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertTrue(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertEqual(1, button.click_count)

    async def test_anchor_requires_a_same_tenant_same_product_purchase_target(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L&STARTDATE=2026/09/10"
        )
        safe_href = (
            "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
        )
        safe_anchor = FakeElement(
            "<a>立即選位</a>", tag_name="A", attrs={"href": safe_href}
        )
        safe_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>立即選位</a></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [safe_anchor]},
        )
        safe_tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [safe_row], url=source)
        self.assertTrue(await nodriver_ctbc_date_auto_select(safe_tab, config()))
        self.assertTrue(safe_anchor.clicked)

        unsafe_hrefs = (
            "https://example.com/UTK0204_?PERFORMANCE_ID=P1&PRODUCT_ID=P16ANF0L",
            "https://tix.ctbcsports.com:444/BROTHERS/UTK0204_?PERFORMANCE_ID=P1&PRODUCT_ID=P16ANF0L",
            "/BROTHERS/member/account",
            "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=OTHER",
            "/OTHER/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
        )
        for href in unsafe_hrefs:
            with self.subTest(href=href):
                anchor = FakeElement(
                    "<a>其他連結</a>", tag_name="A", attrs={"href": href}
                )
                row = FakeElement(
                    "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
                    "<td><a>其他連結</a></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(tab, config()))
                self.assertFalse(anchor.clicked)

        base_redirected_anchor = FakeElement(
            "<a>看似站內的連結</a>",
            tag_name="A",
            attrs={
                "href": safe_href,
                "resolved_href": (
                    "https://example.com/UTK0204_?"
                    "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
                ),
            },
        )
        base_redirected_row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>看似站內的連結</a></td></tr>",
            child_lists={
                CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [base_redirected_anchor]
            },
        )
        base_redirected_tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [base_redirected_row], url=source
        )
        self.assertFalse(
            await nodriver_ctbc_date_auto_select(base_redirected_tab, config())
        )
        self.assertFalse(base_redirected_anchor.clicked)

        for label, attrs in (
            ("onclick", {"href": safe_href, "onclick": "return evil()"}),
            ("target", {"href": safe_href, "target": "_blank"}),
            ("download", {"href": safe_href, "download": "ticket.html"}),
            ("ping", {"href": safe_href, "ping": "https://example.com/ping"}),
        ):
            with self.subTest(anchor_side_effect=label):
                anchor = FakeElement(
                    "<a>立即選位</a>", tag_name="A", attrs=attrs
                )
                row = FakeElement(
                    "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
                    "<td><a>立即選位</a></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
                )
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source), config()
                ))
                self.assertFalse(anchor.clicked)

    async def test_anchor_href_change_after_scan_is_rejected(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=P16ANF0L&STARTDATE=2026/09/10"
        )
        anchor = FakeElement(
            "<a>立即選位</a>",
            tag_name="A",
            attrs={
                "href": "UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
            },
            commit_attrs={"href": "https://example.com/changed"},
        )
        row = FakeElement(
            "<tr><td>2026/09/10(四)18:35</td><td>洲際棒球場</td>"
            "<td><a>立即選位</a></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [anchor]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertFalse(await nodriver_ctbc_date_auto_select(tab, config()))
        self.assertFalse(anchor.clicked)

    async def test_structural_fallback_rejects_ambiguous_or_unsafe_controls(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        cases = (
            ("hidden", FakeElement(attrs={"onclick": "FutureBuy('P1')"}, visible=False)),
            ("aria-disabled", FakeElement(
                attrs={"onclick": "FutureBuy('P1')", "aria-disabled": "true"}
            )),
            ("assignment", FakeElement(
                attrs={"onclick": "location.href='https://evil.example/'"}
            )),
            ("multiple-statements", FakeElement(
                attrs={"onclick": "FutureBuy('P1');alert('x')"}
            )),
            ("submit", FakeElement(
                attrs={"onclick": "FutureBuy('P1')", "type": "submit"}
            )),
            ("implicit-form-submit", FakeElement(
                attrs={"onclick": "", "has_form": True}
            )),
            ("formaction", FakeElement(
                attrs={
                    "onclick": "FutureBuy('P1')",
                    "type": "button",
                    "formaction": "https://evil.example/",
                }
            )),
        )
        for label, button in cases:
            with self.subTest(label=label):
                row = FakeElement(
                    "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
                    "<td><button>訂購</button></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    tab, config(date_keyword='"9/08"')
                ))
                self.assertFalse(button.clicked)

        first = FakeElement(attrs={"onclick": "FutureBuy('P1')"})
        second = FakeElement(attrs={"onclick": "OtherBuy('P1')"})
        ambiguous_row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>訂購一</button><button>訂購二</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [first, second]},
        )
        ambiguous_tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [ambiguous_row], url=source)
        self.assertFalse(await nodriver_ctbc_date_auto_select(
            ambiguous_tab, config(date_keyword='"9/08"')
        ))
        self.assertFalse(first.clicked)
        self.assertFalse(second.clicked)

    async def test_native_commit_rejects_a_control_that_changed_after_scan(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        button = FakeElement(
            "<button>會員立即選位</button>",
            attrs={"onclick": "FutureBuy('P1')"},
            native_commit_allowed=False,
        )
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
            "<td><button>會員立即選位</button></td></tr>",
            child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)

        self.assertFalse(await nodriver_ctbc_date_auto_select(
            tab, config(date_keyword='"9/08"')
        ))
        self.assertFalse(button.clicked)

    async def test_native_commit_revalidates_the_complete_control_fingerprint(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        mutations = (
            ("tag_name", {}, "A"),
            ("href", {"href": "/changed"}, None),
            ("role", {"role": "link"}, None),
            ("type", {"type": "submit"}, None),
            ("target", {"target": "_blank"}, None),
            ("formaction", {"formaction": "/changed"}, None),
            ("onclick", {"onclick": "OtherBuy('P2')"}, None),
        )
        for label, commit_attrs, commit_tag_name in mutations:
            with self.subTest(field=label):
                button = FakeElement(
                    "<button>會員立即選位</button>",
                    attrs={
                        "onclick": "FutureBuy('P1')",
                        "role": "button",
                        "type": "button",
                    },
                    commit_attrs=commit_attrs,
                    commit_tag_name=commit_tag_name,
                )
                row = FakeElement(
                    "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td>"
                    "<td><button>會員立即選位</button></td></tr>",
                    child_lists={CTBC_DATE_PURCHASE_CONTROL_SELECTOR: [button]},
                )
                tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
                self.assertFalse(await nodriver_ctbc_date_auto_select(
                    tab, config(date_keyword='"9/08"')
                ))
                self.assertFalse(button.clicked)

    async def test_wdragons_logged_in_date_uses_native_notice_preserving_flow(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16MXT0U"
        onclick = "location.href='UTK0204_?PERFORMANCE_ID=P16DRAGON&PRODUCT_ID=P16MXT0U'"
        button = FakeElement(
            "<button id='buy_btn'>購買</button>",
            attrs={"id": "buy_btn", "onclick": onclick},
        )
        row = FakeElement(
            "<tr><td>2026/09/08(二)18:35</td><td>臺北大巨蛋</td><td><button>購買</button></td></tr>",
            children={"button#buy_btn": button},
        )
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [row], url=source)
        cfg = config(date_keyword='"9/08"', area_keyword='"L2"')

        self.assertTrue(await nodriver_ctbc_date_auto_select(tab, cfg))
        self.assertTrue(button.clicked)
        self.assertEqual(0, tab.evaluate_count)

    async def test_failed_fast_destination_remains_fail_closed_until_area_page(self):
        seat_url = "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=35&PERFORMANCE_PRICE_AREA_ID=P16D12YO"
        tab = FakeTab("", [], url=seat_url, evaluate_results=[True, True])
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["transition"]["pending_navigation"] = {
            "kind": "FAST", "phase": "RETURNING", "nonce": "d" * 32,
            "route": "0205", "performance_id": "P16D0B8Z", "product_id": "P16ANF0L",
            "group_id": "35", "price_area_id": "P16D12YO",
            "area_url": "https://tix.ctbcsports.com/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
            "created_at": time.monotonic(),
        }
        try:
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertFalse(await _ctbc_attest_fast_destination(tab, "seat", config()))
            self.assertIsNotNone(
                ctbc_identity_runtime(identity)["transition"][
                    "pending_navigation"
                ]
            )
        finally:
            ctbc_identity_runtime(identity)["transition"][
                "pending_navigation"
            ] = None

    async def test_captcha_does_not_submit_when_force_submit_is_off(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": False}
        submit_key = _ctbc_purchase_state_key(tab)
        with patch(
            "platforms.utiki.workflow.dispatch_submit",
            AsyncMock(),
        ) as dispatch:
            self.assertTrue(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None
            ))
        dispatch.assert_not_awaited()
        self.assertFalse(tab.button.clicked)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_submits_when_force_submit_is_on(self):
        tab = FakeSubmitTab(dialog="", dialogs=["", "", "成功加入購物車"])
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = _ctbc_purchase_state_key(tab)
        try:
            self.assertTrue(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None
            ))
            self.assertTrue(tab.button.clicked)
            self.assertEqual(1, tab.button.click_count)
        finally:
            ctbc_tab_runtime(tab)["purchase"]["active_submit"] = None
            ctbc_tab_runtime(tab)["purchase"]["outcomes"].pop(submit_key, None)

    async def test_delayed_success_does_not_submit_twice(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = _ctbc_purchase_state_key(tab)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIsNotNone(self._active_submit_for(tab, submit_key))

        tab.dialog = "成功加入購物車"
        tab.dialog_visible = True
        self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("success", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(1, tab.dialog_button.click_count)

    async def test_delayed_checkout_completes_the_source_submit(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        source_url = str(tab.target.url)
        submit_key = self._track_submit_state(tab)
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": time.monotonic(),
            "phase": "awaiting_result",
            "source_url": source_url,
            "document_id": tab.document_id,
        })
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        tab.target.url = checkout_url

        result = await _reconcile_utiki_purchase(
            tab, checkout_url, "checkout", cfg
        )

        self.assertEqual("success", result)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("success", self._outcome_for(tab, submit_key)["kind"])

    async def test_delayed_negative_dialog_overrides_checkout_success(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": time.monotonic(),
            "phase": "awaiting_result",
            "source_url": str(tab.target.url),
            "document_id": tab.document_id,
        })
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        tab.target.url = checkout_url
        self.assertEqual(
            "success",
            await _reconcile_utiki_purchase(tab, checkout_url, "checkout", cfg),
        )

        tab.dialog = "已超出可購買額度"
        tab.dialog_visible = True
        result = await _reconcile_utiki_purchase(
            tab, checkout_url, "checkout", cfg
        )

        self.assertEqual("blocked", result)
        self.assertEqual("quota", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(1, tab.dialog_button.click_count)

    async def test_outer_router_checkout_success_and_quota_priority(self):
        cfg = config()
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="unverified"),
        ), patch(
            "platforms.utiki.workflow._handle_ctbc_checkout",
            AsyncMock(),
        ) as checkout:
            success_tab = FakeSubmitTab(dialog="")
            success_key = self._track_submit_state(success_tab)
            self._set_active_submit(success_tab, success_key, {
                "dispatched_at": time.monotonic(),
                "source_url": str(success_tab.target.url),
                "document_id": success_tab.document_id,
            })
            success_tab.target.url = checkout_url
            await nodriver_ctbc_main(success_tab, checkout_url, cfg, ocr=None)
            checkout.assert_awaited_once_with(success_tab, cfg)
            self.assertEqual(
                "success", self._outcome_for(success_tab, success_key)["kind"]
            )

            checkout.reset_mock()
            quota_tab = FakeSubmitTab(dialog="已超出可購買額度")
            quota_key = self._track_submit_state(quota_tab)
            self._set_active_submit(quota_tab, quota_key, {
                "dispatched_at": time.monotonic(),
                "source_url": str(quota_tab.target.url),
                "document_id": quota_tab.document_id,
            })
            quota_tab.target.url = checkout_url
            await nodriver_ctbc_main(quota_tab, checkout_url, cfg, ocr=None)
            checkout.assert_not_awaited()
            self.assertEqual(
                "quota", self._outcome_for(quota_tab, quota_key)["kind"]
            )

    async def test_checkout_two_ticks_notifies_once_and_retains_runtime(self):
        cfg = config()
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        tab = FakeSubmitTab(dialog="", url=checkout_url)
        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="unverified"),
        ), patch(
            "platforms.utiki.workflow._reconcile_utiki_purchase",
            AsyncMock(return_value="success"),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.send_discord_notification",
        ) as discord, patch(
            "platforms.utiki.workflow.send_telegram_notification",
        ) as telegram:
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)

        identity = _ctbc_tab_identity(tab)
        self.assertIn(identity, _state["tabs"])
        self.assertEqual(
            1,
            len(ctbc_identity_runtime(identity)["purchase"]["checkout_notified"]),
        )
        discord.assert_called_once_with(cfg, "order", "CTBC Sports")
        telegram.assert_called_once_with(cfg, "order", "CTBC Sports")

    async def test_checkout_notification_uses_submit_nonce_as_order_epoch(self):
        cfg = config()
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_SEAT_URL)
        first_key = _ctbc_purchase_state_key(tab)
        tab.target.url = checkout_url
        purchase = ctbc_tab_runtime(tab)["purchase"]
        first_outcome = {"kind": "success", "nonce": "a" * 32}
        purchase["outcomes"][first_key] = first_outcome
        purchase["last_submit"] = {
            "purchase_key": first_key,
            "nonce": first_outcome["nonce"],
        }

        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="unverified"),
        ), patch(
            "platforms.utiki.workflow._reconcile_utiki_purchase",
            AsyncMock(return_value="success"),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.send_discord_notification",
        ) as discord, patch(
            "platforms.utiki.workflow.send_telegram_notification",
        ) as telegram:
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
            second_outcome = {"kind": "success", "nonce": "b" * 32}
            purchase["outcomes"][first_key] = second_outcome
            purchase["last_submit"] = {
                "purchase_key": first_key,
                "nonce": second_outcome["nonce"],
            }
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
            await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)

        self.assertIs(second_outcome, purchase["outcomes"][first_key])
        self.assertEqual(first_key, purchase["last_submit"]["purchase_key"])
        self.assertEqual("b" * 32, purchase["last_submit"]["nonce"])
        self.assertEqual({
            ("submit", first_key, "a" * 32),
            ("submit", first_key, "b" * 32),
        }, purchase["checkout_notified"])
        self.assertEqual(2, discord.call_count)
        self.assertEqual(2, telegram.call_count)

    async def test_outer_router_reaches_seat_conflict_recovery(self):
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_SEAT_URL)
        cfg = config()
        submit_key = self._track_submit_state(tab)
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = {
            "kind": "seat_conflict", "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": "source-document", "nonce": "a" * 32,
            "page_type": "seat", "dialog_dismissed": True,
            "reload_dispatched": False, "recovery_document_id": "",
        }

        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="normal"),
        ), patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page:
            await nodriver_ctbc_main(
                tab, CTBC_TEST_SEAT_URL, cfg, ocr=None
            )

        reload_page.assert_awaited_once_with(
            tab, cfg, "selected seats were taken"
        )

    async def test_outer_router_reaches_captcha_dismiss_race_recovery(self):
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_SEAT_URL)
        cfg = config()
        submit_key = self._track_submit_state(tab)
        ctbc_tab_runtime(tab)["document"]["last_document"] = "source-document"
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = {
            "kind": "captcha", "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": "source-document", "nonce": "a" * 32,
            "reset_phase": "", "reset_source_document_id": "",
        }

        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="normal"),
        ), patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"old-image", b"new-image"]),
        ):
            await nodriver_ctbc_main(
                tab, CTBC_TEST_SEAT_URL, cfg, ocr=None
            )

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_outer_router_auth_uses_workflow_document_token(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤", url=CTBC_TEST_SEAT_URL)
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        source_document = f"{CTBC_TEST_SEAT_URL}|481522.125"
        runtime["document"]["last_document"] = source_document
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": time.monotonic(),
            "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": source_document,
            "document_id": "481522.125",
        })
        snapshot = parse_seat_snapshot(make_ctbc_seat_snapshot())

        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            AsyncMock(return_value="normal"),
        ), patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow.read_seat_snapshot",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.workflow.commit_seat",
            AsyncMock(return_value=CommitResult.COMMITTED),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
            AsyncMock(),
        ), patch(
            "platforms.utiki.workflow._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(side_effect=(
                RuntimeError("fingerprint failed"),
                "current-credentials",
                "current-credentials",
            )),
        ):
            await nodriver_ctbc_main(
                tab, CTBC_TEST_SEAT_URL, cfg, ocr=None
            )
            outcome = self._outcome_for(tab, submit_key)
            self.assertEqual("auth", outcome["kind"])
            self.assertEqual(source_document, outcome["source_document_id"])

            await nodriver_ctbc_main(
                tab, CTBC_TEST_SEAT_URL, cfg, ocr=None
            )
            self.assertIs(outcome, self._outcome_for(tab, submit_key))

            runtime["document"]["last_document"] = (
                f"{CTBC_TEST_SEAT_URL}|481523.250"
            )
            await nodriver_ctbc_main(
                tab, CTBC_TEST_SEAT_URL, cfg, ocr=None
            )

        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_active_submit_blocks_a_different_purchase(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        second_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A2"
        )
        tab = FakeSubmitTab(dialog="", url=first_url)
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        first_key = self._track_submit_state(tab)
        active = self._set_active_submit(tab, first_key, time.monotonic())
        tab.target.url = second_url
        second_key = _ctbc_purchase_state_key(tab)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertIs(active, ctbc_tab_runtime(tab)["purchase"]["active_submit"])
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(0, tab.button.click_count)

    async def test_different_purchase_waits_then_ignores_source_unknown(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        second_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A2"
        )
        tab = FakeSubmitTab(dialog="", url=first_url)
        cfg = config()
        first_key = self._track_submit_state(tab)
        active = self._set_active_submit(tab, first_key, 100.0)
        tab.target.url = second_url

        with patch("platforms.utiki.workflow.time.monotonic", return_value=105.0):
            self.assertEqual(
                "pending",
                await _reconcile_utiki_purchase(tab, second_url, "seat", cfg),
            )
        self.assertIs(active, ctbc_tab_runtime(tab)["purchase"]["active_submit"])

        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertEqual(
                "blocked",
                await _reconcile_utiki_purchase(tab, second_url, "seat", cfg),
            )
        self.assertIsNone(ctbc_tab_runtime(tab)["purchase"]["active_submit"])
        self.assertEqual("unknown", self._outcome_for(tab, first_key)["kind"])
        self.assertIsNone(
            await _reconcile_utiki_purchase(tab, second_url, "seat", cfg)
        )

    async def test_late_dialog_on_new_purchase_still_uses_source_pointer(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        second_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A2"
        )
        tab = FakeSubmitTab(dialog="", url=first_url)
        cfg = config()
        first_key = self._track_submit_state(tab)
        self._set_active_submit(tab, first_key, 100.0)
        tab.target.url = second_url
        second_key = _ctbc_purchase_state_key(tab)
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertEqual(
                "blocked",
                await _reconcile_utiki_purchase(tab, second_url, "seat", cfg),
            )

        tab.dialog = "該位置已被選走"
        tab.dialog_visible = True
        self.assertEqual(
            "blocked",
            await _reconcile_utiki_purchase(tab, second_url, "seat", cfg),
        )

        self.assertEqual("seat_conflict", self._outcome_for(tab, first_key)["kind"])
        self.assertIsNone(self._outcome_for(tab, second_key))

    async def test_unknown_dialog_preserves_text_without_dismissal(self):
        tab = FakeSubmitTab(dialog="  系統  稍後再試  ")
        submit_key = self._track_submit_state(tab)
        self._set_active_submit(tab, submit_key, time.monotonic())

        self.assertEqual(
            "rejected", await _handle_ctbc_submit_dialog(tab, config())
        )

        outcome = self._outcome_for(tab, submit_key)
        self.assertEqual("unknown", outcome["kind"])
        self.assertEqual("系統 稍後再試", outcome["text"])
        self.assertEqual(0, tab.dialog_button.click_count)

    async def test_success_dismiss_failure_remains_blocked(self):
        tab = FakeSubmitTab(dialog="成功加入購物車")
        submit_key = self._track_submit_state(tab)
        self._set_active_submit(tab, submit_key, time.monotonic())

        with patch(
            "platforms.utiki.workflow.dismiss_dialog",
            AsyncMock(return_value=CommitResult.REJECTED),
        ) as dismiss:
            self.assertEqual(
                "rejected", await _handle_ctbc_submit_dialog(tab, config())
            )

        dismiss.assert_awaited_once()
        tab.dialog = ""
        tab.dialog_visible = False
        with patch(
            "platforms.utiki.workflow.dispatch_submit",
            AsyncMock(),
        ) as dispatch:
            self.assertFalse(
                await _ctbc_captcha_and_submit(tab, config(), ocr=None)
            )

        dispatch.assert_not_awaited()
        self.assertEqual("success", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(0, tab.dialog_button.click_count)
        self.assertEqual(0, tab.button.click_count)

    async def test_dialog_attribution_collision_preserves_both_blockers(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        second_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A2"
        )
        tab = FakeSubmitTab(dialog="驗證碼錯誤", url=first_url)
        first_key = _ctbc_purchase_state_key(tab)
        tab.target.url = second_url
        second_key = _ctbc_purchase_state_key(tab)
        purchase = ctbc_tab_runtime(tab)["purchase"]
        purchase["outcomes"][first_key] = {
            "kind": "unknown", "reason": "submit_outcome",
            "source_url": first_url, "source_document_id": "source-a",
            "nonce": "a" * 32,
        }
        purchase["outcomes"][second_key] = {
            "kind": "quota", "reason": "", "source_url": second_url,
            "source_document_id": "source-b", "nonce": "b" * 32,
        }
        purchase["last_submit"] = {
            "purchase_key": first_key, "nonce": "a" * 32,
        }

        self.assertEqual(
            "rejected", await _handle_ctbc_submit_dialog(tab, config())
        )

        self.assertEqual("unknown", self._outcome_for(tab, first_key)["kind"])
        self.assertEqual("quota", self._outcome_for(tab, second_key)["kind"])
        self.assertEqual(0, tab.dialog_button.click_count)
        self.assertEqual(0, tab.captcha_refresh.click_count)

    async def test_rejected_new_submit_preserves_prior_dialog_provenance(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        second_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A2"
        )
        tab = FakeSubmitTab(dialog="", url=first_url)
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        first_key = _ctbc_purchase_state_key(tab)
        purchase = ctbc_tab_runtime(tab)["purchase"]
        purchase["outcomes"][first_key] = {
            "kind": "unknown", "reason": "submit_outcome",
            "source_url": first_url, "source_document_id": "source-a",
            "nonce": "a" * 32,
        }
        purchase["last_submit"] = {
            "purchase_key": first_key, "nonce": "a" * 32,
        }
        tab.target.url = second_url

        with patch(
            "platforms.utiki.workflow.dispatch_submit",
            AsyncMock(return_value=SubmitDispatchResult.STALE),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(first_key, purchase["last_submit"]["purchase_key"])
        tab.dialog = "已超出可購買額度"
        tab.dialog_visible = True
        self.assertEqual("quota", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertEqual("quota", self._outcome_for(tab, first_key)["kind"])

    async def test_delayed_auth_keeps_source_document_on_fingerprint_failure(self):
        source_url = str(FakeSubmitTab().target.url)
        tab = FakeSubmitTab(
            dialog="帳號或密碼錯誤",
            url="https://tix.ctbcsports.com/BROTHERS/UTK0206_",
        )
        tab.target.url = source_url
        source_key = _ctbc_purchase_state_key(tab)
        tab.target.url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        purchase = ctbc_tab_runtime(tab)["purchase"]
        purchase["outcomes"][source_key] = {
            "kind": "unknown", "reason": "submit_outcome",
            "source_url": source_url, "source_document_id": "source-document",
            "nonce": "a" * 32,
        }
        purchase["last_submit"] = {
            "purchase_key": source_key, "nonce": "a" * 32,
        }
        ctbc_tab_runtime(tab)["document"]["last_document"] = "checkout-document"

        with patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(side_effect=RuntimeError("fingerprint failed")),
        ):
            self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, config()))

        outcome = self._outcome_for(tab, source_key)
        self.assertEqual("auth", outcome["kind"])
        self.assertEqual("source-document", outcome["source_document_id"])

    async def test_delayed_seat_conflict_closes_and_clears_submit_guard(self):
        conflict_text = "該位置已被選走"
        tab = FakeSubmitTab(dialog="", dialogs=["", "", "", conflict_text])
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        self.assertIsNotNone(self._active_submit_for(tab, submit_key))

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page, patch(
            "platforms.utiki.workflow.read_seat_snapshot",
            AsyncMock(),
        ) as read_seat, patch(
            "platforms.utiki.workflow._ctbc_captcha_and_submit",
            AsyncMock(return_value=True),
        ) as submit:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("seat_conflict", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual("", tab.captcha_input.value)
        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")
        read_seat.assert_not_awaited()
        submit.assert_not_awaited()

    async def test_submit_outcome_timeout_never_clicks_twice(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        workflow_document = f"{tab.target.url}|481522.125"
        ctbc_tab_runtime(tab)["document"]["last_document"] = workflow_document

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(1, tab.button.click_count)
        active = self._active_submit_for(tab, submit_key)
        self.assertIsNotNone(active)
        self.assertEqual(workflow_document, active["source_document_id"])
        self.assertEqual("document-1", active["document_id"])

        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.button.click_count)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("unknown", self._outcome_for(tab, submit_key)["kind"])

    async def test_cosmetic_purchase_url_changes_never_submit_twice(self):
        cases = []
        for mode, route, group_id in (
            ("reserved", "UTK0205_", "G1"),
            ("general", "UTK0203_", ""),
        ):
            prefix = f"https://tix.ctbcsports.com/BROTHERS/{route}?"
            source_url = (
                f"{prefix}PERFORMANCE_ID=P1&GROUP_ID={group_id}&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            )
            cases.extend((
                (
                    f"{mode}-query-order",
                    source_url,
                    f"{prefix}GROUP_ID={group_id}&PERFORMANCE_PRICE_AREA_ID=A1&"
                    "PERFORMANCE_ID=P1",
                ),
                (f"{mode}-tracking", source_url, f"{source_url}&tracking=fixture"),
                (f"{mode}-fragment", source_url, f"{source_url}#fixture"),
            ))
        cases.append((
            "general-route-alias",
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0202_?"
                "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
            ),
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_PRICE_AREA_ID=A1&GROUP_ID=&PERFORMANCE_ID=P1"
            ),
        ))
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, source_url, cosmetic_url in cases:
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", url=source_url)
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_change_url(target=cosmetic_url):
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = target

                tab.button.click = click_and_change_url
                quantity_plan = (
                    self._quantity_plan(source_url)
                    if label.startswith("general") else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.time.monotonic",
                    return_value=100.0,
                ), patch(
                    "platforms.utiki.workflow.dispatch_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertFalse(
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            submit_plan=build_submit_plan(quantity_plan),
                        )
                    )
                self.assertIsNotNone(self._active_submit_for(tab, submit_key))
                with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
                    self.assertEqual(
                        "blocked",
                        await _reconcile_utiki_purchase(
                            tab,
                            cosmetic_url,
                            (
                                "general_admission"
                                if label.startswith("general")
                                else "seat"
                            ),
                            cfg,
                        ),
                    )

                self.assertEqual(submit_key, _ctbc_purchase_state_key(tab))
                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("unknown", self._outcome_for(tab, submit_key)["kind"])

    async def test_submit_navigation_requires_same_tenant_checkout(self):
        reserved_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        general_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
            "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        destinations = (
            (
                "reserved-checkout",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_",
                True,
            ),
            (
                "general-checkout",
                general_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_",
                True,
            ),
            (
                "cross-tenant-checkout",
                reserved_url,
                "https://tix.wdragons.com/UTK0206_",
                False,
            ),
            (
                "same-origin-unknown",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/complete",
                False,
            ),
            (
                "checkout-prefix-only",
                reserved_url,
                "https://tix.ctbcsports.com/BROTHERS/UTK0206_extra",
                False,
            ),
        )

        for label, source_url, destination, expected in destinations:
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", url=source_url)
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_navigate(target=destination):
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = target

                tab.button.click = click_and_navigate
                quantity_plan = (
                    self._quantity_plan(source_url)
                    if label == "general-checkout" else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.time.monotonic",
                    return_value=100.0,
                ), patch(
                    "platforms.utiki.workflow.dispatch_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertEqual(
                        expected,
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            submit_plan=build_submit_plan(quantity_plan),
                        ),
                    )
                self.assertEqual(1, tab.button.click_count)
                if expected:
                    self.assertIsNone(self._active_submit_for(tab, submit_key))
                    self.assertEqual(
                        "success", self._outcome_for(tab, submit_key)["kind"]
                    )
                    tab.target.url = source_url
                    self.assertFalse(
                        await _ctbc_captcha_and_submit(
                            tab, cfg, ocr=None,
                            submit_plan=build_submit_plan(quantity_plan),
                        )
                    )
                    self.assertEqual(1, tab.button.click_count)
                    continue

                self.assertIsNotNone(self._active_submit_for(tab, submit_key))
                with patch(
                    "platforms.utiki.workflow.time.monotonic",
                    return_value=111.0,
                ):
                    self.assertEqual(
                        "blocked",
                        await _reconcile_utiki_purchase(
                            tab, destination, "unknown", cfg
                        ),
                    )
                self.assertIsNone(self._active_submit_for(tab, submit_key))
                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("unknown", self._outcome_for(tab, submit_key)["kind"])

    async def test_checkout_only_completes_its_own_tab_submit(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        waiting_tab = FakeSubmitTab(dialog="")
        checkout_tab = FakeSubmitTab(dialog="")
        waiting_key = _ctbc_purchase_state_key(waiting_tab)
        checkout_key = _ctbc_purchase_state_key(checkout_tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(waiting_tab, cfg, ocr=None)
            )

            async def click_and_checkout():
                checkout_tab.button.clicked = True
                checkout_tab.button.click_count += 1
                checkout_tab.target.url = (
                    "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
                )

            checkout_tab.button.click = click_and_checkout
            self.assertTrue(
                await _ctbc_captcha_and_submit(checkout_tab, cfg, ocr=None)
            )

        self.assertIsNotNone(self._active_submit_for(waiting_tab, waiting_key))
        checkout_purchase = ctbc_tab_runtime(checkout_tab)["purchase"]
        self.assertIsNone(checkout_purchase["active_submit"])
        self.assertEqual(
            "success", checkout_purchase["outcomes"][checkout_key]["kind"]
        )

    async def test_submit_dialog_after_unverified_navigation_uses_source_purchase(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        tab = FakeSubmitTab(
            dialog="",
            dialogs=["", "", "已超出可購買額度"],
        )
        submit_key = _ctbc_purchase_state_key(tab)

        async def click_and_leave_purchase():
            tab.button.clicked = True
            tab.button.click_count += 1
            tab.target.url = "https://tix.ctbcsports.com/BROTHERS/complete"

        tab.button.click = click_and_leave_purchase
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("quota", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(1, tab.button.click_count)

    async def test_negative_dialog_takes_priority_over_checkout_navigation(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, dialog, expected in (
            ("quota", "已超出可購買額度", "quota"),
            ("seat-conflict", "該位置已被選走", "seat_conflict"),
        ):
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="", dialogs=["", "", dialog])
                submit_key = _ctbc_purchase_state_key(tab)

                async def click_and_checkout():
                    tab.button.clicked = True
                    tab.button.click_count += 1
                    tab.target.url = (
                        "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
                    )

                tab.button.click = click_and_checkout
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )

                self.assertIsNone(self._active_submit_for(tab, submit_key))
                self.assertEqual(expected, self._outcome_for(tab, submit_key)["kind"])
                if expected == "seat_conflict":
                    self.assertEqual(
                        "seat", self._outcome_for(tab, submit_key)["page_type"]
                    )
                self.assertNotEqual(
                    "success", self._outcome_for(tab, submit_key)["kind"]
                )
                self.assertEqual(1, tab.dialog_button.click_count)

    async def test_incomplete_purchase_identity_never_submits(self):
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        urls = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?PERFORMANCE_ID=P1",
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID="
            ),
            (
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&PERFORMANCE_ID=P2&GROUP_ID=G1&"
                "PERFORMANCE_PRICE_AREA_ID=A1"
            ),
        )

        for url in urls:
            with self.subTest(url=url):
                tab = FakeSubmitTab(dialog="", url=url)
                self.assertIsNone(_ctbc_purchase_state_key(tab))
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )
                self.assertEqual(0, tab.button.click_count)
                self.assertIsNone(ctbc_tab_runtime(tab)["purchase"]["active_submit"])

    async def test_submit_blockers_survive_cosmetic_url_variants(self):
        source_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cosmetic_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "tracking=fixture&PERFORMANCE_PRICE_AREA_ID=A1&GROUP_ID=G1&"
            "PERFORMANCE_ID=P1#fixture"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        quota_tab = FakeSubmitTab(dialog="已超出可購買額度", url=source_url)
        quota_key = _ctbc_purchase_state_key(quota_tab)
        self.assertFalse(await _ctbc_captcha_and_submit(quota_tab, cfg, ocr=None))
        quota_tab.dialog = ""
        quota_tab.dialog_visible = False
        quota_tab.target.url = cosmetic_url
        self.assertEqual(quota_key, _ctbc_purchase_state_key(quota_tab))
        self.assertFalse(await _ctbc_captcha_and_submit(quota_tab, cfg, ocr=None))
        self.assertEqual(0, quota_tab.button.click_count)
        self.assertEqual(
            "quota",
            self._outcome_for(quota_tab, quota_key)["kind"],
        )

        unknown_tab = FakeSubmitTab(dialog="", url=source_url)
        unknown_key = _ctbc_purchase_state_key(unknown_tab)
        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None)
            )
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(
                await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None)
            )
        unknown_tab.target.url = cosmetic_url
        self.assertEqual(unknown_key, _ctbc_purchase_state_key(unknown_tab))
        self.assertFalse(await _ctbc_captcha_and_submit(unknown_tab, cfg, ocr=None))
        self.assertEqual(1, unknown_tab.button.click_count)
        self.assertEqual(
            "unknown",
            self._outcome_for(unknown_tab, unknown_key)["kind"],
        )

    async def test_submit_state_isolated_by_purchase_identity(self):
        first_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}

        for label, second_url in (
            (
                "performance",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P2&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "group",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G2&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "price-area",
                "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A2",
            ),
            (
                "mode",
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
            (
                "tenant",
                "https://tix.wdragons.com/UTK0205_?"
                "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1",
            ),
        ):
            with self.subTest(label=label):
                tab = FakeSubmitTab(dialog="已超出可購買額度", url=first_url)
                first_key = _ctbc_purchase_state_key(tab)
                self.assertFalse(
                    await _ctbc_captcha_and_submit(tab, cfg, ocr=None)
                )
                tab.dialog = ""
                tab.dialog_visible = False
                tab.target.url = second_url
                second_key = _ctbc_purchase_state_key(tab)
                self.assertNotEqual(first_key, second_key)
                quantity_plan = (
                    self._quantity_plan(second_url)
                    if label == "mode" else None
                )

                async def dispatch_quantity(*_args):
                    await tab.button.click()
                    return SubmitDispatchResult.DISPATCHED

                with patch(
                    "platforms.utiki.workflow.dispatch_submit",
                    AsyncMock(side_effect=dispatch_quantity),
                ):
                    self.assertFalse(await _ctbc_captcha_and_submit(
                        tab, cfg, ocr=None,
                        submit_plan=build_submit_plan(quantity_plan),
                    ))

                self.assertEqual(1, tab.button.click_count)
                self.assertEqual("quota", self._outcome_for(tab, first_key)["kind"])
                self.assertIsNotNone(self._active_submit_for(tab, second_key))

    async def test_late_conflict_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "該位置已被選走"
        tab.dialog_visible = True
        self.assertEqual("seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertEqual("seat_conflict", self._outcome_for(tab, submit_key)["kind"])

    async def test_late_captcha_error_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        ctbc_tab_runtime(tab)["document"]["last_document"] = "document-1"

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "驗證碼錯誤"
        tab.dialog_visible = True
        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"old-image", b"new-image"]),
        ):
            self.assertEqual(
                "captcha", await _handle_ctbc_submit_dialog(tab, cfg)
            )
        self.assertIsNone(self._outcome_for(tab, submit_key))
        self.assertEqual("", tab.captcha_input.value)
        self.assertEqual(1, tab.captcha_refresh.click_count)

    async def test_late_auth_error_replaces_unknown_submit_outcome(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        with patch("platforms.utiki.workflow.time.monotonic", return_value=100.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        with patch("platforms.utiki.workflow.time.monotonic", return_value=111.0):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        tab.dialog = "帳號或密碼錯誤"
        tab.dialog_visible = True
        self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertEqual("auth", self._outcome_for(tab, submit_key)["kind"])

    async def test_auth_error_without_credentials_blocks_until_new_document(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": False}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"

        with patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(return_value=""),
        ), patch(
            "platforms.utiki.workflow._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ):
            self.assertEqual("auth", await _handle_ctbc_submit_dialog(tab, cfg))
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
            self.assertEqual(0, tab.button.click_count)

            ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
            self.assertTrue(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertIsNone(self._outcome_for(tab, submit_key))
        self.assertEqual(0, tab.button.click_count)

    async def test_auth_fingerprint_failure_keeps_document_blocker(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
            "document_id": "time-origin-1",
        })

        with patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(side_effect=RuntimeError("CDP read failed")),
        ) as fingerprint:
            self.assertEqual(
                "auth", await _handle_ctbc_submit_dialog(tab, cfg)
            )

        fingerprint.assert_awaited_once()
        expected_dialog = fingerprint.await_args.kwargs["expected_dialog"]
        self.assertEqual(str(tab.target.url), expected_dialog.page_url)
        self.assertEqual(tab.document_id, expected_dialog.document_id)
        self.assertEqual("帳號或密碼錯誤", expected_dialog.text)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        auth_outcome = self._outcome_for(tab, submit_key)
        self.assertEqual("auth", auth_outcome["kind"])
        self.assertEqual("", auth_outcome["fingerprint"])
        self.assertEqual("document-1", auth_outcome["source_document_id"])

        with patch(
            "platforms.utiki.workflow._fill_ctbc_inline_login",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._ctbc_login_fingerprint",
            AsyncMock(return_value="current-credentials"),
        ), patch(
            "platforms.utiki.workflow._dispatch_ctbc_submit",
            AsyncMock(),
        ) as dispatch:
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None
            ))
        dispatch.assert_not_awaited()

    async def test_click_exception_after_dispatch_blocks_second_submit(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        async def click_then_raise():
            tab.button.clicked = True
            tab.button.click_count += 1
            raise RuntimeError("CDP result unavailable")

        tab.button.click = click_then_raise
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.button.click_count)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("unknown", self._outcome_for(tab, submit_key)["kind"])

    async def test_conflict_waits_for_new_document_before_resubmitting(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        pending = {
            "kind": "seat_conflict",
            "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": "document-1",
            "dom_document_id": "time-origin-1",
            "product_id": "PRODUCT_FIXTURE",
            "page_type": "seat",
            "dialog_dismissed": True,
            "clear_phase": "",
            "clear_result": "",
            "reload_dispatched": False,
            "recovery_document_id": "",
        }
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = pending

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(side_effect=[False, True]),
        ) as reload_page, patch(
            "platforms.utiki.workflow.clear_conflict_selection",
            AsyncMock(return_value=CommitResult.STALE),
        ) as clear_selection:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertEqual(2, reload_page.await_count)
            clear_selection.assert_awaited_once()

            ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_conflict_records_awaiting_reload_without_redispatch(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        pending = {
            "kind": "seat_conflict", "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": "document-1", "page_type": "seat",
            "dialog_dismissed": True, "clear_phase": "done",
            "clear_result": "committed", "reload_dispatched": False,
            "recovery_document_id": "",
        }
        runtime["purchase"]["outcomes"][submit_key] = pending

        async def dispatch_reload(*_args, **_kwargs):
            runtime["refresh"]["reload_request"] = {
                "request_token": "conflict-reload",
                "reason": "selected seats were taken",
                "phase": "awaiting_document",
                "source_document_token": "document-1",
            }
            return False

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(side_effect=dispatch_reload),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(pending["reload_dispatched"])
            self.assertEqual("document-1", pending["recovery_document_id"])
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            reload_page.assert_awaited_once()

            runtime["document"]["last_document"] = "document-2"
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_conflict_dismiss_failure_blocks_reload(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dismiss_allowed = False
        cfg = config()
        submit_key = self._track_submit_state(tab)

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page, patch(
            "platforms.utiki.workflow.read_seat_snapshot",
            AsyncMock(),
        ) as read_seat:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertFalse(
            self._outcome_for(tab, submit_key)["dialog_dismissed"]
        )
        reload_page.assert_not_awaited()
        read_seat.assert_not_awaited()

    async def test_conflict_unknown_clear_is_not_dispatched_twice(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        pending = {
            "kind": "seat_conflict",
            "source_url": CTBC_TEST_SEAT_URL,
            "source_document_id": "document-1",
            "dom_document_id": "time-origin-1",
            "product_id": "PRODUCT_FIXTURE",
            "page_type": "seat",
            "dialog_dismissed": True,
            "clear_phase": "",
            "clear_result": "",
            "reload_dispatched": False,
            "recovery_document_id": "",
        }
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = pending

        with patch(
            "platforms.utiki.workflow.clear_conflict_selection",
            AsyncMock(side_effect=RuntimeError("CDP result unavailable")),
        ) as clear_selection, patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))
            ctbc_identity_runtime(identity)["document"]["last_document"] = (
                "document-2"
            )
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        clear_selection.assert_awaited_once()
        reload_page.assert_awaited_once_with(
            tab, cfg, "selected seats were taken"
        )
        self.assertEqual("done", pending["clear_phase"])
        self.assertEqual("unknown", pending["clear_result"])
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_conflict_clear_losing_outcome_stops_all_later_effects(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
            "document_id": "time-origin-1",
            "product_id": "PRODUCT_FIXTURE",
        })
        replacement = {"kind": "quota", "text": "new outcome"}

        async def replace_outcome(*_args):
            runtime["purchase"]["outcomes"][submit_key] = replacement
            return CommitResult.COMMITTED

        with patch(
            "platforms.utiki.workflow.clear_conflict_selection",
            AsyncMock(side_effect=replace_outcome),
        ), patch(
            "platforms.utiki.workflow._reset_ctbc_captcha", AsyncMock()
        ) as reset_captcha, patch(
            "platforms.utiki.workflow._reload_with_interval", AsyncMock()
        ) as reload_page:
            self.assertEqual(
                "seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg)
            )
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertIs(runtime["purchase"]["outcomes"][submit_key], replacement)
        reset_captcha.assert_not_awaited()
        reload_page.assert_not_awaited()

    async def test_conflict_busy_clear_blocks_second_recovery_effects(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
            "document_id": "time-origin-1",
            "product_id": "PRODUCT_FIXTURE",
        })
        entered = asyncio.Event()
        release = asyncio.get_running_loop().create_future()

        async def pending_clear(*_args):
            entered.set()
            return await release

        with patch(
            "platforms.utiki.workflow.clear_conflict_selection",
            AsyncMock(side_effect=pending_clear),
        ), patch(
            "platforms.utiki.workflow._reset_ctbc_captcha",
            AsyncMock(return_value=True),
        ) as reset_captcha, patch(
            "platforms.utiki.workflow._reload_with_interval", AsyncMock()
        ) as reload_page:
            first = asyncio.create_task(
                _handle_ctbc_submit_dialog(tab, cfg)
            )
            await entered.wait()
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))
            reset_captcha.assert_not_awaited()
            reload_page.assert_not_awaited()
            release.set_result(CommitResult.COMMITTED)
            self.assertEqual("seat_conflict", await first)

        reset_captcha.assert_awaited_once_with(
            tab, refresh_image=False, submit_key=submit_key
        )
        reload_page.assert_not_awaited()

    async def test_conflict_clear_purchase_change_records_result_for_return(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        cfg = config()
        old_url = str(tab.target.url)
        old_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, old_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
            "document_id": "time-origin-1",
            "product_id": "PRODUCT_FIXTURE",
        })
        new_url = CTBC_TEST_SEAT_URL.replace(
            "PERFORMANCE_ID=P1", "PERFORMANCE_ID=P2"
        )

        async def change_purchase(*_args):
            tab.target.url = new_url
            return CommitResult.COMMITTED

        with patch(
            "platforms.utiki.workflow.clear_conflict_selection",
            AsyncMock(side_effect=change_purchase),
        ) as clear_selection, patch(
            "platforms.utiki.workflow._reset_ctbc_captcha", AsyncMock()
        ) as reset_captcha, patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page:
            self.assertEqual(
                "seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg)
            )
            self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))
            self.assertNotEqual(old_key, _ctbc_purchase_state_key(tab))
            blocker = runtime["purchase"]["outcomes"].get(old_key)
            self.assertEqual("done", blocker["clear_phase"])
            self.assertEqual("committed", blocker["clear_result"])
            self.assertEqual("ABCD", tab.captcha_input.value)
            reset_captcha.assert_not_awaited()
            reload_page.assert_not_awaited()

            tab.target.url = old_url
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))

        clear_selection.assert_awaited_once()
        reset_captcha.assert_not_awaited()
        reload_page.assert_awaited_once_with(
            tab, cfg, "selected seats were taken"
        )

    async def test_conflict_click_without_close_blocks_reload(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dialog_closes_on_click = False
        cfg = config()
        submit_key = self._track_submit_state(tab)

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(await nodriver_ctbc_seat_main(tab, cfg, ocr=None))

        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertFalse(
            self._outcome_for(tab, submit_key)["dialog_dismissed"]
        )
        reload_page.assert_not_awaited()

    async def test_conflict_dismiss_race_recovers_after_dialog_disappears(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        tab.dismiss_allowed = False
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"

        self.assertEqual("seat_conflict", await _handle_ctbc_submit_dialog(tab, cfg))
        tab.dialog = ""
        tab.dialog_visible = False

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))

        self.assertTrue(
            self._outcome_for(tab, submit_key)["dialog_dismissed"]
        )
        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")

    async def test_conflict_survives_spontaneous_change_until_recovery_document(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = {
            "kind": "seat_conflict",
            "source_document_id": "document-1",
            "page_type": "seat",
            "dialog_dismissed": False,
            "reload_dispatched": False,
            "recovery_document_id": "",
        }

        with patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertTrue(await _recover_ctbc_seat_conflict(tab, cfg))

        pending = self._outcome_for(tab, submit_key)
        self.assertEqual("seat_conflict", pending["kind"])
        self.assertEqual("document-2", pending["recovery_document_id"])
        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")

        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-3"
        self.assertFalse(await _recover_ctbc_seat_conflict(tab, cfg))
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_general_conflict_uses_same_reload_recovery(self):
        tab = FakeSubmitTab(
            dialog="",
            url=(
                "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
                "PERFORMANCE_ID=P1&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=A1"
            ),
        )
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"
        ctbc_tab_runtime(tab)["purchase"]["outcomes"][submit_key] = {
            "kind": "seat_conflict",
            "source_document_id": "document-1",
            "page_type": "general_admission",
            "dialog_dismissed": True,
            "reload_dispatched": False,
            "recovery_document_id": "",
        }

        with patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=True),
        ) as reload_page:
            self.assertFalse(
                await nodriver_ctbc_general_admission_main(tab, cfg, ocr=None)
            )

        reload_page.assert_awaited_once_with(tab, cfg, "selected seats were taken")

    async def test_visible_conflict_blocks_click_immediately_before_submit(self):
        tab = FakeSubmitTab(dialog="該位置已被選走")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertEqual("seat_conflict", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(1, tab.dialog_button.click_count)

    async def test_quota_dialog_blocks_only_current_purchase(self):
        original_url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        tab = FakeSubmitTab(dialog="已超出可購買額度", url=original_url)
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        original_key = self._track_submit_state(tab)
        self._set_active_submit(tab, original_key, time.monotonic())
        self.assertEqual("quota", await _handle_ctbc_submit_dialog(tab, cfg))
        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertIsNone(self._active_submit_for(tab, original_key))
        self.assertEqual("quota", self._outcome_for(tab, original_key)["kind"])

        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)

        tab.target.url = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=P2&GROUP_ID=G1&PERFORMANCE_PRICE_AREA_ID=A1"
        )
        new_key = self._track_submit_state(tab)
        self.assertIsNone(self._outcome_for(tab, new_key))

    async def test_quota_state_survives_dialog_dismiss_race(self):
        tab = FakeSubmitTab(dialog="已超出可購買額度")
        tab.dismiss_allowed = False
        submit_key = self._track_submit_state(tab)
        self._set_active_submit(tab, submit_key, time.monotonic())

        self.assertEqual("quota", await _handle_ctbc_submit_dialog(tab, config()))

        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("quota", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual(0, tab.dialog_button.click_count)

    async def test_captcha_dismiss_failure_defers_reset_without_resubmit(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        tab.dismiss_allowed = False
        submit_key = self._track_submit_state(tab)
        ctbc_tab_runtime(tab)["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
        })
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, config()))
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, config()))
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual(0, tab.dialog_button.click_count)
        self.assertEqual("captcha", self._outcome_for(tab, submit_key)["kind"])
        self.assertEqual("ABCD", tab.captcha_input.value)
        self.assertEqual(0, tab.captcha_refresh.click_count)

        tab.dialog = ""
        tab.dialog_visible = False
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"old-image", b"new-image"]),
        ):
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertEqual("", tab.captcha_input.value)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_refresh_exception_never_clicks_refresh_twice(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"

        async def refresh_then_raise():
            tab.captcha_refresh.clicked = True
            tab.captcha_refresh.click_count += 1
            raise RuntimeError("CDP result unavailable")

        tab.captcha_refresh.click = refresh_then_raise
        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(return_value=b"unchanged-image"),
        ):
            self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))
            self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(0, tab.button.click_count)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertEqual(1, tab.captcha_input.apply_count)
        self.assertEqual("unknown", self._outcome_for(tab, submit_key)["reset_phase"])
        self.assertEqual("captcha", self._outcome_for(tab, submit_key)["kind"])

        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-2"
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertEqual(1, tab.captcha_input.apply_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_refresh_waits_for_changed_image_without_second_click(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)["document"]["last_document"] = "document-1"

        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"same", b"same", b"same", b"changed"]),
        ):
            self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))
            self.assertEqual("awaiting_change", self._outcome_for(
                tab, submit_key
            )["reset_phase"])
            self.assertTrue(await _recover_ctbc_captcha_dialog(tab, cfg))
            self.assertTrue(await _recover_ctbc_captcha_dialog(tab, cfg))

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_click_exception_can_attest_changed_image(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        ctbc_tab_runtime(tab)["document"]["last_document"] = "document-1"

        async def refresh_then_raise():
            tab.captcha_refresh.click_count += 1
            raise RuntimeError("CDP result unavailable")

        tab.captcha_refresh.click = refresh_then_raise
        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"old-image", b"changed-image"]),
        ):
            self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_token_await_losing_outcome_never_clicks_refresh(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
        })
        replacement = {"kind": "quota", "text": "new outcome"}

        async def replace_outcome(*_args):
            runtime["purchase"]["outcomes"][submit_key] = replacement
            return b"old-image"

        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=replace_outcome),
        ):
            self.assertEqual(
                "captcha", await _handle_ctbc_submit_dialog(tab, cfg)
            )

        self.assertIs(runtime["purchase"]["outcomes"][submit_key], replacement)
        self.assertEqual(0, tab.captcha_refresh.click_count)

    async def test_captcha_query_await_document_change_stops_before_input_clear(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
        })
        original_query = tab.query_selector

        async def change_document(selector):
            element = await original_query(selector)
            if selector == "#CHK":
                runtime["document"]["last_document"] = "document-2"
            return element

        tab.query_selector = change_document
        self.assertEqual("captcha", await _handle_ctbc_submit_dialog(tab, cfg))

        outcome = self._outcome_for(tab, submit_key)
        self.assertEqual("", outcome["reset_phase"])
        self.assertEqual("document-1", outcome["reset_source_document_id"])
        self.assertEqual("ABCD", tab.captcha_input.value)
        self.assertEqual(0, tab.captcha_refresh.click_count)

    async def test_captcha_empty_token_retries_preflight_then_clicks_once(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
        })

        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"", b"old-image", b"new-image"]),
        ):
            self.assertEqual(
                "captcha", await _handle_ctbc_submit_dialog(tab, cfg)
            )
            self.assertEqual("", self._outcome_for(
                tab, submit_key
            )["reset_phase"])
            self.assertTrue(await _recover_ctbc_captcha_dialog(tab, cfg))

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_captcha_missing_control_retries_preflight_then_clicks_once(self):
        tab = FakeSubmitTab(dialog="驗證碼錯誤")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        self._set_active_submit(tab, submit_key, {
            "dispatched_at": 1.0,
            "source_document_id": "document-1",
        })
        original_query = tab.query_selector
        control_ready = False

        async def delayed_control(selector):
            nonlocal control_ready
            if selector == "#chang_pic" and not control_ready:
                control_ready = True
                return None
            return await original_query(selector)

        tab.query_selector = delayed_control
        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=[b"old-image", b"old-image", b"new-image"]),
        ):
            self.assertEqual(
                "captcha", await _handle_ctbc_submit_dialog(tab, cfg)
            )
            self.assertEqual("", self._outcome_for(
                tab, submit_key
            )["reset_phase"])
            self.assertTrue(await _recover_ctbc_captcha_dialog(tab, cfg))

        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertIsNone(self._outcome_for(tab, submit_key))

    async def test_concurrent_captcha_preflight_clicks_refresh_once(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        outcome = {
            "kind": "captcha",
            "source_document_id": "document-1",
            "reset_phase": "",
            "reset_source_document_id": "",
            "reset_before_token": "",
        }
        runtime["purchase"]["outcomes"][submit_key] = outcome
        entered = asyncio.Event()
        release = asyncio.get_running_loop().create_future()
        reads = 0

        async def delayed_image(*_args):
            nonlocal reads
            reads += 1
            if reads == 1:
                entered.set()
                await release
                return b"old-image"
            return b"new-image"

        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=delayed_image),
        ) as image_reader:
            first = asyncio.create_task(_reset_ctbc_captcha(
                tab,
                refresh_image=True,
                submit_key=submit_key,
                config_dict=cfg,
            ))
            await entered.wait()
            self.assertEqual("preflighting", outcome["reset_phase"])
            self.assertFalse(await _reset_ctbc_captcha(
                tab,
                refresh_image=True,
                submit_key=submit_key,
                config_dict=cfg,
            ))
            self.assertEqual(1, image_reader.await_count)
            self.assertEqual(1, tab.captcha_input.apply_count)
            self.assertEqual(0, tab.captcha_refresh.click_count)
            release.set_result(None)
            self.assertTrue(await first)

        self.assertEqual(2, image_reader.await_count)
        self.assertEqual(1, tab.captcha_input.apply_count)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertEqual("done", outcome["reset_phase"])

    async def test_captcha_preflight_purchase_change_can_retry_on_return(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        old_url = str(tab.target.url)
        submit_key = self._track_submit_state(tab)
        runtime = ctbc_tab_runtime(tab)
        runtime["document"]["last_document"] = "document-1"
        outcome = {
            "kind": "captcha",
            "source_document_id": "document-1",
            "reset_phase": "",
            "reset_source_document_id": "",
            "reset_before_token": "",
        }
        runtime["purchase"]["outcomes"][submit_key] = outcome
        entered = asyncio.Event()
        release = asyncio.get_running_loop().create_future()
        reads = 0

        async def delayed_image(*_args):
            nonlocal reads
            reads += 1
            if reads == 1:
                entered.set()
                await release
                return b"stale-image"
            return b"old-image" if reads == 2 else b"new-image"

        with patch(
            "platforms.utiki.workflow._get_ctbc_captcha_image_bytes",
            AsyncMock(side_effect=delayed_image),
        ):
            first = asyncio.create_task(_reset_ctbc_captcha(
                tab,
                refresh_image=True,
                submit_key=submit_key,
                config_dict=cfg,
            ))
            await entered.wait()
            tab.target.url = old_url.replace(
                "PERFORMANCE_ID=P1", "PERFORMANCE_ID=P2"
            )
            runtime["document"]["last_document"] = "document-2"
            release.set_result(None)
            self.assertFalse(await first)
            self.assertEqual("", outcome["reset_phase"])
            self.assertEqual(0, tab.captcha_refresh.click_count)

            tab.target.url = old_url
            runtime["document"]["last_document"] = "document-1"
            self.assertTrue(await _reset_ctbc_captcha(
                tab,
                refresh_image=True,
                submit_key=submit_key,
                config_dict=cfg,
            ))

        self.assertEqual(3, reads)
        self.assertEqual(1, tab.captcha_refresh.click_count)
        self.assertEqual("done", outcome["reset_phase"])

    async def test_auth_error_blocks_retry_until_credentials_change(self):
        tab = FakeSubmitTab(dialog="帳號或密碼錯誤")
        cfg = config()
        cfg["ocr_captcha"] = {"enable": True, "force_submit": True}
        submit_key = self._track_submit_state(tab)
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)
        self.assertEqual(1, tab.dialog_button.click_count)
        self.assertIsNone(self._active_submit_for(tab, submit_key))
        self.assertEqual("auth", self._outcome_for(tab, submit_key)["kind"])
        self.assertFalse(await _ctbc_captcha_and_submit(tab, cfg, ocr=None))
        self.assertEqual(0, tab.button.click_count)
