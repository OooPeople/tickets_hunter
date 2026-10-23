#encoding=utf-8
"""uTiki reload request lifecycle and shared refresh coordination.

Mutable state is supplied by ``workflow``.  This module owns refresh behavior,
but deliberately owns no tab registry or parallel runtime.
"""

import asyncio
import contextlib
import os
import time
import uuid

import util
from nodriver_common import check_and_handle_pause, check_and_handle_quit
from refresh_coordinator import UTK_SPORTS_SCOPE, get_shared_refresh_coordinator

from .document_health import _inspect_utk_document
from . import rate_limit
from .rate_limit import (
    UTK_PAUSE_CLEANUP_RETRY_SECONDS,
    UTK_RECOVERY_PROBE_MAX_SECONDS,
    _refresh_settings,
    _utk_emergency_remaining,
)
from .runtime import TabRuntime


UTK_RELOAD_WAIT_MAX_SECONDS = 30.0
UTK_RELOAD_FINAL_OBSERVATION_SECONDS = 0.25


def build_refresh_identity(tab_identity):
    """為單一標準分頁建立程序內唯一的刷新協調識別。"""
    return f"{util.get_instance_id()}:{os.getpid()}:{tab_identity}"


def _pop_reload_request_if_token(runtime, request_token):
    """僅移除目前協程所持有的邏輯刷新請求。"""
    refresh = runtime["refresh"]
    current = refresh["reload_request"]
    if current and current.get("request_token") == request_token:
        refresh["reload_request"] = None
        return current
    return None


def _reload_request_is_current(runtime, request):
    """判斷刷新請求是否仍為執行期目前持有的請求。"""

    current = runtime["refresh"]["reload_request"]
    return bool(
        current is request
        and current.get("request_token") == request.get("request_token")
    )


def _reload_request_is_awaiting(request):
    """判斷刷新請求是否正在等待新文件。"""

    return bool(
        request and request.get("phase") in {"awaiting_document", "completed"}
    )


def _mark_reload_awaiting(
    runtime, request, *, reservation=None, allow_paused_ambiguous=False,
):
    """標記刷新請求已送出並開始等待文件切換。"""

    if not _reload_request_is_current(runtime, request):
        return False
    if reservation is not None:
        exact_reserved = _reserved_request_is_current(
            runtime, request, reservation
        )
        paused_ambiguous = bool(
            allow_paused_ambiguous
            and request.get("phase") == "reserved"
            and request.get("paused")
            and not request.get("pause_cleanup_done")
            and request.get("reservation_id") == reservation.reservation_id
        )
        if not exact_reserved and not paused_ambiguous:
            return False
    elif request.get("phase") != "queued":
        return False
    request.update({
        "phase": "awaiting_document",
        "reservation_id": "",
        "shared_enqueued": False,
        "dispatched_at": time.monotonic(),
        "completed_document_token": "",
    })
    runtime["document"]["snapshot"] = None
    return True


def _reconcile_reload_document(runtime, snapshot):
    """依最新文件快照協調刷新請求狀態。"""

    request = runtime["refresh"]["reload_request"]
    if not request or request.get("phase") != "awaiting_document":
        return False
    if snapshot.get("classification") not in {
        "normal", "unverified", "blocked",
    }:
        return False
    document_id = str(snapshot.get("document_id") or "")
    source_token = str(request.get("source_document_token") or "")
    if not source_token or not document_id or document_id == source_token:
        return False
    request["phase"] = "completed"
    request["completed_document_token"] = document_id
    return True


def _consume_completed_reload(runtime, request):
    """消費已完成的刷新請求並清除執行期狀態。"""

    if not (
        _reload_request_is_current(runtime, request)
        and request.get("phase") == "completed"
    ):
        return False
    _pop_reload_request_if_token(runtime, request.get("request_token"))
    return True


def _reserved_request_is_current(runtime, request, reservation):
    """判斷預約結果是否仍屬於目前刷新請求。"""

    return bool(
        _reload_request_is_current(runtime, request)
        and request.get("phase") == "reserved"
        and not request.get("paused")
        and request.get("reservation_id") == reservation.reservation_id
    )


def _mark_paused_request_clean(runtime, request):
    """將暫停的刷新請求標記為已完成清理。"""

    if not (
        _reload_request_is_current(runtime, request)
        and request.get("paused")
        and request.get("phase") in {"queued", "reserved"}
    ):
        return False
    request.update({
        "phase": "queued", "reservation_id": "",
        "shared_enqueued": False, "pause_cleanup_done": True,
        "pause_cleanup_retry_at": 0.0,
    })
    return True


async def cancel_refresh(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, reason="", *,
    wait=False, abandon_probe=False,
):
    """取消待處理刷新，並視需要釋放持有的復原探測。"""
    refresh = runtime["refresh"]
    request = refresh["reload_request"]
    if request is not None:
        _pop_reload_request_if_token(runtime, request.get("request_token"))
    probe = refresh["recovery_probe"] if abandon_probe else None
    if probe is not None:
        probe["abandon_requested"] = True
    if request is None and probe is None:
        return False
    coordinator = get_shared_refresh_coordinator()

    async def cancel_all():
        """取消目前分頁執行期持有的所有背景工作。"""

        changed = False
        request_error = None
        if request is not None:
            try:
                changed = await coordinator.cancel_request(
                    UTK_SPORTS_SCOPE, refresh_identity,
                    request_token=request.get("request_token"),
                ) or changed
            except BaseException as exc:
                request_error = exc
        try:
            if probe is not None:
                changed = await rate_limit._abandon_recovery_probe(
                    runtime, refresh_identity, cancel_tasks, coordinator, probe
                ) or changed
        except asyncio.CancelledError:
            raise
        except Exception:
            if request_error is None:
                raise
        if request_error is not None:
            raise request_error
        return changed

    cancel_call = cancel_all()
    if wait:
        return await cancel_call
    task = asyncio.create_task(cancel_call)
    cancel_tasks.add(task)
    task.add_done_callback(cancel_tasks.discard)
    return True


async def pause_refresh(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, reason="",
):
    """暫停刷新工作，同時保留本機意圖與進度。"""
    del tab, reason
    refresh = runtime["refresh"]
    request = refresh["reload_request"]
    probe = refresh["recovery_probe"]
    if request is None and probe is None:
        return False
    coordinator = get_shared_refresh_coordinator()
    changed = False
    if request is not None:
        if _reload_request_is_awaiting(request):
            if not request.get("paused"):
                request["paused"] = True
                changed = True
        elif not request.get("paused"):
            request["paused"] = True
            request["pause_cleanup_done"] = not request.get(
                "shared_enqueued", False
            )
            request["pause_cleanup_retry_at"] = 0.0
        if not _reload_request_is_awaiting(request) and rate_limit._pause_cleanup_due(request):
            token = request.get("request_token")
            reservation_id = str(request.get("reservation_id") or "")
            if request.get("phase") == "reserved" and reservation_id:
                changed = await coordinator.release_refresh(
                    UTK_SPORTS_SCOPE, refresh_identity, reservation_id,
                    request_token=token, drop_request=True,
                )
            else:
                changed = await coordinator.cancel_request(
                    UTK_SPORTS_SCOPE, refresh_identity, request_token=token,
                )
            if changed:
                _mark_paused_request_clean(runtime, request)
            elif (
                _reload_request_is_current(runtime, request)
                and not request.get("pause_cleanup_done", False)
            ):
                request["pause_cleanup_retry_at"] = (
                    time.monotonic() + UTK_PAUSE_CLEANUP_RETRY_SECONDS
                )
    if probe is not None:
        if not probe.get("abandon_requested"):
            probe["abandon_requested"] = True
            probe["pause_cleanup_done"] = False
            probe["pause_cleanup_retry_at"] = 0.0
        _, probe_changed = await rate_limit._retry_abandoned_probe_cleanup(
            runtime, refresh_identity, cancel_tasks, coordinator
        )
        changed = probe_changed or changed
    return bool(changed)


def consume_reload_event(runtime: TabRuntime):
    """消費一次需由外層路由重新偵測的同網址刷新事件。"""

    refresh = runtime["refresh"]
    if not refresh["reload_event"]:
        return False
    refresh["reload_event"] = False
    return True


async def _utk_document_guard(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, config_dict, url=None,
):
    """在任何 uTiki 頁面操作前套用 429 斷路保護。"""
    settings = _refresh_settings()
    snapshot = await _inspect_utk_document(tab, runtime, url)
    document = runtime["document"]
    refresh = runtime["refresh"]
    cleanup_pending, _ = await rate_limit._retry_abandoned_probe_cleanup(
        runtime, refresh_identity, cancel_tasks,
    )
    if cleanup_pending:
        return "unknown", snapshot
    document_id = snapshot.get("document_id")
    previous_document_id = document["last_document"]
    if document_id:
        _reconcile_reload_document(runtime, snapshot)
        if previous_document_id and previous_document_id != document_id:
            refresh["reload_event"] = True
        document["last_document"] = document_id
    else:
        return "unknown", snapshot
    if not settings.get("enabled", True):
        probe = refresh["recovery_probe"]
        if probe is not None:
            if not probe.get("abandon_requested"):
                probe["abandon_requested"] = True
                probe["pause_cleanup_done"] = False
                probe["pause_cleanup_retry_at"] = 0.0
            cleanup_pending, _ = await rate_limit._retry_abandoned_probe_cleanup(
                runtime, refresh_identity, cancel_tasks,
            )
            if cleanup_pending:
                return "unknown", snapshot
        request = refresh["reload_request"]
        if request and request.get("phase") == "awaiting_document":
            return "unknown", snapshot
        return "disabled", snapshot
    probe_handled = await rate_limit._finish_utk_recovery_probe(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, snapshot,
    )
    classification = snapshot.get("classification", "unknown")
    if classification == "unknown":
        if refresh["rate_limit_observation"] == document_id:
            return "blocked", snapshot
        unknown = document["unknown_document"]
        if not unknown or unknown.get("document_id") != document_id:
            document["unknown_document"] = {
                "document_id": document_id, "first_seen_at": time.monotonic(),
            }
        return "unknown", snapshot
    document["unknown_document"] = None
    if classification == "blocked":
        request = refresh["reload_request"]
        stale = bool(request) and (
            request.get("document_id") != snapshot.get("document_id")
            or request.get("expected_url") != snapshot.get("url")
        )
        if not _reload_request_is_awaiting(request) and (
            not request or not request.get("allow_blocked_recovery") or stale
        ):
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "HTTP 429", wait=True,
            )
        if (
            not probe_handled
            and refresh["rate_limit_observation"] != snapshot.get("document_id")
            and time.monotonic() >= refresh["rate_limit_retry"]
        ):
            await rate_limit._report_utk_rate_limit(
                tab, runtime, refresh_identity, config_dict,
                snapshot.get("document_id", ""),
                document_started_at=snapshot.get("document_started_at", 0.0),
            )
        return "blocked", snapshot
    request = refresh["reload_request"]
    if request and not _reload_request_is_awaiting(request) and (
        request.get("expected_url") != snapshot.get("url")
        or request.get("document_id") != snapshot.get("document_id")
    ):
        await cancel_refresh(
            tab, runtime, refresh_identity, cancel_tasks,
            "document changed", wait=True,
        )
    if request and request.get("phase") == "awaiting_document":
        return "unknown", snapshot
    return classification, snapshot


async def _invoke_browser_reload(tab, debug):
    """呼叫瀏覽器刷新並記錄除錯資訊。"""

    try:
        await asyncio.wait_for(
            tab.reload(), timeout=UTK_RECOVERY_PROBE_MAX_SECONDS
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        debug.log(f"[CTBC] Reload dispatch outcome is unknown: {exc}")
    return False


async def _detach_shared_reload_request(
    runtime, refresh_identity, request, coordinator,
):
    """將本機執行期與共用刷新請求解除關聯。"""

    if not request.get("shared_enqueued"):
        return True
    if time.monotonic() < float(request.get("pause_cleanup_retry_at") or 0.0):
        return False
    token = request.get("request_token")
    reservation_id = str(request.get("reservation_id") or "")
    if request.get("phase") == "reserved" and reservation_id:
        changed = await coordinator.release_refresh(
            UTK_SPORTS_SCOPE, refresh_identity, reservation_id,
            request_token=token, drop_request=True,
        )
    else:
        changed = await coordinator.cancel_request(
            UTK_SPORTS_SCOPE, refresh_identity, request_token=token,
        )
    if changed and _reload_request_is_current(runtime, request):
        request.update({
            "phase": "queued", "reservation_id": "",
            "shared_enqueued": False, "pause_cleanup_retry_at": 0.0,
        })
        return True
    if _reload_request_is_current(runtime, request):
        request["pause_cleanup_retry_at"] = (
            time.monotonic() + UTK_PAUSE_CLEANUP_RETRY_SECONDS
        )
    return False


async def _reload_without_shared_protection(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, debug,
    reason, apply_local_interval, snapshot, dispatch_deadline,
):
    """在共用協調器不可用時執行受本機間隔保護的刷新。"""

    del cancel_tasks
    if not snapshot.get("document_id"):
        return False
    request = _get_reload_request(
        runtime, snapshot, config_dict, reason,
        apply_local_interval, False, dispatch_deadline, debug,
    )
    coordinator = get_shared_refresh_coordinator()
    if not await _detach_shared_reload_request(
        runtime, refresh_identity, request, coordinator
    ):
        return False
    if (
        request.get("document_id") != snapshot.get("document_id")
        or request.get("expected_url") != snapshot.get("url")
    ):
        _pop_reload_request_if_token(runtime, request.get("request_token"))
        return False
    if request.get("paused"):
        if await check_and_handle_pause(config_dict):
            return False
        request["paused"] = False
    now = time.monotonic()
    deadline = float(request.get("deadline_at") or 0.0)
    if deadline and now >= deadline:
        _pop_reload_request_if_token(runtime, request.get("request_token"))
        return False
    if now < float(request.get("eligible_at") or 0.0):
        return False
    if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
        return False
    if _refresh_settings(force_refresh=True).get("enabled", True):
        return False
    if not _mark_reload_awaiting(runtime, request):
        return False
    return await _invoke_browser_reload(tab, debug)


async def _guard_for_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, deadline,
):
    """在刷新前檢查文件狀態與限流保護。"""

    if deadline is None:
        return await _utk_document_guard(
            tab, runtime, refresh_identity, cancel_tasks, config_dict
        )
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(
            _utk_document_guard(
                tab, runtime, refresh_identity, cancel_tasks, config_dict
            ),
            timeout=remaining,
        )
    except asyncio.TimeoutError:
        return None


async def _poll_committed_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, request, deadline,
):
    """輪詢已提交的刷新，直到觀察到新文件或逾時。"""

    if not _reload_request_is_awaiting(request):
        return False, False
    needs_observation = request.get("phase") == "awaiting_document" or (
        request.get("phase") == "completed"
        and runtime["refresh"]["recovery_probe"] is not None
    )
    if needs_observation:
        if deadline and time.monotonic() >= deadline:
            await _guard_for_reload(
                tab, runtime, refresh_identity, cancel_tasks, config_dict,
                time.monotonic() + UTK_RELOAD_FINAL_OBSERVATION_SECONDS,
            )
        else:
            await _guard_for_reload(
                tab, runtime, refresh_identity, cancel_tasks, config_dict,
                deadline or None,
            )
    if not _reload_request_is_current(runtime, request):
        return True, False
    if request.get("phase") == "completed":
        if runtime["refresh"]["recovery_probe"] is not None:
            return True, False
        if request.get("paused") and await check_and_handle_pause(config_dict):
            return True, False
        request["paused"] = False
        return True, _consume_completed_reload(runtime, request)
    if deadline and time.monotonic() >= deadline:
        await cancel_refresh(
            tab, runtime, refresh_identity, cancel_tasks,
            "reload deadline expired", wait=True, abandon_probe=True,
        )
    return True, False


def _tighten_reload_deadline(request, dispatch_deadline):
    """以呼叫端限制縮短刷新請求的截止時間。"""

    new_deadline = float(dispatch_deadline or 0.0)
    old_deadline = float(request.get("deadline_at") or 0.0)
    if new_deadline > 0 and (old_deadline <= 0 or new_deadline < old_deadline):
        request["deadline_at"] = new_deadline
    return float(request.get("deadline_at") or 0.0)


async def _resume_reload_request(config_dict, request):
    """以目前設定恢復既有刷新請求。"""

    if not request.get("paused"):
        return True
    if await check_and_handle_pause(config_dict):
        return False
    request.update({
        "paused": False, "phase": "queued",
        "pause_cleanup_done": False, "pause_cleanup_retry_at": 0.0,
    })
    return True


def _get_reload_request(
    runtime, snapshot, config_dict, reason, apply_local_interval,
    allow_blocked_recovery, dispatch_deadline, debug,
):
    """取得既有刷新請求或建立新的刷新意圖。"""

    refresh = runtime["refresh"]
    request = refresh["reload_request"]
    if request is not None:
        _tighten_reload_deadline(request, dispatch_deadline)
        return request
    interval = config_dict.get("advanced", {}).get(
        "auto_reload_page_interval", 0.0
    )
    local_delay = (
        (float(interval) if interval and interval > 0 else 1.0)
        if apply_local_interval else 0.0
    )
    request = {
        "document_id": snapshot.get("document_id"),
        "source_document_token": snapshot.get("document_id"),
        "expected_url": snapshot.get("url"),
        "eligible_at": time.monotonic() + local_delay,
        "deadline_at": float(dispatch_deadline or 0.0),
        "next_poll_at": 0.0,
        "reason": reason,
        "allow_blocked_recovery": bool(allow_blocked_recovery),
        "request_token": uuid.uuid4().hex,
        "phase": "queued",
        "paused": False,
        "reservation_id": "",
        "shared_enqueued": False,
        "pause_cleanup_done": False,
        "pause_cleanup_retry_at": 0.0,
    }
    refresh["reload_request"] = request
    debug.log(f"[CTBC] {reason}; queued for shared refresh")
    return request


def _reservation_retry(request, reservation, debug):
    """處理刷新預約要求稍後重試的結果。"""

    retry_delay = max(float(reservation.retry_after_seconds or 0.1), 0.05)
    if reservation.status == "cooldown":
        remaining = max(float(reservation.cooldown_until or 0.0) - time.time(), 0.1)
        retry_delay = min(remaining, 1.0)
    elif reservation.status == "recovery_probe_active":
        retry_delay = max(retry_delay, 1.0)
    request["next_poll_at"] = time.monotonic() + retry_delay
    if reservation.error:
        debug.log(f"[UTK RATE] Shared coordinator failed closed: {reservation.error}")


async def _release_reservation(
    coordinator, refresh_identity, reservation, request_token,
):
    """釋放協調器中的刷新預約。"""

    return await coordinator.release_refresh(
        UTK_SPORTS_SCOPE, refresh_identity, reservation.reservation_id,
        request_token=request_token, drop_request=True,
    )


async def _release_noncommit_reservation(
    runtime, coordinator, refresh_identity, reservation, request,
):
    """釋放未提交的預約並同步本機請求狀態。"""

    if (
        _reload_request_is_current(runtime, request)
        and request.get("paused")
        and request.get("pause_cleanup_done")
    ):
        return False
    released = await _release_reservation(
        coordinator, refresh_identity, reservation,
        request.get("request_token"),
    )
    if _reserved_request_is_current(runtime, request, reservation):
        _pop_reload_request_if_token(runtime, request.get("request_token"))
    elif released:
        _mark_paused_request_clean(runtime, request)
    return released


async def _drop_reserve_result(
    coordinator, refresh_identity, request_token, reservation,
):
    """丟棄已失效的預約結果並釋放其資源。"""

    if reservation is not None and reservation.reserved:
        return await _release_reservation(
            coordinator, refresh_identity, reservation, request_token
        )
    return await coordinator.cancel_request(
        UTK_SPORTS_SCOPE, refresh_identity, request_token=request_token,
    )


async def _reserve_refresh_owned(
    runtime, coordinator, refresh_identity, request, settings,
):
    """為目前請求取得由本機持有的刷新預約。"""

    token = request.get("request_token")
    reserve_task = asyncio.create_task(coordinator.reserve_refresh(
        UTK_SPORTS_SCOPE, refresh_identity,
        min_interval_seconds=settings.get("min_interval_seconds", 1.5),
        jitter_range_seconds=(0.1, 0.3), request_token=token,
    ))
    try:
        return await asyncio.shield(reserve_task)
    except asyncio.CancelledError:
        reservation = None
        with contextlib.suppress(BaseException):
            reservation = await asyncio.shield(reserve_task)
        with contextlib.suppress(BaseException):
            await asyncio.shield(_drop_reserve_result(
                coordinator, refresh_identity, token, reservation
            ))
        _pop_reload_request_if_token(runtime, token)
        raise


async def _read_reserved_preflight(
    tab, runtime, deadline,
):
    """在預約有效期間讀取刷新前文件狀態。"""

    if not deadline:
        return await _inspect_utk_document(tab, runtime, force=True)
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        return None
    try:
        return await asyncio.wait_for(
            _inspect_utk_document(tab, runtime, force=True), timeout=remaining
        )
    except asyncio.TimeoutError:
        return None


async def _preflight_allows_reload(
    tab, runtime, refresh_identity, config_dict, request, reservation,
    preflight, allow_unknown_document, allow_blocked_recovery,
):
    """判斷刷新前檢查結果是否允許送出刷新。"""

    refresh = runtime["refresh"]
    classification = preflight.get("classification")
    if (
        classification == "unknown"
        and refresh["rate_limit_observation"] == preflight.get("document_id")
    ):
        classification = "blocked"
    same_document = preflight.get("document_id") == request.get("document_id")
    recovery_reload = (
        allow_blocked_recovery and reservation.is_recovery_probe and same_document
    )
    follower_reload = (
        allow_blocked_recovery and not reservation.is_recovery_probe
        and same_document
        and refresh["rate_limit_observation"] == preflight.get("document_id")
    )
    if classification == "blocked" and not (recovery_reload or follower_reload):
        await rate_limit._report_utk_rate_limit(
            tab, runtime, refresh_identity, config_dict,
            preflight.get("document_id", ""),
            document_started_at=preflight.get("document_started_at", 0.0),
        )
        return False
    return bool(
        preflight.get("document_id") == request.get("document_id")
        and preflight.get("url") == request.get("expected_url")
        and (classification != "unknown" or allow_unknown_document)
    )


async def _commit_reserved_reload(
    tab, runtime, refresh_identity, cancel_tasks, coordinator, reservation,
    request, preflight, debug,
):
    """提交已預約的刷新並建立等待新文件的狀態。"""

    request_token = request.get("request_token")
    try:
        decision = await coordinator.commit_refresh(
            UTK_SPORTS_SCOPE, refresh_identity, reservation.reservation_id,
            request_token=request_token,
        )
    except BaseException as exc:
        _mark_reload_awaiting(
            runtime, request, reservation=reservation,
            allow_paused_ambiguous=True,
        )
        if isinstance(exc, asyncio.CancelledError):
            raise
        debug.log(f"[UTK RATE] Shared commit outcome is unknown: {exc}")
        return False
    if not _reserved_request_is_current(runtime, request, reservation):
        if (
            _reload_request_is_current(runtime, request)
            and (decision.status == "already_committed" or decision.error)
            and _mark_reload_awaiting(runtime, request)
        ):
            rate_limit._install_recovery_probe(
                runtime, refresh_identity, cancel_tasks, decision, preflight,
            )
            return False
        if decision.granted and decision.is_recovery_probe:
            rate_limit._install_abandoned_probe_cleanup(runtime, decision, preflight)
            await rate_limit._retry_abandoned_probe_cleanup(
                runtime, refresh_identity, cancel_tasks, coordinator
            )
        if decision.granted or decision.status == "already_committed":
            _mark_paused_request_clean(runtime, request)
        elif not (
            _reload_request_is_current(runtime, request)
            and request.get("paused")
            and request.get("pause_cleanup_done")
        ):
            await _release_noncommit_reservation(
                runtime, coordinator, refresh_identity, reservation, request
            )
        return False
    if not decision.granted:
        if decision.status == "already_committed" or decision.error:
            if _mark_reload_awaiting(
                runtime, request, reservation=reservation
            ):
                rate_limit._install_recovery_probe(
                    runtime, refresh_identity, cancel_tasks, decision, preflight,
                )
            if decision.error:
                debug.log(
                    f"[UTK RATE] Shared commit failed closed: {decision.error}"
                )
            return False
        if decision.status != "already_committed":
            await _release_noncommit_reservation(
                runtime, coordinator, refresh_identity, reservation, request
            )
        return False
    if not _mark_reload_awaiting(
        runtime, request, reservation=reservation
    ):
        return False
    rate_limit._install_recovery_probe(
        runtime, refresh_identity, cancel_tasks, decision, preflight,
    )
    return await _invoke_browser_reload(tab, debug)


async def _execute_reserved_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, coordinator,
    reservation, request, allow_unknown_document, allow_blocked_recovery,
    deadline, debug,
):
    """執行已預約刷新的前置檢查、提交與結果處理。"""

    try:
        preflight = await _read_reserved_preflight(tab, runtime, deadline)
        if not _reserved_request_is_current(runtime, request, reservation):
            if not (
                _reload_request_is_current(runtime, request)
                and request.get("paused", False)
            ):
                await _release_noncommit_reservation(
                    runtime, coordinator, refresh_identity, reservation,
                    request,
                )
            return False
        allowed = bool(preflight) and await _preflight_allows_reload(
            tab, runtime, refresh_identity, config_dict, request, reservation,
            preflight, allow_unknown_document, allow_blocked_recovery,
        )
        if not allowed:
            await _release_noncommit_reservation(
                runtime, coordinator, refresh_identity, reservation, request,
            )
            return False
        if await check_and_handle_pause(config_dict):
            await pause_refresh(
                tab, runtime, refresh_identity, cancel_tasks, "paused"
            )
            return False
        if (
            await check_and_handle_quit(config_dict)
            or _utk_emergency_remaining() > 0
            or (deadline and time.monotonic() >= deadline)
        ):
            await _release_noncommit_reservation(
                runtime, coordinator, refresh_identity, reservation, request,
            )
            return False
        if not _reserved_request_is_current(runtime, request, reservation):
            if not (
                _reload_request_is_current(runtime, request)
                and request.get("paused", False)
            ):
                await _release_noncommit_reservation(
                    runtime, coordinator, refresh_identity, reservation,
                    request,
                )
            return False
        return await _commit_reserved_reload(
            tab, runtime, refresh_identity, cancel_tasks, coordinator,
            reservation, request, preflight, debug,
        )
    except BaseException:
        if not _reload_request_is_awaiting(request):
            with contextlib.suppress(BaseException):
                await asyncio.shield(_release_noncommit_reservation(
                    runtime, coordinator, refresh_identity, reservation, request,
                ))
        raise


async def _poll_owned_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict,
    existing, reason, dispatch_deadline,
):
    """輪詢目前分頁持有的既有刷新請求。"""

    new_deadline = float(dispatch_deadline or 0.0)
    existing_reason = str(existing.get("reason") or "") if existing else ""
    reason_mismatch = bool(
        existing and (
            not existing_reason or existing_reason != str(reason or "")
        )
    )
    if reason_mismatch and existing.get("phase") != "completed":
        return True, False, existing, new_deadline
    deadline = (
        float(existing.get("deadline_at") or 0.0)
        if reason_mismatch else (
            _tighten_reload_deadline(existing, dispatch_deadline)
            if existing is not None else new_deadline
        )
    )
    handled, completed = await _poll_committed_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict,
        existing, deadline,
    )
    if reason_mismatch and completed:
        return False, False, None, new_deadline
    return handled, completed, existing, deadline


async def reload(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, config_dict, reason, *,
    apply_local_interval=True, allow_unknown_document=False,
    allow_blocked_recovery=False, dispatch_deadline=None,
):
    """推進一次刷新意圖，僅在觀察到新文件後回報成功。"""
    debug = util.create_debug_logger(config_dict)
    existing = runtime["refresh"]["reload_request"]
    handled, completed, existing, deadline = await _poll_owned_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict,
        existing, reason, dispatch_deadline,
    )
    if handled:
        return completed
    if existing is not None and deadline and time.monotonic() >= deadline:
        await cancel_refresh(
            tab, runtime, refresh_identity, cancel_tasks,
            "reload deadline expired", wait=True,
        )
        return False
    guarded = await _guard_for_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict,
        deadline or None,
    )
    if guarded is None:
        return False
    classification, snapshot = guarded
    if runtime["refresh"]["reload_request"] is not existing:
        return False
    settings = _refresh_settings()
    if not settings.get("enabled", True):
        if runtime["refresh"]["recovery_probe"] is not None:
            return False
        return await _reload_without_shared_protection(
            tab, runtime, refresh_identity, cancel_tasks, config_dict, debug,
            reason, apply_local_interval, snapshot, deadline,
        )
    probe = runtime["refresh"]["recovery_probe"]
    if probe and probe.get("abandon_requested"):
        return False
    if classification == "blocked" and not allow_blocked_recovery:
        return False
    if classification == "unknown" and not allow_unknown_document:
        return False
    request = _get_reload_request(
        runtime, snapshot, config_dict, reason, apply_local_interval,
        allow_blocked_recovery, deadline, debug,
    )
    deadline = float(request.get("deadline_at") or 0.0)
    if not await _resume_reload_request(config_dict, request):
        return False
    if request.get("phase") == "reserved":
        return False
    now = time.monotonic()
    if deadline and now >= deadline:
        await cancel_refresh(
            tab, runtime, refresh_identity, cancel_tasks,
            "reload deadline expired", wait=True,
        )
        return False
    if now < request.get("eligible_at", 0.0) or now < request.get("next_poll_at", 0.0):
        return False
    emergency_remaining = _utk_emergency_remaining()
    if emergency_remaining > 0:
        request["next_poll_at"] = time.monotonic() + min(emergency_remaining, 0.5)
        return False
    token = request.get("request_token")
    coordinator = get_shared_refresh_coordinator()
    reservation = await _reserve_refresh_owned(
        runtime, coordinator, refresh_identity, request, settings
    )
    if (
        _reload_request_is_current(runtime, request)
        and not getattr(reservation, "error", "")
    ):
        request["shared_enqueued"] = True
    if not _reload_request_is_current(runtime, request) or request.get("paused"):
        dropped = await _drop_reserve_result(
            coordinator, refresh_identity, token, reservation
        )
        if dropped:
            _mark_paused_request_clean(runtime, request)
        elif _reload_request_is_current(runtime, request) and request.get("paused"):
            request["pause_cleanup_done"] = False
            request["pause_cleanup_retry_at"] = (
                time.monotonic() + UTK_PAUSE_CLEANUP_RETRY_SECONDS
            )
        return False
    if not reservation.reserved:
        _reservation_retry(request, reservation, debug)
        return False
    request["phase"] = "reserved"
    request["reservation_id"] = reservation.reservation_id
    return await _execute_reserved_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, coordinator,
        reservation, request, allow_unknown_document, allow_blocked_recovery,
        deadline or None, debug,
    )


async def reload_wait(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, config_dict, reason, *,
    apply_local_interval=False, allow_unknown_document=True,
    allow_blocked_recovery=True,
):
    """以短間隔等待啟動或備援流程所需的一次刷新。"""
    source_url = str(getattr(getattr(tab, "target", None), "url", ""))
    deadline = time.monotonic() + UTK_RELOAD_WAIT_MAX_SECONDS
    last_heartbeat_at = 0.0
    while True:
        if time.monotonic() >= deadline:
            request = runtime["refresh"]["reload_request"]
            if _reload_request_is_awaiting(request) and await reload(
                tab, runtime, refresh_identity, cancel_tasks, config_dict,
                reason, apply_local_interval=apply_local_interval,
                allow_unknown_document=allow_unknown_document,
                allow_blocked_recovery=allow_blocked_recovery,
                dispatch_deadline=deadline,
            ):
                return True
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "shared reload wait timed out", wait=True, abandon_probe=True,
            )
            util.create_debug_logger(config_dict).log(
                "[UTK RATE] Shared reload wait timed out; leaving the page "
                "unchanged instead of bypassing the coordinator"
            )
            return False
        heartbeat_now = time.time()
        if heartbeat_now - last_heartbeat_at >= 5.0:
            last_heartbeat_at = heartbeat_now
            try:
                with open(
                    util.get_instance_state_path("heartbeat.txt"),
                    "w", encoding="utf-8",
                ) as heartbeat_file:
                    heartbeat_file.write(str(int(heartbeat_now)))
            except OSError:
                pass
        if await reload(
            tab, runtime, refresh_identity, cancel_tasks, config_dict, reason,
            apply_local_interval=apply_local_interval,
            allow_unknown_document=allow_unknown_document,
            allow_blocked_recovery=allow_blocked_recovery,
            dispatch_deadline=deadline,
        ):
            return True
        if await check_and_handle_pause(config_dict):
            await pause_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "paused",
            )
        if await check_and_handle_quit(config_dict):
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "stopped", wait=True, abandon_probe=True,
            )
            return False
        current_url = str(getattr(getattr(tab, "target", None), "url", ""))
        request = runtime["refresh"]["reload_request"]
        if current_url != source_url and not _reload_request_is_awaiting(request):
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "navigation", wait=True, abandon_probe=True,
            )
            return False
        await asyncio.sleep(0.1)


async def health_check(
    tab, runtime: TabRuntime, refresh_identity, cancel_tasks, url, config_dict,
):
    """只執行限流復原健康檢查，不選擇任何票券控制項。"""

    guard_state, _ = await _utk_document_guard(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, url
    )
    request = runtime["refresh"]["reload_request"]
    if (
        request is not None
        and request.get("phase") == "completed"
        and runtime["refresh"]["recovery_probe"] is None
    ):
        still_paused = bool(
            request.get("paused")
            and await check_and_handle_pause(config_dict)
        )
        if not still_paused:
            request["paused"] = False
            _consume_completed_reload(runtime, request)
    if guard_state == "blocked":
        await reload(
            tab, runtime, refresh_identity, cancel_tasks, config_dict,
            "HTTP 429 recovery probe", apply_local_interval=False,
            allow_unknown_document=False, allow_blocked_recovery=True,
        )
    return guard_state
