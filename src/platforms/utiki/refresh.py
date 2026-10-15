#encoding=utf-8
"""uTiki document health, shared refresh pacing, and 429 recovery.

Mutable state is supplied by ``workflow``.  This module owns refresh behavior,
but deliberately owns no tab registry or parallel runtime.
"""

import asyncio
import contextlib
import glob
import json
import math
import os
import re
import time
import uuid

import util
from global_settings import get_global_settings_path, load_global_settings
from nodriver_common import check_and_handle_pause, check_and_handle_quit
from refresh_coordinator import UTK_SPORTS_SCOPE, get_shared_refresh_coordinator

from .profiles import PageKind, classify_utiki_page


UTK_RECOVERY_PROBE_RENEW_SECONDS = 5.0
UTK_RECOVERY_PROBE_MAX_SECONDS = 120.0
UTK_RELOAD_WAIT_MAX_SECONDS = 30.0
UTK_RELOAD_FINAL_OBSERVATION_SECONDS = 0.25
UTK_EMERGENCY_MARKER_SECONDS = 6.0
UTK_PAUSE_CLEANUP_RETRY_SECONDS = 0.5
UTK_EMERGENCY_PROCESS_TOKEN = uuid.uuid4().hex


def build_refresh_identity(tab_identity):
    """為單一標準分頁建立程序內唯一的刷新協調識別。"""
    return f"{util.get_instance_id()}:{os.getpid()}:{tab_identity}"


def _refresh_settings(*, force_refresh=False):
    """依強制刷新設定建立節流參數。"""

    settings_path = get_global_settings_path() if force_refresh else None
    return load_global_settings(settings_path).get("utk_sports", {})


def _emergency_marker_path():
    """取得跨程序緊急限流標記的檔案路徑。"""

    return os.path.join(
        util.get_app_root(),
        ".runtime",
        "utk_sports_emergency_"
        f"{os.getpid()}_{UTK_EMERGENCY_PROCESS_TOKEN}_{uuid.uuid4().hex}.until",
    )


def _mark_utk_emergency_window(seconds=UTK_EMERGENCY_MARKER_SECONDS):
    """在 429 資料庫回報完成前盡力標記跨程序緊急停止區間。"""
    marker_path = _emergency_marker_path()
    marker_dir = os.path.dirname(marker_path)
    temp_path = f"{marker_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        os.makedirs(marker_dir, exist_ok=True)
        deadline = time.time() + max(float(seconds), UTK_EMERGENCY_MARKER_SECONDS)
        with open(temp_path, "w", encoding="utf-8") as marker_file:
            marker_file.write(str(deadline))
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temp_path, marker_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(temp_path)


def _utk_emergency_remaining():
    """計算跨程序緊急停止區間的剩餘秒數。"""

    pattern = os.path.join(
        util.get_app_root(), ".runtime", "utk_sports_emergency_*.until"
    )
    deadline = 0.0
    now = time.time()
    for marker_path in glob.glob(pattern):
        try:
            with open(marker_path, encoding="utf-8") as marker_file:
                marker_deadline = float(marker_file.read().strip())
        except (OSError, TypeError, ValueError):
            with contextlib.suppress(OSError):
                os.remove(marker_path)
            continue
        if marker_deadline <= now:
            with contextlib.suppress(OSError):
                os.remove(marker_path)
            continue
        deadline = max(deadline, marker_deadline)
    return max(deadline - now, 0.0)


def _classify_utk_document_state(snapshot):
    """依文件快照判別正常、載入中或限流狀態。"""

    if not isinstance(snapshot, dict) or not snapshot.get("ready"):
        return "unknown"
    body_text = str(snapshot.get("body_text") or "")
    has_code = bool(
        re.search(r"(^|\s|[:：])(?:HTTP\s*)?429(?:\s|[-:：]|$)", body_text, re.I)
    )
    has_message = bool(re.search(r"請求頻率過高|Too Many Requests", body_text, re.I))
    if has_code and has_message and not snapshot.get("has_normal_root"):
        return "blocked"
    if snapshot.get("has_normal_root"):
        return "normal"
    if not snapshot.get("requires_normal_root", True):
        return "unverified"
    return "unknown"


def _decode_json_result(raw_value):
    """將瀏覽器回傳值解碼為 JSON 資料。"""

    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _canonical_document_token(value):
    """取得非零的標準文件時間來源識別，無效時採保守失敗。"""
    token = str(value or "").strip()
    try:
        numeric = float(token)
    except (TypeError, ValueError):
        return ""
    return token if math.isfinite(numeric) and numeric > 0 else ""


async def _inspect_utk_document(tab, runtime, url=None, *, force=False):
    """判別目前文件，避免把載入中狀態誤認為 HTTP 429。"""
    current_url = str(url or getattr(getattr(tab, "target", None), "url", ""))
    document = runtime["document"]
    cached = document["snapshot"]
    if (
        not force
        and cached
        and cached.get("url") == current_url
        and time.monotonic() - cached.get("checked_at", 0.0) < 0.2
    ):
        return dict(cached["snapshot"])
    normal_selector = {
        PageKind.DATE: "#PerformanceListTable",
        PageKind.AREA: ".seats, #mapdata",
        PageKind.RESERVED_SEAT: "#TBL, .ticket",
        PageKind.GENERAL_ADMISSION: "#table_tickettype, .ticket",
        PageKind.SERVER_ASSIGNED_QUANTITY: "#table_tickettype",
    }.get(classify_utiki_page(current_url))
    selector_expression = (
        f"!!document.querySelector({json.dumps(normal_selector)})"
        if normal_selector else "false"
    )
    try:
        raw_value = await tab.evaluate(f'''
            (() => {{
                const ready = document.readyState === 'interactive' ||
                    document.readyState === 'complete';
                const bodyText = document.body
                    ? (document.body.innerText || document.body.textContent || '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 12000)
                    : '';
                const normalRoot = {selector_expression};
                const timeOrigin = Number(performance.timeOrigin ||
                    (performance.timing && performance.timing.navigationStart) || 0);
                const documentToken = Number.isFinite(timeOrigin) && timeOrigin > 0
                    ? String(timeOrigin) : '';
                return JSON.stringify({{
                    ready,
                    body_text: bodyText,
                    document_id: documentToken,
                    document_started_at: timeOrigin > 0 ? timeOrigin / 1000 : 0,
                    url: location.href,
                    ready_state: document.readyState,
                    has_normal_root: normalRoot,
                    requires_normal_root: {str(bool(normal_selector)).lower()}
                }});
            }})()
        ''')
        snapshot = _decode_json_result(raw_value)
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        return {
            "classification": "unknown", "document_id": "",
            "document_started_at": 0.0, "url": current_url,
            "ready_state": "", "has_normal_root": False,
            "requires_normal_root": bool(normal_selector),
        }
    snapshot["document_id"] = _canonical_document_token(
        snapshot.get("document_id")
    )
    try:
        snapshot["document_started_at"] = max(
            float(snapshot.get("document_started_at") or 0.0), 0.0
        )
    except (TypeError, ValueError):
        snapshot["document_started_at"] = 0.0
    snapshot["url"] = str(snapshot.get("url") or current_url)
    snapshot["classification"] = (
        _classify_utk_document_state(snapshot)
        if snapshot["document_id"] else "unknown"
    )
    document["snapshot"] = {
        "url": snapshot["url"], "checked_at": time.monotonic(),
        "snapshot": dict(snapshot),
    }
    return snapshot


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


def _pause_cleanup_due(record):
    """判斷暫停中的刷新紀錄是否已到清理時間。"""

    return bool(
        record and not record.get("pause_cleanup_done", False)
        and time.monotonic() >= float(record.get("pause_cleanup_retry_at") or 0.0)
    )


async def _abandon_recovery_probe(
    runtime, refresh_identity, cancel_tasks, coordinator, probe,
):
    """放棄復原探測並安排其資源清理。"""

    lease_task = probe.get("lease_task")
    if lease_task is not None and lease_task is not asyncio.current_task():
        if not lease_task.done():
            lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
        cancel_tasks.discard(lease_task)
    completion = await coordinator.complete_recovery_probe(
        UTK_SPORTS_SCOPE, refresh_identity,
        probe.get("permit_id", ""), success=False,
    )
    if completion.accepted or "not owned" in str(completion.error or ""):
        current = runtime["refresh"]["recovery_probe"]
        if current and current.get("permit_id") == probe.get("permit_id"):
            runtime["refresh"]["recovery_probe"] = None
    return bool(completion.accepted)


async def _retry_abandoned_probe_cleanup(
    runtime, refresh_identity, cancel_tasks, coordinator=None,
):
    """重試清理先前放棄的復原探測。"""

    probe = runtime["refresh"]["recovery_probe"]
    if not probe or not probe.get("abandon_requested"):
        return False, False
    if not _pause_cleanup_due(probe):
        return True, False
    coordinator = coordinator or get_shared_refresh_coordinator()
    accepted = await _abandon_recovery_probe(
        runtime, refresh_identity, cancel_tasks, coordinator, probe
    )
    if runtime["refresh"]["recovery_probe"] is not probe:
        return False, True
    probe["pause_cleanup_retry_at"] = (
        time.monotonic() + UTK_PAUSE_CLEANUP_RETRY_SECONDS
    )
    return True, accepted


async def cancel_refresh(
    tab, runtime, refresh_identity, cancel_tasks, reason="", *,
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
                changed = await _abandon_recovery_probe(
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
    tab, runtime, refresh_identity, cancel_tasks, reason="",
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
        if not _reload_request_is_awaiting(request) and _pause_cleanup_due(request):
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
        _, probe_changed = await _retry_abandoned_probe_cleanup(
            runtime, refresh_identity, cancel_tasks, coordinator
        )
        changed = probe_changed or changed
    return bool(changed)


def consume_reload_event(runtime):
    """消費一次需由外層路由重新偵測的同網址刷新事件。"""

    refresh = runtime["refresh"]
    if not refresh["reload_event"]:
        return False
    refresh["reload_event"] = False
    return True


async def _report_utk_rate_limit(
    tab, runtime, refresh_identity, config_dict, document_id,
    probe_permit_id=None, document_started_at=0.0, suspected=False,
):
    """回報 uTiki 限流狀態並建立復原探測資訊。"""

    settings = _refresh_settings()
    if not settings.get("enabled", True):
        return None
    refresh = runtime["refresh"]
    coordinator = get_shared_refresh_coordinator()
    _mark_utk_emergency_window()
    report = await coordinator.report_rate_limited(
        UTK_SPORTS_SCOPE, refresh_identity,
        initial_cooldown_seconds=settings.get("initial_cooldown_seconds", 60),
        max_cooldown_seconds=settings.get("max_cooldown_seconds", 240),
        probe_permit_id=probe_permit_id,
        document_started_at=document_started_at,
    )
    debug = util.create_debug_logger(config_dict)
    if report.accepted:
        refresh["rate_limit_observation"] = document_id
        refresh["rate_limit_retry"] = 0.0
    else:
        retry_seconds = max(float(report.retry_after_seconds or 1.0), 0.5)
        refresh["rate_limit_retry"] = time.monotonic() + retry_seconds
        _mark_utk_emergency_window(retry_seconds + 1.0)
    if report.accepted and report.new_incident:
        if suspected:
            debug.log(
                "[UTK RATE] Ticket page stayed unrecognizable; starting a "
                f"shared safety cooldown for {report.retry_after_seconds:.1f}s"
            )
        else:
            debug.log(
                "[UTK RATE] HTTP 429 detected; all baseball instances cooling down "
                f"for {report.retry_after_seconds:.1f}s "
                f"(level {report.consecutive_incidents})"
            )
    elif report.error:
        debug.log(f"[UTK RATE] Failed to record HTTP 429 safely: {report.error}")
    return report


async def _clear_completed_recovery_probe(runtime, cancel_tasks, probe):
    """清除已完成復原探測的本機狀態與工作。"""

    if runtime["refresh"]["recovery_probe"] is not probe:
        return False
    lease_task = probe.get("lease_task")
    if lease_task is not None and lease_task is not asyncio.current_task():
        if not lease_task.done():
            lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
        cancel_tasks.discard(lease_task)
    if runtime["refresh"]["recovery_probe"] is not probe:
        return False
    runtime["refresh"]["recovery_probe"] = None
    return True


async def _finish_utk_recovery_probe(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, snapshot,
):
    """僅在新文件變為正常或確認限流後完成復原探測。"""
    refresh = runtime["refresh"]
    probe = refresh["recovery_probe"]
    if not probe:
        return False
    now = time.monotonic()
    probe.setdefault("started_at", now)
    probe.setdefault("next_renew_at", now + UTK_RECOVERY_PROBE_RENEW_SECONDS)
    coordinator = get_shared_refresh_coordinator()
    if now - probe.get("started_at", now) >= UTK_RECOVERY_PROBE_MAX_SECONDS:
        await coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE, refresh_identity,
            probe.get("permit_id", ""), success=False,
        )
        await _clear_completed_recovery_probe(
            runtime, cancel_tasks, probe
        )
        return True
    if not probe.get("lease_task") and now >= probe.get("next_renew_at", 0.0):
        renewed = await coordinator.renew_recovery_probe(
            UTK_SPORTS_SCOPE, probe.get("permit_id", "")
        )
        if renewed is False:
            refresh["recovery_probe"] = None
            return False
        probe["next_renew_at"] = now + (
            UTK_RECOVERY_PROBE_RENEW_SECONDS if renewed else 1.0
        )
    if snapshot.get("document_id") == probe.get("source_document_id"):
        return False
    classification = snapshot.get("classification")
    if classification == "unknown":
        return False
    if time.monotonic() < refresh["rate_limit_retry"]:
        return True
    if classification == "blocked":
        report = await _report_utk_rate_limit(
            tab, runtime, refresh_identity, config_dict,
            snapshot.get("document_id", ""),
            probe_permit_id=probe.get("permit_id"),
            document_started_at=snapshot.get("document_started_at", 0.0),
        )
        if report and report.accepted:
            await _clear_completed_recovery_probe(
                runtime, cancel_tasks, probe
            )
        return True
    settings = _refresh_settings()
    if settings.get("enabled", True):
        completion = await coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE, refresh_identity, probe.get("permit_id", ""),
            success=(classification == "normal"),
        )
        if completion.accepted:
            await _clear_completed_recovery_probe(
                runtime, cancel_tasks, probe
            )
            if classification == "normal":
                util.create_debug_logger(config_dict).log(
                    "[UTK RATE] Recovery probe succeeded; shared refresh resumed"
                )
        elif "not owned" in str(completion.error or ""):
            await _clear_completed_recovery_probe(
                runtime, cancel_tasks, probe
            )
        else:
            refresh["rate_limit_retry"] = time.monotonic() + 1.0
    return True


async def _utk_document_guard(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, url=None,
):
    """在任何 uTiki 頁面操作前套用 429 斷路保護。"""
    settings = _refresh_settings()
    snapshot = await _inspect_utk_document(tab, runtime, url)
    document = runtime["document"]
    refresh = runtime["refresh"]
    cleanup_pending, _ = await _retry_abandoned_probe_cleanup(
        runtime, refresh_identity, cancel_tasks
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
            cleanup_pending, _ = await _retry_abandoned_probe_cleanup(
                runtime, refresh_identity, cancel_tasks
            )
            if cleanup_pending:
                return "unknown", snapshot
        request = refresh["reload_request"]
        if request and request.get("phase") == "awaiting_document":
            return "unknown", snapshot
        return "disabled", snapshot
    probe_handled = await _finish_utk_recovery_probe(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, snapshot
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
            await _report_utk_rate_limit(
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
        await _report_utk_rate_limit(
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


async def _renew_probe_lease(runtime, refresh_identity, permit_id, started_at):
    """延長目前復原探測許可的租期。"""

    refresh = runtime["refresh"]
    while True:
        await asyncio.sleep(UTK_RECOVERY_PROBE_RENEW_SECONDS)
        current = refresh["recovery_probe"]
        if (
            not current or current.get("permit_id") != permit_id
            or current.get("abandon_requested")
        ):
            return
        coordinator = get_shared_refresh_coordinator()
        if time.monotonic() - started_at >= UTK_RECOVERY_PROBE_MAX_SECONDS:
            await coordinator.complete_recovery_probe(
                UTK_SPORTS_SCOPE, refresh_identity, permit_id, success=False,
            )
            current = refresh["recovery_probe"]
            if current and current.get("permit_id") == permit_id:
                refresh["recovery_probe"] = None
            return
        renewed = await coordinator.renew_recovery_probe(
            UTK_SPORTS_SCOPE, permit_id
        )
        if renewed is False:
            current = refresh["recovery_probe"]
            if current and current.get("permit_id") == permit_id:
                refresh["recovery_probe"] = None
            return
        current["next_renew_at"] = time.monotonic() + (
            UTK_RECOVERY_PROBE_RENEW_SECONDS if renewed else 1.0
        )


def _install_recovery_probe(
    runtime, refresh_identity, cancel_tasks, decision, preflight,
):
    """將協調器核發的復原探測安裝至本機執行期。"""

    if not decision.is_recovery_probe:
        return
    started_at = time.monotonic()
    probe = {
        "permit_id": decision.permit_id,
        "source_document_id": preflight.get("document_id"),
        "started_at": started_at,
        "next_renew_at": started_at + UTK_RECOVERY_PROBE_RENEW_SECONDS,
    }
    runtime["refresh"]["recovery_probe"] = probe
    lease_task = asyncio.create_task(_renew_probe_lease(
        runtime, refresh_identity, decision.permit_id, started_at
    ))
    probe["lease_task"] = lease_task
    cancel_tasks.add(lease_task)
    lease_task.add_done_callback(cancel_tasks.discard)


def _install_abandoned_probe_cleanup(runtime, decision, preflight):
    """登記失去所有權之復原探測的後續清理。"""

    now = time.monotonic()
    probe = {
        "permit_id": decision.permit_id,
        "source_document_id": preflight.get("document_id"),
        "started_at": now,
        "next_renew_at": now + UTK_RECOVERY_PROBE_RENEW_SECONDS,
        "abandon_requested": True,
        "pause_cleanup_done": False,
        "pause_cleanup_retry_at": 0.0,
    }
    runtime["refresh"]["recovery_probe"] = probe
    return probe


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
            _install_recovery_probe(
                runtime, refresh_identity, cancel_tasks, decision, preflight
            )
            return False
        if decision.granted and decision.is_recovery_probe:
            _install_abandoned_probe_cleanup(runtime, decision, preflight)
            await _retry_abandoned_probe_cleanup(
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
                _install_recovery_probe(
                    runtime, refresh_identity, cancel_tasks, decision, preflight
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
    _install_recovery_probe(
        runtime, refresh_identity, cancel_tasks, decision, preflight
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
    tab, runtime, refresh_identity, cancel_tasks, config_dict, reason, *,
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
    tab, runtime, refresh_identity, cancel_tasks, config_dict, reason, *,
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
    tab, runtime, refresh_identity, cancel_tasks, url, config_dict,
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
