import unittest
from unittest.mock import patch

from tests.ctbc_support import (
    CTBC_TEST_AREA_URL,
    CtbcStateIsolationMixin,
    FakeTab,
    FakeUtikiDomPort,
    config,
    make_ctbc_area_snapshot,
)

from platforms.utiki.dom import CommitResult, commit_area
from platforms.utiki.selection import parse_area_snapshot, select_area_snapshot
from platforms.utiki.workflow import (
    _ctbc_tab_identity,
    _state,
    nodriver_ctbc_area_auto_select,
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


if __name__ == "__main__":
    unittest.main()
