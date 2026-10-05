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
        self.assertEqual(3, version)
        self.assertIn("refresh_reservation", tables)
        self.assertIn("refresh_commit_receipt", tables)

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


if __name__ == "__main__":
    unittest.main()
