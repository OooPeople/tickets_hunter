# encoding=utf-8
"""Existing uTiki 429 observations, emergency markers, and recovery probes."""

import asyncio
import contextlib
import glob
import os
import time
import uuid

import util
from global_settings import get_global_settings_path, load_global_settings
from refresh_coordinator import UTK_SPORTS_SCOPE, get_shared_refresh_coordinator

UTK_RECOVERY_PROBE_RENEW_SECONDS = 5.0
UTK_RECOVERY_PROBE_MAX_SECONDS = 120.0
UTK_EMERGENCY_MARKER_SECONDS = 6.0
UTK_PAUSE_CLEANUP_RETRY_SECONDS = 0.5
UTK_EMERGENCY_PROCESS_TOKEN = uuid.uuid4().hex


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


async def _report_utk_rate_limit(
    tab,
    runtime,
    refresh_identity,
    config_dict,
    document_id,
    probe_permit_id=None,
    document_started_at=0.0,
    suspected=False,
):
    """回報 uTiki 限流狀態並建立復原探測資訊。"""

    settings = _refresh_settings()
    if not settings.get("enabled", True):
        return None
    refresh = runtime["refresh"]
    coordinator = get_shared_refresh_coordinator()
    _mark_utk_emergency_window()
    report = await coordinator.report_rate_limited(
        UTK_SPORTS_SCOPE,
        refresh_identity,
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


def _pause_cleanup_due(record):
    """判斷暫停中的刷新紀錄是否已到清理時間。"""

    return bool(
        record
        and not record.get("pause_cleanup_done", False)
        and time.monotonic() >= float(record.get("pause_cleanup_retry_at") or 0.0)
    )


async def _abandon_recovery_probe(
    runtime,
    refresh_identity,
    cancel_tasks,
    coordinator,
    probe,
):
    """放棄復原探測並安排其資源清理。"""

    lease_task = probe.get("lease_task")
    if lease_task is not None and lease_task is not asyncio.current_task():
        if not lease_task.done():
            lease_task.cancel()
        await asyncio.gather(lease_task, return_exceptions=True)
        cancel_tasks.discard(lease_task)
    completion = await coordinator.complete_recovery_probe(
        UTK_SPORTS_SCOPE,
        refresh_identity,
        probe.get("permit_id", ""),
        success=False,
    )
    if completion.accepted or "not owned" in str(completion.error or ""):
        current = runtime["refresh"]["recovery_probe"]
        if current and current.get("permit_id") == probe.get("permit_id"):
            runtime["refresh"]["recovery_probe"] = None
    return bool(completion.accepted)


async def _retry_abandoned_probe_cleanup(
    runtime,
    refresh_identity,
    cancel_tasks,
    coordinator=None,
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
    probe["pause_cleanup_retry_at"] = time.monotonic() + UTK_PAUSE_CLEANUP_RETRY_SECONDS
    return True, accepted


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
    tab,
    runtime,
    refresh_identity,
    cancel_tasks,
    config_dict,
    snapshot,
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
            UTK_SPORTS_SCOPE,
            refresh_identity,
            probe.get("permit_id", ""),
            success=False,
        )
        await _clear_completed_recovery_probe(runtime, cancel_tasks, probe)
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
            tab,
            runtime,
            refresh_identity,
            config_dict,
            snapshot.get("document_id", ""),
            probe_permit_id=probe.get("permit_id"),
            document_started_at=snapshot.get("document_started_at", 0.0),
        )
        if report and report.accepted:
            await _clear_completed_recovery_probe(runtime, cancel_tasks, probe)
        return True
    settings = _refresh_settings()
    if settings.get("enabled", True):
        completion = await coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE,
            refresh_identity,
            probe.get("permit_id", ""),
            success=(classification == "normal"),
        )
        if completion.accepted:
            await _clear_completed_recovery_probe(runtime, cancel_tasks, probe)
            if classification == "normal":
                util.create_debug_logger(config_dict).log(
                    "[UTK RATE] Recovery probe succeeded; shared refresh resumed"
                )
        elif "not owned" in str(completion.error or ""):
            await _clear_completed_recovery_probe(runtime, cancel_tasks, probe)
        else:
            refresh["rate_limit_retry"] = time.monotonic() + 1.0
    return True


async def _renew_probe_lease(
    runtime,
    refresh_identity,
    permit_id,
    started_at,
):
    """延長目前復原探測許可的租期。"""

    refresh = runtime["refresh"]
    while True:
        await asyncio.sleep(UTK_RECOVERY_PROBE_RENEW_SECONDS)
        current = refresh["recovery_probe"]
        if (
            not current
            or current.get("permit_id") != permit_id
            or current.get("abandon_requested")
        ):
            return
        coordinator = get_shared_refresh_coordinator()
        if time.monotonic() - started_at >= UTK_RECOVERY_PROBE_MAX_SECONDS:
            await coordinator.complete_recovery_probe(
                UTK_SPORTS_SCOPE,
                refresh_identity,
                permit_id,
                success=False,
            )
            current = refresh["recovery_probe"]
            if current and current.get("permit_id") == permit_id:
                refresh["recovery_probe"] = None
            return
        renewed = await coordinator.renew_recovery_probe(UTK_SPORTS_SCOPE, permit_id)
        if renewed is False:
            current = refresh["recovery_probe"]
            if current and current.get("permit_id") == permit_id:
                refresh["recovery_probe"] = None
            return
        current["next_renew_at"] = time.monotonic() + (
            UTK_RECOVERY_PROBE_RENEW_SECONDS if renewed else 1.0
        )


def _install_recovery_probe(
    runtime,
    refresh_identity,
    cancel_tasks,
    decision,
    preflight,
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
    lease_task = asyncio.create_task(
        _renew_probe_lease(
            runtime,
            refresh_identity,
            decision.permit_id,
            started_at,
        )
    )
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
