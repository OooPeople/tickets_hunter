import asyncio
import unittest

from tests.support.utiki_deterministic import (
    ControlledPendingIO,
    FakeClock,
    TaskLeakGuard,
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
