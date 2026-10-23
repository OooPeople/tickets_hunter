import asyncio
import builtins
import json
import tempfile
import time
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from nodriver_tixcraft import (
    _cleanup_utiki_if_driver_missing,
    _pause_utk_refresh,
    main as nodriver_main,
)

from tests.ctbc_support import (
    CtbcStateIsolationMixin,
    FakeSubmitTab,
    FakeTab,
    ctbc_identity_runtime,
    ctbc_tab_runtime,
    config,
)

from platforms.utiki.document_health import (
    _classify_utk_document_state,
    _inspect_utk_document,
)
from platforms.utiki.rate_limit import (
    _abandon_recovery_probe,
    _clear_completed_recovery_probe,
    _finish_utk_recovery_probe,
    _mark_utk_emergency_window,
    _utk_emergency_remaining,
)
from platforms.utiki.refresh import (
    _commit_reserved_reload,
    _guard_for_reload,
    _pop_reload_request_if_token,
    _utk_document_guard,
    build_refresh_identity,
)
from platforms.utiki.workflow import (
    _ctbc_purchase_state_key,
    _ctbc_tab_identity,
    _state,
    nodriver_ctbc_main,
    nodriver_utk_sports_cancel_refresh,
    nodriver_utk_sports_cleanup_tab,
    nodriver_utk_sports_consume_reload_event,
    nodriver_utk_sports_health_check,
    nodriver_utk_sports_pause_refresh,
    nodriver_utk_sports_reload,
    nodriver_utk_sports_reload_wait,
)


CTBC_DATE_ROW_SELECTOR = "#PerformanceListTable table > tbody > tr"


class CtbcSharedRefreshTest(CtbcStateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    ctbc_state = _state

    class _StopOuterLoop(Exception):
        pass

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
        marker_patch = patch("platforms.utiki.rate_limit._mark_utk_emergency_window")
        remaining_patch = patch(
            "platforms.utiki.refresh._utk_emergency_remaining", return_value=0.0
        )
        marker_patch.start()
        remaining_patch.start()
        self.addCleanup(marker_patch.stop)
        self.addCleanup(remaining_patch.stop)

    async def _run_outer_main_case(
        self, tab, *, current_urls, quit_results, handler=None, stop_after=0,
    ):
        cfg = config()

        async def stop_driver():
            identity = str(getattr(
                tab, "_tickets_hunter_utiki_tab_token", ""
            ) or "")
            if identity:
                self.assertNotIn(identity, _state["tabs"])

        driver = types.SimpleNamespace(
            main_tab=None,
            config=types.SimpleNamespace(port=0),
            stop=AsyncMock(side_effect=stop_driver),
        )
        args = types.SimpleNamespace(instance=None, input=None, mcp_debug=False)
        sleep_calls = {"count": 0}

        async def bounded_sleep(_seconds):
            sleep_calls["count"] += 1
            if stop_after and sleep_calls["count"] >= stop_after:
                raise self._StopOuterLoop

        handler = handler or AsyncMock(return_value=tab)
        original_print = builtins.print
        with tempfile.TemporaryDirectory() as temp_root:
            try:
                builtins.print = lambda *_args, **_kwargs: None
                with ExitStack() as stack:
                    for patcher in (
                        patch("nodriver_tixcraft.get_config_dict", return_value=cfg),
                        patch(
                            "nodriver_tixcraft.get_extension_config",
                            return_value=types.SimpleNamespace(),
                        ),
                        patch("nodriver_tixcraft.nodriver_overwrite_prefs"),
                        patch(
                            "nodriver_tixcraft.uc.start",
                            AsyncMock(return_value=driver),
                        ),
                        patch(
                            "nodriver_tixcraft.nodriver_goto_homepage",
                            AsyncMock(return_value=tab),
                        ),
                        patch(
                            "nodriver_tixcraft.nodrver_block_urls",
                            AsyncMock(return_value=tab),
                        ),
                        patch(
                            "nodriver_tixcraft.nodriver_resize_window", AsyncMock()
                        ),
                        patch(
                            "nodriver_tixcraft.reload_config",
                            AsyncMock(return_value=(cfg, 0)),
                        ),
                        patch(
                            "nodriver_tixcraft.nodriver_current_url",
                            AsyncMock(side_effect=current_urls),
                        ),
                        patch(
                            "nodriver_tixcraft.check_and_handle_quit",
                            AsyncMock(side_effect=quit_results),
                        ),
                        patch(
                            "nodriver_tixcraft.check_and_handle_pause",
                            AsyncMock(return_value=False),
                        ),
                        patch(
                            "nodriver_tixcraft._advance_cloudflare_gate",
                            AsyncMock(return_value=(False, True, 0, False, 0)),
                        ),
                        patch(
                            "nodriver_tixcraft.nodriver_utk_sports_health_check",
                            AsyncMock(return_value="normal"),
                        ),
                        patch(
                            "nodriver_tixcraft.check_refresh_datetime_gate",
                            AsyncMock(return_value=False),
                        ),
                        patch("nodriver_tixcraft.nodriver_utiki_main", handler),
                        patch("nodriver_tixcraft.write_last_url_to_file"),
                        patch(
                            "nodriver_tixcraft.util.get_app_root",
                            return_value=temp_root,
                        ),
                        patch(
                            "nodriver_tixcraft.util.get_instance_state_path",
                            side_effect=lambda name: str(Path(temp_root, name)),
                        ),
                        patch("nodriver_tixcraft.os.path.exists", return_value=False),
                        patch(
                            "nodriver_tixcraft.asyncio.sleep",
                            AsyncMock(side_effect=bounded_sleep),
                        ),
                    ):
                        stack.enter_context(patcher)
                    try:
                        await nodriver_main(args)
                    except self._StopOuterLoop:
                        pass
            finally:
                builtins.print = original_print
        return driver, handler

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

    @staticmethod
    def reservation(reservation_id, *, probe=False):
        return types.SimpleNamespace(
            reserved=True, status="reserved", retry_after_seconds=0.0,
            reservation_id=reservation_id, is_recovery_probe=probe,
            cooldown_until=0.0, error="",
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
                "document_id": "123",
                "url": source,
                "ready_state": "complete",
                "has_normal_root": False,
            })],
        )
        runtime = ctbc_identity_runtime(_ctbc_tab_identity(tab))
        snapshot = await _inspect_utk_document(tab, runtime)
        self.assertEqual("blocked", snapshot["classification"])
        self.assertEqual("123", snapshot["document_id"])

    async def test_server_assigned_root_classifies_and_completes_probe(self):
        source = (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_001?"
            "PERFORMANCE_ID=P1&GROUP_ID=G1&"
            "PERFORMANCE_PRICE_AREA_ID=A1&ATYPE=N"
        )
        payloads = [
            {
                "ready": True, "body_text": "normal",
                "document_id": "100", "url": source,
                "ready_state": "complete", "has_normal_root": True,
                "requires_normal_root": True,
            },
            {
                "ready": True, "body_text": "normal",
                "document_id": "100", "url": source,
                "ready_state": "complete", "has_normal_root": False,
                "requires_normal_root": True,
            },
            {
                "ready": True,
                "body_text": "HTTP 429 Too Many Requests 請求頻率過高",
                "document_id": "100", "url": source,
                "ready_state": "complete", "has_normal_root": False,
                "requires_normal_root": True,
            },
            {
                "ready": True, "body_text": "normal",
                "document_id": "101", "url": source,
                "ready_state": "complete", "has_normal_root": True,
                "requires_normal_root": True,
            },
        ]
        evaluate_calls = {"count": 0}

        async def evaluate(script):
            self.assertIn(
                'document.querySelector("#table_tickettype")', script
            )
            self.assertIn("requires_normal_root: true", script)
            payload = payloads[evaluate_calls["count"]]
            evaluate_calls["count"] += 1
            return json.dumps(payload)

        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        tab.evaluate = AsyncMock(side_effect=evaluate)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        classifications = []
        for _ in range(3):
            snapshot = await _inspect_utk_document(tab, runtime, force=True)
            classifications.append(snapshot["classification"])
        self.assertEqual(["normal", "unknown", "blocked"], classifications)

        runtime["document"].update({"last_document": "100", "snapshot": None})
        runtime["refresh"]["reload_request"] = {
            "request_token": "server-probe-request",
            "phase": "awaiting_document",
            "source_document_token": "100",
            "reason": "HTTP 429 recovery probe",
            "paused": False,
            "deadline_at": 0.0,
        }
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "server-probe",
            "source_document_id": "100",
            "started_at": time.monotonic(),
            "next_renew_at": float("inf"),
        }
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True, recovery_required=False, error="",
            )),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertEqual(
                "normal",
                await nodriver_utk_sports_health_check(tab, source, config()),
            )

        coordinator.complete_recovery_probe.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(identity),
            "server-probe", success=True,
        )
        self.assertIsNone(runtime["refresh"]["recovery_probe"])
        self.assertIsNone(runtime["refresh"]["reload_request"])
        self.assertEqual(4, evaluate_calls["count"])

    async def test_document_token_is_stable_across_history_url_drift(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tracked = source + "&utm_source=test#fragment"
        snapshots = [
            {
                "ready": True, "body_text": "正常售票頁",
                "document_id": "123.5", "url": source,
                "ready_state": "complete", "has_normal_root": True,
            },
            {
                "ready": True, "body_text": "正常售票頁",
                "document_id": "123.5", "url": tracked,
                "ready_state": "complete", "has_normal_root": True,
            },
        ]
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [], url=source,
            evaluate_results=[json.dumps(item) for item in snapshots],
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        refresh_identity = build_refresh_identity(identity)

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ):
            first_state, first = await _utk_document_guard(
                tab, runtime, refresh_identity, _state["cancel_tasks"],
                config(), source,
            )
            tab.target.url = tracked
            second_state, second = await _utk_document_guard(
                tab, runtime, refresh_identity, _state["cancel_tasks"],
                config(), tracked,
            )

        self.assertEqual("normal", first_state)
        self.assertEqual("normal", second_state)
        self.assertEqual("123.5", first["document_id"])
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertNotEqual(first["url"], second["url"])
        self.assertEqual("123.5", runtime["document"]["last_document"])
        self.assertFalse(runtime["refresh"]["reload_event"])

    async def test_missing_document_token_cannot_signal_document_change(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(
            CTBC_DATE_ROW_SELECTOR, [], url=source,
            evaluate_results=[json.dumps({
                "ready": True, "body_text": "正常售票頁",
                "document_id": "0", "url": source,
                "ready_state": "complete", "has_normal_root": True,
            })],
        )
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["document"]["last_document"] = "123"

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ):
            state, snapshot = await _utk_document_guard(
                tab, runtime, build_refresh_identity(identity),
                _state["cancel_tasks"], config(), source,
            )

        self.assertEqual("unknown", state)
        self.assertEqual("", snapshot["document_id"])
        self.assertEqual("123", runtime["document"]["last_document"])
        self.assertFalse(runtime["refresh"]["reload_event"])

        runtime["document"]["snapshot"] = None
        tab.evaluate_results.append(json.dumps({
            "ready": True, "body_text": "正常售票頁",
            "document_id": "", "url": source,
            "ready_state": "complete", "has_normal_root": True,
        }))
        disabled = {
            "utk_sports": dict(
                self.global_settings["utk_sports"], enabled=False
            )
        }
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=disabled,
        ):
            disabled_state, _ = await _utk_document_guard(
                tab, runtime, build_refresh_identity(identity),
                _state["cancel_tasks"], config(), source,
            )
        self.assertEqual("unknown", disabled_state)
        self.assertEqual("123", runtime["document"]["last_document"])

    async def test_awaiting_reload_needs_a_healthy_new_document_token(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tracked = source + "&utm_source=test#fragment"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        request = {
            "request_token": "await-observation", "phase": "awaiting_document",
            "source_document_token": "100", "paused": False,
            "deadline_at": 0.0, "reason": "observe completion",
        }
        runtime["document"]["last_document"] = "100"
        runtime["refresh"]["reload_request"] = request
        snapshots = [
            {"classification": "normal", "document_id": "100", "url": tracked},
            {"classification": "unknown", "document_id": "", "url": tracked},
            {"classification": "unknown", "document_id": "101", "url": source},
            {"classification": "normal", "document_id": "101", "url": source},
        ]

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=snapshots),
        ):
            for _ in range(3):
                state, _ = await _utk_document_guard(
                    tab, runtime, build_refresh_identity(identity),
                    _state["cancel_tasks"], config(), source,
                )
                self.assertEqual("unknown", state)
                self.assertEqual("awaiting_document", request["phase"])
            state, _ = await _utk_document_guard(
                tab, runtime, build_refresh_identity(identity),
                _state["cancel_tasks"], config(), source,
            )
            self.assertEqual("normal", state)
            self.assertEqual("completed", request["phase"])
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "observe completion", apply_local_interval=False
            ))

        self.assertIsNone(runtime["refresh"]["reload_request"])
        self.assertTrue(nodriver_utk_sports_consume_reload_event(tab))
        self.assertFalse(nodriver_utk_sports_consume_reload_event(tab))

    async def test_different_reason_cannot_advance_unfinished_reload(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        inspect = AsyncMock()
        original_deadline = time.monotonic() + 60.0

        with patch(
            "platforms.utiki.refresh._inspect_utk_document", inspect
        ):
            for existing_reason in ("owner reason", ""):
                for phase in ("queued", "reserved", "awaiting_document"):
                    with self.subTest(
                        phase=phase, existing_reason=existing_reason
                    ):
                        request = {
                            "request_token": f"owner-{phase}",
                            "phase": phase,
                            "reason": existing_reason,
                            "deadline_at": original_deadline,
                        }
                        runtime["refresh"]["reload_request"] = request
                        self.assertFalse(await nodriver_utk_sports_reload(
                            tab, config(), "different reason",
                            apply_local_interval=False,
                            allow_unknown_document=True,
                            allow_blocked_recovery=True,
                            dispatch_deadline=original_deadline - 30.0,
                        ))
                        self.assertIs(
                            request, runtime["refresh"]["reload_request"]
                        )
                        self.assertEqual(
                            original_deadline, request["deadline_at"]
                        )

        inspect.assert_not_awaited()

    async def test_completed_reload_is_not_returned_to_different_reason(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        completed = {
            "request_token": "completed-owner",
            "phase": "completed",
            "reason": "HTTP 429 recovery probe",
            "paused": False,
            "deadline_at": 1.0,
        }
        runtime["refresh"]["reload_request"] = completed
        snapshot = {
            "classification": "normal", "document_id": "101", "url": source,
        }
        decision = types.SimpleNamespace(
            granted=False, status="queued", retry_after_seconds=0.25,
            cooldown_until=0.0, is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        new_deadline = time.monotonic() + 60.0

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "selected seats were taken",
                apply_local_interval=False,
                allow_blocked_recovery=True,
                dispatch_deadline=new_deadline,
            ))

        request = runtime["refresh"]["reload_request"]
        self.assertIsNot(request, completed)
        self.assertEqual("selected seats were taken", request["reason"])
        self.assertEqual(new_deadline, request["deadline_at"])
        self.assertTrue(request["allow_blocked_recovery"])
        coordinator.reserve_refresh.assert_awaited_once()

    async def test_missing_reason_completed_cannot_be_claimed(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        completed = {
            "request_token": "legacy-completed",
            "phase": "completed",
            "paused": False,
            "deadline_at": 0.0,
        }
        runtime["refresh"]["reload_request"] = completed
        snapshot = {
            "classification": "normal", "document_id": "101", "url": source,
        }
        decision = types.SimpleNamespace(
            granted=False, status="queued", retry_after_seconds=0.25,
            cooldown_until=0.0, is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "new reason", apply_local_interval=False,
            ))

        request = runtime["refresh"]["reload_request"]
        self.assertIsNot(completed, request)
        self.assertEqual("new reason", request["reason"])
        self.assertEqual("queued", request["phase"])
        coordinator.reserve_refresh.assert_awaited_once()

    async def test_guard_cannot_advance_a_replacement_reload_request(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        completed = {
            "request_token": "completed-a", "phase": "completed",
            "reason": "reason-a", "paused": False, "deadline_at": 0.0,
        }
        runtime["refresh"]["reload_request"] = completed
        replacement = {
            "request_token": "queued-c", "phase": "queued",
            "reason": "reason-c", "paused": False,
            "deadline_at": time.monotonic() + 60.0,
        }
        snapshot = {
            "classification": "normal", "document_id": "101", "url": source,
        }

        async def replace_during_guard(*_args, **_kwargs):
            runtime["refresh"]["reload_request"] = replacement
            return "normal", snapshot

        coordinator = types.SimpleNamespace(reserve_refresh=AsyncMock())
        with patch(
            "platforms.utiki.refresh._guard_for_reload",
            AsyncMock(side_effect=replace_during_guard),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "reason-b", apply_local_interval=False,
            ))

        self.assertIs(replacement, runtime["refresh"]["reload_request"])
        self.assertEqual("reason-c", replacement["reason"])
        coordinator.reserve_refresh.assert_not_awaited()

    async def test_document_evaluate_failure_does_not_use_url_as_token(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        tab.evaluate = AsyncMock(side_effect=RuntimeError("execution failed"))
        runtime = ctbc_identity_runtime(_ctbc_tab_identity(tab))

        snapshot = await _inspect_utk_document(tab, runtime, force=True)

        self.assertEqual("unknown", snapshot["classification"])
        self.assertEqual("", snapshot["document_id"])
        self.assertEqual(source, snapshot["url"])

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator), \
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

    async def test_completed_same_reason_cannot_fake_next_conflict_reload(self):
        tab = FakeSubmitTab(dialog="")
        source = str(tab.target.url)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["document"]["last_document"] = "100"
        runtime["refresh"]["reload_request"] = {
            "request_token": "old-conflict",
            "phase": "awaiting_document",
            "source_document_token": "100",
            "reason": "selected seats were taken",
            "paused": True,
            "deadline_at": 0.0,
        }
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "old-probe",
            "source_document_id": "100",
            "started_at": time.monotonic(),
            "next_renew_at": float("inf"),
        }
        submit_key = _ctbc_purchase_state_key(tab)
        conflict = {
            "kind": "seat_conflict", "source_url": source,
            "source_document_id": "100", "nonce": "a" * 32,
            "page_type": "seat", "dialog_dismissed": True,
            "clear_phase": "done", "clear_result": "committed",
            "reload_dispatched": False, "recovery_document_id": "",
        }
        runtime["purchase"]["outcomes"][submit_key] = conflict
        snapshot = {
            "classification": "normal", "document_id": "101", "url": source,
        }
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True, recovery_required=False, error="",
            )),
        )

        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.workflow._ctbc_attest_fast_destination",
            AsyncMock(return_value=True),
        ):
            await nodriver_ctbc_main(tab, source, config(), ocr=None)

        request = runtime["refresh"]["reload_request"]
        self.assertEqual("selected seats were taken", request["reason"])
        self.assertEqual("queued", request["phase"])
        self.assertEqual("101", request["source_document_token"])
        self.assertFalse(conflict["reload_dispatched"])
        self.assertIs(conflict, runtime["purchase"]["outcomes"][submit_key])
        coordinator.complete_recovery_probe.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(identity),
            "old-probe", success=True,
        )

    async def test_checkout_cleans_exact_refresh_work_and_keeps_runtime(self):
        checkout_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        tab = FakeSubmitTab(dialog="", url=checkout_url)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        runtime["document"]["last_document"] = "100"
        request = {
            "request_token": "checkout-request",
            "phase": "awaiting_document",
            "reason": "old refresh",
            "source_document_token": "100",
            "paused": False,
        }
        runtime["refresh"]["reload_request"] = request
        lease_task = asyncio.create_task(asyncio.Event().wait())
        _state["cancel_tasks"].add(lease_task)
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "checkout-probe",
            "source_document_id": "100",
            "started_at": time.monotonic(),
            "next_renew_at": float("inf"),
            "lease_task": lease_task,
        }
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True, recovery_required=True, error="",
            )),
        )
        cancel_facade = AsyncMock(wraps=nodriver_utk_sports_cancel_refresh)
        cfg = config()

        try:
            with patch(
                "platforms.utiki.workflow.check_and_handle_pause",
                AsyncMock(return_value=False),
            ), patch(
                "platforms.utiki.rate_limit.load_global_settings",
                return_value=self.global_settings,
            ), patch(
                "platforms.utiki.refresh._inspect_utk_document",
                AsyncMock(return_value={
                    "classification": "unverified",
                    "document_id": "101",
                    "url": checkout_url,
                }),
            ), patch(
                "platforms.utiki.workflow._reconcile_utiki_purchase",
                AsyncMock(return_value="success"),
            ), patch(
                "platforms.utiki.workflow.nodriver_utk_sports_cancel_refresh",
                cancel_facade,
            ), patch(
                "platforms.utiki.refresh.get_shared_refresh_coordinator",
                return_value=coordinator,
            ), patch(
                "platforms.utiki.submission.send_discord_notification",
            ) as discord, patch(
                "platforms.utiki.submission.send_telegram_notification",
            ) as telegram:
                await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
                await nodriver_ctbc_main(tab, checkout_url, cfg, ocr=None)
            self.assertTrue(lease_task.done())
            self.assertTrue(lease_task.cancelled())
            self.assertNotIn(lease_task, _state["cancel_tasks"])
        finally:
            if not lease_task.done():
                lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)

        self.assertIs(runtime, _state["tabs"][identity])
        self.assertIsNone(runtime["refresh"]["reload_request"])
        self.assertIsNone(runtime["refresh"]["recovery_probe"])
        self.assertNotIn(lease_task, _state["cancel_tasks"])
        self.assertEqual(1, len(runtime["purchase"]["checkout_notified"]))
        self.assertEqual(2, cancel_facade.await_count)
        for cancel_call in cancel_facade.await_args_list:
            self.assertEqual((tab, "checkout reached"), cancel_call.args)
            self.assertEqual(
                {"wait": True, "abandon_probe": True}, cancel_call.kwargs
            )
        coordinator.complete_recovery_probe.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(identity),
            "checkout-probe", success=False,
        )
        discord.assert_called_once_with(cfg, "order", "CTBC Sports")
        telegram.assert_called_once_with(cfg, "order", "CTBC Sports")

    async def test_probe_lease_cleanup_propagates_outer_cancellation(self):
        for cleanup_kind in ("completed", "abandon"):
            with self.subTest(cleanup_kind=cleanup_kind):
                tab = FakeSubmitTab(dialog="")
                runtime = ctbc_tab_runtime(tab)
                lease_started = asyncio.Event()
                lease_finalizing = asyncio.Event()
                initial_wait = asyncio.Event()
                finalize_release = asyncio.Event()

                async def lease_worker():
                    lease_started.set()
                    try:
                        await initial_wait.wait()
                    except asyncio.CancelledError:
                        lease_finalizing.set()
                        await finalize_release.wait()
                        raise

                lease_task = asyncio.create_task(lease_worker())
                _state["cancel_tasks"].add(lease_task)
                lease_task.add_done_callback(_state["cancel_tasks"].discard)
                cleanup_task = None
                try:
                    await asyncio.wait_for(lease_started.wait(), timeout=1.0)
                    probe = {
                        "permit_id": f"{cleanup_kind}-probe",
                        "lease_task": lease_task,
                    }
                    runtime["refresh"]["recovery_probe"] = probe
                    coordinator = types.SimpleNamespace(
                        complete_recovery_probe=AsyncMock(return_value=
                            types.SimpleNamespace(accepted=True, error="")
                        ),
                    )
                    if cleanup_kind == "completed":
                        cleanup = _clear_completed_recovery_probe(
                            runtime, _state["cancel_tasks"], probe
                        )
                    else:
                        cleanup = _abandon_recovery_probe(
                            runtime, "identity", _state["cancel_tasks"],
                            coordinator, probe,
                        )
                    cleanup_task = asyncio.create_task(cleanup)
                    await asyncio.wait_for(
                        lease_finalizing.wait(), timeout=1.0
                    )
                    expected_probe = probe
                    if cleanup_kind == "abandon":
                        expected_probe = {"permit_id": "replacement"}
                        runtime["refresh"]["recovery_probe"] = expected_probe
                    cleanup_task.cancel()
                    finalize_release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(cleanup_task, timeout=1.0)

                    self.assertIs(
                        expected_probe, runtime["refresh"]["recovery_probe"]
                    )
                    coordinator.complete_recovery_probe.assert_not_awaited()
                finally:
                    finalize_release.set()
                    cleanup_tasks = [lease_task]
                    if cleanup_task is not None:
                        cleanup_tasks.append(cleanup_task)
                    for task in cleanup_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.wait_for(asyncio.gather(
                        *cleanup_tasks, return_exceptions=True
                    ), timeout=1.0)
                    runtime["refresh"]["recovery_probe"] = None

    async def test_paused_workflow_uses_pause_facade_before_health_or_dispatch(self):
        tab = FakeSubmitTab(dialog="")
        pause = AsyncMock(return_value=True)
        health = AsyncMock(return_value="normal")
        dispatch = AsyncMock()
        with patch(
            "platforms.utiki.workflow.check_and_handle_pause",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_pause_refresh",
            pause,
        ), patch(
            "platforms.utiki.workflow.nodriver_utk_sports_health_check",
            health,
        ), patch(
            "platforms.utiki.workflow.nodriver_ctbc_seat_main", dispatch,
        ):
            self.assertIs(
                tab,
                await nodriver_ctbc_main(
                    tab, str(tab.target.url), config(), ocr=None
                ),
            )

        pause.assert_awaited_once_with(tab, "paused")
        health.assert_not_awaited()
        dispatch.assert_not_awaited()

    async def test_outer_router_pause_helper_only_targets_utiki(self):
        tab = FakeSubmitTab(dialog="")
        pause = AsyncMock(return_value=True)
        with patch(
            "nodriver_tixcraft.nodriver_utk_sports_pause_refresh", pause
        ):
            self.assertTrue(await _pause_utk_refresh(
                tab, str(tab.target.url), ""
            ))
            self.assertTrue(await _pause_utk_refresh(
                tab, "https://example.com/", str(tab.target.url)
            ))
            self.assertFalse(await _pause_utk_refresh(
                tab, "https://example.com/", "https://example.com/old"
            ))

        self.assertEqual(2, pause.await_count)
        pause.assert_awaited_with(tab, "paused")

    async def test_driver_missing_cleanup_uses_existing_runtime(self):
        tab = FakeSubmitTab(dialog="")
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)
        with patch(
            "platforms.utiki.workflow.refresh_engine.cancel_refresh",
            AsyncMock(return_value=False),
        ) as cancel:
            self.assertTrue(await _cleanup_utiki_if_driver_missing(None, tab))

        self.assertNotIn(identity, _state["tabs"])
        cancel.assert_awaited_once()

    async def test_outer_main_terminal_paths_cleanup_existing_runtime(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        cases = (
            ("explicit-stop", [], [True], 0),
            ("url-reader-quit", [("", True)], [False], 0),
            (
                "leave-utiki",
                [(source, False), ("https://example.com/", False)],
                [False, False],
                3,
            ),
        )
        for label, urls, quits, stop_after in cases:
            with self.subTest(label=label):
                _state["tabs"].clear()
                tab = FakeTab("", [], url=source)
                tab.target.target_id = f"outer-{label}"
                identity = _ctbc_tab_identity(tab)
                ctbc_identity_runtime(identity)

                driver, _handler = await self._run_outer_main_case(
                    tab,
                    current_urls=urls,
                    quit_results=quits,
                    stop_after=stop_after,
                )

                self.assertNotIn(identity, _state["tabs"])
                if label != "leave-utiki":
                    driver.stop.assert_awaited_once()

    async def test_silent_websocket_threshold_is_not_terminal_cleanup(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab("", [], url=source)
        tab.target.target_id = "outer-silent-websocket"
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)

        with patch("nodriver_tixcraft.get_url_error_count", return_value=15):
            driver, _handler = await self._run_outer_main_case(
                tab,
                current_urls=[("", False)],
                quit_results=[False],
                stop_after=2,
            )

        self.assertIs(runtime, _state["tabs"][identity])
        driver.stop.assert_not_awaited()

    async def test_outer_main_utiki_handler_exception_cleans_runtime(self):
        class HandlerFailed(Exception):
            pass

        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab("", [], url=source)
        tab.target.target_id = "outer-handler-failure"
        identity = _ctbc_tab_identity(tab)
        ctbc_identity_runtime(identity)
        handler = AsyncMock(side_effect=HandlerFailed)

        with self.assertRaises(HandlerFailed):
            await self._run_outer_main_case(
                tab,
                current_urls=[(source, False)],
                quit_results=[False],
                handler=handler,
            )

        self.assertNotIn(identity, _state["tabs"])
        handler.assert_awaited_once()

    async def test_outer_main_pauses_utiki_when_current_url_is_missing(self):
        class StopMainLoop(Exception):
            pass

        cfg = config()
        tab = FakeSubmitTab(dialog="")
        source = str(tab.target.url)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        driver = types.SimpleNamespace(
            main_tab=None,
            config=types.SimpleNamespace(port=0),
            stop=AsyncMock(),
        )
        args = types.SimpleNamespace(
            instance=None, input=None, mcp_debug=False,
        )
        sleep_calls = {"count": 0}

        async def bounded_sleep(_seconds):
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 5:
                raise StopMainLoop

        original_print = builtins.print
        with tempfile.TemporaryDirectory() as temp_root:
            try:
                builtins.print = lambda *_args, **_kwargs: None
                with patch(
                    "nodriver_tixcraft.get_config_dict", return_value=cfg,
                ), patch(
                    "nodriver_tixcraft.get_extension_config",
                    return_value=types.SimpleNamespace(),
                ), patch(
                    "nodriver_tixcraft.nodriver_overwrite_prefs",
                ), patch(
                    "nodriver_tixcraft.uc.start", AsyncMock(return_value=driver),
                ), patch(
                    "nodriver_tixcraft.nodriver_goto_homepage",
                    AsyncMock(return_value=tab),
                ), patch(
                    "nodriver_tixcraft.nodrver_block_urls",
                    AsyncMock(return_value=tab),
                ), patch(
                    "nodriver_tixcraft.nodriver_resize_window", AsyncMock(),
                ), patch(
                    "nodriver_tixcraft.reload_config",
                    AsyncMock(return_value=(cfg, 0)),
                ), patch(
                    "nodriver_tixcraft.nodriver_current_url",
                    AsyncMock(side_effect=[
                        (source, False), (None, False), ("", False),
                    ]),
                ), patch(
                    "nodriver_tixcraft.check_and_handle_quit",
                    AsyncMock(return_value=False),
                ), patch(
                    "nodriver_tixcraft.check_and_handle_pause",
                    AsyncMock(return_value=True),
                ), patch(
                    "nodriver_tixcraft._pause_utk_refresh",
                    AsyncMock(return_value=True),
                ) as pause, patch(
                    "nodriver_tixcraft.write_last_url_to_file",
                ), patch(
                    "nodriver_tixcraft.util.get_app_root",
                    return_value=temp_root,
                ), patch(
                    "nodriver_tixcraft.util.get_instance_state_path",
                    side_effect=lambda name: str(Path(temp_root, name)),
                ), patch(
                    "nodriver_tixcraft.os.path.exists", return_value=False,
                ), patch(
                    "nodriver_tixcraft.asyncio.sleep",
                    AsyncMock(side_effect=bounded_sleep),
                ):
                    with self.assertRaises(StopMainLoop):
                        await nodriver_main(args)
                self.assertEqual(
                    [
                        call(tab, source, source),
                        call(tab, "", source),
                        call(tab, "", source),
                    ],
                    pause.await_args_list,
                )
                self.assertIs(runtime, _state["tabs"][identity])
            finally:
                builtins.print = original_print

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
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
        destination = dict(snapshot, document_id=source + "|101")
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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(
                        side_effect=[snapshot, snapshot, destination]
                    )), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "scheduled refresh",
                    apply_local_interval=False,
                )
                self.assertFalse(reloaded)
                request = runtime["refresh"]["reload_request"]
                self.assertEqual("awaiting_document", request["phase"])
                self.assertEqual("", request["reservation_id"])
                self.assertFalse(request["shared_enqueued"])
                self.assertFalse(nodriver_utk_sports_consume_reload_event(tab))
                self.assertTrue(await nodriver_utk_sports_reload(
                    tab, config(), "scheduled refresh",
                    apply_local_interval=False,
                ))
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

    async def test_reload_exception_stays_awaiting_until_new_document(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        initial = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        destination = dict(initial, document_id="101")
        tab.reload = AsyncMock(side_effect=RuntimeError("CDP result unavailable"))
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="reload-unknown", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=[initial, initial, initial, destination]),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "ambiguous reload", apply_local_interval=False
            ))
            request = runtime["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "ambiguous reload", apply_local_interval=False
            ))
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "ambiguous reload", apply_local_interval=False
            ))

        tab.reload.assert_awaited_once()
        coordinator.reserve_refresh.assert_awaited_once()
        coordinator.commit_refresh.assert_awaited_once()

    async def test_cancel_after_browser_reload_starts_is_sticky_awaiting(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        current = {"document_id": "100"}
        reload_started = asyncio.Event()

        async def inspect(*_args, **_kwargs):
            return {
                "classification": "normal",
                "document_id": current["document_id"], "url": source,
            }

        async def pending_reload():
            reload_started.set()
            await asyncio.Event().wait()

        tab.reload = AsyncMock(side_effect=pending_reload)
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="cancelled-browser", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=inspect),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "cancelled browser", apply_local_interval=False
            ))
            await asyncio.wait_for(reload_started.wait(), timeout=1.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            request = runtime["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "cancelled browser", apply_local_interval=False
            ))
            current["document_id"] = "101"
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "cancelled browser", apply_local_interval=False
            ))

        tab.reload.assert_awaited_once()
        coordinator.commit_refresh.assert_awaited_once()

    async def test_commit_error_is_sticky_and_never_dispatches_browser_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        coordinator = self.coordinator_for(types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="unused", is_recovery_probe=False, error="",
        ))
        coordinator.commit_refresh = AsyncMock(
            side_effect=RuntimeError("commit response lost")
        )

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "commit unknown", apply_local_interval=False
            ))
            request = runtime["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "commit unknown", apply_local_interval=False
            ))

        self.assertFalse(tab.reloaded)
        coordinator.commit_refresh.assert_awaited_once()

    async def test_already_committed_waits_for_document_without_redispatch(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        initial = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        destination = dict(initial, document_id="101")
        decision = types.SimpleNamespace(
            granted=False, status="already_committed", retry_after_seconds=0.0,
            permit_id="", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        coordinator.reserve_refresh.return_value = self.reservation("already")

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=[initial, initial, initial, destination]),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "already committed", apply_local_interval=False
            ))
            request = runtime["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "already committed", apply_local_interval=False
            ))
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "already committed", apply_local_interval=False
            ))

        self.assertFalse(tab.reloaded)
        coordinator.commit_refresh.assert_awaited_once()

    async def test_late_commit_replay_cannot_reopen_completed_request(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        reservation = self.reservation("late-replay", probe=True)
        for phase, status, error in (
            ("awaiting_document", "already_committed", ""),
            ("completed", "already_committed", ""),
            ("completed", "failed", "coordinator unavailable"),
        ):
            with self.subTest(phase=phase, status=status):
                completed_token = "101" if phase == "completed" else ""
                request = {
                    "request_token": f"late-{phase}-{status}", "phase": phase,
                    "source_document_token": "100",
                    "completed_document_token": completed_token,
                    "dispatched_at": 55.0, "paused": True,
                    "reservation_id": "", "shared_enqueued": False,
                }
                runtime["refresh"].update({
                    "reload_request": request, "recovery_probe": None,
                })
                decision = types.SimpleNamespace(
                    granted=False, status=status, retry_after_seconds=0.0,
                    permit_id="late-probe", is_recovery_probe=True, error=error,
                )
                coordinator = types.SimpleNamespace(
                    commit_refresh=AsyncMock(return_value=decision),
                    release_refresh=AsyncMock(return_value=False),
                )

                self.assertFalse(await _commit_reserved_reload(
                    tab, runtime, "identity", _state["cancel_tasks"],
                    coordinator, reservation, request,
                    {"document_id": "100"},
                    types.SimpleNamespace(log=lambda *_args: None),
                ))

                self.assertEqual(phase, request["phase"])
                self.assertEqual(
                    completed_token, request["completed_document_token"]
                )
                self.assertEqual(55.0, request["dispatched_at"])
                self.assertTrue(request["paused"])
                self.assertIsNone(runtime["refresh"]["recovery_probe"])

    async def test_commit_exception_respects_exact_pause_release_result(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        reservation = self.reservation("pause-race")
        for released in (True, False):
            with self.subTest(released=released):
                commit_started = asyncio.Event()
                finish_commit = asyncio.Event()
                deadline = time.monotonic() + 30.0
                request = {
                    "request_token": f"pause-race-{released}",
                    "phase": "reserved", "document_id": "100",
                    "source_document_token": "100",
                    "expected_url": str(tab.target.url),
                    "eligible_at": 0.0, "deadline_at": deadline,
                    "next_poll_at": 0.0, "paused": False,
                    "reservation_id": reservation.reservation_id,
                    "shared_enqueued": True,
                    "pause_cleanup_done": False,
                    "pause_cleanup_retry_at": 0.0,
                }
                runtime["refresh"].update({
                    "reload_request": request, "recovery_probe": None,
                })

                async def fail_commit(*_args, **_kwargs):
                    commit_started.set()
                    await asyncio.wait_for(finish_commit.wait(), timeout=1.0)
                    raise RuntimeError("commit outcome unavailable")

                coordinator = types.SimpleNamespace(
                    commit_refresh=AsyncMock(side_effect=fail_commit),
                    release_refresh=AsyncMock(return_value=released),
                    cancel_request=AsyncMock(return_value=False),
                )
                with patch(
                    "platforms.utiki.refresh.get_shared_refresh_coordinator",
                    return_value=coordinator,
                ):
                    task = asyncio.create_task(_commit_reserved_reload(
                        tab, runtime, "identity", _state["cancel_tasks"],
                        coordinator, reservation, request,
                        {"document_id": "100"},
                        types.SimpleNamespace(log=lambda *_args: None),
                    ))
                    await asyncio.wait_for(commit_started.wait(), timeout=1.0)
                    self.assertEqual(
                        released, await nodriver_utk_sports_pause_refresh(tab)
                    )
                    finish_commit.set()
                    self.assertFalse(await task)

                self.assertEqual(f"pause-race-{released}", request["request_token"])
                self.assertEqual(deadline, request["deadline_at"])
                self.assertTrue(request["paused"])
                expected_phase = "queued" if released else "awaiting_document"
                self.assertEqual(expected_phase, request["phase"])
                if released:
                    self.assertTrue(request["pause_cleanup_done"])
                else:
                    self.assertFalse(request["pause_cleanup_done"])
                    with patch(
                        "platforms.utiki.refresh._utk_document_guard",
                        AsyncMock(return_value=("unknown", {
                            "classification": "unknown",
                            "document_id": "100", "url": str(tab.target.url),
                        })),
                    ):
                        self.assertFalse(await nodriver_utk_sports_reload(
                            tab, config(), "pause race",
                            apply_local_interval=False,
                        ))
                coordinator.commit_refresh.assert_awaited_once()
                self.assertEqual(0, tab.reload_count)

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(side_effect=inspect)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(await nodriver_utk_sports_reload(
                    tab, config(), "ordered reload", apply_local_interval=False
                ))
            self.assertEqual(["reserve", "preflight", "commit", "reload"], events)
            self.assertEqual(
                "awaiting_document",
                runtime["refresh"]["reload_request"]["phase"],
            )
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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._utk_document_guard", AsyncMock(
                        return_value=("normal", snapshot))), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(
                        return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator",
                          return_value=coordinator), \
                    patch(
                "platforms.utiki.refresh.time",
                types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
            ):
                self.assertFalse(await reload_once())
                request = runtime["refresh"]["reload_request"]
                self.assertEqual(107.25, request["eligible_at"])
                clock["now"] = request["eligible_at"] - 0.001
                self.assertFalse(await reload_once())
                clock["now"] = request["eligible_at"]
                self.assertFalse(await reload_once())

            self.assertEqual(1, tab.reload_count)
            self.assertEqual("awaiting_document", request["phase"])
            self.assertEqual(1, coordinator.reserve_refresh.await_count)
            self.assertEqual(1, coordinator.commit_refresh.await_count)
        finally:
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["recovery_probe"] = None
            runtime["refresh"]["reload_event"] = False

    async def test_queued_pause_resume_preserves_intent_and_dispatches_once(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["advanced"]["auto_reload_page_interval"] = 5.0
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        snapshot = {
            "classification": "normal", "document_id": "100",
            "url": str(tab.target.url),
        }
        clock = {"now": 100.0}
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="resume-permit", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        queued = types.SimpleNamespace(
            reserved=False, status="queued", retry_after_seconds=0.1,
            reservation_id="", is_recovery_probe=False,
            cooldown_until=0.0, error="",
        )
        reserved = coordinator.reserve_refresh.return_value
        coordinator.reserve_refresh.side_effect = [queued, reserved]
        coordinator.cancel_request.return_value = True

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=200.0
            ))
            request = runtime["refresh"]["reload_request"]
            token = request["request_token"]
            self.assertEqual("queued", request["phase"])
            self.assertEqual("100", request["source_document_token"])
            self.assertEqual(105.0, request["eligible_at"])
            self.assertEqual(200.0, request["deadline_at"])

            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=190.0
            ))
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual(190.0, request["deadline_at"])
            clock["now"] = 104.9
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=300.0
            ))
            coordinator.reserve_refresh.assert_not_awaited()
            clock["now"] = 105.0
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=300.0
            ))
            self.assertTrue(request["shared_enqueued"])
            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual(token, request["request_token"])
            self.assertEqual(105.0, request["eligible_at"])
            self.assertEqual("queued", request["phase"])
            self.assertTrue(request["paused"])
            coordinator.cancel_request.assert_awaited_once_with(
                "utk-sports", build_refresh_identity(identity),
                request_token=token,
            )
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            coordinator.cancel_request.assert_awaited_once()

            clock["now"] = 105.09
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=300.0
            ))
            self.assertFalse(request["paused"])
            self.assertEqual(190.0, request["deadline_at"])
            clock["now"] = 105.1
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "paced recovery", dispatch_deadline=300.0
            ))

        self.assertEqual(2, coordinator.reserve_refresh.await_count)
        self.assertEqual(1, coordinator.commit_refresh.await_count)
        self.assertEqual(1, tab.reload_count)
        self.assertEqual("awaiting_document", request["phase"])

    async def test_reserved_pause_releases_shared_work_but_keeps_intent(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_identity_runtime(_ctbc_tab_identity(tab))
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="unused", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        preflight_started = asyncio.Event()
        finish_preflight = asyncio.Event()

        async def inspect(*_args, **_kwargs):
            preflight_started.set()
            await asyncio.wait_for(finish_preflight.wait(), timeout=1.0)
            return snapshot

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=inspect),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            reload_task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "reserved pause", apply_local_interval=False
            ))
            await asyncio.wait_for(preflight_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            token = request["request_token"]
            deadline = request["deadline_at"]
            reservation_id = request["reservation_id"]
            self.assertEqual("reserved", request["phase"])

            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual("queued", request["phase"])
            self.assertEqual("", request["reservation_id"])
            self.assertTrue(request["paused"])
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            coordinator.release_refresh.assert_awaited_once()
            finish_preflight.set()
            self.assertFalse(await reload_task)
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual(token, request["request_token"])
            self.assertEqual(deadline, request["deadline_at"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "reserved pause", apply_local_interval=False
            ))

        coordinator.release_refresh.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            reservation_id, request_token=token, drop_request=True,
        )
        coordinator.commit_refresh.assert_awaited_once()
        self.assertTrue(tab.reloaded)
        self.assertEqual("awaiting_document", request["phase"])

    async def test_pause_during_normal_commit_preserves_intent_until_resume(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        commit_started = asyncio.Event()
        finish_commit = asyncio.Event()
        granted = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="normal-permit", is_recovery_probe=False, error="",
        )

        async def commit(*_args, **_kwargs):
            if not commit_started.is_set():
                commit_started.set()
                await asyncio.wait_for(finish_commit.wait(), timeout=1.0)
            return granted

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(side_effect=[
                self.reservation("reservation-first"),
                self.reservation("reservation-resumed"),
            ]),
            commit_refresh=AsyncMock(side_effect=commit),
            release_refresh=AsyncMock(return_value=False),
            cancel_request=AsyncMock(return_value=False),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "commit pause", apply_local_interval=False
            ))
            await asyncio.wait_for(commit_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            token = request["request_token"]
            deadline = request["deadline_at"]
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            finish_commit.set()
            self.assertFalse(await task)
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertTrue(request["paused"])
            self.assertTrue(request["pause_cleanup_done"])
            self.assertFalse(tab.reloaded)

            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "commit pause", apply_local_interval=False
            ))

        self.assertEqual(token, request["request_token"])
        self.assertEqual(deadline, request["deadline_at"])
        self.assertEqual(2, coordinator.reserve_refresh.await_count)
        self.assertEqual(2, coordinator.commit_refresh.await_count)
        self.assertTrue(tab.reloaded)
        self.assertEqual("awaiting_document", request["phase"])

    async def test_awaiting_pause_observes_then_consumes_only_after_resume(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        source = str(tab.target.url)
        request = {
            "request_token": "paused-awaiting", "phase": "awaiting_document",
            "source_document_token": "100", "paused": False,
            "deadline_at": 0.0, "reason": "paused observation",
        }
        runtime["document"]["last_document"] = "100"
        runtime["refresh"]["reload_request"] = request
        destination = {
            "classification": "normal", "document_id": "101", "url": source,
        }

        with patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=types.SimpleNamespace(),
        ):
            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=destination),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(side_effect=[True, False]),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "paused observation", apply_local_interval=False
            ))
            self.assertEqual("completed", request["phase"])
            self.assertTrue(request["paused"])
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "paused observation", apply_local_interval=False
            ))

        self.assertIsNone(runtime["refresh"]["reload_request"])

    async def test_completed_probe_is_not_consumed_before_probe_completion(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        request = {
            "request_token": "probe-awaiting-completion", "phase": "completed",
            "source_document_token": "100", "completed_document_token": "101",
            "paused": False, "deadline_at": 0.0,
            "reason": "probe completion",
        }
        runtime["refresh"]["reload_request"] = request
        runtime["refresh"]["recovery_probe"] = {"permit_id": "probe"}
        guard = AsyncMock(return_value=("normal", {
            "classification": "normal", "document_id": "101",
            "url": str(tab.target.url),
        }))

        with patch("platforms.utiki.refresh._utk_document_guard", guard):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "probe completion", apply_local_interval=False
            ))
            self.assertIs(request, runtime["refresh"]["reload_request"])
            runtime["refresh"]["recovery_probe"] = None
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "probe completion", apply_local_interval=False
            ))

        guard.assert_awaited_once()

    async def test_paused_probe_commit_retries_cleanup_before_resume(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        commit_started = asyncio.Event()
        finish_commit = asyncio.Event()
        probe_decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="probe-permit", is_recovery_probe=True, error="",
        )
        normal_decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="normal-permit", is_recovery_probe=False, error="",
        )
        commit_calls = {"count": 0}
        clock = {"now": 100.0}

        async def commit(*_args, **_kwargs):
            commit_calls["count"] += 1
            if commit_calls["count"] == 1:
                commit_started.set()
                await asyncio.wait_for(finish_commit.wait(), timeout=1.0)
                return probe_decision
            return normal_decision

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(side_effect=[
                self.reservation("probe-reservation", probe=True),
                self.reservation("normal-reservation"),
            ]),
            commit_refresh=AsyncMock(side_effect=commit),
            release_refresh=AsyncMock(return_value=False),
            cancel_request=AsyncMock(return_value=False),
            complete_recovery_probe=AsyncMock(side_effect=[
                types.SimpleNamespace(
                    accepted=False, recovery_required=True,
                    error="temporarily unavailable",
                ),
                types.SimpleNamespace(
                    accepted=True, recovery_required=True, error="",
                ),
            ]),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "probe commit pause", apply_local_interval=False
            ))
            await asyncio.wait_for(commit_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            finish_commit.set()
            self.assertFalse(await task)
            probe = runtime["refresh"]["recovery_probe"]
            self.assertEqual("probe-permit", probe["permit_id"])
            self.assertTrue(probe["abandon_requested"])
            self.assertNotIn("lease_task", probe)

            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            self.assertEqual(1, coordinator.complete_recovery_probe.await_count)
            self.assertEqual(
                "unknown",
                await nodriver_utk_sports_health_check(tab, source, config()),
            )
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "probe commit pause",
                apply_local_interval=False,
            ))
            self.assertEqual(1, coordinator.reserve_refresh.await_count)
            self.assertEqual(1, coordinator.commit_refresh.await_count)
            self.assertFalse(tab.reloaded)
            self.assertIs(request, runtime["refresh"]["reload_request"])

            clock["now"] = 100.5
            self.assertEqual(
                "normal",
                await nodriver_utk_sports_health_check(tab, source, config()),
            )
            self.assertIsNone(runtime["refresh"]["recovery_probe"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "probe commit pause",
                apply_local_interval=False,
            ))

        self.assertEqual(
            "awaiting_document",
            runtime["refresh"]["reload_request"]["phase"],
        )
        self.assertEqual(2, coordinator.complete_recovery_probe.await_count)
        for call in coordinator.complete_recovery_probe.await_args_list:
            self.assertEqual("probe-permit", call.args[2])
            self.assertFalse(call.kwargs["success"])
        self.assertEqual(2, coordinator.reserve_refresh.await_count)
        self.assertEqual(2, coordinator.commit_refresh.await_count)
        self.assertTrue(tab.reloaded)

    async def test_pause_release_before_reservation_missing_keeps_request(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        commit_started = asyncio.Event()
        finish_commit = asyncio.Event()
        missing = types.SimpleNamespace(
            granted=False, status="reservation_missing",
            retry_after_seconds=0.1, permit_id="",
            is_recovery_probe=False, error="",
        )

        async def commit(*_args, **_kwargs):
            commit_started.set()
            await asyncio.wait_for(finish_commit.wait(), timeout=1.0)
            return missing

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=self.reservation("missing")),
            commit_refresh=AsyncMock(side_effect=commit),
            release_refresh=AsyncMock(return_value=True),
            cancel_request=AsyncMock(return_value=False),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "missing ordering", apply_local_interval=False
            ))
            await asyncio.wait_for(commit_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
            finish_commit.set()
            self.assertFalse(await task)

        self.assertIs(request, runtime["refresh"]["reload_request"])
        self.assertTrue(request["paused"])
        coordinator.release_refresh.assert_awaited_once()
        self.assertFalse(tab.reloaded)

    async def test_pause_during_rate_limit_report_does_not_pop_request(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        initial = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        blocked = dict(initial, classification="blocked")
        report_started = asyncio.Event()
        finish_report = asyncio.Event()

        async def report(*_args, **_kwargs):
            report_started.set()
            await asyncio.wait_for(finish_report.wait(), timeout=1.0)
            return types.SimpleNamespace(
                accepted=True, new_incident=True, consecutive_incidents=1,
                retry_after_seconds=60.0, error="",
            )

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=self.reservation("rate-limit")),
            commit_refresh=AsyncMock(),
            release_refresh=AsyncMock(return_value=True),
            cancel_request=AsyncMock(return_value=False),
            report_rate_limited=AsyncMock(side_effect=report),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", initial)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=blocked),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "report pause", apply_local_interval=False,
                allow_blocked_recovery=True,
            ))
            await asyncio.wait_for(report_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
            finish_report.set()
            self.assertFalse(await task)

        self.assertIs(request, runtime["refresh"]["reload_request"])
        self.assertTrue(request["paused"])
        coordinator.commit_refresh.assert_not_awaited()

    async def test_pause_while_release_is_awaited_does_not_pop_request(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        initial = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        stale = dict(initial, document_id="different")
        release_started = asyncio.Event()
        finish_release = asyncio.Event()
        release_calls = {"count": 0}

        async def release(*_args, **_kwargs):
            release_calls["count"] += 1
            if release_calls["count"] == 1:
                release_started.set()
                await asyncio.wait_for(finish_release.wait(), timeout=1.0)
            return True

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=self.reservation("release-race")),
            release_refresh=AsyncMock(side_effect=release),
            cancel_request=AsyncMock(return_value=False),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", initial)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=stale),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "release pause", apply_local_interval=False
            ))
            await asyncio.wait_for(release_started.wait(), timeout=1.0)
            request = runtime["refresh"]["reload_request"]
            self.assertTrue(await nodriver_utk_sports_pause_refresh(tab))
            finish_release.set()
            self.assertFalse(await task)

        self.assertIs(request, runtime["refresh"]["reload_request"])
        self.assertTrue(request["paused"])
        self.assertEqual(2, coordinator.release_refresh.await_count)
        self.assertFalse(tab.reloaded)

    async def test_temporary_pause_cleanup_failure_keeps_local_intent(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100",
            "url": str(tab.target.url),
        }
        clock = {"now": 100.0}
        coordinator = types.SimpleNamespace(
            cancel_request=AsyncMock(return_value=False),
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=False, status="queued", retry_after_seconds=0.25,
                reservation_id="", is_recovery_probe=False,
                cooldown_until=0.0, error="",
            )),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "temporary pause failure",
                apply_local_interval=False,
            ))
            request = runtime["refresh"]["reload_request"]
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            coordinator.cancel_request.assert_awaited_once()
            clock["now"] = 100.5
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))

        self.assertIs(request, runtime["refresh"]["reload_request"])
        self.assertTrue(request["paused"])
        self.assertEqual("queued", request["phase"])
        self.assertEqual(2, coordinator.cancel_request.await_count)

    async def test_expired_queued_deadline_is_terminal(self):
        tab = FakeSubmitTab(dialog="")
        cfg = config()
        cfg["advanced"]["auto_reload_page_interval"] = 10.0
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100",
            "url": str(tab.target.url),
        }
        clock = {"now": 100.0}
        coordinator = types.SimpleNamespace(
            cancel_request=AsyncMock(return_value=True),
            reserve_refresh=AsyncMock(),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "deadline", dispatch_deadline=105.0
            ))
            token = runtime["refresh"]["reload_request"]["request_token"]
            clock["now"] = 105.0
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, cfg, "deadline", dispatch_deadline=200.0
            ))

        self.assertIsNone(runtime["refresh"]["reload_request"])
        coordinator.cancel_request.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            request_token=token,
        )
        coordinator.reserve_refresh.assert_not_awaited()

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
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
        replacement = {"request_token": "replacement-preflight"}
        old_tokens = []

        async def cancel_preflight(*_args, **_kwargs):
            old_tokens.append(
                runtime["refresh"]["reload_request"]["request_token"]
            )
            runtime["refresh"]["reload_request"] = replacement
            raise asyncio.CancelledError

        try:
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._utk_document_guard", AsyncMock(return_value=("normal", snapshot))), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(side_effect=cancel_preflight)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
                with self.assertRaises(asyncio.CancelledError):
                    await nodriver_utk_sports_reload(
                        tab, config(), "cancelled preflight", apply_local_interval=False
                    )
            coordinator.release_refresh.assert_awaited_once_with(
                "utk-sports", build_refresh_identity(identity),
                "reservation-cancelled",
                request_token=old_tokens[0], drop_request=True,
            )
            self.assertFalse(tab.reloaded)
            self.assertIs(replacement, runtime["refresh"]["reload_request"])
        finally:
            runtime["refresh"]["reload_request"] = None

    async def test_cancelled_reserve_collects_and_releases_late_result(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        reserve_started = asyncio.Event()
        finish_reserve = asyncio.Event()
        reservation = types.SimpleNamespace(
            reserved=True, status="reserved", retry_after_seconds=0.0,
            reservation_id="reservation-late", is_recovery_probe=False,
            cooldown_until=0.0, error="",
        )

        async def reserve(*_args, **_kwargs):
            reserve_started.set()
            await asyncio.wait_for(finish_reserve.wait(), timeout=1.0)
            return reservation

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(side_effect=reserve),
            release_refresh=AsyncMock(return_value=True),
            cancel_request=AsyncMock(return_value=True),
        )
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "cancel reserve", apply_local_interval=False
            ))
            await asyncio.wait_for(reserve_started.wait(), timeout=1.0)
            old_request = runtime["refresh"]["reload_request"]
            replacement = {"request_token": "replacement-reserve"}
            runtime["refresh"]["reload_request"] = replacement
            task.cancel()
            finish_reserve.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        coordinator.release_refresh.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            "reservation-late",
            request_token=old_request["request_token"], drop_request=True,
        )
        self.assertIs(replacement, runtime["refresh"]["reload_request"])

    async def test_cancelled_pause_check_releases_without_popping_replacement(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="unused", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        replacement = {"request_token": "replacement-pause-check"}
        old_tokens = []

        async def cancel_pause_check(_config):
            old_tokens.append(
                runtime["refresh"]["reload_request"]["request_token"]
            )
            runtime["refresh"]["reload_request"] = replacement
            raise asyncio.CancelledError

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(side_effect=cancel_pause_check),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await nodriver_utk_sports_reload(
                    tab, config(), "cancel pause check",
                    apply_local_interval=False,
                )

        coordinator.release_refresh.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            "reservation-1", request_token=old_tokens[0], drop_request=True,
        )
        coordinator.commit_refresh.assert_not_awaited()
        self.assertIs(replacement, runtime["refresh"]["reload_request"])

    def test_old_coroutine_cannot_pop_a_new_local_reload_request(self):
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [])
        identity = _ctbc_tab_identity(tab)
        refresh = ctbc_identity_runtime(identity)["refresh"]
        refresh["reload_request"] = {"request_token": "new-token"}
        try:
            self.assertIsNone(
                _pop_reload_request_if_token(
                    ctbc_identity_runtime(identity), "old-token"
                )
            )
            self.assertEqual(
                "new-token",
                refresh["reload_request"]["request_token"],
            )
        finally:
            refresh["reload_request"] = None

    async def test_old_reload_completion_cannot_mutate_replacement_request(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        snapshot = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        reload_started = asyncio.Event()
        finish_reload = asyncio.Event()

        async def delayed_reload():
            reload_started.set()
            await asyncio.wait_for(finish_reload.wait(), timeout=1.0)

        tab.reload = delayed_reload
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="old-coroutine", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)
        replacement = {"request_token": "replacement", "phase": "queued"}

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(return_value=("normal", snapshot)),
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            task = asyncio.create_task(nodriver_utk_sports_reload(
                tab, config(), "old coroutine", apply_local_interval=False
            ))
            await asyncio.wait_for(reload_started.wait(), timeout=1.0)
            runtime["refresh"]["reload_request"] = replacement
            finish_reload.set()
            self.assertFalse(await task)

        self.assertIs(replacement, runtime["refresh"]["reload_request"])

    async def test_reload_wait_preserves_url_drift_until_new_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tracked = source + "&utm_source=test#fragment"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        current = {"document_id": "100"}
        command_returned = asyncio.Event()
        real_sleep = asyncio.sleep

        async def inspect(*_args, **_kwargs):
            return {
                "classification": "normal",
                "document_id": current["document_id"],
                "url": source,
            }

        async def browser_reload():
            tab.reload_count += 1
            tab.target.url = tracked
            command_returned.set()

        async def yield_once(_seconds):
            await real_sleep(0)

        tab.reload_count = 0
        tab.reload = browser_reload
        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="wait-production", is_recovery_probe=False, error="",
        )
        coordinator = self.coordinator_for(decision)

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=inspect),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.asyncio.sleep", side_effect=yield_once,
        ):
            waiter = asyncio.create_task(nodriver_utk_sports_reload_wait(
                tab, config(), "production waiter"
            ))
            await asyncio.wait_for(command_returned.wait(), timeout=1.0)
            await real_sleep(0)
            self.assertFalse(waiter.done())
            request = ctbc_tab_runtime(tab)["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertEqual("100", request["source_document_token"])
            current["document_id"] = "101"
            self.assertTrue(await asyncio.wait_for(waiter, timeout=1.0))

        self.assertEqual(1, tab.reload_count)
        coordinator.commit_refresh.assert_awaited_once()

    async def test_reload_wait_has_a_bounded_fail_closed_deadline(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        inner_reload = AsyncMock(return_value=True)
        with patch("platforms.utiki.refresh.UTK_RELOAD_WAIT_MAX_SECONDS", 0.0), \
                patch("platforms.utiki.refresh.time.monotonic", return_value=100.0), \
                patch("platforms.utiki.refresh.reload", inner_reload), \
                patch("platforms.utiki.refresh.cancel_refresh", AsyncMock(return_value=True)) as cancel, \
                patch("platforms.utiki.refresh.check_and_handle_pause", AsyncMock(return_value=False)), \
                patch("platforms.utiki.refresh.check_and_handle_quit", AsyncMock(return_value=False)):
            self.assertFalse(await nodriver_utk_sports_reload_wait(
                tab, config(), "bounded startup reload"
            ))
        cancel.assert_awaited_once()
        inner_reload.assert_not_awaited()

    async def test_reload_wait_pause_resumes_same_intent_and_dispatches_once(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        request = {
            "request_token": "wait-resume", "paused": False,
            "deadline_at": 130.0,
        }
        runtime["refresh"]["reload_request"] = request
        reload_calls = {"count": 0}

        async def reload_once(*_args, **kwargs):
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual(130.0, kwargs["dispatch_deadline"])
            reload_calls["count"] += 1
            if reload_calls["count"] == 1:
                return False
            request["paused"] = False
            return True

        async def pause_once(*_args, **_kwargs):
            self.assertIs(request, runtime["refresh"]["reload_request"])
            request["paused"] = True
            return True

        with patch(
            "platforms.utiki.refresh.UTK_RELOAD_WAIT_MAX_SECONDS", 30.0,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: 100.0, time=time.time),
        ), patch(
            "platforms.utiki.refresh.reload", AsyncMock(side_effect=reload_once),
        ) as reload_mock, patch(
            "platforms.utiki.refresh.pause_refresh",
            AsyncMock(side_effect=pause_once),
        ) as pause, patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(side_effect=[True]),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.asyncio.sleep", AsyncMock(),
        ):
            self.assertTrue(await nodriver_utk_sports_reload_wait(
                tab, config(), "paused waiter"
            ))

        self.assertEqual("wait-resume", request["request_token"])
        self.assertEqual(2, reload_mock.await_count)
        pause.assert_awaited_once()

    async def test_reload_wait_pause_keeps_absolute_deadline(self):
        tab = FakeSubmitTab(dialog="")
        clock = {"now": 100.0}

        async def advance(_seconds):
            clock["now"] = 131.0

        reload_mock = AsyncMock(return_value=False)
        cancel = AsyncMock(return_value=True)
        with patch(
            "platforms.utiki.refresh.UTK_RELOAD_WAIT_MAX_SECONDS", 30.0,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.refresh.reload", reload_mock,
        ), patch(
            "platforms.utiki.refresh.pause_refresh", AsyncMock(return_value=True),
        ) as pause, patch(
            "platforms.utiki.refresh.cancel_refresh", cancel,
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=True),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.asyncio.sleep",
            AsyncMock(side_effect=advance),
        ):
            self.assertFalse(await nodriver_utk_sports_reload_wait(
                tab, config(), "paused deadline"
            ))

        self.assertEqual(1, reload_mock.await_count)
        self.assertEqual(130.0, reload_mock.await_args.kwargs["dispatch_deadline"])
        pause.assert_awaited_once()
        cancel.assert_awaited_once()

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
                reloaded = await nodriver_utk_sports_reload(
                    tab,
                    config(),
                    "post-recovery follower",
                    apply_local_interval=False,
                    allow_blocked_recovery=True,
                )
                self.assertFalse(reloaded)
                self.assertEqual(
                    "awaiting_document",
                    runtime["refresh"]["reload_request"]["phase"],
                )
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
        old_request = {
            "request_token": "old-probe-request",
            "phase": "awaiting_document",
            "source_document_token": source + "|old",
            "reason": "HTTP 429 recovery probe",
            "paused": False,
            "deadline_at": 0.0,
        }
        runtime["refresh"]["reload_request"] = old_request
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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator):
                await nodriver_ctbc_main(tab, source, config(), ocr=None)
            self.assertEqual(
                "probe-1",
                coordinator.report_rate_limited.await_args.kwargs["probe_permit_id"],
            )
            self.assertIsNone(runtime["refresh"]["recovery_probe"])
            request = runtime["refresh"]["reload_request"]
            self.assertIsNot(old_request, request)
            self.assertEqual("queued", request["phase"])
            self.assertEqual("HTTP 429 recovery probe", request["reason"])
            self.assertEqual(source + "|new", request["source_document_token"])
        finally:
            runtime["refresh"]["rate_limit_observation"] = None
            runtime["refresh"]["reload_request"] = None
            runtime["refresh"]["recovery_probe"] = None

    async def test_recovery_probe_completes_only_after_production_observes_new_document(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        current = {"document_id": "100"}

        async def inspect(*_args, **_kwargs):
            return {
                "classification": "normal",
                "document_id": current["document_id"], "url": source,
            }

        decision = types.SimpleNamespace(
            granted=True, status="granted", retry_after_seconds=0.0,
            permit_id="real-probe", is_recovery_probe=True, error="",
        )
        coordinator = self.coordinator_for(decision)
        coordinator.complete_recovery_probe = AsyncMock(return_value=
            types.SimpleNamespace(accepted=True, recovery_required=False, error="")
        )

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=inspect),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "real recovery probe", apply_local_interval=False
            ))
            self.assertEqual(
                "real-probe", runtime["refresh"]["recovery_probe"]["permit_id"]
            )
            coordinator.complete_recovery_probe.assert_not_awaited()
            current["document_id"] = "101"
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "real recovery probe", apply_local_interval=False
            ))

        coordinator.complete_recovery_probe.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            "real-probe", success=True,
        )
        self.assertIsNone(runtime["refresh"]["recovery_probe"])
        self.assertIsNone(runtime["refresh"]["reload_request"])

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
            with patch("platforms.utiki.refresh.time.monotonic", return_value=106.0), \
                    patch("platforms.utiki.rate_limit.get_shared_refresh_coordinator", return_value=coordinator):
                self.assertFalse(
                    await _finish_utk_recovery_probe(
                        tab,
                        runtime,
                        build_refresh_identity(identity),
                        _state["cancel_tasks"],
                        config(),
                        snapshot,
                    )
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
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
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

    async def test_paused_probe_cleanup_failure_is_rate_limited(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        probe = {
            "permit_id": "probe-paused-retry",
            "source_document_id": "100",
        }
        runtime["refresh"]["recovery_probe"] = probe
        clock = {"now": 100.0}
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=False, recovery_required=True,
                error="temporarily unavailable",
            )),
        )
        with patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            coordinator.complete_recovery_probe.assert_awaited_once()
            clock["now"] = 100.5
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))

        self.assertIs(probe, runtime["refresh"]["recovery_probe"])
        self.assertEqual(2, coordinator.complete_recovery_probe.await_count)

    async def test_paused_probe_cleanup_retries_when_document_token_is_missing(self):
        tab = FakeSubmitTab(dialog="")
        source = str(tab.target.url)
        runtime = ctbc_tab_runtime(tab)
        runtime["refresh"]["recovery_probe"] = {
            "permit_id": "probe-missing-document",
            "source_document_id": "100",
        }
        clock = {"now": 100.0}
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(side_effect=[
                types.SimpleNamespace(
                    accepted=False, recovery_required=True,
                    error="temporarily unavailable",
                ),
                types.SimpleNamespace(
                    accepted=True, recovery_required=True, error="",
                ),
            ]),
        )
        snapshot = {
            "classification": "unknown", "document_id": "", "url": source,
        }
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=self.global_settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=snapshot),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ), patch(
            "platforms.utiki.rate_limit.time",
            types.SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
        ):
            self.assertFalse(await nodriver_utk_sports_pause_refresh(tab))
            clock["now"] = 100.5
            self.assertEqual(
                "unknown",
                await nodriver_utk_sports_health_check(tab, source, config()),
            )

        self.assertIsNone(runtime["refresh"]["recovery_probe"])
        self.assertEqual(2, coordinator.complete_recovery_probe.await_count)
        for cleanup_call in coordinator.complete_recovery_probe.await_args_list:
            self.assertEqual("probe-missing-document", cleanup_call.args[2])
            self.assertFalse(cleanup_call.kwargs["success"])

    async def test_abandon_probe_cancels_only_its_registered_lease_task(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        identity = _ctbc_tab_identity(tab)
        runtime = ctbc_identity_runtime(identity)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|old",
            "url": source,
        }
        coordinator = self.coordinator_for(types.SimpleNamespace(
            granted=True,
            status="granted",
            retry_after_seconds=0.0,
            permit_id="probe-cancel",
            is_recovery_probe=True,
            error="",
        ))
        coordinator.complete_recovery_probe = AsyncMock(
            return_value=types.SimpleNamespace(
                accepted=True, recovery_required=True, error=""
            )
        )
        unrelated = asyncio.create_task(asyncio.Event().wait())
        _state["cancel_tasks"].add(unrelated)
        try:
            with patch(
                "platforms.utiki.rate_limit.load_global_settings",
                return_value=self.global_settings,
            ), patch(
                "platforms.utiki.refresh._utk_document_guard",
                AsyncMock(return_value=("normal", snapshot)),
            ), patch(
                "platforms.utiki.refresh._inspect_utk_document",
                AsyncMock(return_value=snapshot),
            ), patch(
                "platforms.utiki.refresh.get_shared_refresh_coordinator",
                return_value=coordinator,
            ):
                self.assertFalse(await nodriver_utk_sports_reload(
                    tab, config(), "probe lifecycle", apply_local_interval=False
                ))
                probe = runtime["refresh"]["recovery_probe"]
                lease_task = probe["lease_task"]
                self.assertIn(lease_task, _state["cancel_tasks"])
                self.assertTrue(await nodriver_utk_sports_cancel_refresh(
                    tab, "abandon probe", wait=True, abandon_probe=True
                ))

            self.assertTrue(lease_task.cancelled())
            self.assertNotIn(lease_task, _state["cancel_tasks"])
            self.assertIsNone(runtime["refresh"]["recovery_probe"])
            self.assertFalse(unrelated.done())
            self.assertIn(unrelated, _state["cancel_tasks"])
        finally:
            unrelated.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await unrelated
            _state["cancel_tasks"].discard(unrelated)

    async def test_cleanup_unknown_tab_does_not_allocate_identity_or_runtime(self):
        fresh = FakeTab("", [], url="https://example.com/")
        with_token = FakeTab("", [], url="https://example.com/")
        setattr(with_token, "_tickets_hunter_utiki_tab_token", "missing-token")
        with_target = FakeTab("", [], url="https://example.com/")
        with_target.target.target_id = "missing-target"

        self.assertFalse(await nodriver_utk_sports_cleanup_tab(fresh, "unknown"))
        self.assertFalse(
            await nodriver_utk_sports_cleanup_tab(with_token, "unknown")
        )
        self.assertFalse(
            await nodriver_utk_sports_cleanup_tab(with_target, "unknown")
        )

        self.assertFalse(hasattr(
            fresh, "_tickets_hunter_utiki_tab_token"
        ))
        self.assertFalse(hasattr(
            with_target, "_tickets_hunter_utiki_tab_token"
        ))
        self.assertFalse(_state["tabs"])

    async def test_cleanup_exact_runtime_cleans_probe_and_keeps_other_tasks(self):
        first = FakeTab("", [], url="https://example.com/")
        first.target.target_id = "cleanup-first"
        first_identity = _ctbc_tab_identity(first)
        first_runtime = ctbc_identity_runtime(first_identity)
        second = FakeTab("", [], url="https://example.com/")
        second.target.target_id = "cleanup-second"
        second_identity = _ctbc_tab_identity(second)
        second_runtime = ctbc_identity_runtime(second_identity)
        second_runtime["action"]["sentinel"] = True

        lease_task = asyncio.create_task(asyncio.Event().wait())
        unrelated = asyncio.create_task(asyncio.Event().wait())
        _state["cancel_tasks"].update({lease_task, unrelated})
        first_runtime["refresh"]["reload_request"] = {
            "request_token": "cleanup-request",
        }
        first_runtime["refresh"]["recovery_probe"] = {
            "permit_id": "cleanup-probe",
            "lease_task": lease_task,
        }
        coordinator = types.SimpleNamespace(
            cancel_request=AsyncMock(side_effect=RuntimeError("cancel failed")),
            complete_recovery_probe=AsyncMock(return_value=types.SimpleNamespace(
                accepted=True, recovery_required=True, error="",
            )),
        )
        try:
            with patch(
                "platforms.utiki.refresh.get_shared_refresh_coordinator",
                return_value=coordinator,
            ):
                self.assertTrue(await nodriver_utk_sports_cleanup_tab(
                    first, "terminal cleanup"
                ))
                self.assertFalse(await nodriver_utk_sports_cleanup_tab(
                    first, "terminal cleanup again"
                ))

            self.assertNotIn(first_identity, _state["tabs"])
            self.assertIs(second_runtime, _state["tabs"][second_identity])
            self.assertTrue(second_runtime["action"]["sentinel"])
            self.assertTrue(lease_task.cancelled())
            self.assertNotIn(lease_task, _state["cancel_tasks"])
            self.assertFalse(unrelated.done())
            self.assertIn(unrelated, _state["cancel_tasks"])
            coordinator.cancel_request.assert_awaited_once()
            coordinator.complete_recovery_probe.assert_awaited_once_with(
                "utk-sports",
                build_refresh_identity(first_identity),
                "cleanup-probe",
                success=False,
            )
        finally:
            unrelated.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await unrelated
            _state["cancel_tasks"].discard(unrelated)

    async def test_cleanup_detaches_before_await_and_preserves_replacement(self):
        tab = FakeTab("", [], url="https://example.com/")
        tab.target.target_id = "cleanup-replacement"
        identity = _ctbc_tab_identity(tab)
        old_runtime = ctbc_identity_runtime(identity)
        replacements = []

        async def replace_after_detach(*_args, **_kwargs):
            self.assertNotIn(identity, _state["tabs"])
            replacement = ctbc_tab_runtime(tab)
            replacement["action"]["replacement"] = True
            replacements.append(replacement)
            return False

        with patch(
            "platforms.utiki.workflow.refresh_engine.cancel_refresh",
            AsyncMock(side_effect=replace_after_detach),
        ):
            self.assertTrue(await nodriver_utk_sports_cleanup_tab(
                tab, "replace during cleanup"
            ))

        self.assertIsNot(old_runtime, replacements[0])
        self.assertIs(replacements[0], _state["tabs"][identity])
        self.assertTrue(replacements[0]["action"]["replacement"])

    async def test_temporary_probe_completion_failure_can_be_retried(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_identity_runtime(_ctbc_tab_identity(tab))
        lease_task = asyncio.create_task(asyncio.Event().wait())
        _state["cancel_tasks"].add(lease_task)
        probe = {
            "permit_id": "probe-retry",
            "source_document_id": source + "|old",
            "lease_task": lease_task,
        }
        runtime["refresh"]["recovery_probe"] = probe
        coordinator = types.SimpleNamespace(
            complete_recovery_probe=AsyncMock(side_effect=[
                types.SimpleNamespace(
                    accepted=False,
                    recovery_required=True,
                    error="database is temporarily unavailable",
                ),
                types.SimpleNamespace(
                    accepted=True, recovery_required=True, error=""
                ),
            ]),
        )
        with patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ):
            self.assertFalse(await nodriver_utk_sports_cancel_refresh(
                tab, "first completion", wait=True, abandon_probe=True
            ))
            self.assertIs(probe, runtime["refresh"]["recovery_probe"])
            self.assertTrue(lease_task.cancelled())
            self.assertNotIn(lease_task, _state["cancel_tasks"])

            self.assertTrue(await nodriver_utk_sports_cancel_refresh(
                tab, "retry completion", wait=True, abandon_probe=True
            ))

        self.assertIsNone(runtime["refresh"]["recovery_probe"])
        self.assertFalse(_state["cancel_tasks"])
        self.assertEqual(2, coordinator.complete_recovery_probe.await_count)
        permit_ids = [
            call.args[2]
            for call in coordinator.complete_recovery_probe.await_args_list
        ]
        self.assertEqual(
            ["probe-retry", "probe-retry"],
            permit_ids,
        )

    async def test_expired_reload_deadline_does_not_create_guard_coroutine(self):
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [])
        identity = _ctbc_tab_identity(tab)
        guard = AsyncMock()
        with patch(
            "platforms.utiki.refresh._utk_document_guard", guard
        ), patch("platforms.utiki.refresh.time.monotonic", return_value=10.0):
            result = await _guard_for_reload(
                tab,
                ctbc_identity_runtime(identity),
                build_refresh_identity(identity),
                _state["cancel_tasks"],
                config(),
                9.0,
            )
        self.assertIsNone(result)
        guard.assert_not_called()

    async def test_awaiting_deadline_final_observation_is_bounded(self):
        tab = FakeSubmitTab(dialog="")
        runtime = ctbc_tab_runtime(tab)
        runtime["refresh"]["reload_request"] = {
            "request_token": "bounded-observation", "phase": "awaiting_document",
            "source_document_token": "100", "paused": False,
            "deadline_at": time.monotonic() - 1.0,
            "reason": "bounded observation",
        }
        pending = asyncio.Event()

        async def pending_guard(*_args, **_kwargs):
            await pending.wait()

        with patch(
            "platforms.utiki.refresh.UTK_RELOAD_FINAL_OBSERVATION_SECONDS", 0.01,
        ), patch(
            "platforms.utiki.refresh._utk_document_guard",
            AsyncMock(side_effect=pending_guard),
        ), patch(
            "platforms.utiki.refresh.cancel_refresh",
            AsyncMock(return_value=True),
        ) as cancel:
            self.assertFalse(await asyncio.wait_for(
                nodriver_utk_sports_reload(
                    tab, config(), "bounded observation",
                    apply_local_interval=False,
                    dispatch_deadline=time.monotonic() - 1.0,
                ),
                timeout=0.2,
            ))

        cancel.assert_awaited_once()

    async def test_reenabled_switch_prevents_legacy_uncoordinated_reload(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        disabled = {"utk_sports": dict(self.global_settings["utk_sports"], enabled=False)}
        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            side_effect=[disabled, self.global_settings],
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
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

    async def test_disabled_protection_completes_on_blocked_new_document(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        tab.reload = AsyncMock(side_effect=tab.reload)
        runtime = ctbc_tab_runtime(tab)
        disabled = {
            "utk_sports": dict(self.global_settings["utk_sports"], enabled=False)
        }
        initial = {
            "classification": "normal", "document_id": "100", "url": source,
        }
        destination = dict(
            initial, classification="blocked", document_id="101"
        )
        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(), commit_refresh=AsyncMock(),
        )

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=disabled,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=[initial, initial, destination]),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "disabled lifecycle", apply_local_interval=False
            ))
            request = runtime["refresh"]["reload_request"]
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "disabled lifecycle", apply_local_interval=False
            ))
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "disabled lifecycle", apply_local_interval=False
            ))

        tab.reload.assert_awaited_once()
        coordinator.reserve_refresh.assert_not_awaited()
        coordinator.commit_refresh.assert_not_awaited()

    async def test_disabled_protection_rejects_stale_queued_source(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        runtime = ctbc_tab_runtime(tab)
        runtime["refresh"]["reload_request"] = {
            "request_token": "disabled-stale", "phase": "queued",
            "document_id": "100", "source_document_token": "100",
            "expected_url": source, "shared_enqueued": False,
            "paused": False, "deadline_at": 0.0,
            "reason": "stale disabled source",
        }
        disabled = {
            "utk_sports": dict(self.global_settings["utk_sports"], enabled=False)
        }
        destination = {
            "classification": "normal", "document_id": "101", "url": source,
        }

        with patch(
            "platforms.utiki.rate_limit.load_global_settings",
            return_value=disabled,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(return_value=destination),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=types.SimpleNamespace(),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "stale disabled source",
                apply_local_interval=False,
            ))

        self.assertIsNone(runtime["refresh"]["reload_request"])
        self.assertFalse(tab.reloaded)

    async def test_enabled_queue_hot_switches_to_disabled_without_new_intent(self):
        source = "https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        tab.reload = AsyncMock(side_effect=tab.reload)
        runtime = ctbc_tab_runtime(tab)
        current = {"document_id": "100"}
        enabled = {"value": True}
        deadline = time.monotonic() + 30.0

        async def inspect(*_args, **_kwargs):
            return {
                "classification": "normal",
                "document_id": current["document_id"], "url": source,
            }

        def settings(*_args, **_kwargs):
            return {"utk_sports": dict(
                self.global_settings["utk_sports"], enabled=enabled["value"]
            )}

        coordinator = types.SimpleNamespace(
            reserve_refresh=AsyncMock(return_value=types.SimpleNamespace(
                reserved=False, status="queued", retry_after_seconds=0.1,
                reservation_id="", is_recovery_probe=False,
                cooldown_until=0.0, error="",
            )),
            cancel_request=AsyncMock(return_value=True),
        )

        with patch(
            "platforms.utiki.rate_limit.load_global_settings", side_effect=settings,
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=inspect),
        ), patch(
            "platforms.utiki.refresh.get_shared_refresh_coordinator",
            return_value=coordinator,
        ), patch(
            "platforms.utiki.refresh.check_and_handle_pause",
            AsyncMock(return_value=False),
        ), patch(
            "platforms.utiki.refresh.check_and_handle_quit",
            AsyncMock(return_value=False),
        ):
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "hot switch", apply_local_interval=False,
                dispatch_deadline=deadline,
            ))
            request = runtime["refresh"]["reload_request"]
            token = request["request_token"]
            self.assertTrue(request["shared_enqueued"])
            enabled["value"] = False
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "hot switch", apply_local_interval=False,
                dispatch_deadline=deadline,
            ))
            self.assertIs(request, runtime["refresh"]["reload_request"])
            self.assertEqual(token, request["request_token"])
            self.assertEqual(deadline, request["deadline_at"])
            self.assertEqual("awaiting_document", request["phase"])
            self.assertFalse(await nodriver_utk_sports_reload(
                tab, config(), "hot switch", apply_local_interval=False,
                dispatch_deadline=deadline,
            ))
            current["document_id"] = "101"
            self.assertTrue(await nodriver_utk_sports_reload(
                tab, config(), "hot switch", apply_local_interval=False,
                dispatch_deadline=deadline,
            ))

        coordinator.reserve_refresh.assert_awaited_once()
        coordinator.cancel_request.assert_awaited_once_with(
            "utk-sports", build_refresh_identity(_ctbc_tab_identity(tab)),
            request_token=token,
        )
        tab.reload.assert_awaited_once()

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
            "platforms.utiki.rate_limit.load_global_settings", return_value=disabled
        ), patch(
            "platforms.utiki.refresh._inspect_utk_document",
            AsyncMock(side_effect=snapshots),
        ):
            refresh_identity = build_refresh_identity(identity)
            first, first_snapshot = await _utk_document_guard(
                tab, runtime, refresh_identity, _state["cancel_tasks"],
                config(), source,
            )
            second, second_snapshot = await _utk_document_guard(
                tab, runtime, refresh_identity, _state["cancel_tasks"],
                config(), source,
            )

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
            with patch("platforms.utiki.rate_limit.load_global_settings", return_value=self.global_settings), \
                    patch("platforms.utiki.refresh._inspect_utk_document", AsyncMock(return_value=snapshot)), \
                    patch("platforms.utiki.refresh.get_shared_refresh_coordinator", return_value=coordinator), \
                    patch("platforms.utiki.refresh.time.monotonic", side_effect=[100.0, 100.0, 111.0, 111.0, 111.0]):
                refresh_identity = build_refresh_identity(identity)
                first, _ = await _utk_document_guard(
                    tab, runtime, refresh_identity, _state["cancel_tasks"],
                    config(), source,
                )
                second, _ = await _utk_document_guard(
                    tab, runtime, refresh_identity, _state["cancel_tasks"],
                    config(), source,
                )
            self.assertEqual("unknown", first)
            self.assertEqual("unknown", second)
            coordinator.report_rate_limited.assert_not_awaited()
        finally:
            runtime["document"]["unknown_document"] = None
            runtime["refresh"]["rate_limit_observation"] = None
            runtime["document"]["last_document"] = ""


class CtbcEmergencyMarkerTest(
    CtbcStateIsolationMixin, unittest.IsolatedAsyncioTestCase
):
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

    def test_emergency_marker_real_write_read_and_expiry(self):
        with tempfile.TemporaryDirectory() as root, patch(
            "platforms.utiki.refresh.util.get_app_root", return_value=root
        ):
            with patch("platforms.utiki.refresh.time.time", return_value=100.0):
                _mark_utk_emergency_window()
            markers = list(Path(root, ".runtime").glob(
                "utk_sports_emergency_*.until"
            ))
            self.assertEqual(1, len(markers))
            with patch("platforms.utiki.refresh.time.time", return_value=101.0):
                self.assertEqual(5.0, _utk_emergency_remaining())
            with patch("platforms.utiki.refresh.time.time", return_value=107.0):
                self.assertEqual(0.0, _utk_emergency_remaining())
            self.assertFalse(markers[0].exists())

    async def test_active_real_emergency_marker_prevents_reservation(self):
        source = "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=P1"
        tab = FakeTab(CTBC_DATE_ROW_SELECTOR, [], url=source)
        snapshot = {
            "classification": "normal",
            "document_id": source + "|old",
            "url": source,
        }
        coordinator = types.SimpleNamespace(reserve_refresh=AsyncMock())
        with tempfile.TemporaryDirectory() as root, patch(
            "platforms.utiki.refresh.util.get_app_root", return_value=root
        ):
            _mark_utk_emergency_window(60.0)
            with patch(
                "platforms.utiki.rate_limit.load_global_settings",
                return_value=self.global_settings,
            ), patch(
                "platforms.utiki.refresh._utk_document_guard",
                AsyncMock(return_value=("normal", snapshot)),
            ), patch(
                "platforms.utiki.refresh.get_shared_refresh_coordinator",
                return_value=coordinator,
            ):
                self.assertFalse(await nodriver_utk_sports_reload(
                    tab, config(), "active emergency marker",
                    apply_local_interval=False,
                ))
        coordinator.reserve_refresh.assert_not_awaited()
