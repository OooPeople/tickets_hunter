#encoding=utf-8
"""uTiki document health, shared refresh pacing, and 429 recovery.

Mutable state is supplied by ``workflow``.  This module owns refresh behavior,
but deliberately owns no tab registry or parallel runtime.
"""

import asyncio
import contextlib
import glob
import json
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
UTK_EMERGENCY_MARKER_SECONDS = 6.0
UTK_EMERGENCY_PROCESS_TOKEN = uuid.uuid4().hex


def build_refresh_identity(tab_identity):
    """Build the process-unique coordinator identity for one canonical tab."""
    return f"{util.get_instance_id()}:{os.getpid()}:{tab_identity}"


def _refresh_settings(*, force_refresh=False):
    settings_path = get_global_settings_path() if force_refresh else None
    return load_global_settings(settings_path).get("utk_sports", {})


def _emergency_marker_path():
    return os.path.join(
        util.get_app_root(),
        ".runtime",
        "utk_sports_emergency_"
        f"{os.getpid()}_{UTK_EMERGENCY_PROCESS_TOKEN}_{uuid.uuid4().hex}.until",
    )


def _mark_utk_emergency_window(seconds=UTK_EMERGENCY_MARKER_SECONDS):
    """Best-effort cross-process stopgap while a 429 DB report is pending."""
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
    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


async def _inspect_utk_document(tab, runtime, url=None, *, force=False):
    """Classify the current document without mistaking loading for HTTP 429."""
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
                return JSON.stringify({{
                    ready,
                    body_text: bodyText,
                    document_id: `${{location.href}}|${{timeOrigin}}`,
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
            "classification": "unknown", "document_id": current_url,
            "document_started_at": 0.0, "url": current_url,
            "ready_state": "", "has_normal_root": False,
            "requires_normal_root": bool(normal_selector),
        }
    snapshot["classification"] = _classify_utk_document_state(snapshot)
    snapshot["document_id"] = str(snapshot.get("document_id") or current_url)
    try:
        snapshot["document_started_at"] = max(
            float(snapshot.get("document_started_at") or 0.0), 0.0
        )
    except (TypeError, ValueError):
        snapshot["document_started_at"] = 0.0
    snapshot["url"] = str(snapshot.get("url") or current_url)
    document["snapshot"] = {
        "url": snapshot["url"], "checked_at": time.monotonic(),
        "snapshot": dict(snapshot),
    }
    return snapshot


def _pop_reload_request_if_token(runtime, request_token):
    """Remove only the logical request captured by the current coroutine."""
    refresh = runtime["refresh"]
    current = refresh["reload_request"]
    if current and current.get("request_token") == request_token:
        refresh["reload_request"] = None
        return current
    return None


async def cancel_refresh(
    tab, runtime, refresh_identity, cancel_tasks, reason="", *,
    wait=False, abandon_probe=False,
):
    """Cancel pending work and optionally release an owned recovery probe."""
    refresh = runtime["refresh"]
    request = refresh["reload_request"]
    refresh["reload_request"] = None
    probe = refresh["recovery_probe"] if abandon_probe else None
    if probe is not None:
        probe["abandon_requested"] = True
    if request is None and probe is None:
        return False
    coordinator = get_shared_refresh_coordinator()

    async def cancel_all():
        changed = False
        lease_task = probe.get("lease_task") if probe is not None else None
        if lease_task is not None and lease_task is not asyncio.current_task():
            if not lease_task.done():
                lease_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lease_task
            cancel_tasks.discard(lease_task)
        if request is not None:
            changed = await coordinator.cancel_request(
                UTK_SPORTS_SCOPE, refresh_identity,
                request_token=request.get("request_token"),
            ) or changed
        if probe is not None:
            completion = await coordinator.complete_recovery_probe(
                UTK_SPORTS_SCOPE, refresh_identity,
                probe.get("permit_id", ""), success=False,
            )
            if completion.accepted or "not owned" in str(completion.error or ""):
                current = refresh["recovery_probe"]
                if current and current.get("permit_id") == probe.get("permit_id"):
                    refresh["recovery_probe"] = None
            changed = bool(completion.accepted) or changed
        return changed

    cancel_call = cancel_all()
    if wait:
        return await cancel_call
    task = asyncio.create_task(cancel_call)
    cancel_tasks.add(task)
    task.add_done_callback(cancel_tasks.discard)
    return True


def consume_reload_event(runtime):
    refresh = runtime["refresh"]
    if not refresh["reload_event"]:
        return False
    refresh["reload_event"] = False
    return True


async def _report_utk_rate_limit(
    tab, runtime, refresh_identity, config_dict, document_id,
    probe_permit_id=None, document_started_at=0.0, suspected=False,
):
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


async def _finish_utk_recovery_probe(
    tab, runtime, refresh_identity, config_dict, snapshot,
):
    """Resolve a probe only after a different document becomes normal or 429."""
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
        current = refresh["recovery_probe"]
        if current and current.get("permit_id") == probe.get("permit_id"):
            refresh["recovery_probe"] = None
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
            refresh["recovery_probe"] = None
        return True
    settings = _refresh_settings()
    if settings.get("enabled", True):
        completion = await coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE, refresh_identity, probe.get("permit_id", ""),
            success=(classification == "normal"),
        )
        if completion.accepted:
            refresh["recovery_probe"] = None
            if classification == "normal":
                util.create_debug_logger(config_dict).log(
                    "[UTK RATE] Recovery probe succeeded; shared refresh resumed"
                )
        elif "not owned" in str(completion.error or ""):
            refresh["recovery_probe"] = None
        else:
            refresh["rate_limit_retry"] = time.monotonic() + 1.0
    return True


async def _utk_document_guard(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, url=None,
):
    """Apply the 429 circuit breaker before any uTiki page action."""
    settings = _refresh_settings()
    snapshot = await _inspect_utk_document(tab, runtime, url)
    document = runtime["document"]
    refresh = runtime["refresh"]
    document_id = snapshot.get("document_id")
    previous_document_id = document["last_document"]
    if previous_document_id and previous_document_id != document_id:
        refresh["reload_event"] = True
    document["last_document"] = document_id
    if not settings.get("enabled", True):
        return "disabled", snapshot
    probe_handled = await _finish_utk_recovery_probe(
        tab, runtime, refresh_identity, config_dict, snapshot
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
        if not request or not request.get("allow_blocked_recovery") or stale:
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
    if request and (
        request.get("expected_url") != snapshot.get("url")
        or request.get("document_id") != snapshot.get("document_id")
    ):
        await cancel_refresh(
            tab, runtime, refresh_identity, cancel_tasks,
            "document changed", wait=True,
        )
    return classification, snapshot


async def _reload_without_shared_protection(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, debug,
    apply_local_interval,
):
    await cancel_refresh(
        tab, runtime, refresh_identity, cancel_tasks,
        "shared protection disabled", wait=True, abandon_probe=True,
    )
    interval = config_dict.get("advanced", {}).get(
        "auto_reload_page_interval", 0.0
    )
    if apply_local_interval:
        delay = float(interval) if interval and interval > 0 else 1.0
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
                return False
            await asyncio.sleep(min(0.25, deadline - time.monotonic()))
    if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
        return False
    if _refresh_settings(force_refresh=True).get("enabled", True):
        return False
    try:
        await tab.reload()
        runtime["document"]["snapshot"] = None
        runtime["refresh"]["reload_event"] = True
        return True
    except Exception as exc:
        debug.log(f"[CTBC] Reload failed: {exc}")
        return False


async def _guard_for_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, deadline,
):
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


def _get_reload_request(
    runtime, snapshot, config_dict, reason, apply_local_interval,
    allow_blocked_recovery, debug,
):
    refresh = runtime["refresh"]
    request = refresh["reload_request"]
    if request is not None:
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
        "expected_url": snapshot.get("url"),
        "eligible_at": time.monotonic() + local_delay,
        "next_poll_at": 0.0,
        "reason": reason,
        "allow_blocked_recovery": bool(allow_blocked_recovery),
        "request_token": uuid.uuid4().hex,
    }
    refresh["reload_request"] = request
    debug.log(f"[CTBC] {reason}; queued for shared refresh")
    return request


def _reservation_retry(request, reservation, debug):
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
    return await coordinator.release_refresh(
        UTK_SPORTS_SCOPE, refresh_identity, reservation.reservation_id,
        request_token=request_token, drop_request=True,
    )


async def _read_reserved_preflight(
    tab, runtime, coordinator, refresh_identity, reservation,
    request_token, deadline,
):
    try:
        if deadline is None:
            return await _inspect_utk_document(tab, runtime, force=True)
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            await _release_reservation(
                coordinator, refresh_identity, reservation, request_token
            )
            _pop_reload_request_if_token(runtime, request_token)
            return None
        return await asyncio.wait_for(
            _inspect_utk_document(tab, runtime, force=True), timeout=remaining
        )
    except asyncio.TimeoutError:
        await _release_reservation(
            coordinator, refresh_identity, reservation, request_token
        )
        _pop_reload_request_if_token(runtime, request_token)
        return None
    except BaseException:
        with contextlib.suppress(BaseException):
            await asyncio.shield(_release_reservation(
                coordinator, refresh_identity, reservation, request_token
            ))
        _pop_reload_request_if_token(runtime, request_token)
        raise


async def _preflight_allows_reload(
    tab, runtime, refresh_identity, config_dict, request, reservation,
    preflight, allow_unknown_document, allow_blocked_recovery,
):
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


async def _commit_reserved_reload(
    tab, runtime, refresh_identity, cancel_tasks, coordinator, reservation,
    request_token, preflight, debug,
):
    try:
        decision = await coordinator.commit_refresh(
            UTK_SPORTS_SCOPE, refresh_identity, reservation.reservation_id,
            request_token=request_token,
        )
    except BaseException:
        with contextlib.suppress(BaseException):
            await asyncio.shield(_release_reservation(
                coordinator, refresh_identity, reservation, request_token
            ))
        _pop_reload_request_if_token(runtime, request_token)
        raise
    if not decision.granted:
        if decision.status != "already_committed":
            await _release_reservation(
                coordinator, refresh_identity, reservation, request_token
            )
        _pop_reload_request_if_token(runtime, request_token)
        if decision.error:
            debug.log(f"[UTK RATE] Shared commit failed closed: {decision.error}")
        return False
    _install_recovery_probe(
        runtime, refresh_identity, cancel_tasks, decision, preflight
    )
    _pop_reload_request_if_token(runtime, request_token)
    try:
        await asyncio.wait_for(
            tab.reload(), timeout=UTK_RECOVERY_PROBE_MAX_SECONDS
        )
        runtime["document"]["snapshot"] = None
        runtime["refresh"]["reload_event"] = True
        return True
    except Exception as exc:
        debug.log(f"[CTBC] Reload failed: {exc}")
        return False


async def _execute_reserved_reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, coordinator,
    reservation, request, allow_unknown_document, allow_blocked_recovery,
    deadline, debug,
):
    token = request.get("request_token")
    preflight = await _read_reserved_preflight(
        tab, runtime, coordinator, refresh_identity, reservation, token, deadline
    )
    if preflight is None:
        return False
    allowed = await _preflight_allows_reload(
        tab, runtime, refresh_identity, config_dict, request, reservation,
        preflight, allow_unknown_document, allow_blocked_recovery,
    )
    if (
        not allowed
        or await check_and_handle_pause(config_dict)
        or await check_and_handle_quit(config_dict)
        or _utk_emergency_remaining() > 0
        or (deadline is not None and time.monotonic() >= deadline)
    ):
        await _release_reservation(
            coordinator, refresh_identity, reservation, token
        )
        _pop_reload_request_if_token(runtime, token)
        return False
    return await _commit_reserved_reload(
        tab, runtime, refresh_identity, cancel_tasks, coordinator, reservation,
        token, preflight, debug,
    )


async def reload(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, reason, *,
    apply_local_interval=True, allow_unknown_document=False,
    allow_blocked_recovery=False, dispatch_deadline=None,
):
    """Poll once and reload only after obtaining the shared one-shot permit."""
    debug = util.create_debug_logger(config_dict)
    settings = _refresh_settings()
    if not settings.get("enabled", True):
        return await _reload_without_shared_protection(
            tab, runtime, refresh_identity, cancel_tasks, config_dict, debug,
            apply_local_interval,
        )
    guarded = await _guard_for_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict,
        dispatch_deadline,
    )
    if guarded is None:
        return False
    classification, snapshot = guarded
    if classification == "blocked" and not allow_blocked_recovery:
        return False
    if classification == "unknown" and not allow_unknown_document:
        return False
    request = _get_reload_request(
        runtime, snapshot, config_dict, reason, apply_local_interval,
        allow_blocked_recovery, debug,
    )
    now = time.monotonic()
    if now < request.get("eligible_at", 0.0) or now < request.get("next_poll_at", 0.0):
        return False
    emergency_remaining = _utk_emergency_remaining()
    if emergency_remaining > 0:
        request["next_poll_at"] = time.monotonic() + min(emergency_remaining, 0.5)
        return False
    token = request.get("request_token")
    if dispatch_deadline is not None and time.monotonic() >= dispatch_deadline:
        _pop_reload_request_if_token(runtime, token)
        return False
    coordinator = get_shared_refresh_coordinator()
    reservation = await coordinator.reserve_refresh(
        UTK_SPORTS_SCOPE, refresh_identity,
        min_interval_seconds=settings.get("min_interval_seconds", 1.5),
        jitter_range_seconds=(0.1, 0.3), request_token=token,
    )
    if not reservation.reserved:
        _reservation_retry(request, reservation, debug)
        return False
    return await _execute_reserved_reload(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, coordinator,
        reservation, request, allow_unknown_document, allow_blocked_recovery,
        dispatch_deadline, debug,
    )


async def reload_wait(
    tab, runtime, refresh_identity, cancel_tasks, config_dict, reason, *,
    apply_local_interval=False, allow_unknown_document=True,
    allow_blocked_recovery=True,
):
    """Wait in short chunks for startup/fallback callers needing one reload."""
    source_url = str(getattr(getattr(tab, "target", None), "url", ""))
    deadline = time.monotonic() + UTK_RELOAD_WAIT_MAX_SECONDS
    last_heartbeat_at = 0.0
    while True:
        if time.monotonic() >= deadline:
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
        if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "paused or stopped", wait=True, abandon_probe=True,
            )
            return False
        current_url = str(getattr(getattr(tab, "target", None), "url", ""))
        if current_url != source_url:
            await cancel_refresh(
                tab, runtime, refresh_identity, cancel_tasks,
                "navigation", wait=True, abandon_probe=True,
            )
            return False
        await asyncio.sleep(0.1)


async def health_check(
    tab, runtime, refresh_identity, cancel_tasks, url, config_dict,
):
    guard_state, _ = await _utk_document_guard(
        tab, runtime, refresh_identity, cancel_tasks, config_dict, url
    )
    if guard_state == "blocked":
        await reload(
            tab, runtime, refresh_identity, cancel_tasks, config_dict,
            "HTTP 429 recovery probe", apply_local_interval=False,
            allow_unknown_document=False, allow_blocked_recovery=True,
        )
    return guard_state
