import multiprocessing
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import closing


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from refresh_coordinator import (  # noqa: E402
    OpeningBurstRequest,
    SharedRefreshCoordinator,
    UTK_SPORTS_SCOPE,
)


class FakeClock:
    def __init__(self, initial=1000.0):
        self.value = float(initial)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class AdvanceClockOnBeginConnection:
    """SQLite proxy that simulates lock waiting before BEGIN returns."""

    def __init__(self, connection, clock, seconds):
        self._connection = connection
        self._clock = clock
        self._seconds = float(seconds)

    def execute(self, statement, *args, **kwargs):
        result = self._connection.execute(statement, *args, **kwargs)
        if str(statement).strip().upper() == "BEGIN IMMEDIATE":
            self._clock.advance(self._seconds)
        return result

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _multiprocess_grant_worker(database_path, instance_id, start_event, result_queue):
    coordinator = SharedRefreshCoordinator(
        database_path,
        stale_request_seconds=1.0,
        poll_retry_seconds=0.01,
        database_timeout_seconds=1.0,
        fail_closed_retry_seconds=0.02,
    )
    start_event.wait(5.0)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        decision = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            instance_id,
            min_interval_seconds=0.06,
            jitter_range_seconds=(0.0, 0.0),
        )
        if decision.granted:
            result_queue.put((instance_id, time.time(), decision.sequence))
            return
        time.sleep(max(0.005, decision.retry_after_seconds))
    result_queue.put((instance_id, None, "timeout"))


def _multiprocess_reserve_commit_worker(
    database_path, reserved_event, continue_event, result_queue
):
    coordinator = SharedRefreshCoordinator(
        database_path,
        owner_token="reserve-worker",
        database_timeout_seconds=1.0,
        reservation_lease_seconds=10.0,
    )
    reservation = coordinator.reserve_refresh_sync(
        UTK_SPORTS_SCOPE,
        "worker-tab",
        min_interval_seconds=0.0,
        jitter_range_seconds=(0.0, 0.0),
        request_token="request",
    )
    result_queue.put(("reserve", reservation.reserved, reservation.reservation_id))
    reserved_event.set()
    continue_event.wait(5.0)
    decision = coordinator.commit_refresh_sync(
        UTK_SPORTS_SCOPE,
        "worker-tab",
        reservation.reservation_id,
        request_token="request",
    )
    result_queue.put(("commit", decision.granted, decision.status))


def _multiprocess_opening_burst_worker(
    database_path,
    worker_id,
    participant_id,
    target_at,
    start_event,
    result_queue,
):
    coordinator = SharedRefreshCoordinator(
        database_path,
        owner_token=f"burst-{worker_id}",
        stale_request_seconds=2.0,
        poll_retry_seconds=0.01,
        database_timeout_seconds=1.0,
        fail_closed_retry_seconds=0.02,
        dispatch_lease_seconds=0.5,
    )
    start_event.wait(5.0)
    while time.time() < target_at:
        time.sleep(0.002)
    burst = OpeningBurstRequest(
        campaign_id=f"scheduled:{int(target_at)}",
        participant_id=participant_id,
        scheduled_at=target_at,
        window_until=target_at + 1.0,
        interval_seconds=0.05,
        max_participants=4,
    )
    deadline = target_at + 0.8
    while time.time() < deadline:
        reservation = coordinator.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            f"tab-{worker_id}",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=burst,
        )
        decision = None
        if reservation.reserved:
            decision = coordinator.commit_refresh_sync(
                UTK_SPORTS_SCOPE,
                f"tab-{worker_id}",
                reservation.reservation_id,
            )
        if decision is not None and decision.granted:
            claimed_until = coordinator.claim_dispatch_sync(
                UTK_SPORTS_SCOPE,
                decision.permit_id,
                decision.dispatch_token,
            )
            command_at = time.time()
            if not claimed_until or float(claimed_until) <= command_at:
                result_queue.put((worker_id, participant_id, False, None))
                return
            coordinator.complete_dispatch_sync(
                UTK_SPORTS_SCOPE,
                decision.permit_id,
                decision.dispatch_token,
                possibly_dispatched=True,
            )
            result_queue.put((
                worker_id,
                participant_id,
                decision.is_opening_burst,
                command_at,
            ))
            return
        retry_after = (
            reservation.retry_after_seconds
            if not reservation.reserved
            else getattr(decision, "retry_after_seconds", 0.01)
        )
        time.sleep(max(0.002, min(retry_after, 0.02)))
    result_queue.put((worker_id, participant_id, False, None))


class SharedRefreshCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "refresh.sqlite3"
        self.clock = FakeClock()

    def coordinator(self, owner, **kwargs):
        return SharedRefreshCoordinator(
            self.database_path,
            owner_token=owner,
            clock=self.clock,
            random_uniform=lambda low, high: high,
            poll_retry_seconds=0.25,
            **kwargs,
        )

    def opening_burst(
        self, participant, *, campaign_id="scheduled:1000", target=1000.0
    ):
        return OpeningBurstRequest(
            campaign_id=campaign_id,
            participant_id=participant,
            scheduled_at=target,
            window_until=target + 2.0,
            interval_seconds=0.25,
            max_participants=4,
        )

    def test_opening_burst_serves_each_participant_once_then_returns_to_normal(self):
        coordinators = [self.coordinator(f"owner-{index}") for index in range(5)]
        grants = []
        for index in range(4):
            decision = coordinators[index].poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                f"tab-{index}",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
                opening_burst=self.opening_burst(f"profile-{index}"),
            )
            if index:
                self.assertFalse(decision.granted)
                self.assertEqual("pacing", decision.status)
                self.clock.advance(0.25)
                decision = coordinators[index].poll_refresh_sync(
                    UTK_SPORTS_SCOPE,
                    f"tab-{index}",
                    min_interval_seconds=1.0,
                    jitter_range_seconds=(0.0, 0.0),
                    opening_burst=self.opening_burst(f"profile-{index}"),
                )
            self.assertTrue(decision.granted)
            self.assertTrue(decision.is_opening_burst)
            grants.append(decision)

        repeated = coordinators[0].poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a-restarted",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-0"),
        )
        self.assertFalse(repeated.granted)
        self.assertEqual("opening_barrier", repeated.status)
        self.assertTrue(coordinators[0].cancel_request_sync(
            UTK_SPORTS_SCOPE, "tab-a-restarted"
        ))

        fifth = coordinators[4].poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-4",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-4"),
        )
        self.assertFalse(fifth.granted)
        self.assertEqual("opening_barrier", fifth.status)
        snapshot = coordinators[0].snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(4, len(snapshot["grants"]))
        self.assertEqual("exhausted", snapshot["campaigns"][0]["state"])

        self.clock.advance(1.25)
        normal = coordinators[4].poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-4",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-4"),
        )
        self.assertTrue(normal.granted)
        self.assertFalse(normal.is_opening_burst)

    def test_future_opening_request_waits_then_becomes_burst_at_target(self):
        coordinator = self.coordinator("owner-a")
        burst = self.opening_burst("profile-a")
        self.clock.value = burst.scheduled_at - 0.001

        early = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="request-a",
            opening_burst=burst,
        )

        self.assertFalse(early.granted)
        self.assertEqual("opening_not_started", early.status)
        snapshot = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual([], snapshot["queue"])
        self.assertEqual([], snapshot["campaigns"])
        self.assertEqual([], snapshot["grants"])
        self.assertEqual([], snapshot["receipts"])
        self.assertEqual(0, snapshot["bucket"]["sequence"])

        self.clock.value = burst.scheduled_at
        decision = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="request-a",
            opening_burst=burst,
        )

        self.assertTrue(decision.granted)
        self.assertTrue(decision.is_opening_burst)
        self.assertEqual(burst.campaign_id, decision.campaign_id)
        snapshot = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(1, len(snapshot["grants"]))
        self.assertEqual(1, len(snapshot["receipts"]))
        self.assertTrue(snapshot["receipts"][0]["is_opening_burst"])

    def test_four_early_participants_receive_only_burst_grants(self):
        coordinators = [self.coordinator(f"owner-{index}") for index in range(4)]
        bursts = [self.opening_burst(f"profile-{index}") for index in range(4)]
        self.clock.value = 999.999

        for index, coordinator in enumerate(coordinators):
            early = coordinator.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                f"tab-{index}",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
                request_token=f"request-{index}",
                opening_burst=bursts[index],
            )
            self.assertFalse(early.granted)
            self.assertEqual("opening_not_started", early.status)

        self.clock.value = 1000.0
        for index, coordinator in enumerate(coordinators):
            decision = coordinator.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                f"tab-{index}",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
                request_token=f"request-{index}",
                opening_burst=bursts[index],
            )
            if index:
                self.assertFalse(decision.granted)
                self.assertEqual("pacing", decision.status)
                self.clock.advance(0.25)
                decision = coordinator.poll_refresh_sync(
                    UTK_SPORTS_SCOPE,
                    f"tab-{index}",
                    min_interval_seconds=1.0,
                    jitter_range_seconds=(0.0, 0.0),
                    request_token=f"request-{index}",
                    opening_burst=bursts[index],
                )
            self.assertTrue(decision.granted)
            self.assertTrue(decision.is_opening_burst)

        snapshot = coordinators[0].snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(4, len(snapshot["grants"]))
        self.assertEqual(4, len(snapshot["receipts"]))
        self.assertTrue(all(row["is_opening_burst"] for row in snapshot["receipts"]))
        self.assertEqual("exhausted", snapshot["campaigns"][0]["state"])

    def test_opening_reservation_release_does_not_consume_grant(self):
        coordinator = self.coordinator("owner-a")
        burst = self.opening_burst("profile-a")
        reservation = coordinator.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="first",
            opening_burst=burst,
        )
        self.assertTrue(reservation.reserved)
        self.assertTrue(reservation.is_opening_burst)
        self.assertTrue(coordinator.release_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            reservation.reservation_id,
            request_token="first",
        ))
        self.assertEqual([], coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["grants"])

        decision = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="second",
            opening_burst=burst,
        )
        self.assertTrue(decision.granted)
        self.assertTrue(decision.is_opening_burst)

    def test_duplicate_participant_row_cannot_block_an_unserved_profile(self):
        first = self.coordinator("owner-a1")
        duplicate = self.coordinator("owner-a2")
        second = self.coordinator("owner-b")
        reservation = first.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a1",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        self.assertTrue(reservation.reserved)
        self.assertEqual("reservation_active", duplicate.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a2",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        ).status)
        self.assertEqual("reservation_active", second.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-b"),
        ).status)

        first_decision = first.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a1", reservation.reservation_id
        )
        self.assertTrue(first.claim_dispatch_sync(
            UTK_SPORTS_SCOPE,
            first_decision.permit_id,
            first_decision.dispatch_token,
        ))
        self.assertTrue(first.complete_dispatch_sync(
            UTK_SPORTS_SCOPE,
            first_decision.permit_id,
            first_decision.dispatch_token,
            possibly_dispatched=True,
        ))
        self.clock.advance(0.25)

        second_decision = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-b"),
        )
        self.assertTrue(second_decision.granted)
        self.assertTrue(second_decision.is_opening_burst)

    def test_rate_limit_aborts_opening_campaign_permanently(self):
        first = self.coordinator("owner-a")
        second = self.coordinator("owner-b")
        decision = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        self.assertTrue(decision.is_opening_burst)
        self.clock.advance(0.25)
        report = second.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.assertTrue(report.new_incident)
        campaign = first.snapshot_sync(UTK_SPORTS_SCOPE)["campaigns"][0]
        self.assertEqual("aborted", campaign["state"])

        self.clock.advance(1.0)
        probe = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-b"),
        )
        self.assertTrue(probe.granted)
        self.assertTrue(probe.is_recovery_probe)
        self.assertFalse(probe.is_opening_burst)

    def test_dispatch_lease_requires_exact_claim_and_ack(self):
        first = self.coordinator("owner-a", dispatch_lease_seconds=1.0)
        second = self.coordinator("owner-b", dispatch_lease_seconds=1.0)
        reservation = first.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=0.25,
            jitter_range_seconds=(0.0, 0.0),
            request_token="request",
        )
        decision = first.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            reservation.reservation_id,
            request_token="request",
        )
        self.assertTrue(decision.granted)
        self.assertFalse(first.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, "wrong-token"
        ))
        self.assertFalse(second.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))
        claimed_until = first.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        )
        self.assertGreaterEqual(claimed_until, self.clock.value + 2.0)
        self.assertFalse(first.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))

        blocked = second.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=0.25,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("dispatch_active", blocked.status)
        self.assertTrue(first.complete_dispatch_sync(
            UTK_SPORTS_SCOPE,
            decision.permit_id,
            decision.dispatch_token,
            possibly_dispatched=True,
        ))
        self.assertTrue(first.complete_dispatch_sync(
            UTK_SPORTS_SCOPE,
            decision.permit_id,
            decision.dispatch_token,
            possibly_dispatched=True,
        ))
        self.clock.advance(0.25)
        self.assertTrue(second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=0.25,
            jitter_range_seconds=(0.0, 0.0),
        ).granted)

    def test_aborting_campaign_preserves_claimed_dispatch_accounting(self):
        owner = self.coordinator(
            "owner-a",
            dispatch_lease_seconds=0.5,
            claimed_dispatch_lease_seconds=2.0,
        )
        controller = self.coordinator(
            "owner-b",
            dispatch_lease_seconds=0.5,
            claimed_dispatch_lease_seconds=2.0,
        )
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(decision.granted)
        self.assertTrue(owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))

        self.assertTrue(controller.abort_opening_campaigns_sync(
            UTK_SPORTS_SCOPE, "disabled"
        ))
        snapshot = controller.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNotNone(snapshot["dispatch"])
        self.assertEqual("claimed", snapshot["receipts"][0]["dispatch_status"])
        self.assertTrue(owner.complete_dispatch_sync(
            UTK_SPORTS_SCOPE,
            decision.permit_id,
            decision.dispatch_token,
            possibly_dispatched=True,
        ))
        snapshot = controller.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["dispatch"])
        self.assertEqual(self.clock.value, snapshot["bucket"]["last_dispatch_at"])

    def test_aborting_campaign_revokes_unclaimed_dispatch(self):
        owner = self.coordinator("owner-a")
        controller = self.coordinator("owner-b")
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(decision.granted)
        self.assertTrue(controller.abort_opening_campaigns_sync(
            UTK_SPORTS_SCOPE, "disabled"
        ))
        self.assertFalse(owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))
        snapshot = controller.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["dispatch"])
        self.assertEqual("revoked", snapshot["receipts"][0]["dispatch_status"])

    def test_burst_dispatch_cannot_be_claimed_after_campaign_window(self):
        owner = self.coordinator(
            "owner-a",
            dispatch_lease_seconds=5.0,
            claimed_dispatch_lease_seconds=5.0,
        )
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(decision.granted)
        self.clock.advance(2.0)
        self.assertFalse(owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))
        snapshot = owner.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["dispatch"])
        self.assertEqual("revoked", snapshot["receipts"][0]["dispatch_status"])

    def test_commit_rechecks_time_after_waiting_for_write_lock(self):
        owner = self.coordinator("owner-a")
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        self.assertTrue(reservation.reserved)
        connect = owner._connect
        owner._connect = lambda: AdvanceClockOnBeginConnection(
            connect(), self.clock, 2.0
        )

        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )

        self.assertFalse(decision.granted)
        self.assertEqual("reservation_expired", decision.status)

    def test_claim_rechecks_time_after_waiting_for_write_lock(self):
        owner = self.coordinator(
            "owner-a",
            dispatch_lease_seconds=5.0,
            claimed_dispatch_lease_seconds=5.0,
        )
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(decision.granted)
        connect = owner._connect
        owner._connect = lambda: AdvanceClockOnBeginConnection(
            connect(), self.clock, 2.0
        )

        claimed = owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        )

        self.assertFalse(claimed)
        snapshot = owner.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["dispatch"])
        self.assertEqual("revoked", snapshot["receipts"][0]["dispatch_status"])

    def test_crashed_burst_reservation_does_not_block_the_whole_window(self):
        first = self.coordinator(
            "owner-a",
            stale_request_seconds=5.0,
            reservation_lease_seconds=5.0,
            opening_reservation_lease_seconds=0.75,
        )
        second = self.coordinator(
            "owner-b",
            stale_request_seconds=5.0,
            reservation_lease_seconds=5.0,
            opening_reservation_lease_seconds=0.75,
        )
        abandoned = first.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        )
        self.assertTrue(abandoned.reserved)
        self.clock.advance(0.75)
        replacement = second.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-b"),
        )
        self.assertTrue(replacement.reserved)
        self.assertTrue(replacement.is_opening_burst)
        queue = second.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        self.assertEqual(["tab-b"], [row["instance_id"] for row in queue])

    def test_rate_limit_preserves_claimed_dispatch_until_completion(self):
        owner = self.coordinator("owner-a")
        reporter = self.coordinator("owner-b")
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=0.5,
            jitter_range_seconds=(0.0, 0.0),
        )
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))
        report = reporter.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
        )
        self.assertTrue(report.new_incident)
        self.assertIsNotNone(reporter.snapshot_sync(UTK_SPORTS_SCOPE)["dispatch"])
        self.assertFalse(owner.claim_dispatch_sync(
            UTK_SPORTS_SCOPE, decision.permit_id, decision.dispatch_token
        ))
        self.assertIsNotNone(reporter.snapshot_sync(UTK_SPORTS_SCOPE)["dispatch"])
        self.assertTrue(owner.complete_dispatch_sync(
            UTK_SPORTS_SCOPE,
            decision.permit_id,
            decision.dispatch_token,
            possibly_dispatched=True,
        ))
        snapshot = reporter.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["dispatch"])
        self.assertEqual(self.clock.value, snapshot["bucket"]["last_dispatch_at"])

    def test_expired_dispatch_lease_applies_conservative_gap(self):
        first = self.coordinator("owner-a", dispatch_lease_seconds=1.0)
        second = self.coordinator("owner-b", dispatch_lease_seconds=1.0)
        reservation = first.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=0.5,
            jitter_range_seconds=(0.0, 0.0),
        )
        decision = first.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "tab-a", reservation.reservation_id
        )
        self.assertTrue(decision.granted)
        self.clock.advance(1.0)
        waiting = second.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=0.5,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("pacing", waiting.status)
        self.clock.advance(0.5)
        self.assertTrue(second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=0.5,
            jitter_range_seconds=(0.0, 0.0),
        ).granted)

    def test_overlapping_opening_campaign_does_not_create_second_quota(self):
        first = self.coordinator("owner-a")
        second = self.coordinator("owner-b")
        self.assertTrue(first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=self.opening_burst("profile-a"),
        ).granted)
        overlapping = OpeningBurstRequest(
            campaign_id="scheduled:1001",
            participant_id="profile-b",
            scheduled_at=1001.0,
            window_until=1003.0,
            interval_seconds=0.25,
            max_participants=4,
        )
        self.clock.advance(1.0)
        result = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "tab-b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            opening_burst=overlapping,
        )
        self.assertFalse(result.granted)
        self.assertEqual("opening_barrier", result.status)
        self.assertEqual(
            1, len(first.snapshot_sync(UTK_SPORTS_SCOPE)["campaigns"])
        )

    def test_minimum_gap_and_jitter_are_shared_between_owners(self):
        first = self.coordinator("owner-a")
        second = self.coordinator("owner-b")

        granted = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.5,
            jitter_range_seconds=(0.25, 0.25),
        )
        self.assertTrue(granted.granted)

        waiting = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.5,
            jitter_range_seconds=(0.25, 0.25),
        )
        self.assertFalse(waiting.granted)
        self.assertEqual("pacing", waiting.status)

        self.clock.advance(1.74)
        still_waiting = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.5,
            jitter_range_seconds=(0.25, 0.25),
        )
        self.assertFalse(still_waiting.granted)

        self.clock.advance(0.01)
        second_grant = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.5,
            jitter_range_seconds=(0.25, 0.25),
        )
        self.assertTrue(second_grant.granted)
        self.assertEqual(2, second_grant.sequence)

    def test_fifo_order_is_preserved(self):
        seed = self.coordinator("seed", stale_request_seconds=20.0)
        first = self.coordinator("owner-a", stale_request_seconds=20.0)
        second = self.coordinator("owner-b", stale_request_seconds=20.0)

        self.assertTrue(
            seed.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "seed",
                min_interval_seconds=10.0,
                jitter_range_seconds=(0.0, 0.0),
            ).granted
        )
        self.clock.advance(0.01)
        self.assertEqual(
            "pacing",
            first.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "a",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
            ).status,
        )
        self.clock.advance(0.01)
        self.assertEqual(
            "queued",
            second.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "b",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
            ).status,
        )

        self.clock.advance(9.98)
        second_before_first = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("queued", second_before_first.status)
        first_grant = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(first_grant.granted)

        self.clock.advance(1.0)
        second_grant = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(second_grant.granted)

    def test_one_pending_request_per_instance(self):
        seed = self.coordinator("seed")
        owner_a = self.coordinator("owner-a")
        duplicate = self.coordinator("owner-duplicate")

        self.assertTrue(
            seed.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "seed",
                min_interval_seconds=5.0,
                jitter_range_seconds=(0.0, 0.0),
            ).granted
        )
        self.assertEqual(
            "pacing",
            owner_a.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "same-instance",
                min_interval_seconds=1.0,
                jitter_range_seconds=(0.0, 0.0),
            ).status,
        )
        collision = duplicate.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "same-instance",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("instance_busy", collision.status)
        snapshot = owner_a.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(1, len(snapshot["queue"]))

    def test_stale_fifo_head_is_pruned(self):
        seed = self.coordinator("seed", stale_request_seconds=2.0)
        stale = self.coordinator("stale", stale_request_seconds=2.0)
        current = self.coordinator("current", stale_request_seconds=2.0)

        self.assertTrue(
            seed.poll_refresh_sync(
                UTK_SPORTS_SCOPE,
                "seed",
                min_interval_seconds=3.0,
                jitter_range_seconds=(0.0, 0.0),
            ).granted
        )
        stale.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "stale",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.clock.advance(3.0)
        decision = current.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "current",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(decision.granted)
        self.assertEqual([], current.snapshot_sync(UTK_SPORTS_SCOPE)["queue"])

    def test_rate_limit_incidents_are_deduplicated_and_back_off(self):
        coordinator = self.coordinator(
            "owner-a",
            incident_dedupe_seconds=10.0,
            incident_reset_seconds=900.0,
        )
        first = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
        )
        self.assertTrue(first.new_incident)
        self.assertEqual(1, first.consecutive_incidents)
        self.assertEqual(self.clock.value + 60.0, first.cooldown_until)

        self.clock.advance(5.0)
        duplicate = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "b",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
        )
        self.assertFalse(duplicate.new_incident)
        self.assertEqual(1, duplicate.consecutive_incidents)
        self.assertEqual(first.cooldown_until, duplicate.cooldown_until)

        self.clock.advance(55.0)
        recovery_probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(recovery_probe.is_recovery_probe)
        second = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
            probe_permit_id=recovery_probe.permit_id,
        )
        self.assertEqual(2, second.consecutive_incidents)
        self.assertEqual(self.clock.value + 120.0, second.cooldown_until)

        self.clock.advance(120.0)
        recovery_probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        third = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
            probe_permit_id=recovery_probe.permit_id,
        )
        self.assertEqual(3, third.consecutive_incidents)
        self.assertEqual(self.clock.value + 240.0, third.cooldown_until)

        self.clock.advance(240.0)
        recovery_probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        capped = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
            probe_permit_id=recovery_probe.permit_id,
        )
        self.assertEqual(4, capped.consecutive_incidents)
        self.assertEqual(self.clock.value + 240.0, capped.cooldown_until)

    def test_retry_after_can_extend_cooldown(self):
        coordinator = self.coordinator("owner-a")
        report = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
            retry_after_seconds=300.0,
        )
        self.assertEqual(self.clock.value + 300.0, report.cooldown_until)

    def test_fresh_429_after_successful_probe_is_not_time_deduplicated(self):
        coordinator = self.coordinator(
            "owner-a",
            incident_dedupe_seconds=10.0,
        )
        coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.clock.advance(1.0)
        probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(
            coordinator.complete_recovery_probe_sync(
                UTK_SPORTS_SCOPE,
                "a",
                probe.permit_id,
                success=True,
            ).accepted
        )
        self.clock.advance(1.0)
        fresh = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.assertTrue(fresh.new_incident)
        self.assertEqual(2, fresh.consecutive_incidents)

    def test_document_loaded_before_recovery_does_not_reopen_incident(self):
        coordinator = self.coordinator("owner-a")
        coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
            document_started_at=self.clock.value,
        )
        self.clock.advance(1.0)
        probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(
            coordinator.complete_recovery_probe_sync(
                UTK_SPORTS_SCOPE,
                "a",
                probe.permit_id,
                success=True,
            ).accepted
        )
        stale = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "b",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
            document_started_at=self.clock.value - 0.5,
        )
        self.assertFalse(stale.new_incident)
        self.assertFalse(
            coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"][
                "recovery_required"
            ]
        )

    def test_each_grant_has_a_unique_permit_even_for_the_same_owner(self):
        coordinator = self.coordinator("owner-a")
        first = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.clock.advance(1.0)
        second = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(first.granted and second.granted)
        self.assertNotEqual(first.permit_id, second.permit_id)

    def test_cooldown_reports_without_the_probe_token_do_not_escalate(self):
        coordinator = self.coordinator("owner-a", incident_dedupe_seconds=1.0)
        first = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
        )
        self.clock.advance(20.0)
        duplicate = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "b",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
            retry_after_seconds=600.0,
        )
        self.assertFalse(duplicate.new_incident)
        self.assertEqual(1, duplicate.consecutive_incidents)
        self.assertEqual(first.cooldown_until, duplicate.cooldown_until)

    def test_only_matching_recovery_probe_can_escalate(self):
        coordinator = self.coordinator("owner-a")
        coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=2.0,
            max_cooldown_seconds=8.0,
        )
        self.clock.advance(2.0)
        probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        ignored = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "b",
            initial_cooldown_seconds=2.0,
            max_cooldown_seconds=8.0,
            probe_permit_id="not-the-probe",
        )
        self.assertFalse(ignored.new_incident)
        accepted = coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=2.0,
            max_cooldown_seconds=8.0,
            probe_permit_id=probe.permit_id,
        )
        self.assertTrue(accepted.new_incident)
        self.assertEqual(2, accepted.consecutive_incidents)

    def test_only_one_recovery_probe_is_leased(self):
        first = self.coordinator("owner-a", recovery_probe_lease_seconds=10.0)
        second = self.coordinator("owner-b", recovery_probe_lease_seconds=10.0)
        first.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=2.0,
            max_cooldown_seconds=8.0,
        )
        self.clock.advance(2.0)

        probe = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(probe.granted)
        self.assertTrue(probe.is_recovery_probe)

        blocked = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("recovery_probe_active", blocked.status)

        completion = first.complete_recovery_probe_sync(
            UTK_SPORTS_SCOPE,
            "a",
            probe.permit_id,
            success=True,
        )
        self.assertTrue(completion.accepted)
        self.assertFalse(completion.recovery_required)
        self.clock.advance(1.0)
        normal = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(normal.granted)
        self.assertFalse(normal.is_recovery_probe)

    def test_expired_probe_lease_allows_one_new_probe(self):
        first = self.coordinator("owner-a", recovery_probe_lease_seconds=3.0)
        second = self.coordinator("owner-b", recovery_probe_lease_seconds=3.0)
        first.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.clock.advance(1.0)
        first_probe = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(first_probe.granted)

        self.clock.advance(3.0)
        replacement = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(replacement.granted)
        self.assertTrue(replacement.is_recovery_probe)
        self.assertNotEqual(first_probe.permit_id, replacement.permit_id)

    def test_invalid_or_unavailable_database_fails_closed(self):
        database_directory = Path(self.temp_dir.name) / "is-a-directory"
        database_directory.mkdir()
        coordinator = SharedRefreshCoordinator(
            database_directory,
            owner_token="owner-a",
            fail_closed_retry_seconds=7.0,
        )
        decision = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.5,
        )
        self.assertFalse(decision.granted)
        self.assertEqual("error", decision.status)
        self.assertEqual(7.0, decision.retry_after_seconds)
        self.assertTrue(decision.error)

    def test_version_one_database_is_migrated_in_place(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE refresh_bucket (
                    scope TEXT PRIMARY KEY,
                    next_allowed_at REAL NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    consecutive_429 INTEGER NOT NULL DEFAULT 0,
                    last_429_at REAL NOT NULL DEFAULT 0,
                    recovery_required INTEGER NOT NULL DEFAULT 0,
                    probe_owner TEXT NOT NULL DEFAULT '',
                    probe_expires_at REAL NOT NULL DEFAULT 0,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        coordinator = self.coordinator("owner-a")
        snapshot = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertTrue(snapshot["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(refresh_bucket)"
                )
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("last_recovery_at", columns)
        self.assertIn("last_dispatch_at", columns)
        self.assertEqual(4, version)
        self.assertIn("refresh_reservation", tables)
        self.assertIn("refresh_commit_receipt", tables)
        self.assertIn("refresh_opening_campaign", tables)
        self.assertIn("refresh_opening_grant", tables)
        self.assertIn("refresh_dispatch_lease", tables)

    def test_version_three_tables_receive_additive_burst_columns(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE refresh_bucket (
                    scope TEXT PRIMARY KEY,
                    next_allowed_at REAL NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    consecutive_429 INTEGER NOT NULL DEFAULT 0,
                    last_429_at REAL NOT NULL DEFAULT 0,
                    recovery_required INTEGER NOT NULL DEFAULT 0,
                    probe_owner TEXT NOT NULL DEFAULT '',
                    probe_expires_at REAL NOT NULL DEFAULT 0,
                    last_recovery_at REAL NOT NULL DEFAULT 0,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE refresh_queue (
                    scope TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    owner_token TEXT NOT NULL,
                    process_id INTEGER NOT NULL,
                    enqueued_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    min_interval_seconds REAL NOT NULL,
                    jitter_min_seconds REAL NOT NULL,
                    jitter_max_seconds REAL NOT NULL,
                    PRIMARY KEY (scope, instance_id)
                );
                CREATE TABLE refresh_reservation (
                    scope TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    owner_token TEXT NOT NULL,
                    reserved_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    is_recovery_probe INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE refresh_commit_receipt (
                    reservation_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    permit_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    is_recovery_probe INTEGER NOT NULL DEFAULT 0,
                    committed_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                PRAGMA user_version=3;
                """
            )
            connection.commit()

        coordinator = self.coordinator("owner-a")
        self.assertTrue(coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["ok"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            queue_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(refresh_queue)"
                )
            }
            reservation_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(refresh_reservation)"
                )
            }
            receipt_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(refresh_commit_receipt)"
                )
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(4, version)
        self.assertIn("is_opening_burst", queue_columns)
        self.assertIn("campaign_participant_id", reservation_columns)
        self.assertIn("dispatch_token", receipt_columns)

    def test_cancel_removes_only_callers_pending_request(self):
        seed = self.coordinator("seed")
        owner = self.coordinator("owner-a")
        seed.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "seed",
            min_interval_seconds=10.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        owner.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(owner.cancel_request_sync(UTK_SPORTS_SCOPE, "a"))
        self.assertEqual([], owner.snapshot_sync(UTK_SPORTS_SCOPE)["queue"])

    def test_old_async_cancel_cannot_remove_new_logical_request(self):
        seed = self.coordinator("seed", stale_request_seconds=20.0)
        owner = self.coordinator("owner-a", stale_request_seconds=20.0)
        seed.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "seed",
            min_interval_seconds=10.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        old = owner.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="old",
        )
        self.assertEqual("pacing", old.status)
        new = owner.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="new",
        )
        self.assertEqual("pacing", new.status)

        self.assertFalse(
            owner.cancel_request_sync(
                UTK_SPORTS_SCOPE,
                "a",
                request_token="old",
            )
        )
        queue = owner.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        self.assertEqual(1, len(queue))
        self.assertEqual(new.request_id, queue[0]["request_id"])

    def test_probe_renewal_prevents_replacement_and_old_token_expires(self):
        first = self.coordinator("owner-a", recovery_probe_lease_seconds=3.0)
        second = self.coordinator("owner-b", recovery_probe_lease_seconds=3.0)
        first.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.clock.advance(1.0)
        probe = first.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.clock.advance(2.0)
        self.assertTrue(
            first.renew_recovery_probe_sync(UTK_SPORTS_SCOPE, probe.permit_id)
        )
        self.clock.advance(2.0)
        waiting = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertEqual("recovery_probe_active", waiting.status)

        self.clock.advance(1.0)
        replacement = second.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(replacement.is_recovery_probe)
        stale_completion = first.complete_recovery_probe_sync(
            UTK_SPORTS_SCOPE,
            "a",
            probe.permit_id,
            success=True,
        )
        self.assertFalse(stale_completion.accepted)

    def test_expired_probe_cannot_be_revived_by_renewal(self):
        coordinator = self.coordinator(
            "owner-a", recovery_probe_lease_seconds=3.0
        )
        coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.clock.advance(1.0)
        probe = coordinator.poll_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.clock.advance(3.0)
        self.assertFalse(
            coordinator.renew_recovery_probe_sync(
                UTK_SPORTS_SCOPE, probe.permit_id
            )
        )

    def test_reservation_does_not_consume_spacing_until_commit(self):
        coordinator = self.coordinator(
            "owner-a", reservation_lease_seconds=5.0
        )
        reservation = coordinator.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=1.5,
            jitter_range_seconds=(0.25, 0.25),
            request_token="one",
        )
        self.assertTrue(reservation.reserved)
        snapshot = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(0, snapshot["bucket"]["sequence"])
        self.assertEqual(0.0, snapshot["bucket"]["next_allowed_at"])
        self.assertEqual(1, len(snapshot["queue"]))
        self.assertEqual(reservation.reservation_id, snapshot["reservation"]["reservation_id"])

        self.clock.advance(0.5)
        decision = coordinator.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            reservation.reservation_id,
            request_token="one",
        )
        self.assertTrue(decision.granted)
        snapshot = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(1, snapshot["bucket"]["sequence"])
        self.assertEqual(self.clock.value + 1.75, snapshot["bucket"]["next_allowed_at"])
        self.assertEqual([], snapshot["queue"])
        self.assertIsNone(snapshot["reservation"])

    def test_commit_receipt_prevents_duplicate_dispatch(self):
        coordinator = self.coordinator("owner-a")
        reservation = coordinator.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="one",
        )
        first = coordinator.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            reservation.reservation_id,
            request_token="one",
        )
        replay = coordinator.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            reservation.reservation_id,
            request_token="one",
        )
        self.assertTrue(first.granted)
        self.assertFalse(replay.granted)
        self.assertEqual("already_committed", replay.status)
        self.assertEqual(first.permit_id, replay.permit_id)
        self.assertEqual(1, coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"])

    def test_new_incident_invalidates_uncommitted_reservation(self):
        owner = self.coordinator("owner-a")
        reporter = self.coordinator("owner-b")
        reservation = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="one",
        )
        report = reporter.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "b",
            initial_cooldown_seconds=60.0,
            max_cooldown_seconds=240.0,
        )
        self.assertTrue(report.new_incident)
        decision = owner.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            reservation.reservation_id,
            request_token="one",
        )
        self.assertFalse(decision.granted)
        self.assertEqual("reservation_missing", decision.status)
        snapshot = owner.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertIsNone(snapshot["reservation"])
        self.assertEqual([], snapshot["queue"])
        self.assertEqual(0, snapshot["bucket"]["sequence"])

    def test_expired_reservation_can_be_recovered_without_consuming_gap(self):
        first = self.coordinator(
            "owner-a", reservation_lease_seconds=1.0, stale_request_seconds=2.0
        )
        second = self.coordinator(
            "owner-b", reservation_lease_seconds=1.0, stale_request_seconds=2.0
        )
        reservation = first.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.clock.advance(1.1)
        expired = first.commit_refresh_sync(
            UTK_SPORTS_SCOPE, "a", reservation.reservation_id
        )
        self.assertFalse(expired.granted)
        self.assertEqual("reservation_expired", expired.status)
        self.clock.advance(1.0)
        replacement = second.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "b",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
        )
        self.assertTrue(replacement.reserved)
        self.assertEqual(0, first.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"])

    def test_old_release_cannot_remove_new_logical_reservation(self):
        owner = self.coordinator("owner-a", reservation_lease_seconds=1.0)
        old = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="old",
        )
        self.clock.advance(1.1)
        new = owner.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="new",
        )
        self.assertTrue(new.reserved)
        self.assertFalse(
            owner.release_refresh_sync(
                UTK_SPORTS_SCOPE,
                "a",
                old.reservation_id,
                request_token="old",
            )
        )
        snapshot = owner.snapshot_sync(UTK_SPORTS_SCOPE)
        self.assertEqual(new.reservation_id, snapshot["reservation"]["reservation_id"])
        self.assertEqual(new.request_id, snapshot["queue"][0]["request_id"])

    def test_recovery_probe_lease_is_created_only_by_commit(self):
        coordinator = self.coordinator("owner-a", reservation_lease_seconds=5.0)
        coordinator.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "a",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.clock.advance(1.0)
        reservation = coordinator.reserve_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            min_interval_seconds=0.0,
            jitter_range_seconds=(0.0, 0.0),
            request_token="probe",
        )
        self.assertTrue(reservation.is_recovery_probe)
        self.assertEqual(
            "", coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_owner"]
        )
        decision = coordinator.commit_refresh_sync(
            UTK_SPORTS_SCOPE,
            "a",
            reservation.reservation_id,
            request_token="probe",
        )
        self.assertTrue(decision.granted)
        self.assertTrue(decision.is_recovery_probe)
        self.assertEqual(
            decision.permit_id,
            coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_owner"],
        )

    def test_spawned_incident_invalidates_worker_reservation_before_commit(self):
        context = multiprocessing.get_context("spawn")
        reserved_event = context.Event()
        continue_event = context.Event()
        result_queue = context.Queue()
        process = context.Process(
            target=_multiprocess_reserve_commit_worker,
            args=(
                str(self.database_path),
                reserved_event,
                continue_event,
                result_queue,
            ),
        )
        process.start()
        self.assertTrue(reserved_event.wait(5.0))
        reserve_result = result_queue.get(timeout=5.0)
        self.assertEqual(("reserve", True), reserve_result[:2])

        reporter = SharedRefreshCoordinator(
            self.database_path,
            owner_token="reporter",
            database_timeout_seconds=1.0,
        )
        report = reporter.report_rate_limited_sync(
            UTK_SPORTS_SCOPE,
            "reporter-tab",
            initial_cooldown_seconds=1.0,
            max_cooldown_seconds=4.0,
        )
        self.assertTrue(report.new_incident)
        continue_event.set()
        commit_result = result_queue.get(timeout=5.0)
        process.join(timeout=5.0)
        self.assertEqual(0, process.exitcode)
        self.assertEqual(("commit", False, "reservation_missing"), commit_result)

    def test_sqlite_transactions_coordinate_spawned_processes(self):
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_grant_worker,
                args=(str(self.database_path), f"instance-{index}", start_event, result_queue),
            )
            for index in range(3)
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=8.0) for _ in processes]
        for process in processes:
            process.join(timeout=5.0)
            self.assertEqual(0, process.exitcode)

        self.assertTrue(all(timestamp is not None for _, timestamp, _ in results), results)
        self.assertEqual(3, len({instance for instance, _, _ in results}))
        ordered = sorted(results, key=lambda item: item[1])
        intervals = [
            ordered[index + 1][1] - ordered[index][1]
            for index in range(len(ordered) - 1)
        ]
        self.assertTrue(all(interval >= 0.045 for interval in intervals), intervals)
        self.assertEqual([1, 2, 3], sorted(item[2] for item in results))

    def test_opening_burst_is_bounded_across_spawned_processes(self):
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        target_at = time.time() + 0.8
        participants = ["profile-a", "profile-a", "profile-b", "profile-c", "profile-d"]
        processes = [
            context.Process(
                target=_multiprocess_opening_burst_worker,
                args=(
                    str(self.database_path),
                    index,
                    participant,
                    target_at,
                    start_event,
                    result_queue,
                ),
            )
            for index, participant in enumerate(participants)
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=8.0) for _ in processes]
        for process in processes:
            process.join(timeout=5.0)
            self.assertEqual(0, process.exitcode)

        granted = [item for item in results if item[3] is not None]
        self.assertEqual(4, len(granted), results)
        self.assertTrue(all(item[2] for item in granted), granted)
        self.assertEqual(4, len({item[1] for item in granted}))
        ordered = sorted(granted, key=lambda item: item[3])
        gaps = [
            ordered[index + 1][3] - ordered[index][3]
            for index in range(len(ordered) - 1)
        ]
        self.assertTrue(all(gap >= 0.035 for gap in gaps), gaps)


if __name__ == "__main__":
    unittest.main()
