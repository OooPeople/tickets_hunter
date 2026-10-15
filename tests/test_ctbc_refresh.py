import asyncio
import json
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from tests.ctbc_support import (
    CtbcStateIsolationMixin,
    FakeSubmitTab,
    FakeTab,
    ctbc_identity_runtime,
    ctbc_tab_runtime,
    config,
)

from platforms.utiki.workflow import (
    _classify_utk_document_state,
    _ctbc_tab_identity,
    _finish_utk_recovery_probe,
    _inspect_utk_document,
    _pop_reload_request_if_token,
    _state,
    _utk_document_guard,
    nodriver_ctbc_main,
    nodriver_utk_sports_cancel_refresh,
    nodriver_utk_sports_consume_reload_event,
    nodriver_utk_sports_reload,
    nodriver_utk_sports_reload_wait,
)


CTBC_DATE_ROW_SELECTOR = "#PerformanceListTable table > tbody > tr"


class CtbcSharedRefreshTest(CtbcStateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    ctbc_state = _state

    def setUp(self):
        super().setUp()
        self.global_settings = {
            "utk_sports": {
                "enabled": True,
                "min_interval_seconds": 1.5,
                "initial_cooldown_seconds": 60,
                "max_cooldown_seconds": 240,
            }
        }
        marker_patch = patch("platforms.utiki.workflow._mark_utk_emergency_window")
        remaining_patch = patch(
            "platforms.utiki.workflow._utk_emergency_remaining", return_value=0.0
        )
        marker_patch.start()
        remaining_patch.start()
        self.addCleanup(marker_patch.stop)
        self.addCleanup(remaining_patch.stop)

    @staticmethod
    def coordinator_for(decision, *, report=None):
        reservation = types.SimpleNamespace(
            reserved=bool(decision.granted),
            status="reserved" if decision.granted else decision.status,
            retry_after_seconds=getattr(decision, "retry_after_seconds", 0.0),
            reservation_id="reservation-1" if decision.granted else "",
            is_recovery_probe=getattr(decision, "is_recovery_probe", False),
            cooldown_until=getattr(decision, "cooldown_until", 0.0),
            error=getattr(decision, "error", ""),
        )
        return types.SimpleNamespace(
            cancel_request=AsyncMock(return_value=False),
            reserve_refresh=AsyncMock(return_value=reservation),
            commit_refresh=AsyncMock(return_value=decision),
            release_refresh=AsyncMock(return_value=True),
            report_rate_limited=AsyncMock(return_value=report),
        )

    def test_rate_limit_classifier_requires_code_message_and_no_normal_root(self):
        self.assertEqual(
            "blocked",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "429 - 請求頻率過高 HTTP 429 - Too Many Requests",
                "has_normal_root": False,
            }),
        )
        self.assertEqual(
            "normal",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "票價 429 元",
                "has_normal_root": True,
            }),
        )
        self.assertEqual(
            "unknown",
            _classify_utk_document_state({
                "ready": False,
                "body_text": "429 - 請求頻率過高",
                "has_normal_root": False,
            }),
        )
        self.assertEqual(
            "unknown",
            _classify_utk_document_state({
                "ready": True,
                "body_text": "一般錯誤代碼 429",
                "has_normal_root": False,
            }),
        )

    async def test_document_inspection_classifies_the_dom_snapshot(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR,
            [],
            url=source,
            evaluate_results=[json.dumps({
                "ready": True,
                "body_text": "429 - 請求頻率過高 HTTP 429 - Too Many Requests",
                "document_id": source + "|123",
                "url": source,
                "ready_state": "complete",
                "has_normal_root": False,
            })],
        )
        snapshot = await _inspect_utk_document(tab)
        self.assertEqual("blocked", snapshot["classification"])
        self.assertEqual(source + "|123", snapshot["document_id"])

    async def test_429_guard_runs_before_date_dispatch_and_reports_once(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|429",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=0.25,
                cooldown_until=time.time() + 60,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            ),
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator), \
                    patch("platforms.utiki.workflow.nodriver_ctbc_date_auto_select", AsyncMock()) as select:
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            select.assert_not_awaited()
            self.assertEqual(1, coordinator.report_rate_limited.await_count)
        finally:
            runtime["refresh"].update({
                "rate_limit_observation": None,
                "reload_request": None,
                "recovery_probe": None,
                "reload_event": False,
            })

    async def test_cooldown_expiry_grants_one_reload_of_the_blocked_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|429",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="probe-reload",
                is_recovery_probe=True,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            ),
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            self.assertTrue(tab.reloaded)
            self.assertEqual(
                "probe-reload", runtime["refresh"]["recovery_probe"]["permit_id"]
            )
            self.assertEqual(1, coordinator.report_rate_limited.await_count)
        finally:
            runtime["refresh"].update({
                "rate_limit_observation": None,
                "reload_request": None,
                "recovery_probe": None,
                "reload_event": False,
            })

    async def test_shared_permit_is_required_before_programmatic_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        decision = types.SimpleNamespace(
            granted=True,
            status="granted",
            retry_after_seconds=0.0,
            permit_id="permit-1",
            is_recovery_probe=False,
            error="",
        )
        coordinator = self.coordinator_for(decision)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "scheduled refresh",
                    apply_local_interval=False,
                )
            self.assertTrue(reloaded)
            self.assertTrue(tab.reloaded)
            self.assertTrue(nodriver_utk_sports_consume_reload_event(tab))
            self.assertFalse(nodriver_utk_sports_consume_reload_event(tab))
            self.assertEqual(
                1.5,
                coordinator.reserve_refresh.await_args.kwargs["min_interval_seconds"],
            )
            coordinator.commit_refresh.assert_awaited_once()
        finally:
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["recovery_probe"] = None

    async def test_reload_orders_reserve_preflight_commit_then_network(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        events = []

        async def reserve(*_args, **_kwargs):
            events.append("reserve")
            return types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-order",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )

        async def inspect(*_args, **_kwargs):
            events.append("preflight")
            return snapshot

        async def commit(*_args, **_kwargs):
            events.append("commit")
            return types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="permit-order",
                is_recovery_probe=False,
                error="",
            )

        async def reload():
            events.append("reload")
            tab.reloaded = True

        tab.reload = reload
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(side_effect=reserve),
            commit_refresh=AsyncMock(side_effect=commit),
            release_refresh=AsyncMock(return_value=True),
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(side_effect=inspect)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertTrue(await nodriver_utk_sports_reload(
                    tab, config(), "ordered reload", apply_local_interval=False
                ))
            self.assertEqual(["reserve", "preflight", "commit", "reload"], events)
        finally:
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["reload_event"] = False

    async def test_local_interval_delays_reload_until_configured_deadline(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["advanced"]["auto_reload_page_interval"] = 7.25
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        snapshot = {
            "classification": "normal",
            "document_id": str(tab.target.url) + "|100",
            "url": str(tab.target.url),
        }
        clock = {"now": 100.0}
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="interval-permit", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)

        async def reload_once():
            return await nodriver_utk_sports_reload(
                tab, cfg, "seat conflict", apply_local_interval=True
            )

        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._utk_document_guard", AsyncMock(
                        return_value=("normal", snapshot))), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(
                        return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator",
                          return_value=coordinator), \
                    patch(
                "platforms.utiki.workflow.time",
                types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
            ):
                self.assertFalse(await reload_once())
                request = runtime["refresh"]["reload_request"]
                self.assertEqual(107.25, request["eligible_at"])
                clock["now"] = request["eligible_at"] - 0.001
                self.assertFalse(await reload_once())
                clock["now"] = request["eligible_at"]
                self.assertTrue(await reload_once())

            self.assertEqual(1, tab.reload_count)
            self.assertEqual(1, coordinator.reserve_refresh.await_count)
            self.assertEqual(1, coordinator.commit_refresh.await_count)
        finally:
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["recovery_probe"] = None
            runtime["refresh"]["reload_event"] = False

    async def test_incident_during_preflight_revokes_reload_at_commit(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-revoked",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )),
            commit_refresh=AsyncMock(return_value=types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=60.0,
                cooldown_until=time.time() + 60,
                error="",
            )),
            release_refresh=AsyncMock(return_value=False),
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(await nodriver_utk_sports_reload(
                    tab, config(), "revoked reload", apply_local_interval=False
                ))
            coordinator.commit_refresh.assert_awaited_once()
            self.assertFalse(tab.reloaded)
        finally:
            runtime["refresh"]["reload_request"] = None

    async def test_cancelled_preflight_releases_its_reservation(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|100",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=True,
                status="reserved",
                retry_after_seconds=0.0,
                reservation_id="reservation-cancelled",
                is_recovery_probe=False,
                cooldown_until=0.0,
                error="",
            )),
            release_refresh=AsyncMock(return_value=True),
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(side_effect=asyncio.CancelledError)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                with self.assertRaises(asyncio.CancelledError):
                    await nodriver_utk_sports_reload(
                        tab, config(), "cancelled preflight", apply_local_interval=False
                    )
            coordinator.release_refresh.assert_awaited_once()
            self.assertFalse(tab.reloaded)
        finally:
            runtime["refresh"]["reload_request"] = None

    def test_old_coroutine_cannot_pop_a_new_local_reload_request(self):
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [])
        identity = _ctbc_tab_identity(tab)
        refresh = ctbc_identity_runtime(identity)["refresh"]
        refresh["reload_request"] = {"request_token": "new-token"}
        try:
            self.assertIsNone(
                _pop_reload_request_if_token(identity, "old-token")
            )
            self.assertEqual(
                "new-token",
                refresh["reload_request"]["request_token"],
            )
        finally:
            refresh["reload_request"] = None

    async def test_reload_wait_has_a_bounded_fail_closed_deadline(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        inner_reload = AsyncMock(return_value=True)
        with patch("platforms.utiki.workflow.UTK_RELOAD_WAIT_MAX_SECONDS", 0.0), \
                patch("platforms.utiki.workflow.time.monotonic", return_value=100.0), \
                patch("platforms.utiki.workflow.nodriver_utk_sports_reload", inner_reload), \
                patch("platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh", AsyncMock(return_value=True)) as cancel, \
                patch("platforms.utiki.workflow.check_and_handle_pause", AsyncMock(return_value=False)), \
                patch("platforms.utiki.workflow.check_and_handle_quit", AsyncMock(return_value=False)):
            self.assertFalse(await nodriver_utk_sports_reload_wait(
                tab, config(), "bounded startup reload"
            ))
        cancel.assert_awaited_once()
        inner_reload.assert_not_awaited()

    async def test_old_429_follower_reloads_without_reopening_the_incident(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|old-429",
            "url": source,
        }
        runtime["refresh"]["rate_limit_observation"] = snapshot["document_id"]
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=True,
                status="granted",
                retry_after_seconds=0.0,
                permit_id="paced-follower",
                is_recovery_probe=False,
                error="",
            )
        )
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "post-recovery follower",
                    apply_local_interval=False,
                    allow_blocked_recovery=True,
                )
            self.assertTrue(reloaded)
            coordinator.report_rate_limited.assert_not_awaited()
        finally:
            runtime["refresh"].update({
                "rate_limit_observation": None,
                "reload_request": None,
                "recovery_probe": None,
                "reload_event": False,
            })

    async def test_probe_429_passes_its_permit_for_backoff_escalation(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "probe-1",
            "source_document_id": source + "|old",
        }
        snapshot = {
            "classification": "blocked",
            "document_id": source + "|new",
            "url": source,
        }
        coordinator = self.coordinator_for(
            types.SimpleNamespace(
                granted=False,
                status="cooldown",
                retry_after_seconds=0.25,
                cooldown_until=time.time() + 120,
                error="",
            ),
            report=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=2,
                retry_after_seconds=120.0,
                error="",
            ),
        )
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            self.assertEqual(
                "probe-1",
                coordinator.report_rate_limited.await_args.kwargs["probe_permit_id"],
            )
            self.assertIsNone(runtime["refresh"]["recovery_probe"])
        finally:
            runtime["refresh"]["rate_limit_observation"] = None
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["recovery_probe"] = None

    async def test_probe_lease_is_renewed_until_document_is_classified(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "probe-renew",
            "source_document_id": source + "|old",
            "started_at": 100.0,
            "next_renew_at": 101.0,
        }
        snapshot = {
            "classification": "unknown",
            "document_id": source + "|old",
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            renew_recovery_probe=AsyncMock(return_value=True),
        )
        try:
            with patch("platforms.utiki.workflow.time.monotonic", return_value=106.0), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(
                    await _finish_utk_recovery_probe(tab, config(), snapshot)
                )
            coordinator.renew_recovery_probe.assert_awaited_once_with(
                "utk-sports", "probe-renew"
            )
            self.assertEqual(
                111.0,
                runtime["refresh"]["recovery_probe"]["next_renew_at"],
            )
        finally:
            runtime["refresh"]["recovery_probe"] = None

    async def test_pause_abandons_owned_probe_without_marking_recovery_success(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "probe-pause",
            "source_document_id": source + "|old",
        }
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True,
                recovery_required=True,
                error="",
            )),
        )
        with patch(
            "platforms.utiki.workflow.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertTrue(
                await nodriver_utk_sports_cancel_refresh(
                    tab,
                    "paused",
                    wait=True,
                    abandon_probe=True,
                )
            )
        self.assertFalse(
            coordinator.complete_recovery_probe.await_args.kwargs["success"]
        )
        self.assertIsNone(runtime["refresh"]["recovery_probe"])

    async def test_reenabled_switch_prevents_legacy_uncoordinated_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        disabled = {"utk_sports": dict(self.global_settings["utk_sports"], enabled=False)}
        with patch(
            "platforms.utiki.workflow.load_global_settings",
            side_effect=[disabled, self.global_settings],
        ), patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.workflow.check_and_handle_quit",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(
                await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "switch changed",
                    apply_local_interval=False,
                )
            )
        self.assertFalse(tab.reloaded)

    async def test_disabled_guard_still_observes_document_changes(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        disabled = {
            "utk_sports": dict(self.global_settings["utk_sports"], enabled=False)
        }
        snapshots = [
            {
                "classification": "normal",
                "document_id": source + "|old",
                "url": source,
            },
            {
                "classification": "normal",
                "document_id": source + "|new",
                "url": source,
            },
        ]

        with patch(
            "platforms.utiki.workflow.load_global_settings", return_value=disabled
        ), patch(
            "platforms.utiki.workflow._inspect_utk_document",
            AsyncMock(side_effect=snapshots),
        ):
            first, first_snapshot = await _utk_document_guard(tab, config(), source)
            second, second_snapshot = await _utk_document_guard(tab, config(), source)

        self.assertEqual("disabled", first)
        self.assertEqual("disabled", second)
        self.assertEqual(source + "|old", first_snapshot["document_id"])
        self.assertEqual(source + "|new", second_snapshot["document_id"])
        self.assertEqual(
            source + "|new", runtime["document"]["last_document"]
        )
        self.assertTrue(runtime["refresh"]["reload_event"])

    async def test_persistent_unknown_document_never_opens_shared_cooldown(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        snapshot = {
            "classification": "unknown",
            "document_id": source + "|unknown",
            "document_started_at": 1000.0,
            "url": source,
        }
        coordinator = types.SimpleNamespace(
            report_rate_limited=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True,
                new_incident=True,
                consecutive_incidents=1,
                retry_after_seconds=60.0,
                error="",
            )),
        )
        try:
            with patch("platforms.utiki.workflow.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.workflow._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.workflow.get_shared_refresh_coordinator", return_value=coordinator), \
                    patch("platforms.utiki.workflow.time.monotonic", side_effect=[100.0, 100.0, 111.0, 111.0, 111.0]):
                first, _ = await _utk_document_guard(tab, config(), source)
                second, _ = await _utk_document_guard(tab, config(), source)
            self.assertEqual("unknown", first)
            self.assertEqual("unknown", second)
            coordinator.report_rate_limited.assert_not_awaited()
        finally:
            runtime["document"]["unknown_document"] = None
            runtime["refresh"]["rate_limit_observation"] = None
            runtime["document"]["last_document"] = ""
