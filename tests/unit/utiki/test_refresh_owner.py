"""Batch 3 owner uses the real SQLite coordinator and exact browser claims."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

from platforms.utiki.adapters.refresh import SqliteRefreshAdapter
from platforms.utiki.application.engine import UtikiEngine
from platforms.utiki.application.engine import PlanningContext, _Readiness
from platforms.utiki.application.ports import CoreControl, CoreGate
from platforms.utiki.application.refresh_contract import (
    CompletedProbe, RefreshDocument, RefreshPhase, RefreshProgress, RefreshReason,
)
from platforms.utiki.application.refresh_host import IsolatedRefreshHost
from platforms.utiki.production_run import UtikiProductionRun, _RunHost
from platforms.utiki.domain.effects import (
    DispatchStage, EffectEvidence, EffectReceipt, EffectStatus,
)
from platforms.utiki.domain.identity import TabKey
from refresh_coordinator import SharedRefreshCoordinator, UTK_SPORTS_SCOPE
from tests.ctbc_support import make_effective_policy
from tests.ctbc_support import make_ctbc_quantity_snapshot
from platforms.utiki.selection import select_quantity_snapshot
from utiki_config import UtikiBehavior


URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
    "PERFORMANCE_ID=P16D0B8Z&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)


class Host:
    def __init__(self, tab):
        self.tab = tab
        self.control = CoreControl(1, CoreGate.PURCHASE_ALLOWED)
        self.clearance = None
        self.observed = []

    def current_control(self, tab):
        assert tab == self.tab
        return self.control

    def current_purchase_authority(self, tab):
        assert tab == self.tab
        return None

    def current_inline_login_credentials(self, tab, tenant_key):
        assert tab == self.tab
        return None

    def current_health_clearance(self, tab):
        assert tab == self.tab
        return self.clearance

    async def verify_health_without_mutation(self, document):
        self.observed.append(document.document_id)
        if document.classification == "normal":
            self.clearance = (document.url, document.document_id)
            return True
        return False


class Browser:
    def __init__(self, tab):
        self.bound_tab = tab
        self.document = RefreshDocument(tab, URL, "1000", "normal", 1.0)
        self.dispatches = []
        self.change_on_dispatch = True
        self.receipt_mode = "possible"
        self.dispatch_started = None
        self.dispatch_release = None

    async def observe_refresh_document(self, tab):
        assert tab == self.bound_tab
        return self.document

    async def dispatch_reload(self, claim, expected_url):
        assert expected_url == URL
        self.dispatches.append(claim)
        if self.dispatch_started is not None:
            self.dispatch_started.set()
        if self.dispatch_release is not None:
            await self.dispatch_release.wait()
        if self.change_on_dispatch:
            self.document = replace(self.document, document_id="2000", started_at=2.0)
        if self.receipt_mode == "no-effect":
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.REJECTED, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE,
            DispatchStage.REQUEST_BOUNDARY,
        )


class InterleavingAdapter(SqliteRefreshAdapter):
    def __init__(self, coordinator, settings):
        super().__init__(coordinator, lambda force: settings.copy())
        self.after_reserve = None
        self.after_commit = None
        self.commit_lost = False
        self.release_fails = False
        self.renew_result = True

    async def reserve(self, instance_id, token, minimum):
        result = await super().reserve(instance_id, token, minimum)
        if self.after_reserve is not None:
            self.after_reserve()
        return result

    async def commit(self, instance_id, reservation_id, token):
        result = await super().commit(instance_id, reservation_id, token)
        if self.after_commit is not None:
            self.after_commit()
        if self.commit_lost:
            self.commit_lost = False
            return replace(result, granted=False, status="error", error="lost receipt")
        return result

    async def release(self, instance_id, reservation_id, token):
        if self.release_fails:
            return False
        return await super().release(instance_id, reservation_id, token)

    async def renew(self, permit_id):
        return self.renew_result


def setup(tmp_path, *, enabled=True, clock=None):
    tab = TabKey(1, "isolated-refresh")
    host = Host(tab)
    browser = Browser(tab)
    now = [100.0] if clock is None else clock
    coordinator = SharedRefreshCoordinator(
        tmp_path / "refresh.sqlite3", clock=lambda: now[0],
        random_uniform=lambda _a, _b: 0.1,
    )
    settings = {
        "enabled": enabled, "min_interval_seconds": 1.0,
        "initial_cooldown_seconds": 1.0, "max_cooldown_seconds": 4.0,
    }
    adapter = InterleavingAdapter(coordinator, settings)
    policy = make_effective_policy(UtikiBehavior("全票", "row_seat", "manual"))
    engine = UtikiEngine(policy, browser, host, refresh_coordinator=adapter)
    handle = engine.attach(tab)
    harness = IsolatedRefreshHost(engine, host)
    return engine, handle, browser, host, adapter, settings, coordinator, now, harness


def paused_run_owner(engine, handle, host):
    def set_gate(gate):
        host.control = CoreControl(host.control.revision + 1, gate)

    async def observe(_url):
        return None

    host.set_gate = set_gate
    host.observe = observe
    run_owner = object.__new__(UtikiProductionRun)
    run_owner.tab = SimpleNamespace(target=SimpleNamespace(target_id=handle.tab.target_id))
    run_owner.key = handle.tab
    run_owner.host = host
    run_owner.engine = engine
    run_owner.handle = handle
    run_owner._closed = False
    run_owner._closing_requested = False
    run_owner.close_status = ""
    return run_owner


def test_formal_close_overrides_pause_signal_and_retires_original_owner(tmp_path):
    async def run():
        previous, handle, browser, _, adapter, _, _, _, _ = setup(tmp_path)
        tab = SimpleNamespace(target=SimpleNamespace(target_id=handle.tab.target_id))
        host = _RunHost(tab, handle.tab, None, lambda: CoreGate.PAUSED)
        engine = UtikiEngine(previous._policy, browser, host, refresh_coordinator=adapter)
        bound = engine.attach(handle.tab)
        owner = object.__new__(UtikiProductionRun)
        owner.tab = tab
        owner.key = handle.tab
        owner.host = host
        owner.engine = engine
        owner.handle = bound
        owner._closed = False
        owner._closing_requested = False
        owner.close_status = ""
        await owner.close()
        assert owner.closed
        assert owner.handoff_ready
        assert owner.close_status == ""
        assert host.current_control(handle.tab).gate is CoreGate.CLOSING
        assert engine.context(bound) is None
        await owner.close()
        assert owner.handoff_ready

    asyncio.run(run())


def test_departure_handoff_waits_for_exact_original_probe_then_releases_tab(tmp_path):
    from nodriver_tixcraft import retire_utiki_route_owner

    async def run():
        engine, handle, browser, host, adapter, _, coordinator, clock, harness = setup(tmp_path)

        async def real_renew(permit_id):
            return await SqliteRefreshAdapter.renew(adapter, permit_id)

        adapter.renew = real_renew
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        run_owner = paused_run_owner(engine, handle, host)

        ready, result = await retire_utiki_route_owner(run_owner)
        assert not ready and result.progress == "manual-reconciliation-required"
        assert len(browser.dispatches) == 1

        before = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_expires_at"]
        clock[0] += 5
        intent.next_probe_renew_at = 0
        result = await run_owner.maintain_originals_without_url()
        after = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_expires_at"]
        assert result.progress == "manual-reconciliation-required"
        assert after > before
        assert len(browser.dispatches) == 1

        browser.document = replace(browser.document, document_id="2000", started_at=2.0,
                                   classification="normal")
        ready, result = await retire_utiki_route_owner(run_owner)
        assert ready and result.progress == "closed"
        assert not coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_owner"]
        assert len(browser.dispatches) == 1

    asyncio.run(run())


def test_missing_url_cleans_original_queue_without_dispatch(tmp_path):
    async def run():
        engine, handle, browser, host, _, _, coordinator, _, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.request_id
        run_owner = paused_run_owner(engine, handle, host)
        result = await run_owner.maintain_originals_without_url()
        assert result.progress == "observation-unavailable"
        assert intent.phase is RefreshPhase.ABANDONED
        assert not coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        assert browser.dispatches == []

    asyncio.run(run())


def test_unconfirmed_close_blocks_new_work_and_can_retry_original_handle(tmp_path):
    async def run():
        engine, handle, browser, host, _, _, _, _, _ = setup(tmp_path)
        run_owner = paused_run_owner(engine, handle, host)
        original_close = engine.close
        calls = [0]

        def uncertain_close(bound):
            calls[0] += 1
            if calls[0] == 1:
                return False
            return original_close(bound)

        engine.close = uncertain_close
        await run_owner.close()
        assert not run_owner.closed
        assert not run_owner.handoff_ready
        assert run_owner.close_status == "owner-close-unconfirmed"
        result = await run_owner.tick(URL)
        assert result.progress == "closing"
        assert run_owner.closed and run_owner.handoff_ready
        assert calls == [2]
        assert browser.dispatches == []

    asyncio.run(run())


def test_real_sqlite_refresh_one_reload_then_new_document_clearance(tmp_path):
    async def run():
        engine, handle, browser, host, _, _, coordinator, _, harness = setup(tmp_path)
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        assert host.observed == ["1000"]
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert len(browser.dispatches) == 1
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.phase is RefreshPhase.AWAITING_CLEARANCE
        assert engine.context(handle) is None
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.COMPLETE
        assert engine.context(handle) is not None
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.COMPLETE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_ready_and_control_race_releases_exact_reservation(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, _, harness = setup(tmp_path)
        def pause():
            host.control = CoreControl(2, CoreGate.PAUSED)
            engine.update_control(handle)
        adapter.after_reserve = pause
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        snap = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        assert snap["reservation"] is None and snap["queue"] == []
    asyncio.run(run())


def test_lost_commit_replay_does_not_grant_reload(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, _, coordinator, _, harness = setup(tmp_path)
        adapter.commit_lost = True
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.COMMIT_UNKNOWN
        assert browser.dispatches == []
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        assert engine.refresh_intent(handle).phase is RefreshPhase.ABANDONED
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_release_false_is_visible_and_never_local_fallback(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, settings, _, _, harness = setup(tmp_path)
        def disable():
            settings["enabled"] = False
        adapter.after_reserve = disable
        adapter.release_fails = True
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.CLEANUP_PENDING
        assert engine.refresh_intent(handle).phase is RefreshPhase.CLEANUP_PENDING
        assert browser.dispatches == []
        adapter.release_fails = False
        engine.refresh_intent(handle).next_retry_at = 0
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
    asyncio.run(run())


def test_cleanup_failure_has_bounded_manual_outlet(tmp_path):
    async def run():
        engine, handle, browser, _host, adapter, settings, _, _, harness = setup(tmp_path)
        adapter.after_reserve = lambda: settings.__setitem__("enabled", False)
        adapter.release_fails = True
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.CLEANUP_PENDING
        intent = engine.refresh_intent(handle)
        assert intent is not None
        for _ in range(2):
            intent.next_retry_at = 0
            assert await harness.tick(handle, schedule_due=True) is RefreshProgress.CLEANUP_PENDING
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.MANUAL_RECONCILIATION
        assert intent.phase is RefreshPhase.RECONCILIATION_REQUIRED
        assert browser.dispatches == []

    asyncio.run(run())


def test_reload_possible_and_probe_renew_none_are_not_second_dispatch(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, clock, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2.0
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        adapter.renew_result = None
        intent.next_probe_renew_at = 0
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        assert intent.probe_lost is False
        assert intent.probe_unknown is True
        adapter.renew_result = False
        intent.next_probe_renew_at = 0
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert intent.probe_lost is True
        assert intent.probe_unknown is False
        assert intent.phase is RefreshPhase.OWNERSHIP_LOST
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_real_sqlite_probe_renews_through_new_document_clearance(tmp_path):
    async def run():
        engine, handle, browser, _host, adapter, _, coordinator, clock, harness = setup(tmp_path)

        async def real_renew(permit_id):
            return await SqliteRefreshAdapter.renew(adapter, permit_id)

        adapter.renew = real_renew
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        browser.document = replace(browser.document, document_id="2000", classification="unverified")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.AWAITING_CLEARANCE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        for _ in range(7):
            clock[0] += 5
            intent.next_probe_renew_at = 0
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.AWAITING_CLEARANCE
        assert clock[0] > 132  # Beyond the original 30-second SQLite lease.
        browser.document = replace(browser.document, classification="normal")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.COMPLETE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1

    asyncio.run(run())


def test_expired_real_sqlite_probe_has_bounded_manual_outlet(tmp_path):
    async def run():
        engine, handle, browser, _host, adapter, _, coordinator, clock, harness = setup(tmp_path)

        async def real_renew(permit_id):
            return await SqliteRefreshAdapter.renew(adapter, permit_id)

        adapter.renew = real_renew
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        clock[0] = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_expires_at"] + 1
        intent.next_probe_renew_at = 0
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert intent.phase is RefreshPhase.OWNERSHIP_LOST
        browser.document = replace(browser.document, document_id="2000", classification="normal")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert len(browser.dispatches) == 1

    asyncio.run(run())


def test_probe_completion_database_error_preserves_owned_lease(tmp_path):
    async def run():
        engine, handle, browser, _host, adapter, _, _coordinator, clock, harness = setup(tmp_path)

        async def real_renew(permit_id):
            return await SqliteRefreshAdapter.renew(adapter, permit_id)

        adapter.renew = real_renew
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        browser.document = replace(browser.document, document_id="2000", classification="normal")
        original_complete = adapter.complete_probe
        calls = 0

        async def failed_once(instance_id, permit_id, success):
            nonlocal calls
            calls += 1
            if calls == 1:
                return CompletedProbe(False, True, "database unavailable")
            return await original_complete(instance_id, permit_id, success)

        adapter.complete_probe = failed_once
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.AWAITING_CLEARANCE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.phase is RefreshPhase.AWAITING_CLEARANCE
        assert not intent.probe_lost
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.COMPLETE
        assert calls == 2 and len(browser.dispatches) == 1

    asyncio.run(run())


def test_probe_timeout_completes_exact_lease_then_requires_manual_reconciliation(tmp_path):
    async def run():
        engine, handle, browser, _host, adapter, _, coordinator, clock, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        intent.probe_started_at = time.monotonic() - 121
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert intent.phase is RefreshPhase.RECONCILIATION_REQUIRED
        assert not coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_owner"]
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.MANUAL_RECONCILIATION
        assert len(browser.dispatches) == 1

    asyncio.run(run())


def test_formal_paused_tick_cancels_its_real_sqlite_queue(tmp_path):
    async def run():
        engine, handle, browser, host, _adapter, _, coordinator, _, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.request_id
        assert any(
            row["request_id"] == intent.request_id
            for row in coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        )

        run_owner = paused_run_owner(engine, handle, host)
        result = await run_owner.tick(URL, paused=True)
        assert result.progress == "paused"
        assert not any(
            row["request_id"] == intent.request_id
            for row in coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        )
        assert browser.dispatches == []

    asyncio.run(run())


def test_observation_failure_still_cleans_original_queue_while_paused(tmp_path):
    async def run():
        engine, handle, browser, host, _adapter, _, coordinator, _, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.request_id
        run_owner = paused_run_owner(engine, handle, host)

        async def unavailable(_url):
            raise RuntimeError("synthetic observation failure")

        async def submit_unavailable(_handle):
            raise RuntimeError("synthetic submit readback failure")

        host.observe = unavailable
        engine.advance_submit = submit_unavailable
        result = await run_owner.tick(URL, paused=True)
        assert result.progress == "observation-unavailable"
        assert intent.phase is RefreshPhase.ABANDONED
        assert not any(
            row["request_id"] == intent.request_id
            for row in coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        )
        assert browser.dispatches == []

    asyncio.run(run())


def test_formal_paused_tick_releases_its_real_sqlite_reservation(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, clock, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        intent = engine.refresh_intent(handle)
        assert intent is not None
        clock[0] += 2
        reservation = await adapter.reserve(intent.instance_id, intent.token, 1.0)
        assert reservation.reserved and reservation.reservation_id
        intent.reservation_id = reservation.reservation_id
        intent.phase = RefreshPhase.RESERVED
        run_owner = paused_run_owner(engine, handle, host)
        result = await run_owner.tick(URL, paused=True)
        assert result.progress == "paused"
        assert intent.phase is RefreshPhase.ABANDONED
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["reservation"] is None
        assert browser.dispatches == []

    asyncio.run(run())


def test_formal_paused_tick_renews_existing_probe_without_reload(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, clock, harness = setup(tmp_path)

        async def real_renew(permit_id):
            return await SqliteRefreshAdapter.renew(adapter, permit_id)

        adapter.renew = real_renew
        browser.document = replace(browser.document, classification="blocked")
        browser.change_on_dispatch = False
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        clock[0] += 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.is_probe
        before = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_expires_at"]
        clock[0] += 5
        intent.next_probe_renew_at = 0
        run_owner = paused_run_owner(engine, handle, host)
        result = await run_owner.tick(URL, paused=True)
        assert result.progress == "paused"
        after = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["probe_expires_at"]
        assert after > before
        assert intent.phase is RefreshPhase.RELOAD_POSSIBLE
        assert len(browser.dispatches) == 1

    asyncio.run(run())


def test_purchase_ready_at_reserve_return_cancels_before_commit(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, _, harness = setup(tmp_path)
        plan = select_quantity_snapshot(make_ctbc_quantity_snapshot(), "全票", 1)
        assert plan is not None
        def purchase_ready():
            current = engine._current(handle)
            assert current is not None
            current.readiness = _Readiness(
                plan, PlanningContext(
                    handle.generation, engine._policy.policy_revision,
                    current.control_revision,
                ),
            )
        adapter.after_reserve = purchase_ready
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        snap = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)
        assert snap["reservation"] is None and snap["queue"] == []
        assert snap["bucket"]["sequence"] == 0
    asyncio.run(run())


def test_committed_but_pre_dispatch_control_revocation_consumes_pacing(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, _, harness = setup(tmp_path)
        def pause():
            host.control = CoreControl(2, CoreGate.PAUSED)
            assert engine.update_control(handle)
        adapter.after_commit = pause
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.phase is RefreshPhase.ABANDONED
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_disabled_scheduled_immediate_but_health_uses_local_interval(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, _, _, harness = setup(tmp_path, enabled=False)
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert len(browser.dispatches) == 1
        second = setup(tmp_path / "second", enabled=False)
        engine2, handle2, browser2, host2, _, _, _, _, harness2 = second
        assert await harness2.tick(
            handle2, schedule_due=True, reason=RefreshReason.HEALTH,
        ) is RefreshProgress.WAITING
        assert browser2.dispatches == []
        intent = engine2.refresh_intent(handle2)
        assert intent is not None and intent.eligible_at > 0
    asyncio.run(run())


def test_closed_run_late_reload_receipt_reconciles_its_own_token(tmp_path):
    async def run():
        engine, handle, browser, host, _, _, coordinator, _, harness = setup(tmp_path)
        browser.dispatch_started = asyncio.Event()
        browser.dispatch_release = asyncio.Event()
        task = asyncio.create_task(harness.tick(handle, schedule_due=True))
        await browser.dispatch_started.wait()
        original = engine.refresh_intent(handle)
        assert original is not None and original.phase is RefreshPhase.RELOAD_POSSIBLE
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.WAITING
        assert len(browser.dispatches) == 1
        host.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.dispatch_release.set()
        assert await task is RefreshProgress.RELOAD_POSSIBLE
        assert original.receipt is not None
        assert original.receipt.evidence is EffectEvidence.REQUEST_POSSIBLE
        assert await engine.reconcile_closed_refresh(handle) is RefreshProgress.COMPLETE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
        host.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        replacement = engine.attach(handle.tab)
        assert replacement.generation > handle.generation
    asyncio.run(run())


def test_retired_request_cleanup_cannot_cancel_rebound_request(tmp_path):
    async def run():
        engine, handle, browser, host, adapter, _, coordinator, _, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        old = engine.refresh_intent(handle)
        assert old is not None and old.request_id
        host.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        host.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        rebound = UtikiEngine(engine._policy, browser, host, refresh_coordinator=adapter)
        second = rebound.attach(handle.tab)
        assert await IsolatedRefreshHost(rebound, host).tick(
            second, schedule_due=False,
        ) is RefreshProgress.WAITING
        new = rebound.refresh_intent(second)
        assert new is not None and new.token != old.token
        assert new.request_id in {
            row["request_id"] for row in coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        }
        assert await engine.reconcile_closed_refresh(handle) is RefreshProgress.BLOCKED
        requests = {
            row["request_id"] for row in coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["queue"]
        }
        assert new.request_id in requests and old.request_id not in requests
    asyncio.run(run())


def test_shared_settings_failure_never_grants_disabled_local_reload(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, _, _, _, harness = setup(tmp_path)
        def unavailable(_force):
            raise OSError("settings unavailable")
        adapter._settings_reader = unavailable
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.SETTINGS_UNKNOWN
        assert browser.dispatches == []
        assert engine.refresh_intent(handle).phase is RefreshPhase.QUEUED
    asyncio.run(run())


def test_probe_new_429_is_reported_once_and_ends_original_refresh(tmp_path):
    async def run():
        engine, handle, browser, _, _, _, coordinator, clock, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="blocked")
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        first = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]
        assert first["consecutive_429"] == 1
        clock[0] += 2.0
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.BLOCKED
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.phase is RefreshPhase.BLOCKED_429
        assert len(browser.dispatches) == 1
        second = coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]
        assert second["consecutive_429"] == 2
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["consecutive_429"] == 2
        assert len(browser.dispatches) == 1
    asyncio.run(run())


def test_disable_after_commit_abandons_permit_without_refunding_pacing(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, settings, coordinator, _, harness = setup(tmp_path)
        adapter.after_commit = lambda: settings.__setitem__("enabled", False)
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
        # A later explicit disabled tick may create a different local request.
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_enable_before_local_dispatch_requires_new_shared_reservation(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, settings, coordinator, _, harness = setup(
            tmp_path, enabled=False,
        )
        original_settings = adapter.settings
        calls = [0]
        def changing_settings(*, force=False):
            calls[0] += 1
            if calls[0] == 2:
                settings["enabled"] = True
            return original_settings(force=force)
        adapter.settings = changing_settings
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"] is None
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_reload_dispatch_timeout_stays_possible_until_new_document(tmp_path):
    async def run():
        engine, handle, browser, host, _, _, coordinator, _, harness = setup(tmp_path)
        async def timed_out(claim, expected_url):
            assert expected_url == URL
            browser.dispatches.append(claim)
            raise TimeoutError("browser did not confirm navigation")
        browser.dispatch_reload = timed_out
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.RELOAD_POSSIBLE
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.RELOAD_POSSIBLE
        assert len(browser.dispatches) == 1
        browser.document = replace(browser.document, document_id="2000", started_at=2.0)
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.COMPLETE
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_lost_reserve_receipt_reuses_original_request_and_reservation(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, _, coordinator, _, harness = setup(tmp_path)
        original_reserve = adapter.reserve
        calls = [0]
        async def lost_once(instance_id, token, minimum):
            result = await original_reserve(instance_id, token, minimum)
            calls[0] += 1
            if calls[0] == 1:
                return replace(result, reserved=False, status="error", error="lost receipt")
            return result
        adapter.reserve = lost_once
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.RESERVE_UNKNOWN
        original = engine.refresh_intent(handle)
        assert original is not None and original.reservation_id == ""
        assert browser.dispatches == []
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert engine.refresh_intent(handle) is original
        assert calls[0] == 2 and len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_settings_failure_after_commit_keeps_consumed_permit_until_recheck(tmp_path):
    async def run():
        engine, handle, browser, _, adapter, _, coordinator, _, harness = setup(tmp_path)
        original_settings = adapter.settings
        calls = [0]
        def intermittent(*, force=False):
            calls[0] += 1
            if calls[0] == 3:
                raise OSError("settings source unavailable")
            return original_settings(force=force)
        adapter.settings = intermittent
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.SETTINGS_UNKNOWN
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.phase is RefreshPhase.PERMITTED
        assert browser.dispatches == []
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
        adapter.settings = original_settings
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert len(browser.dispatches) == 1
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
    asyncio.run(run())


def test_unverified_challenge_document_cannot_use_health_as_mutation_port(tmp_path):
    async def run():
        engine, handle, browser, _, _, _, coordinator, _, harness = setup(tmp_path)
        browser.document = replace(browser.document, classification="unverified")
        assert await harness.tick(
            handle, schedule_due=True, reason=RefreshReason.HEALTH,
        ) is RefreshProgress.BLOCKED
        assert browser.dispatches == []
        assert engine.refresh_intent(handle) is None
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"] is None
    asyncio.run(run())


def test_new_429_after_completed_schedule_starts_recovery_before_next_due(tmp_path):
    async def run():
        engine, handle, browser, _, _, _, coordinator, _, harness = setup(tmp_path)
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.AWAITING_CLEARANCE
        assert await harness.tick(handle, schedule_due=True) is RefreshProgress.COMPLETE
        browser.document = replace(
            browser.document, document_id="3000", started_at=3.0,
            classification="blocked",
        )
        assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        intent = engine.refresh_intent(handle)
        assert intent is not None and intent.reason is RefreshReason.RATE_LIMIT_RECOVERY
        assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["consecutive_429"] == 1
        assert len(browser.dispatches) == 1
    asyncio.run(run())
