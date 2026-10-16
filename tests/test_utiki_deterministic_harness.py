import asyncio
import unittest
from unittest.mock import patch

from tests.ctbc_support import (
    CTBC_TEST_DATE_URL,
    CtbcStateIsolationMixin,
    config,
    make_ctbc_date_snapshot,
)
from tests.support.utiki_deterministic import (
    BrowserEffectSentinel,
    ControlledPendingIO,
    FakeClock,
    TaskLeakGuard,
)

from platforms.utiki.dom import CommitResult
from platforms.utiki.selection import parse_date_snapshot
from platforms.utiki.workflow import (
    _state,
    nodriver_ctbc_date_auto_select,
)


class DeterministicHarnessTest(unittest.IsolatedAsyncioTestCase):
    async def test_clock_inputs_are_independent_and_do_not_complete_io(self):
        clock = FakeClock(monotonic=10.0, wall=1_000.0)
        provider = ControlledPendingIO[str]("read")

        async with TaskLeakGuard() as tasks:
            task = tasks.create_task(provider.request("snapshot"), name="read")
            call = await provider.next_call()

            self.assertFalse(task.done())
            self.assertEqual(15.0, clock.advance_monotonic(5.0))
            self.assertEqual(1_000.0, clock.wall_time())
            self.assertFalse(task.done())

            self.assertEqual(1_007.0, clock.advance_wall(7.0))
            self.assertEqual(15.0, clock.monotonic())
            self.assertFalse(task.done())

            call.resolve("ready")
            self.assertEqual("ready", await task)

        provider.assert_all_completed()

    async def test_pending_io_records_calls_and_propagates_controlled_failure(self):
        events = []
        provider = ControlledPendingIO[str]("inspect", event_log=events)

        async with TaskLeakGuard() as tasks:
            task = tasks.create_task(
                provider.request("tab-1", document="document-1"),
                name="inspect",
            )
            call = await provider.next_call()

            self.assertEqual(0, call.sequence)
            self.assertEqual(("tab-1",), call.args)
            self.assertEqual({"document": "document-1"}, call.kwargs)
            self.assertEqual(["inspect"], events)

            call.fail(RuntimeError("controlled failure"))
            with self.assertRaisesRegex(RuntimeError, "controlled failure"):
                await task

        provider.assert_all_completed()

    async def test_task_guard_reports_and_cleans_up_unfinished_tasks(self):
        never_complete = asyncio.Event()

        with self.assertRaisesRegex(
            AssertionError,
            r"unfinished asyncio task\(s\): leaked<",
        ):
            async with TaskLeakGuard() as tasks:
                leaked = tasks.create_task(
                    never_complete.wait(), name="leaked"
                )

        self.assertTrue(leaked.cancelled())


class UtikiDeterministicCharacterizationTest(
    CtbcStateIsolationMixin,
    unittest.IsolatedAsyncioTestCase,
):
    ctbc_state = _state

    async def test_inspect_then_commit_is_explicit_and_has_no_hidden_browser_io(self):
        events = []
        reader = ControlledPendingIO[object]("inspect_date", event_log=events)
        committer = ControlledPendingIO[CommitResult](
            "commit_date", event_log=events
        )
        tab = BrowserEffectSentinel(CTBC_TEST_DATE_URL)
        cfg = config()
        cfg["area_auto_select"]["enable"] = False
        snapshot = parse_date_snapshot(make_ctbc_date_snapshot())
        clock = FakeClock(monotonic=50.0, wall=2_000.0)

        async def source_is_current(*_args, **_kwargs):
            events.append("reconcile_navigation")
            return True

        async def cancel_refresh(*_args, **_kwargs):
            events.append("cancel_refresh")
            return True

        with (
            patch(
                "platforms.utiki.workflow._ctbc_reconcile_navigation_source",
                side_effect=source_is_current,
            ),
            patch(
                "platforms.utiki.workflow.read_date_snapshot",
                side_effect=reader.request,
            ),
            patch(
                "platforms.utiki.workflow.commit_date_action",
                side_effect=committer.request,
            ),
            patch(
                "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
                side_effect=cancel_refresh,
            ),
        ):
            async with TaskLeakGuard() as tasks:
                workflow = tasks.create_task(
                    nodriver_ctbc_date_auto_select(tab, cfg),
                    name="date-workflow",
                )

                read_call = await reader.next_call()
                self.assertEqual((tab,), read_call.args)
                committer.assert_no_calls()
                tab.assert_no_effects()

                clock.advance_monotonic(30.0)
                clock.advance_wall(300.0)
                self.assertFalse(workflow.done())
                committer.assert_no_calls()
                tab.assert_no_effects()

                read_call.resolve(snapshot)
                commit_call = await committer.next_call()
                self.assertIs(tab, commit_call.args[0])
                self.assertIs(snapshot, commit_call.args[1])
                self.assertFalse(workflow.done())
                tab.assert_no_effects()

                commit_call.resolve(CommitResult.REJECTED)
                self.assertFalse(await workflow)

        self.assertEqual(
            [
                "reconcile_navigation",
                "inspect_date",
                "cancel_refresh",
                "commit_date",
            ],
            events,
        )
        reader.assert_all_completed()
        committer.assert_all_completed()
        tab.assert_no_effects()
