import unittest
from unittest.mock import AsyncMock, patch

from tests.ctbc_support import (
    CTBC_TEST_AREA_URL,
    CTBC_TEST_QUANTITY_URL,
    CtbcStateIsolationMixin,
    FakeSubmitTab,
    FakeTab,
    FakeUtikiDomPort,
    FakeUtikiQuantityDomPort,
    config,
    make_ctbc_area_snapshot,
    make_ctbc_quantity_snapshot,
)

from platforms.utiki.dom import (
    CommitResult,
    SubmitDispatchResult,
    commit_area,
    dispatch_quantity_submit,
)
from platforms.utiki.selection import (
    parse_area_snapshot,
    parse_quantity_snapshot,
    select_area_snapshot,
    select_quantity_snapshot,
)
from platforms.utiki.workflow import (
    _ctbc_captcha_and_submit,
    _ctbc_purchase_state_key,
    _ctbc_tab_identity,
    _state,
    nodriver_ctbc_area_auto_select,
    nodriver_ctbc_general_admission_main,
)


class UtikiDomContractTest(CtbcStateIsolationMixin,
                           unittest.IsolatedAsyncioTestCase):
    ctbc_state = _state

    async def test_area_workflow_commits_once_for_one_document_intent(self):
        snapshot = parse_area_snapshot(
            make_ctbc_area_snapshot(include_back=False)
        )
        dom_port = FakeUtikiDomPort(snapshot, CommitResult.COMMITTED)
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL)

        with (
            patch(
                "platforms.utiki.workflow.read_area_snapshot",
                side_effect=dom_port.read_area_snapshot,
            ),
            patch(
                "platforms.utiki.workflow.commit_area",
                side_effect=dom_port.commit_area,
            ),
        ):
            self.assertTrue(await nodriver_ctbc_area_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))
            self.assertFalse(await nodriver_ctbc_area_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))

        self.assertEqual(["inspect_area", "commit_area"], dom_port.events)
        self.assertEqual(1, dom_port.commit_count)
        self.assertEqual(
            "DISPATCH_UNKNOWN",
            _state["pending_navigation"][_ctbc_tab_identity(tab)]["phase"],
        )

    async def test_rejected_area_commit_does_not_leave_pending_success(self):
        snapshot = parse_area_snapshot(
            make_ctbc_area_snapshot(include_back=False)
        )
        dom_port = FakeUtikiDomPort(snapshot, CommitResult.REJECTED)
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL)

        with (
            patch(
                "platforms.utiki.workflow.read_area_snapshot",
                side_effect=dom_port.read_area_snapshot,
            ),
            patch(
                "platforms.utiki.workflow.commit_area",
                side_effect=dom_port.commit_area,
            ),
        ):
            self.assertFalse(await nodriver_ctbc_area_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))

        self.assertEqual(1, dom_port.commit_count)
        self.assertNotIn(
            _ctbc_tab_identity(tab), _state["pending_navigation"]
        )

    async def test_unknown_area_dispatch_keeps_pending_and_does_not_retry(self):
        snapshot = parse_area_snapshot(
            make_ctbc_area_snapshot(include_back=False)
        )
        dom_port = FakeUtikiDomPort(snapshot, None)
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL)

        with (
            patch(
                "platforms.utiki.workflow.read_area_snapshot",
                side_effect=dom_port.read_area_snapshot,
            ),
            patch(
                "platforms.utiki.workflow.commit_area",
                side_effect=dom_port.commit_area,
            ),
        ):
            self.assertFalse(await nodriver_ctbc_area_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))
            self.assertFalse(await nodriver_ctbc_area_auto_select(
                tab, config(area_keyword='"109區前排"')
            ))

        pending = _state["pending_navigation"][_ctbc_tab_identity(tab)]
        self.assertEqual("DISPATCH_UNKNOWN", pending["phase"])
        self.assertEqual(1, dom_port.commit_count)

    async def test_dom_committer_raises_for_unrecognized_driver_result(self):
        snapshot = parse_area_snapshot(
            make_ctbc_area_snapshot(include_back=False)
        )
        plan = select_area_snapshot(
            snapshot,
            config(area_keyword='"109區前排"'),
            "P16D0B8Z",
            require_direct_destination=False,
        )
        tab = FakeTab("", [], url=CTBC_TEST_AREA_URL, evaluate_results=[None])

        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            await commit_area(
                tab, snapshot, plan, 2, "b" * 32,
                "ticketsHunter.ctbc.fast-navigation.v1",
            )

    async def test_quantity_workflow_uses_inspect_commit_submit_order(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        dom_port = FakeUtikiQuantityDomPort(
            snapshot, CommitResult.COMMITTED
        )
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)

        async def submit_effect(*_args, **_kwargs):
            dom_port.events.append("submit")
            return True

        with (
            patch(
                "platforms.utiki.workflow.read_quantity_snapshot",
                side_effect=dom_port.read_quantity_snapshot,
            ),
            patch(
                "platforms.utiki.workflow.commit_quantity",
                side_effect=dom_port.commit_quantity,
            ),
            patch(
                "platforms.utiki.workflow._ctbc_attest_fast_destination",
                AsyncMock(return_value=True),
            ),
            patch(
                "platforms.utiki.workflow._ctbc_captcha_and_submit",
                AsyncMock(side_effect=submit_effect),
            ),
        ):
            self.assertTrue(await nodriver_ctbc_general_admission_main(
                tab, config(), ocr=None
            ))

        self.assertEqual(
            ["inspect_quantity", "commit_quantity", "submit"],
            dom_port.events,
        )
        self.assertEqual(1, dom_port.commit_count)

    async def test_stale_quantity_commit_reloads_without_submitting(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        dom_port = FakeUtikiQuantityDomPort(snapshot, CommitResult.STALE)
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["advanced"]["auto_reload_page_interval"] = 5
        reload_calls = 0

        async def reload_effect(reload_tab, _config, reason):
            nonlocal reload_calls
            reload_calls += 1
            identity = _ctbc_tab_identity(reload_tab)
            if reload_calls == 1:
                _state["reload_request"][identity] = {"reason": reason}
                return False
            _state["reload_request"].pop(identity, None)
            return True

        with (
            patch(
                "platforms.utiki.workflow.read_quantity_snapshot",
                side_effect=dom_port.read_quantity_snapshot,
            ),
            patch(
                "platforms.utiki.workflow.commit_quantity",
                side_effect=dom_port.commit_quantity,
            ),
            patch(
                "platforms.utiki.workflow._ctbc_attest_fast_destination",
                AsyncMock(return_value=True),
            ),
            patch(
                "platforms.utiki.workflow._reload_with_interval",
                AsyncMock(side_effect=reload_effect),
            ) as reload_page,
            patch(
                "platforms.utiki.workflow._ctbc_captcha_and_submit",
                AsyncMock(return_value=True),
            ) as submit,
        ):
            self.assertFalse(await nodriver_ctbc_general_admission_main(
                tab, cfg, ocr=None
            ))
            self.assertFalse(await nodriver_ctbc_general_admission_main(
                tab, cfg, ocr=None
            ))

        self.assertEqual(
            ["inspect_quantity", "commit_quantity"], dom_port.events
        )
        self.assertEqual(2, reload_page.await_count)
        reload_page.assert_awaited_with(
            tab, cfg, "quantity state changed during commit"
        )
        submit.assert_not_awaited()

    async def test_quantity_dispatch_is_not_purchase_success(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })
        submit_key = _ctbc_purchase_state_key(tab)

        with patch(
            "platforms.utiki.workflow.dispatch_quantity_submit",
            AsyncMock(return_value=SubmitDispatchResult.DISPATCHED),
        ) as dispatch, patch(
            "platforms.utiki.workflow._observe_ctbc_submit_outcome",
            AsyncMock(return_value=False),
        ) as observe:
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))

        self.assertEqual(1, dispatch.await_count)
        self.assertEqual(1, observe.await_count)
        self.assertEqual(
            "awaiting_result", _state["submitted"][submit_key]["phase"]
        )
        self.assertNotIn(submit_key, _state["submit_blocked"])

    async def test_unknown_quantity_dispatch_is_sticky(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })
        submit_key = _ctbc_purchase_state_key(tab)

        with patch(
            "platforms.utiki.workflow.dispatch_quantity_submit",
            AsyncMock(side_effect=RuntimeError("unknown result")),
        ) as dispatch:
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))

        self.assertEqual(1, dispatch.await_count)
        self.assertNotIn(submit_key, _state["submitted"])
        self.assertEqual("unknown", _state["submit_blocked"][submit_key])

    async def test_late_stale_dispatch_cannot_reload_after_losing_guard(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })
        submit_key = _ctbc_purchase_state_key(tab)
        replacement = {
            "dispatched_at": 1.0,
            "phase": "awaiting_result",
            "source_url": CTBC_TEST_QUANTITY_URL,
        }

        async def lose_guard(*_args):
            _state["submitted"][submit_key] = replacement
            return SubmitDispatchResult.STALE

        with patch(
            "platforms.utiki.workflow.dispatch_quantity_submit",
            AsyncMock(side_effect=lose_guard),
        ), patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(),
        ) as reload_page:
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))

        reload_page.assert_not_awaited()
        self.assertIs(_state["submitted"][submit_key], replacement)

    async def test_captcha_rejection_allows_one_new_quantity_dispatch(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })

        with patch(
            "platforms.utiki.workflow.dispatch_quantity_submit",
            AsyncMock(return_value=SubmitDispatchResult.DISPATCHED),
        ) as dispatch, patch(
            "platforms.utiki.workflow._observe_ctbc_submit_outcome",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))
            tab.dialog = "驗證碼錯誤"
            tab.dialog_visible = True
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))
            tab.captcha_input.value = "WXYZ"
            self.assertFalse(await _ctbc_captcha_and_submit(
                tab, cfg, ocr=None, quantity_plan=plan
            ))

        self.assertEqual(2, dispatch.await_count)
        self.assertEqual(1, tab.dialog_button.click_count)

    async def test_general_submit_without_quantity_plan_is_blocked(self):
        tab = FakeSubmitTab(dialog="", url=CTBC_TEST_QUANTITY_URL)
        cfg = config()
        cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })

        self.assertFalse(await _ctbc_captcha_and_submit(
            tab, cfg, ocr=None
        ))
        self.assertEqual(0, tab.button.click_count)

    async def test_quantity_dispatch_rejects_unknown_driver_result(self):
        snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
        plan = select_quantity_snapshot(snapshot, "全票", 2)
        tab = FakeTab(
            "", [], url=CTBC_TEST_QUANTITY_URL,
            evaluate_results=[True],
        )

        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            await dispatch_quantity_submit(tab, plan)


if __name__ == "__main__":
    unittest.main()
