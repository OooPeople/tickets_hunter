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

from platforms.utiki.dom import CommitResult, commit_area
from platforms.utiki.selection import (
    parse_area_snapshot,
    parse_quantity_snapshot,
    select_area_snapshot,
)
from platforms.utiki.workflow import (
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

        async def submit_effect(*_args):
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


if __name__ == "__main__":
    unittest.main()
