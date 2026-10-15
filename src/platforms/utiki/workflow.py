#encoding=utf-8
"""uTiki2 sports ticketing workflow.

The CTBC site uses the same UTK naming convention as a few older ticketing
sites. Keep all browser side effects and mutable runtime state in this module.
"""

import asyncio
import base64
import contextlib
import glob
import hashlib
import json
import os
import re
import time
import uuid
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from zendriver import cdp

import util
from global_settings import get_global_settings_path, load_global_settings
from nodriver_common import (
    check_and_handle_pause,
    check_and_handle_quit,
    nodriver_get_captcha_image_from_dom_snapshot,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
)
from refresh_coordinator import (
    UTK_SPORTS_SCOPE,
    get_shared_refresh_coordinator,
)
from .auth import (
    cookie_domain_is_in_scope,
    get_tenant_auth_profile,
    parse_ctbc_cookie_string,
)
from .dom import (
    CommitResult,
    SubmitDispatchResult,
    commit_area,
    commit_quantity,
    dismiss_dialog,
    dispatch_quantity_submit,
    read_area_snapshot,
    read_dialog_snapshot,
    read_quantity_snapshot,
)
from .profiles import (
    CTBC_MAP_DOMAIN,
    PageKind,
    TENANT_PROFILES,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
    same_origin,
    unique_query,
)
from .selection import (
    AreaActionPlan,
    AreaSnapshot,
    DialogSnapshot,
    QuantityPlan,
    QuantitySnapshot,
    choose_candidate_index as _choose_candidate_index,
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    select_area_snapshot as _select_ctbc_area_snapshot,
    select_quantity_snapshot,
)


__all__ = [
    "is_utk_sports_url",
    "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_consume_reload_event",
    "nodriver_utk_sports_health_check",
    "nodriver_ctbc_inject_cookies",
    "nodriver_ctbc_main",
    "nodriver_utk_sports_reload",
    "nodriver_utk_sports_reload_wait",
]


UTK_SPORTS_PROFILES = {
    host: profile.as_legacy_dict()
    for host, profile in TENANT_PROFILES.items()
}
CTBC_DATE_ROW_SELECTOR = "#PerformanceListTable table > tbody > tr"
CTBC_DATE_PURCHASE_CONTROL_SELECTOR = (
    "td:last-child button, td:last-child a[href], "
    "td:last-child [role=\"button\"]"
)
CTBC_AREA_ROW_SELECTOR = ".seats table.f1 > tbody > tr.saleTr"
CTBC_FAST_PATH_TTL_SECONDS = 1.5
CTBC_PENDING_NAVIGATION_TTL_SECONDS = 15.0
CTBC_FAST_MARKER_CLOCK_SKEW_SECONDS = 1.0
CTBC_FAST_MARKER_KEY = "ticketsHunter.ctbc.fast-navigation.v1"
CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS = 10.0
CTBC_QUANTITY_STALE_RELOAD_REASON = "quantity state changed during commit"
UTK_RECOVERY_PROBE_RENEW_SECONDS = 5.0
UTK_RECOVERY_PROBE_MAX_SECONDS = 120.0
UTK_RELOAD_WAIT_MAX_SECONDS = 30.0
# This must cover the coordinator's default 5-second fail-closed retry.  Every
# pre-report writer uses the same-or-longer duration, so a later process cannot
# shorten a marker written after a transient SQLite failure.
UTK_EMERGENCY_MARKER_SECONDS = 6.0
UTK_EMERGENCY_PROCESS_TOKEN = uuid.uuid4().hex
_state = {
    "submitted": {},
    "seat_conflict_pending": {},
    "submit_blocked": {},
    "checkout_notified": set(),
    "ticket_type": {},
    "auth_failure": {},
    "captcha_reset": {},
    "pending_navigation": {},
    "reload_request": {},
    "rate_limit_observation": {},
    "rate_limit_retry": {},
    "recovery_probe": {},
    "reload_event": set(),
    "cancel_tasks": set(),
    "document_snapshot": {},
    "last_document": {},
    "unknown_document": {},
    "tab_tokens": [],
}


def _get_utiki_behavior(config_dict, key, default):
    """Read canonical family behavior with one-release legacy fallback."""
    canonical = config_dict.get("utiki")
    if isinstance(canonical, dict) and key in canonical:
        return canonical[key]
    legacy = config_dict.get("ctbc")
    if isinstance(legacy, dict) and key in legacy:
        return legacy[key]
    return default


def _ctbc_tab_identity(tab):
    """Use the tab object itself so recycled Python object IDs cannot leak state."""
    try:
        hash(tab)
        identity = tab
    except TypeError:
        identity = None
        for existing_tab, token in _state["tab_tokens"]:
            if existing_tab is tab:
                identity = token
                break
        if identity is None:
            identity = f"ctbc-tab-{id(tab)}-{time.monotonic_ns()}"
            _state["tab_tokens"].append((tab, identity))
    return identity


def _ctbc_tab_state_key(tab):
    identity = _ctbc_tab_identity(tab)
    return (identity, str(tab.target.url))


def _pop_reload_request_if_token(identity, request_token):
    """Remove only the logical request that the current coroutine captured."""
    current = _state["reload_request"].get(identity)
    if current and current.get("request_token") == request_token:
        return _state["reload_request"].pop(identity, None)
    return None


def _pop_pending_navigation_if_nonce(identity, nonce):
    current = _state["pending_navigation"].get(identity)
    if current and current.get("nonce") == nonce:
        return _state["pending_navigation"].pop(identity, None)
    return None


def _ctbc_pending_navigation_is_current(identity, pending):
    current = _state["pending_navigation"].get(identity)
    return bool(
        current
        and pending
        and current.get("nonce") == pending.get("nonce")
    )


async def nodriver_ctbc_inject_cookies(tab, config_dict):
    """Inject a copied CTBC cookie set; account/password remains the fallback."""
    debug = util.create_debug_logger(config_dict)
    auth_profile = get_tenant_auth_profile(str(tab.target.url))
    if not auth_profile or not auth_profile.supports_cookie_login:
        debug.log("[CTBC COOKIE] Cookie login is unavailable for this tenant")
        return False
    raw_value = config_dict.get("accounts", {}).get(
        auth_profile.cookie_config_key, ""
    )
    cookies = parse_ctbc_cookie_string(raw_value)
    if not cookies:
        if str(raw_value or "").strip():
            debug.log("[CTBC COOKIE] No valid CTBC session cookies were found")
        return False

    try:
        existing_cookies = await tab.browser.cookies.get_all()
        for cookie in existing_cookies:
            if (
                cookie.name.startswith(auth_profile.cookie_name_prefix)
                and cookie_domain_is_in_scope(auth_profile, cookie.domain)
            ):
                try:
                    await tab.send(cdp.network.delete_cookies(
                        name=cookie.name,
                        domain=cookie.domain,
                        path=cookie.path or "/",
                    ))
                except Exception:
                    pass
    except Exception as exc:
        debug.log(f"[CTBC COOKIE] Could not clear the current CTBC cookie set: {exc}")

    injected = 0
    for name, value in cookies:
        try:
            result = await tab.send(cdp.network.set_cookie(
                name=name,
                value=value,
                domain=auth_profile.cookie_set_host,
                path="/",
                secure=True,
                http_only=True,
            ))
            if result is not False:
                injected += 1
        except Exception as exc:
            debug.log(f"[CTBC COOKIE] Failed to inject {name}: {exc}")

    if injected != len(cookies):
        debug.log(f"[CTBC COOKIE] Injected {injected}/{len(cookies)} cookies")
        return False
    debug.log(f"[CTBC COOKIE] Injected {injected} session cookies")
    return True


def _is_ctbc_url(url):
    return is_utiki_sports_url(url)


is_utk_sports_url = is_utiki_sports_url


def _get_utk_sports_profile(url):
    profile = get_utiki_tenant_profile(url)
    return profile.as_legacy_dict() if profile else None


def _get_ctbc_page_type(url):
    page_kind = classify_utiki_page(url)
    return None if page_kind is PageKind.UNKNOWN else page_kind.value


def _ctbc_unique_query(url):
    return unique_query(url)


def _ctbc_area_action_payload(plan):
    if not isinstance(plan, AreaActionPlan):
        return None
    return {
        "action_name": plan.action_name,
        "route": plan.route,
        "performance_id": plan.performance_id,
        "price_area_id": plan.price_area_id,
        "group_id": plan.group_id,
        "stable_args": list(plan.stable_args),
        "status": plan.action_status,
    }


def _build_ctbc_destination_url(area_url, target):
    if _get_ctbc_page_type(area_url) != "area":
        return None
    source = urlsplit(area_url)
    if target.get("route") not in {"0202", "0205"}:
        return None
    area_query = _ctbc_unique_query(area_url)
    if (area_query or {}).get("PERFORMANCE_ID") != target.get("performance_id"):
        return None
    path = f"{source.path.rsplit('/', 1)[0]}/UTK{target['route']}_"
    query = urlencode({
        "PERFORMANCE_ID": target["performance_id"],
        "GROUP_ID": target["group_id"],
        "PERFORMANCE_PRICE_AREA_ID": target["price_area_id"],
    })
    return urlunsplit(("https", source.netloc, path, query, ""))


def _ctbc_same_origin(first, second):
    return same_origin(first, second)


def _ctbc_purchase_state_key(tab):
    """Key submit state by purchase identity, not cosmetic URL details."""
    identity = _ctbc_tab_identity(tab)
    current_url = str(tab.target.url)
    try:
        parsed = urlsplit(current_url)
    except ValueError:
        return None

    purchase_url = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
    )
    page_type = _get_ctbc_page_type(purchase_url)
    query = _ctbc_unique_query(purchase_url)
    profile = _get_utk_sports_profile(purchase_url)
    if page_type not in ("seat", "general_admission") or query is None or not profile:
        return None

    performance_id = query.get("PERFORMANCE_ID")
    group_id = query.get("GROUP_ID")
    price_area_id = query.get("PERFORMANCE_PRICE_AREA_ID")
    token_pattern = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token_pattern.fullmatch(str(performance_id or ""))
        or not token_pattern.fullmatch(str(price_area_id or ""))
        or group_id is None
        or (group_id and not token_pattern.fullmatch(str(group_id)))
        or (page_type == "seat" and not group_id)
    ):
        return None

    return (
        identity,
        profile["host"],
        profile["tenant_path"],
        page_type,
        performance_id,
        group_id,
        price_area_id,
    )


def _ctbc_submit_reached_checkout(source_url, destination_url):
    """Accept submit navigation only for the source tenant's checkout route."""
    try:
        source = urlsplit(source_url)
        destination = urlsplit(destination_url)
    except ValueError:
        return False

    source_without_fragment = urlunsplit(
        (source.scheme, source.netloc, source.path, source.query, "")
    )
    return (
        _get_ctbc_page_type(source_without_fragment) in ("seat", "general_admission")
        and _get_ctbc_page_type(destination_url) == "checkout"
        and _ctbc_same_origin(source, destination)
    )


def _parse_ctbc_date_target(onclick, current_url):
    """Strictly parse the site's ordinary date-button navigation target."""
    if _get_ctbc_page_type(current_url) != "date":
        return None
    match = re.fullmatch(
        r"\s*location\.href\s*=\s*(?P<quote>['\"])(?P<url>[^'\"]+)(?P=quote)\s*;?\s*",
        str(onclick or ""),
    )
    if not match:
        return None

    target_url = urljoin(current_url, match.group("url"))
    current = urlsplit(current_url)
    target = urlsplit(target_url)
    profile = _get_utk_sports_profile(current_url)
    if (
        not profile
        or target.scheme != "https"
        or target.hostname != profile["host"]
        or not _ctbc_same_origin(current, target)
        or target.fragment
        or _get_ctbc_page_type(target_url) != "area"
    ):
        return None
    current_tenant = current.path.rsplit("/", 1)[0]
    if target.path != f"{current_tenant}/UTK0204_":
        return None

    target_query = _ctbc_unique_query(target_url)
    current_query = _ctbc_unique_query(current_url)
    if target_query is None or current_query is None:
        return None
    if set(target_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}:
        return None
    token_pattern = re.compile(r"[A-Za-z0-9_-]+")
    performance_id = target_query["PERFORMANCE_ID"]
    product_id = target_query["PRODUCT_ID"]
    if not token_pattern.fullmatch(performance_id) or not token_pattern.fullmatch(product_id):
        return None
    if product_id != current_query.get("PRODUCT_ID"):
        return None
    return {
        "url": urlunsplit((target.scheme, target.netloc, target.path, target.query, "")),
        "performance_id": performance_id,
        "product_id": product_id,
        "tenant_path": current_tenant,
        "host": profile["host"],
        "map_namespace": profile["map_namespace"],
        "fast_area_prefetch": profile["fast_area_prefetch"],
    }


def _parse_ctbc_vip_date_action(onclick, current_url):
    """Parse the site's membership presale action without executing it."""
    if _get_ctbc_page_type(current_url) != "date":
        return None

    current_query = _ctbc_unique_query(current_url)
    product_id = (current_query or {}).get("PRODUCT_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", product_id):
        return None

    match = re.fullmatch(
        r"\s*VipSellCheck\(\s*(?P<quote>['\"])(?P<token>[A-Za-z0-9_-]{1,128})"
        r"(?P=quote)\s*\)\s*;?\s*",
        str(onclick or ""),
    )
    if not match:
        return None
    return {
        "kind": "vip_native",
        "token": match.group("token"),
        "product_id": product_id,
    }


def _classify_ctbc_date_action(onclick, current_url):
    """Classify known actions while keeping an official-row native fallback."""
    date_target = _parse_ctbc_date_target(onclick, current_url)
    if date_target:
        return {
            "kind": "normal",
            "date_target": date_target,
        }

    vip_action = _parse_ctbc_vip_date_action(onclick, current_url)
    if vip_action:
        return vip_action

    # Future UTK tenants may use a different membership-check function.  The
    # caller may use a native click only after proving that this is the sole
    # actionable control in the selected row's purchase cell.
    if (
        _get_ctbc_page_type(current_url) == "date"
        and _ctbc_safe_generic_button_handler(onclick)
    ):
        return {"kind": "structural_native"}
    return None


async def _ctbc_date_control_snapshot(control):
    """Return the structural/actionable state of a date-row purchase control."""
    raw_value = await control.apply(r'''function(el) {
        // __ticketsHunterDateControlSnapshot
        const row = el.closest('tr');
        const cell = el.closest('td');
        const tableRoot = row && row.closest('#PerformanceListTable');
        const style = getComputedStyle(el);
        const rects = el.getClientRects();
        const blockedAncestor = el.closest('[hidden], [inert], [aria-hidden="true"]');
        const disabled = el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true';
        const visible = el.isConnected && rects.length > 0 &&
            style.display !== 'none' && style.visibility !== 'hidden' &&
            style.pointerEvents !== 'none' && !blockedAncestor;
        const inPurchaseCell = !!tableRoot && !!cell && cell === row.lastElementChild;
        return JSON.stringify({
            actionable: !!(visible && !disabled && inPurchaseCell),
            connected: !!el.isConnected,
            visible: !!visible,
            disabled: !!disabled,
            in_purchase_cell: !!inPurchaseCell,
            tag_name: (el.tagName || '').toUpperCase(),
            href: el.getAttribute('href') || '',
            resolved_href: el.href || '',
            role: el.getAttribute('role') || '',
            type: el.getAttribute('type') || '',
            target: el.getAttribute('target') || '',
            download: el.getAttribute('download') || '',
            ping: el.getAttribute('ping') || '',
            formaction: el.getAttribute('formaction') || '',
            has_form: !!el.closest('form'),
            onclick: el.getAttribute('onclick') || '',
            text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()
        });
    }''')
    return _decode_ctbc_json_result(raw_value)


def _ctbc_safe_generic_button_handler(onclick):
    """Allow one inert-looking site function call, never arbitrary JavaScript."""
    source = str(onclick or "").strip()
    if not source:
        return True
    match = re.fullmatch(
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*"
        r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\((?P<args>.*)\)\s*;?",
        source,
    )
    if not match:
        return False
    arguments = match.group("args").strip()
    if not arguments:
        return True
    return all(
        re.fullmatch(r"(?P<q>['\"])[A-Za-z0-9_-]{1,128}(?P=q)", item.strip())
        for item in arguments.split(",")
    )


def _ctbc_date_control_action(snapshot, current_url):
    """Return a fail-closed action for one structurally attested date control."""
    if not isinstance(snapshot, dict) or not snapshot.get("actionable"):
        return None

    tag_name = str(snapshot.get("tag_name") or "").upper()
    role = str(snapshot.get("role") or "").lower()
    onclick = str(snapshot.get("onclick") or "")
    if tag_name == "A":
        if onclick or any(
            str(snapshot.get(key) or "")
            for key in ("target", "download", "ping")
        ):
            return None
        href = str(snapshot.get("resolved_href") or snapshot.get("href") or "")
        # Anchors are navigational by nature.  Do not use the broad structural
        # fallback for them: their own href must be the exact same-tenant,
        # same-product UTK0204 target before a native click is permitted.
        current = urlsplit(current_url)
        resolved = urlsplit(urljoin(current_url, href))
        if not _ctbc_same_origin(current, resolved):
            return None
        date_target = _parse_ctbc_date_target(
            f"location.href={json.dumps(href, ensure_ascii=False)}", current_url
        )
        if not date_target:
            return None
        return {
            "kind": "anchor_native",
            "date_target": date_target,
        }

    date_target = _parse_ctbc_date_target(onclick, current_url)
    if date_target:
        return {"kind": "normal", "date_target": date_target}
    vip_action = _parse_ctbc_vip_date_action(onclick, current_url)
    if vip_action:
        return vip_action

    # The compatibility fallback intentionally remains independent of button
    # text, but it is limited to an actual non-submit BUTTON and one simple
    # site function call. This preserves future membership-sale handlers
    # without executing assignments, multi-statement JavaScript or form posts.
    control_type = str(snapshot.get("type") or "").lower()
    if (
        tag_name != "BUTTON"
        or str(snapshot.get("formaction") or "")
        or control_type not in ("", "button")
        or (not control_type and snapshot.get("has_form"))
        or not _ctbc_safe_generic_button_handler(onclick)
    ):
        return None
    return {"kind": "structural_native"}


async def _ctbc_commit_native_date_action(
    control, source_url, expected_fingerprint, direct_url=""
):
    """Atomically re-attest and natively click one selected purchase control."""
    source_json = json.dumps(source_url)
    fingerprint_json = json.dumps(expected_fingerprint, ensure_ascii=False)
    direct_url_json = json.dumps(str(direct_url or ""), ensure_ascii=False)
    committed = await control.apply(f'''function(el) {{
        // __ticketsHunterCommitNativeDateAction
        const sourceUrl = {source_json};
        const expectedFingerprint = {fingerprint_json};
        const directUrl = {direct_url_json};
        if (location.href !== sourceUrl || !el.isConnected) return false;
        const row = el.closest('tr');
        const cell = el.closest('td');
        if (!row || !cell || !row.closest('#PerformanceListTable') ||
            cell !== row.lastElementChild) return false;
        const isActionable = candidate => {{
            const style = getComputedStyle(candidate);
            return candidate.isConnected && candidate.getClientRects().length > 0 &&
                style.display !== 'none' && style.visibility !== 'hidden' &&
                style.pointerEvents !== 'none' &&
                !candidate.closest('[hidden], [inert], [aria-hidden="true"]') &&
                !candidate.matches(':disabled') &&
                candidate.getAttribute('aria-disabled') !== 'true';
        }};
        const controls = Array.from(cell.querySelectorAll(
            'button, a[href], [role="button"]'
        )).filter(isActionable);
        if (controls.length !== 1 || controls[0] !== el) return false;
        const currentFingerprint = {{
            tag_name: (el.tagName || '').toUpperCase(),
            href: el.getAttribute('href') || '',
            resolved_href: el.href || '',
            role: el.getAttribute('role') || '',
            type: el.getAttribute('type') || '',
            target: el.getAttribute('target') || '',
            download: el.getAttribute('download') || '',
            ping: el.getAttribute('ping') || '',
            formaction: el.getAttribute('formaction') || '',
            has_form: !!el.closest('form'),
            onclick: el.getAttribute('onclick') || ''
        }};
        for (const key of [
            'tag_name', 'href', 'resolved_href', 'role', 'type', 'target',
            'download', 'ping', 'formaction', 'has_form', 'onclick'
        ]) {{
            if ((currentFingerprint[key] || '') !==
                (expectedFingerprint[key] || '')) return false;
        }}
        if (directUrl) {{
            location.assign(directUrl);
            return true;
        }}
        el.click();
        return true;
    }}''')
    return committed is True


def _select_ctbc_seat_indexes(
    seats,
    needed,
    allow_non_adjacent=False,
    seat_sorting="row_seat",
):
    """Return seat indexes using an explicit display or numeric priority."""
    if needed <= 0:
        return []

    numeric_sortings = {
        "row_seat",
        "row_asc_aisle",
        "row_asc_seat_desc",
        "row_desc_seat_asc",
    }
    if seat_sorting not in numeric_sortings | {"display"}:
        seat_sorting = "row_seat"

    full_row_edges = {}
    for seat in seats:
        title = str(seat.get("title", "") or "").strip()
        match = re.search(r"-(\d+)排-(\d+)號\s*$", title)
        if not match:
            if seat_sorting == "row_asc_aisle" and title:
                return []
            continue
        row_number = int(match.group(1))
        seat_number = int(match.group(2))
        row_edges = full_row_edges.setdefault(
            row_number,
            {"min_seat_number": seat_number, "max_seat_number": seat_number},
        )
        row_edges["min_seat_number"] = min(
            row_edges["min_seat_number"], seat_number
        )
        row_edges["max_seat_number"] = max(
            row_edges["max_seat_number"], seat_number
        )

    available = [seat for seat in seats if seat.get("available") and not seat.get("selected")]
    if len(available) < needed:
        return []

    rows = {}
    for seat in available:
        title = seat.get("title", "")
        match = re.search(r"-(\d+)排-(\d+)號\s*$", title)
        if not match:
            continue
        row_number = int(match.group(1))
        seat_number = int(match.group(2))
        parsed_seat = dict(seat)
        parsed_seat["_row_number"] = row_number
        parsed_seat["_seat_number"] = seat_number
        rows.setdefault(row_number, []).append(parsed_seat)

    block_candidates = []
    for row_number, seats_in_row in rows.items():
        row_seats = sorted(seats_in_row, key=lambda item: item["_seat_number"])
        for start in range(0, len(row_seats) - needed + 1):
            block = row_seats[start:start + needed]
            if all(
                _ctbc_seats_are_physically_adjacent(block[offset], block[offset + 1])
                for offset in range(len(block) - 1)
            ):
                row_edges = full_row_edges[row_number]
                min_seat_number = min(item["_seat_number"] for item in block)
                max_seat_number = max(item["_seat_number"] for item in block)
                block_candidates.append({
                    "seats": block,
                    "row_number": row_number,
                    "min_seat_number": min_seat_number,
                    "max_seat_number": max_seat_number,
                    "dom_index": min(item.get("index", 0) for item in block),
                    "touches_aisle": (
                        min_seat_number == row_edges["min_seat_number"]
                        or max_seat_number == row_edges["max_seat_number"]
                    ),
                })

    if block_candidates:
        if seat_sorting == "row_asc_aisle":
            block_candidates = [
                item for item in block_candidates if item["touches_aisle"]
            ]
            if not block_candidates:
                return []
            block_candidates.sort(key=lambda item: (
                item["row_number"], item["dom_index"],
            ))
        elif seat_sorting == "display":
            block_candidates.sort(key=lambda item: item["dom_index"])
        elif seat_sorting == "row_seat":
            block_candidates.sort(key=lambda item: (
                item["row_number"], item["min_seat_number"],
                item["dom_index"],
            ))
        elif seat_sorting == "row_asc_seat_desc":
            block_candidates.sort(key=lambda item: (
                item["row_number"], -item["max_seat_number"],
                item["dom_index"],
            ))
        elif seat_sorting == "row_desc_seat_asc":
            block_candidates.sort(key=lambda item: (
                -item["row_number"], item["min_seat_number"],
                item["dom_index"],
            ))
        target_seats = sorted(
            block_candidates[0]["seats"], key=lambda item: item.get("index", 0)
        )
        return [seat["index"] for seat in target_seats]

    if seat_sorting == "row_asc_aisle" or not allow_non_adjacent:
        return []

    ordered_available = available
    if seat_sorting in numeric_sortings:
        def numeric_seat_key(seat):
            match = re.search(r"-(\d+)排-(\d+)號\s*$", seat.get("title", ""))
            if not match:
                return (1, 0, 0, seat.get("row_index", 0), seat.get("cell_index", 0))
            row_number = int(match.group(1))
            seat_number = int(match.group(2))
            if seat_sorting == "row_asc_seat_desc":
                row_number, seat_number = row_number, -seat_number
            elif seat_sorting == "row_desc_seat_asc":
                row_number, seat_number = -row_number, seat_number
            return (
                0,
                row_number,
                seat_number,
                seat.get("row_index", 0),
                seat.get("cell_index", 0),
            )

        ordered_available = sorted(available, key=numeric_seat_key)

    chosen = ordered_available[:needed]
    return [seat["index"] for seat in chosen]


def _ctbc_seats_are_physically_adjacent(first, second):
    """Support both horizontal and rotated vertical UTK seat-map layouts."""
    coordinate_fields = ("center_x", "center_y", "width", "height")
    if all(
        isinstance(seat.get(field), (int, float))
        for seat in (first, second)
        for field in coordinate_fields
    ):
        dx = abs(first["center_x"] - second["center_x"])
        dy = abs(first["center_y"] - second["center_y"])
        seat_size = max(
            first["width"], first["height"], second["width"], second["height"]
        )
        if seat_size > 0:
            aligned = min(dx, dy) <= max(2.0, seat_size * 0.35)
            close = max(dx, dy) <= seat_size * 1.6
            distinct = max(dx, dy) >= seat_size * 0.35
            return aligned and close and distinct

    row_delta = abs(first.get("row_index", 0) - second.get("row_index", 0))
    cell_delta = abs(first.get("cell_index", 0) - second.get("cell_index", 0))
    return (row_delta == 0 and cell_delta == 1) or (
        row_delta == 1 and cell_delta == 0
    )


def _utk_refresh_settings(*, force_refresh=False):
    """Hot-read the one global UTK Sports pacing configuration."""
    settings_path = get_global_settings_path() if force_refresh else None
    return load_global_settings(settings_path).get("utk_sports", {})


def _utk_refresh_identity(tab):
    """Return one stable, process-unique queue identity for this browser tab."""
    return f"{util.get_instance_id()}:{os.getpid()}:{id(tab):x}"


def _utk_emergency_marker_path():
    return os.path.join(
        util.get_app_root(),
        ".runtime",
        "utk_sports_emergency_"
        f"{os.getpid()}_{UTK_EMERGENCY_PROCESS_TOKEN}_{uuid.uuid4().hex}.until",
    )


def _mark_utk_emergency_window(seconds=UTK_EMERGENCY_MARKER_SECONDS):
    """Best-effort cross-process stopgap while a 429 DB report is pending."""
    marker_path = _utk_emergency_marker_path()
    marker_dir = os.path.dirname(marker_path)
    temp_path = f"{marker_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        os.makedirs(marker_dir, exist_ok=True)
        new_deadline = time.time() + max(float(seconds), UTK_EMERGENCY_MARKER_SECONDS)
        with open(temp_path, "w", encoding="utf-8") as marker_file:
            marker_file.write(str(new_deadline))
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temp_path, marker_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(temp_path)


def _utk_emergency_remaining():
    marker_pattern = os.path.join(
        util.get_app_root(), ".runtime", "utk_sports_emergency_*.until"
    )
    deadline = 0.0
    now = time.time()
    for marker_path in glob.glob(marker_pattern):
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


async def _inspect_utk_document(tab, url=None, *, force=False):
    """Classify the current document without mistaking a loading page for 429."""
    current_url = str(url or getattr(getattr(tab, "target", None), "url", ""))
    identity = _ctbc_tab_identity(tab)
    cached = _state["document_snapshot"].get(identity)
    if (
        not force
        and cached
        and cached.get("url") == current_url
        and time.monotonic() - cached.get("checked_at", 0.0) < 0.2
    ):
        return dict(cached["snapshot"])
    page_type = _get_ctbc_page_type(current_url)
    normal_selector = {
        "date": "#PerformanceListTable",
        "area": ".seats, #mapdata",
        "seat": "#TBL, .ticket",
        "general_admission": "#table_tickettype, .ticket",
    }.get(page_type)
    selector_expression = (
        f"!!document.querySelector({json.dumps(normal_selector)})"
        if normal_selector
        else "false"
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
        snapshot = _decode_ctbc_json_result(raw_value)
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        return {
            "classification": "unknown",
            "document_id": current_url,
            "document_started_at": 0.0,
            "url": current_url,
            "ready_state": "",
            "has_normal_root": False,
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
    _state["document_snapshot"][identity] = {
        "url": snapshot["url"],
        "checked_at": time.monotonic(),
        "snapshot": dict(snapshot),
    }
    return snapshot


async def nodriver_utk_sports_cancel_refresh(
    tab,
    reason="",
    *,
    wait=False,
    abandon_probe=False,
):
    """Cancel pending work and optionally release an owned recovery probe."""
    identity = _ctbc_tab_identity(tab)
    request = _state["reload_request"].pop(identity, None)
    probe = _state["recovery_probe"].get(identity) if abandon_probe else None
    if probe is not None:
        probe["abandon_requested"] = True
    if request is None and probe is None:
        return False
    coordinator = get_shared_refresh_coordinator()
    async def cancel_all():
        changed = False
        if request is not None:
            changed = await coordinator.cancel_request(
                UTK_SPORTS_SCOPE,
                _utk_refresh_identity(tab),
                request_token=request.get("request_token"),
            ) or changed
        if probe is not None:
            completion = await coordinator.complete_recovery_probe(
                UTK_SPORTS_SCOPE,
                _utk_refresh_identity(tab),
                probe.get("permit_id", ""),
                success=False,
            )
            if completion.accepted or "not owned" in str(completion.error or ""):
                current = _state["recovery_probe"].get(identity)
                if current and current.get("permit_id") == probe.get("permit_id"):
                    _state["recovery_probe"].pop(identity, None)
            changed = bool(completion.accepted) or changed
        return changed

    cancel_call = cancel_all()
    if wait:
        return await cancel_call
    task = asyncio.create_task(cancel_call)
    _state["cancel_tasks"].add(task)
    task.add_done_callback(_state["cancel_tasks"].discard)
    return True


def nodriver_utk_sports_consume_reload_event(tab):
    """Tell the outer router that a same-URL reload needs fresh CF detection."""
    identity = _ctbc_tab_identity(tab)
    if identity not in _state["reload_event"]:
        return False
    _state["reload_event"].discard(identity)
    return True


async def _report_utk_rate_limit(
    tab,
    config_dict,
    document_id,
    probe_permit_id=None,
    document_started_at=0.0,
    suspected=False,
):
    settings = _utk_refresh_settings()
    if not settings.get("enabled", True):
        return None
    identity = _ctbc_tab_identity(tab)
    coordinator = get_shared_refresh_coordinator()
    _mark_utk_emergency_window()
    report = await coordinator.report_rate_limited(
        UTK_SPORTS_SCOPE,
        _utk_refresh_identity(tab),
        initial_cooldown_seconds=settings.get("initial_cooldown_seconds", 60),
        max_cooldown_seconds=settings.get("max_cooldown_seconds", 240),
        probe_permit_id=probe_permit_id,
        document_started_at=document_started_at,
    )
    debug = util.create_debug_logger(config_dict)
    if report.accepted:
        _state["rate_limit_observation"][identity] = document_id
        _state["rate_limit_retry"].pop(identity, None)
    else:
        retry_seconds = max(float(report.retry_after_seconds or 1.0), 0.5)
        _state["rate_limit_retry"][identity] = time.monotonic() + retry_seconds
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


async def _finish_utk_recovery_probe(tab, config_dict, snapshot):
    """Resolve a probe only after a different document becomes normal or 429."""
    identity = _ctbc_tab_identity(tab)
    probe = _state["recovery_probe"].get(identity)
    if not probe:
        return False
    now = time.monotonic()
    probe.setdefault("started_at", now)
    probe.setdefault(
        "next_renew_at", now + UTK_RECOVERY_PROBE_RENEW_SECONDS
    )
    coordinator = get_shared_refresh_coordinator()
    if now - probe.get("started_at", now) >= UTK_RECOVERY_PROBE_MAX_SECONDS:
        completion = await coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE,
            _utk_refresh_identity(tab),
            probe.get("permit_id", ""),
            success=False,
        )
        # The hard lifecycle limit has been reached. Stop local renewals even
        # if SQLite is temporarily unavailable; the DB lease then expires
        # naturally instead of being hammered on every 100ms health pass.
        current = _state["recovery_probe"].get(identity)
        if current and current.get("permit_id") == probe.get("permit_id"):
            _state["recovery_probe"].pop(identity, None)
        return True
    if not probe.get("lease_task") and now >= probe.get("next_renew_at", 0.0):
        renewed = await coordinator.renew_recovery_probe(
            UTK_SPORTS_SCOPE, probe.get("permit_id", "")
        )
        if renewed is False:
            _state["recovery_probe"].pop(identity, None)
            return False
        probe["next_renew_at"] = now + (
            UTK_RECOVERY_PROBE_RENEW_SECONDS if renewed else 1.0
        )
    if snapshot.get("document_id") == probe.get("source_document_id"):
        return False
    classification = snapshot.get("classification")
    if classification == "unknown":
        return False
    if time.monotonic() < _state["rate_limit_retry"].get(identity, 0.0):
        return True
    if classification == "blocked":
        report = await _report_utk_rate_limit(
            tab,
            config_dict,
            snapshot.get("document_id", ""),
            probe_permit_id=probe.get("permit_id"),
            document_started_at=snapshot.get("document_started_at", 0.0),
        )
        if report and report.accepted:
            _state["recovery_probe"].pop(identity, None)
    else:
        settings = _utk_refresh_settings()
        if settings.get("enabled", True):
            completion = await coordinator.complete_recovery_probe(
                UTK_SPORTS_SCOPE,
                _utk_refresh_identity(tab),
                probe.get("permit_id", ""),
                success=(classification == "normal"),
            )
            if completion.accepted:
                _state["recovery_probe"].pop(identity, None)
                if classification == "normal":
                    util.create_debug_logger(config_dict).log(
                        "[UTK RATE] Recovery probe succeeded; shared refresh resumed"
                    )
            elif "not owned" in str(completion.error or ""):
                _state["recovery_probe"].pop(identity, None)
            else:
                _state["rate_limit_retry"][identity] = time.monotonic() + 1.0
    return True


async def _utk_document_guard(tab, config_dict, url=None):
    """Apply the 429 circuit breaker before any UTK page-specific action."""
    settings = _utk_refresh_settings()
    snapshot = await _inspect_utk_document(tab, url)
    identity = _ctbc_tab_identity(tab)
    document_id = snapshot.get("document_id")
    previous_document_id = _state["last_document"].get(identity)
    if previous_document_id and previous_document_id != document_id:
        _state["reload_event"].add(identity)
    _state["last_document"][identity] = document_id
    if not settings.get("enabled", True):
        return "disabled", snapshot
    probe_handled = await _finish_utk_recovery_probe(tab, config_dict, snapshot)
    classification = snapshot.get("classification", "unknown")
    if classification == "unknown":
        if _state["rate_limit_observation"].get(identity) == document_id:
            return "blocked", snapshot
        unknown = _state["unknown_document"].get(identity)
        if not unknown or unknown.get("document_id") != document_id:
            unknown = {
                "document_id": document_id,
                "first_seen_at": time.monotonic(),
            }
            _state["unknown_document"][identity] = unknown
        # Unknown is a local fail-closed state, not evidence of HTTP 429.
        # Cloudflare interstitials, loading/empty documents and CDP failures can
        # all remain unknown for a long time.  Promoting those to a shared
        # incident would unnecessarily cool down every CTBC/Dragons process.
        # Only the explicit code+message classifier above may open a global
        # rate-limit incident.
        return "unknown", snapshot

    _state["unknown_document"].pop(identity, None)
    if classification == "blocked":
        request = _state["reload_request"].get(identity)
        request_is_stale = bool(request) and (
            request.get("document_id") != snapshot.get("document_id")
            or request.get("expected_url") != snapshot.get("url")
        )
        if (
            not request
            or not request.get("allow_blocked_recovery")
            or request_is_stale
        ):
            await nodriver_utk_sports_cancel_refresh(tab, "HTTP 429", wait=True)
        if (
            not probe_handled
            and _state["rate_limit_observation"].get(identity)
            != snapshot.get("document_id")
            and time.monotonic() >= _state["rate_limit_retry"].get(identity, 0.0)
        ):
            await _report_utk_rate_limit(
                tab,
                config_dict,
                snapshot.get("document_id", ""),
                document_started_at=snapshot.get("document_started_at", 0.0),
            )
        return "blocked", snapshot

    request = _state["reload_request"].get(identity)
    if request and (
        request.get("expected_url") != snapshot.get("url")
        or request.get("document_id") != snapshot.get("document_id")
    ):
        await nodriver_utk_sports_cancel_refresh(tab, "document changed", wait=True)
    return classification, snapshot


async def nodriver_utk_sports_reload(
    tab,
    config_dict,
    reason,
    *,
    apply_local_interval=True,
    allow_unknown_document=False,
    allow_blocked_recovery=False,
    dispatch_deadline=None,
):
    """Poll once and reload only after obtaining the shared one-shot permit."""
    debug = util.create_debug_logger(config_dict)
    settings = _utk_refresh_settings()
    if not settings.get("enabled", True):
        await nodriver_utk_sports_cancel_refresh(
            tab,
            "shared protection disabled",
            wait=True,
            abandon_probe=True,
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
        # The switch may have been re-enabled while this tab was serving its
        # legacy local delay.  Re-enter through the coordinator next loop
        # instead of allowing one final uncoordinated network request.
        if _utk_refresh_settings(force_refresh=True).get("enabled", True):
            return False
        try:
            await tab.reload()
            identity = _ctbc_tab_identity(tab)
            _state["document_snapshot"].pop(identity, None)
            _state["reload_event"].add(identity)
            return True
        except Exception as exc:
            debug.log(f"[CTBC] Reload failed: {exc}")
            return False

    if dispatch_deadline is not None:
        remaining = float(dispatch_deadline) - time.monotonic()
        if remaining <= 0:
            return False
        try:
            classification, snapshot = await asyncio.wait_for(
                _utk_document_guard(tab, config_dict), timeout=remaining
            )
        except asyncio.TimeoutError:
            return False
    else:
        classification, snapshot = await _utk_document_guard(tab, config_dict)
    if classification == "blocked" and not allow_blocked_recovery:
        return False
    if classification == "unknown" and not allow_unknown_document:
        return False

    identity = _ctbc_tab_identity(tab)
    request = _state["reload_request"].get(identity)
    if request is None:
        interval = config_dict.get("advanced", {}).get(
            "auto_reload_page_interval", 0.0
        )
        local_delay = (
            (float(interval) if interval and interval > 0 else 1.0)
            if apply_local_interval
            else 0.0
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
        _state["reload_request"][identity] = request
        debug.log(f"[CTBC] {reason}; queued for shared refresh")
    request_token = request.get("request_token")

    now = time.monotonic()
    if now < request.get("eligible_at", 0.0) or now < request.get("next_poll_at", 0.0):
        return False

    emergency_remaining = _utk_emergency_remaining()
    if emergency_remaining > 0:
        request["next_poll_at"] = time.monotonic() + min(
            emergency_remaining, 0.5
        )
        return False

    if dispatch_deadline is not None and time.monotonic() >= dispatch_deadline:
        _pop_reload_request_if_token(identity, request_token)
        return False

    coordinator = get_shared_refresh_coordinator()
    reservation = await coordinator.reserve_refresh(
        UTK_SPORTS_SCOPE,
        _utk_refresh_identity(tab),
        min_interval_seconds=settings.get("min_interval_seconds", 1.5),
        jitter_range_seconds=(0.1, 0.3),
        request_token=request_token,
    )
    if not reservation.reserved:
        retry_delay = max(float(reservation.retry_after_seconds or 0.1), 0.05)
        if reservation.status == "cooldown":
            cooldown_remaining = max(
                float(reservation.cooldown_until or 0.0) - time.time(), 0.1
            )
            retry_delay = min(cooldown_remaining, 1.0)
        elif reservation.status == "recovery_probe_active":
            retry_delay = max(retry_delay, 1.0)
        request["next_poll_at"] = time.monotonic() + retry_delay
        if reservation.error:
            debug.log(
                "[UTK RATE] Shared coordinator failed closed: "
                f"{reservation.error}"
            )
        return False

    async def release_reservation():
        return await coordinator.release_refresh(
            UTK_SPORTS_SCOPE,
            _utk_refresh_identity(tab),
            reservation.reservation_id,
            request_token=request_token,
            drop_request=True,
        )

    # Reservation does not consume the shared interval. Browser-side checks
    # happen first; commit below is the final linearization point immediately
    # before the network action.
    try:
        if dispatch_deadline is not None:
            remaining = float(dispatch_deadline) - time.monotonic()
            if remaining <= 0:
                await release_reservation()
                _pop_reload_request_if_token(identity, request_token)
                return False
            preflight = await asyncio.wait_for(
                _inspect_utk_document(tab, force=True), timeout=remaining
            )
        else:
            preflight = await _inspect_utk_document(tab, force=True)
    except asyncio.TimeoutError:
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False
    except BaseException:
        with contextlib.suppress(BaseException):
            await asyncio.shield(release_reservation())
        _pop_reload_request_if_token(identity, request_token)
        raise
    preflight_classification = preflight.get("classification")
    if (
        preflight_classification == "unknown"
        and _state["rate_limit_observation"].get(identity)
        == preflight.get("document_id")
    ):
        preflight_classification = "blocked"
    same_blocked_document = (
        preflight.get("document_id") == request.get("document_id")
    )
    recovery_probe_reload = (
        allow_blocked_recovery
        and reservation.is_recovery_probe
        and same_blocked_document
    )
    paced_follower_reload = (
        allow_blocked_recovery
        and not reservation.is_recovery_probe
        and same_blocked_document
        and _state["rate_limit_observation"].get(identity)
        == preflight.get("document_id")
    )
    if preflight_classification == "blocked" and not (
        recovery_probe_reload or paced_follower_reload
    ):
        await _report_utk_rate_limit(
            tab,
            config_dict,
            preflight.get("document_id", ""),
            document_started_at=preflight.get("document_started_at", 0.0),
        )
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False
    if (
        preflight.get("document_id") != request.get("document_id")
        or preflight.get("url") != request.get("expected_url")
        or (
            preflight_classification == "unknown"
            and not allow_unknown_document
        )
    ):
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False
    if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False

    if _utk_emergency_remaining() > 0:
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False

    if dispatch_deadline is not None and time.monotonic() >= dispatch_deadline:
        await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        return False

    try:
        decision = await coordinator.commit_refresh(
            UTK_SPORTS_SCOPE,
            _utk_refresh_identity(tab),
            reservation.reservation_id,
            request_token=request_token,
        )
    except BaseException:
        with contextlib.suppress(BaseException):
            await asyncio.shield(release_reservation())
        _pop_reload_request_if_token(identity, request_token)
        raise
    if not decision.granted:
        # The commit transaction re-checks cooldown and recovery phase.  Any
        # incident opened during preflight therefore revokes this dispatch.
        if decision.status != "already_committed":
            await release_reservation()
        _pop_reload_request_if_token(identity, request_token)
        if decision.error:
            debug.log(
                "[UTK RATE] Shared commit failed closed: "
                f"{decision.error}"
            )
        return False

    if decision.is_recovery_probe:
        started_at = time.monotonic()
        probe_state = {
            "permit_id": decision.permit_id,
            "source_document_id": preflight.get("document_id"),
            "started_at": started_at,
            "next_renew_at": started_at + UTK_RECOVERY_PROBE_RENEW_SECONDS,
        }
        _state["recovery_probe"][identity] = probe_state
    _pop_reload_request_if_token(identity, request_token)
    if decision.is_recovery_probe:
        async def renew_probe_lease():
            while True:
                await asyncio.sleep(UTK_RECOVERY_PROBE_RENEW_SECONDS)
                current = _state["recovery_probe"].get(identity)
                if (
                    not current
                    or current.get("permit_id") != decision.permit_id
                    or current.get("abandon_requested")
                ):
                    return
                if (
                    time.monotonic() - started_at
                    >= UTK_RECOVERY_PROBE_MAX_SECONDS
                ):
                    completion = await get_shared_refresh_coordinator().complete_recovery_probe(
                        UTK_SPORTS_SCOPE,
                        _utk_refresh_identity(tab),
                        decision.permit_id,
                        success=False,
                    )
                    current = _state["recovery_probe"].get(identity)
                    if current and current.get("permit_id") == decision.permit_id:
                        _state["recovery_probe"].pop(identity, None)
                    return
                renewed = await get_shared_refresh_coordinator().renew_recovery_probe(
                    UTK_SPORTS_SCOPE, decision.permit_id
                )
                if renewed is False:
                    current = _state["recovery_probe"].get(identity)
                    if current and current.get("permit_id") == decision.permit_id:
                        _state["recovery_probe"].pop(identity, None)
                    return
                current["next_renew_at"] = time.monotonic() + (
                    UTK_RECOVERY_PROBE_RENEW_SECONDS if renewed else 1.0
                )

        probe_state["lease_task"] = asyncio.create_task(renew_probe_lease())
    try:
        reload_call = tab.reload()
        await asyncio.wait_for(
            reload_call, timeout=UTK_RECOVERY_PROBE_MAX_SECONDS
        )
        _state["document_snapshot"].pop(identity, None)
        _state["reload_event"].add(identity)
        return True
    except Exception as exc:
        debug.log(f"[CTBC] Reload failed: {exc}")
        # A CDP exception does not prove that the browser rejected the
        # navigation.  Keep a recovery lease until the next document guard or
        # lease expiry instead of immediately releasing a possible in-flight
        # probe and creating a parallel retry.
        return False


async def nodriver_utk_sports_reload_wait(
    tab,
    config_dict,
    reason,
    *,
    apply_local_interval=False,
    allow_unknown_document=True,
    allow_blocked_recovery=True,
):
    """Wait in short chunks for startup/fallback callers needing one reload."""
    source_url = str(getattr(getattr(tab, "target", None), "url", ""))
    wait_deadline = time.monotonic() + UTK_RELOAD_WAIT_MAX_SECONDS
    last_heartbeat_at = 0.0
    while True:
        if time.monotonic() >= wait_deadline:
            await nodriver_utk_sports_cancel_refresh(
                tab, "shared reload wait timed out", wait=True, abandon_probe=True
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
                    "w",
                    encoding="utf-8",
                ) as heartbeat_file:
                    heartbeat_file.write(str(int(heartbeat_now)))
            except OSError:
                pass
        if await nodriver_utk_sports_reload(
            tab,
            config_dict,
            reason,
            apply_local_interval=apply_local_interval,
            allow_unknown_document=allow_unknown_document,
            allow_blocked_recovery=allow_blocked_recovery,
            dispatch_deadline=wait_deadline,
        ):
            return True
        if await check_and_handle_pause(config_dict) or await check_and_handle_quit(config_dict):
            await nodriver_utk_sports_cancel_refresh(
                tab, "paused or stopped", wait=True, abandon_probe=True
            )
            return False
        current_url = str(getattr(getattr(tab, "target", None), "url", ""))
        if current_url != source_url:
            await nodriver_utk_sports_cancel_refresh(
                tab, "navigation", wait=True, abandon_probe=True
            )
            return False
        await asyncio.sleep(0.1)


async def _reload_with_interval(tab, config_dict, reason):
    return await nodriver_utk_sports_reload(
        tab,
        config_dict,
        reason,
        apply_local_interval=True,
        allow_unknown_document=False,
    )


async def _poll_ctbc_quantity_reload(tab, config_dict):
    """Continue one queued quantity recovery without resetting its interval."""
    request = _state["reload_request"].get(_ctbc_tab_identity(tab))
    if not request or request.get("reason") != CTBC_QUANTITY_STALE_RELOAD_REASON:
        return False
    await _reload_with_interval(
        tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON
    )
    return True


async def nodriver_utk_sports_health_check(tab, url, config_dict):
    """Run only rate-limit recovery, without selecting any ticket control."""
    guard_state, _ = await _utk_document_guard(tab, config_dict, url)
    if guard_state == "blocked":
        await nodriver_utk_sports_reload(
            tab,
            config_dict,
            "HTTP 429 recovery probe",
            apply_local_interval=False,
            allow_unknown_document=False,
            allow_blocked_recovery=True,
        )
    return guard_state


async def _element_text(element):
    try:
        html = await element.get_html()
        return re.sub(r"\s+", " ", util.remove_html_tags(html)).strip()
    except Exception:
        return ""


def _decode_ctbc_json_result(raw_value):
    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _ctbc_selection_config_fingerprint(config_dict):
    area_config = config_dict.get("area_auto_select", {})
    payload = {
        "ticket_number": config_dict.get("ticket_number", 1),
        "keyword_exclude": config_dict.get("keyword_exclude", ""),
        "area_auto_fallback": config_dict.get("area_auto_fallback", False),
        "area_enable": area_config.get("enable", False),
        "area_keyword": area_config.get("area_keyword", ""),
        "area_mode": area_config.get("mode", "from top to bottom"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ctbc_choose_date_candidate(candidates, config_dict):
    date_config = config_dict.get("date_auto_select", {})
    return _choose_candidate_index(
        candidates,
        date_config.get("date_keyword", "").strip(),
        date_config.get("mode", "from top to bottom"),
        config_dict.get("date_auto_fallback", False),
    )


def _ctbc_navigation_urls_match(first_url, second_url):
    try:
        first = urlsplit(str(first_url or ""))
        second = urlsplit(str(second_url or ""))
    except ValueError:
        return False
    first_query = _ctbc_unique_query(first_url)
    second_query = _ctbc_unique_query(second_url)
    return bool(
        not first.fragment
        and not second.fragment
        and _ctbc_same_origin(first, second)
        and first.path == second.path
        and first_query is not None
        and first_query == second_query
    )


def _ctbc_new_pending_navigation(
    tab, kind, source_url, area_url, target, config_dict
):
    destination_url = _build_ctbc_destination_url(area_url, target)
    area_query = _ctbc_unique_query(area_url)
    if (
        not destination_url
        or area_query is None
        or set(area_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}
        or not re.fullmatch(r"[A-Za-z0-9_-]+", area_query.get("PRODUCT_ID", ""))
    ):
        return None
    identity = _ctbc_tab_identity(tab)
    return {
        "kind": kind,
        "phase": "PREPARED",
        "nonce": uuid.uuid4().hex,
        "source_url": source_url,
        "source_document_id": _state["last_document"].get(identity, ""),
        "area_url": area_url,
        "destination_url": destination_url,
        "route": target.get("route"),
        "performance_id": target.get("performance_id"),
        "product_id": area_query.get("PRODUCT_ID"),
        "group_id": target.get("group_id"),
        "price_area_id": target.get("price_area_id"),
        "config_fingerprint": _ctbc_selection_config_fingerprint(config_dict),
        "created_at": time.monotonic(),
    }


def _ctbc_pending_from_fast_marker(marker):
    if (
        not isinstance(marker, dict)
        or type(marker.get("version")) is not int
        or marker.get("version") != 1
    ):
        return None
    nonce = str(marker.get("nonce") or "")
    source_url = str(marker.get("source") or "")
    destination_url = str(marker.get("destination") or "")
    expires_value = marker.get("expires_at")
    if isinstance(expires_value, bool) or not isinstance(expires_value, (int, float)):
        return None
    try:
        expires_at = float(expires_value)
        source = urlsplit(source_url)
        destination = urlsplit(destination_url)
    except (TypeError, ValueError):
        return None
    source_query = _ctbc_unique_query(source_url)
    destination_query = _ctbc_unique_query(destination_url)
    page_type = _get_ctbc_page_type(destination_url)
    token = re.compile(r"[A-Za-z0-9_-]+")
    max_expiry = time.time() * 1000 + 1000 * (
        CTBC_PENDING_NAVIGATION_TTL_SECONDS
        + CTBC_FAST_MARKER_CLOCK_SKEW_SECONDS
    )
    if (
        not re.fullmatch(r"[a-f0-9]{32}", nonce)
        or not 0 < expires_at <= max_expiry
        or _get_ctbc_page_type(source_url) != "date"
        or page_type not in {"seat", "general_admission"}
        or not _ctbc_same_origin(source, destination)
        or source.path.rsplit("/", 1)[0] != destination.path.rsplit("/", 1)[0]
        or source_query is None
        or destination_query is None
        or set(destination_query) != {
            "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
        }
    ):
        return None
    product_id = str(source_query.get("PRODUCT_ID") or "")
    performance_id = str(destination_query.get("PERFORMANCE_ID") or "")
    group_id = str(destination_query.get("GROUP_ID") or "")
    price_area_id = str(destination_query.get("PERFORMANCE_PRICE_AREA_ID") or "")
    if not all(token.fullmatch(value) for value in (
        product_id, performance_id, price_area_id
    )) or (group_id and not token.fullmatch(group_id)) or (
        page_type == "seat" and not group_id
    ):
        return None
    area_path = f"{source.path.rsplit('/', 1)[0]}/UTK0204_"
    area_url = urlunsplit((source.scheme, source.netloc, area_path, urlencode({
        "PERFORMANCE_ID": performance_id, "PRODUCT_ID": product_id,
    }), ""))
    created_at = (
        time.monotonic()
        + (expires_at - time.time() * 1000) / 1000
        - CTBC_PENDING_NAVIGATION_TTL_SECONDS
    )
    return {
        "kind": "FAST", "phase": "RETURNING", "nonce": nonce,
        "source_url": source_url, "source_document_id": "",
        "area_url": area_url, "destination_url": destination_url,
        "route": "0205" if page_type == "seat" else "0202",
        "performance_id": performance_id, "product_id": product_id,
        "group_id": group_id, "price_area_id": price_area_id,
        "marker_expires_at": expires_at, "created_at": created_at,
    }


def _ctbc_fast_marker_matches_pending(marker_pending, pending):
    if not marker_pending or not pending or pending.get("kind") != "FAST":
        return False
    if marker_pending.get("nonce") != pending.get("nonce"):
        return False
    for key in (
        "route", "performance_id", "product_id", "group_id", "price_area_id"
    ):
        if marker_pending.get(key) != pending.get(key):
            return False
    return all(
        _ctbc_navigation_urls_match(marker_pending.get(key), pending.get(key))
        for key in ("source_url", "area_url", "destination_url")
    )


async def _ctbc_prefetch_area_snapshot(tab, date_target):
    """Fetch one selected area's fresh HTML and exact map in the current browser session."""
    area_url_json = json.dumps(date_target["url"])
    performance_json = json.dumps(date_target["performance_id"])
    product_json = json.dumps(date_target["product_id"])
    map_namespace_json = json.dumps(date_target["map_namespace"])
    raw_value = await tab.evaluate(f'''
        (async () => {{
            const started = performance.now();
            const areaUrl = {area_url_json};
            const expectedPerformance = {performance_json};
            const expectedProduct = {product_json};
            const mapNamespace = {map_namespace_json};
            const fail = reason => JSON.stringify({{ok: false, reason}});
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 1200);
            try {{
                const response = await fetch(areaUrl, {{
                    credentials: 'include', cache: 'no-store', redirect: 'manual',
                    signal: controller.signal
                }});
                if (!response.ok || response.type === 'opaqueredirect' || response.url !== areaUrl) {{
                    return fail('area-response');
                }}
                const contentType = (response.headers.get('content-type') || '').toLowerCase();
                if (!contentType.startsWith('text/html')) return fail('area-content-type');
                const html = await response.text();
                if (!html || html.length > 250000) return fail('area-size');
                const doc = new DOMParser().parseFromString(html, 'text/html');
                const rows = Array.from(doc.querySelectorAll('.seats table.f1 > tbody > tr.saleTr'));
                if (!rows.length || rows.length > 200 || !doc.querySelector('#mapdata')) return fail('area-markers');
                if (doc.querySelector('#byServer, #ATYPE_CB') || /\\bSendA\\s*\\(/.test(html)) return fail('special-mode');

                const uniqueMatch = regex => {{
                    const values = Array.from(html.matchAll(regex), match => match[1]);
                    return values.length === 1 ? values[0] : null;
                }};
                const lp = uniqueMatch(/\\bvar\\s+_lp\\s*=\\s*['\"]([^'\"]+)['\"]\\s*;/g);
                const wt = uniqueMatch(/\\bvar\\s+_wt\\s*=\\s*([0-9]+)\\s*;/g);
                const season = uniqueMatch(/\\bvar\\s+seasonTicketId\\s*=\\s*['\"]([^'\"]*)['\"]\\s*;/g);
                const product = uniqueMatch(/\\bvar\\s+productId\\s*=\\s*['\"]([^'\"]+)['\"]\\s*;/g);
                if (lp !== '0' || wt === null || season !== '' || product !== expectedProduct) return fail('special-flags');

                const mapPattern = /https:\\/\\/imgs2\\.utiki\\.com\\.tw\\/Data\\/[A-Za-z0-9_]+\\/Images\\/Temp\\/[A-Za-z0-9_-]+\\/1_[A-Za-z0-9_-]+_live\\.map\\?v=[A-Za-z0-9_-]+/g;
                const mapUrls = Array.from(new Set(html.match(mapPattern) || []));
                if (mapUrls.length !== 1) return fail('map-url-count');
                const mapUrl = new URL(mapUrls[0]);
                const expectedMapPath = `/Data/${{mapNamespace}}/Images/Temp/${{expectedPerformance}}/1_${{expectedPerformance}}_live.map`;
                const mapKeys = Array.from(mapUrl.searchParams.keys());
                const version = mapUrl.searchParams.get('v') || '';
                if (mapUrl.protocol !== 'https:' || mapUrl.hostname !== 'imgs2.utiki.com.tw' ||
                    mapUrl.pathname !== expectedMapPath || mapKeys.length !== 1 || mapKeys[0] !== 'v' ||
                    !/^[A-Za-z0-9_-]{{8,128}}$/.test(version) || mapUrl.hash) {{
                    return fail('map-url');
                }}

                const mapResponse = await fetch(mapUrl.href, {{
                    credentials: 'omit', cache: 'no-store', redirect: 'error',
                    signal: controller.signal
                }});
                if (!mapResponse.ok) return fail('map-response');
                const mapType = (mapResponse.headers.get('content-type') || '').toLowerCase();
                if (!mapType.startsWith('text/plain') && !mapType.startsWith('text/html')) return fail('map-content-type');
                const mapText = await mapResponse.text();
                if (!mapText || mapText.length > 100000) return fail('map-size');
                const mapDoc = new DOMParser().parseFromString(mapText, 'text/html');
                const areas = Array.from(mapDoc.querySelectorAll('area[id][href]')).map(area => ({{
                    id: area.getAttribute('id') || '',
                    href: area.getAttribute('href') || '',
                    title: area.getAttribute('title') || ''
                }}));
                if (!areas.length || areas.length > 500) return fail('map-markers');
                return JSON.stringify({{
                    ok: true,
                    rows: rows.map(row => ({{
                        area: (row.querySelector('td[data-title^="票區"]')?.textContent || '').trim(),
                        price: (row.querySelector('td[data-title^="票價"]')?.textContent || '').trim(),
                        status: (row.querySelector('td[data-title^="空位"]')?.textContent || '').trim(),
                        rel: row.getAttribute('rel') || ''
                    }})),
                    areas,
                    map_url: mapUrl.href,
                    elapsed_ms: performance.now() - started
                }});
            }} catch (error) {{
                return fail(error?.name === 'AbortError' ? 'timeout' : 'fetch-error');
            }} finally {{
                clearTimeout(timeout);
            }}
        }})()
    ''', await_promise=True)
    return _decode_ctbc_json_result(raw_value)


async def _ctbc_read_fast_navigation_marker(tab):
    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    try:
        raw_value = await tab.evaluate(f'''(() => {{
            // __ticketsHunterCtbcFastMarkerRead
            try {{
                const raw = sessionStorage.getItem({marker_key_json});
                if (raw === null) return JSON.stringify({{
                    available: true, present: false, marker: null,
                    raw: null, page_url: location.href
                }});
                try {{
                    return JSON.stringify({{
                        available: true, present: true, marker: JSON.parse(raw),
                        raw, page_url: location.href
                    }});
                }} catch (_error) {{
                    return JSON.stringify({{
                        available: true, present: true, marker: null,
                        raw, page_url: location.href
                    }});
                }}
            }} catch (_error) {{
                return JSON.stringify({{
                    available: false, present: false, marker: null,
                    page_url: location.href
                }});
            }}
        }})()''')
    except Exception:
        return {"available": False, "present": False, "marker": None}
    result = _decode_ctbc_json_result(raw_value)
    return result if isinstance(result, dict) else {
        "available": False, "present": False, "marker": None,
    }


async def _ctbc_delete_fast_navigation_marker(tab, nonce):
    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    nonce_json = json.dumps(nonce)
    try:
        return await tab.evaluate(f'''(() => {{
            // __ticketsHunterCtbcFastMarkerDelete
            const expectedNonce = {nonce_json};
            try {{
                const raw = sessionStorage.getItem({marker_key_json});
                if (raw === null) return true;
                const marker = JSON.parse(raw);
                if (!marker || marker.nonce !== expectedNonce) return false;
                sessionStorage.removeItem({marker_key_json});
                return true;
            }} catch (_error) {{
                return false;
            }}
        }})()''')
    except Exception:
        return False


async def _ctbc_delete_malformed_fast_marker(tab, expected_raw):
    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    expected_raw_json = json.dumps(expected_raw)
    try:
        return await tab.evaluate(f'''(() => {{
            // __ticketsHunterCtbcMalformedMarkerDelete
            const expectedRaw = {expected_raw_json};
            try {{
                const current = sessionStorage.getItem({marker_key_json});
                if (current !== expectedRaw) return false;
                const confirmed = sessionStorage.getItem({marker_key_json});
                if (confirmed !== expectedRaw) return false;
                sessionStorage.removeItem({marker_key_json});
                return true;
            }} catch (_error) {{
                return false;
            }}
        }})()''')
    except Exception:
        return False


async def _ctbc_commit_fast_navigation(tab, pending, onclick):
    source_json = json.dumps(pending["source_url"])
    onclick_json = json.dumps(onclick)
    destination_json = json.dumps(pending["destination_url"])
    nonce_json = json.dumps(pending["nonce"])
    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    ttl_ms = int(CTBC_PENDING_NAVIGATION_TTL_SECONDS * 1000)
    return await tab.evaluate(f'''
        (() => {{
            // __ticketsHunterCtbcFastCommit
            const sourceUrl = {source_json};
            const expectedOnclick = {onclick_json};
            const destinationUrl = {destination_json};
            const navigationNonce = {nonce_json};
            const markerKey = {marker_key_json};
            if (location.href !== sourceUrl || window.__ticketsHunterCtbcDateCommit) return 'pending';
            const matches = Array.from(document.querySelectorAll('#PerformanceListTable button#buy_btn'))
                .filter(button => (button.getAttribute('onclick') || '') === expectedOnclick);
            if (matches.length !== 1) return 'rejected';
            const button = matches[0];
            const row = button.closest('tr');
            const cell = button.closest('td');
            const style = getComputedStyle(button);
            if (!button.isConnected || !row || !cell ||
                !row.closest('#PerformanceListTable') || cell !== row.lastElementChild ||
                button.getClientRects().length === 0 || style.display === 'none' ||
                style.visibility === 'hidden' || style.pointerEvents === 'none' ||
                button.closest('[hidden], [inert], [aria-hidden="true"]') ||
                button.matches(':disabled') || button.getAttribute('aria-disabled') === 'true') return 'rejected';
            const marker = {{version: 1, nonce: navigationNonce, source: sourceUrl,
                destination: destinationUrl, expires_at: Date.now() + {ttl_ms}}};
            try {{
                const existing = JSON.parse(sessionStorage.getItem(markerKey) || 'null');
                if (existing) {{
                    if (!/^[a-f0-9]{{32}}$/.test(existing.nonce || '') ||
                        existing.version !== 1 || existing.expires_at > Date.now()) return 'pending';
                    const current = JSON.parse(sessionStorage.getItem(markerKey) || 'null');
                    if (!current || current.nonce !== existing.nonce) return 'pending';
                    sessionStorage.removeItem(markerKey);
                }}
                sessionStorage.setItem(markerKey, JSON.stringify(marker));
                const stored = JSON.parse(sessionStorage.getItem(markerKey) || 'null');
                if (!stored || stored.version !== marker.version ||
                    stored.expires_at !== marker.expires_at ||
                    stored.nonce !== navigationNonce ||
                    stored.source !== sourceUrl || stored.destination !== destinationUrl) {{
                    if (stored && stored.nonce === navigationNonce) sessionStorage.removeItem(markerKey);
                    return 'rejected';
                }}
            }} catch (_error) {{
                return 'rejected';
            }}
            window.__ticketsHunterCtbcDateCommit = navigationNonce;
            location.assign(destinationUrl);
            return 'dispatched';
        }})()
    ''')


async def _ctbc_try_fast_area_navigation(tab, config_dict, date_target, onclick, source_url):
    """Use the attested normal-mode shortcut; False alone permits native fallback."""
    debug = util.create_debug_logger(config_dict)
    identity = _ctbc_tab_identity(tab)
    if _state["pending_navigation"].get(identity):
        return True
    if not date_target.get("fast_area_prefetch", False):
        debug.log("[UTK SPORTS] Native area flow required by this platform")
        return False
    started = time.monotonic()
    try:
        snapshot = await _ctbc_prefetch_area_snapshot(tab, date_target)
    except Exception as exc:
        debug.log(f"[CTBC FAST] Prefetch failed: {exc}")
        return False
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        reason = snapshot.get("reason", "invalid-result") if isinstance(snapshot, dict) else "invalid-result"
        debug.log(f"[CTBC FAST] Native area-page fallback: {reason}")
        return False
    if time.monotonic() - started > CTBC_FAST_PATH_TTL_SECONDS:
        debug.log("[CTBC FAST] Native area-page fallback: snapshot expired")
        return False
    target = _select_ctbc_area_snapshot(
        snapshot, config_dict, date_target["performance_id"], require_direct_destination=True
    )
    if not target or not target.direct_destination:
        debug.log("[CTBC FAST] Native area-page fallback: no attested area matched")
        return False
    target_action = _ctbc_area_action_payload(target)
    pending = _ctbc_new_pending_navigation(
        tab, "FAST", source_url, date_target["url"], target_action, config_dict
    )
    if not pending:
        return False
    if _state["pending_navigation"].get(identity):
        return True
    _state["pending_navigation"][identity] = pending
    try:
        committed = await _ctbc_commit_fast_navigation(tab, pending, onclick)
    except Exception as exc:
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC FAST] Dispatch outcome unknown: {exc}")
        return True
    if committed is False or committed == "rejected":
        await _ctbc_delete_fast_navigation_marker(tab, pending["nonce"])
        _pop_pending_navigation_if_nonce(identity, pending["nonce"])
        return bool(_state["pending_navigation"].get(identity))
    if committed == "pending":
        pending["phase"] = "DISPATCH_UNKNOWN"
        return True
    pending["phase"] = "DISPATCH_UNKNOWN"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    debug.log(
        f"[CTBC FAST] Selected: {target.text} ({target.status}) "
        f"in {elapsed_ms} ms"
    )
    return True


async def nodriver_ctbc_date_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    if not await _ctbc_reconcile_navigation_source(
        tab, current_url, "date", config_dict
    ):
        return False
    rows = []
    for _ in range(10):
        try:
            rows = await tab.query_selector_all(CTBC_DATE_ROW_SELECTOR)
        except Exception:
            rows = []
        if rows:
            break
        await tab.sleep(0.2)

    candidates = []
    for row in rows or []:
        try:
            controls = await row.query_selector_all(CTBC_DATE_PURCHASE_CONTROL_SELECTOR)
            if not controls:
                # Compatibility with older DOM/test adapters.  The structural
                # snapshot below still requires the control to be in the last
                # purchase cell of the official performance table.
                legacy_button = await row.query_selector('button#buy_btn')
                controls = [legacy_button] if legacy_button else []

            actionable_controls = []
            for control in controls:
                snapshot = await _ctbc_date_control_snapshot(control)
                if isinstance(snapshot, dict) and snapshot.get("actionable"):
                    actionable_controls.append((control, snapshot))
            if len(actionable_controls) != 1:
                continue

            button, button_snapshot = actionable_controls[0]
            onclick = str(button_snapshot.get("onclick") or "")
            action = _ctbc_date_control_action(button_snapshot, current_url)
            if not action:
                continue
            row_text = await _element_text(row)
            if not row_text or util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                continue
            candidates.append({
                "text": row_text,
                "element": button,
                "onclick": onclick,
                "fingerprint": {
                    key: (
                        bool(button_snapshot.get(key))
                        if key == "has_form"
                        else str(button_snapshot.get(key) or "")
                    )
                    for key in (
                        "tag_name", "href", "resolved_href", "role", "type",
                        "target", "download", "ping", "formaction", "has_form",
                        "onclick",
                    )
                },
                "action": action,
            })
        except Exception as exc:
            debug.log(f"[CTBC DATE] Failed to inspect row: {exc}")

    target_index = _ctbc_choose_date_candidate(candidates, config_dict)

    if target_index is None:
        await _reload_with_interval(tab, config_dict, "no purchasable date matched")
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "purchasable date found")
    target = candidates[target_index]
    try:
        action = target["action"]
        date_target = action.get("date_target") if action.get("kind") == "normal" else None
        if date_target and config_dict.get("area_auto_select", {}).get("enable", False):
            if await _ctbc_try_fast_area_navigation(
                tab, config_dict, date_target, target["onclick"], current_url
            ):
                return True
        committed = await _ctbc_commit_native_date_action(
            target["element"],
            current_url,
            target["fingerprint"],
            direct_url=(
                action.get("date_target", {}).get("url", "")
                if action.get("kind") == "anchor_native"
                else ""
            ),
        )
        if not committed:
            debug.log(f"[CTBC DATE] Native commit rejected ({action.get('kind', 'unknown')})")
            return False
        debug.log(f"[CTBC DATE] Selected ({action.get('kind')}): {target['text']}")
        return True
    except Exception as exc:
        debug.log(f"[CTBC DATE] Click failed: {exc}")
        return False


async def nodriver_ctbc_area_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    if not await _ctbc_reconcile_area_navigation(
        tab, current_url, config_dict
    ):
        return False
    try:
        snapshot = await read_area_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC AREA] Action-ready wait failed: {exc}")
        snapshot = None
    if not isinstance(snapshot, AreaSnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason
            if isinstance(snapshot, AreaSnapshot)
            else "invalid-result"
        )
        await _reload_with_interval(tab, config_dict, f"area map was not ready ({reason})")
        return False

    page_url = snapshot.page_url
    query = _ctbc_unique_query(page_url)
    performance_id = query.get("PERFORMANCE_ID", "") if query else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", performance_id):
        return False
    target = _select_ctbc_area_snapshot(
        snapshot, config_dict, performance_id, require_direct_destination=False
    )
    if target is None:
        await _reload_with_interval(tab, config_dict, "no available area matched")
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "available area found")
    identity = _ctbc_tab_identity(tab)
    if _state["pending_navigation"].get(identity):
        return False
    pending = _ctbc_new_pending_navigation(
        tab, "NATIVE", page_url, page_url,
        _ctbc_area_action_payload(target), config_dict,
    )
    if not pending:
        return False
    _state["pending_navigation"][identity] = pending
    try:
        commit_result = await commit_area(
            tab, snapshot, target, config_dict.get("ticket_number", 1),
            pending["nonce"], CTBC_FAST_MARKER_KEY,
        )
        if commit_result in {CommitResult.REJECTED, CommitResult.STALE}:
            _pop_pending_navigation_if_nonce(identity, pending["nonce"])
            debug.log(
                "[CTBC AREA] Area commit "
                f"{commit_result.value}; no click was sent"
            )
            return False
        if commit_result is not CommitResult.COMMITTED:
            raise RuntimeError("area commit returned an unknown result")
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC AREA] Selected: {target.text} ({target.status})")
        return True
    except Exception as exc:
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC AREA] Click failed: {exc}")
        return False


async def nodriver_ctbc_ticket_type_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    buttons = []
    for _ in range(10):
        try:
            buttons = await tab.query_selector_all('.ticket button[onclick*="setType"]')
        except Exception:
            buttons = []
        if buttons:
            break
        await tab.sleep(0.2)

    candidates = []
    for button in buttons or []:
        try:
            disabled = await button.apply('function(el) { return !!el.disabled; }')
            text = await _element_text(button)
            if disabled or not text:
                continue
            candidates.append({"text": text, "element": button})
        except Exception as exc:
            debug.log(f"[CTBC TICKET TYPE] Failed to inspect button: {exc}")

    keyword = _get_utiki_behavior(config_dict, "ticket_type_keyword", "全票")
    target_index = _choose_candidate_index(candidates, keyword, "from top to bottom", False)
    if target_index is None:
        debug.log(f"[CTBC TICKET TYPE] No ticket type matched: {keyword}")
        return False

    try:
        target = candidates[target_index]
        await target["element"].click()
        counter = await target["element"].query_selector('.checkNum')
        if not counter:
            return False
        type_id = await counter.apply('function(el) { return el.id || ""; }')
        type_name = await counter.apply('function(el) { return el.getAttribute("val") || ""; }')
        if not type_id or not type_name:
            return False
        _state["ticket_type"][_ctbc_tab_state_key(tab)] = {
            "id": type_id,
            "name": type_name,
        }
        debug.log(f"[CTBC TICKET TYPE] Selected: {target['text']}")
        return True
    except Exception as exc:
        debug.log(f"[CTBC TICKET TYPE] Click failed: {exc}")
        return False


async def nodriver_ctbc_seat_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    ticket_number = int(config_dict.get("ticket_number", 1))
    seat_elements = []
    for _ in range(12):
        try:
            seat_elements = await tab.query_selector_all('#TBL td[title]')
        except Exception:
            seat_elements = []
        if seat_elements:
            break
        await tab.sleep(0.2)

    raw_state = await tab.evaluate('''
        (() => {
            const seats = Array.from(document.querySelectorAll('#TBL td[title]'));
            const metadata = seats.map((seat, index) => {
                const style = (seat.getAttribute('style') || '').toLowerCase();
                const selected = style.includes('icon_chair_select');
                const available = !selected &&
                    (style.includes('seat-empty') || getComputedStyle(seat).cursor === 'pointer');
                const rect = seat.getBoundingClientRect();
                return {
                    index: index,
                    title: seat.getAttribute('title') || '',
                    row_index: seat.parentElement ? seat.parentElement.rowIndex : 0,
                    cell_index: seat.cellIndex,
                    center_x: rect.left + rect.width / 2,
                    center_y: rect.top + rect.height / 2,
                    width: rect.width,
                    height: rect.height,
                    selected: selected,
                    available: available
                };
            });
            const limitInput = document.querySelector('#QUANTITY_LIMIT');
            return {
                seats: metadata,
                selected_count: metadata.filter(seat => seat.selected).length,
                quantity_limit: limitInput ? parseInt(limitInput.value || '0', 10) : 0
            };
        })()
    ''')
    state = util.parse_nodriver_result(raw_state)
    if not isinstance(state, dict):
        return False

    selected_count = int(state.get("selected_count", 0) or 0)
    if selected_count >= ticket_number:
        return await _validate_ctbc_seat_selection(tab, config_dict)

    quantity_limit = int(state.get("quantity_limit", 0) or 0)
    if quantity_limit and ticket_number > quantity_limit:
        debug.log(f"[CTBC SEAT] Requested {ticket_number}, limit is {quantity_limit}")
        return False

    needed = ticket_number - selected_count
    indexes = _select_ctbc_seat_indexes(
        state.get("seats", []),
        needed,
        config_dict.get("advanced", {}).get("disable_adjacent_seat", False),
        _get_utiki_behavior(config_dict, "seat_sorting", "row_seat"),
    )
    if len(indexes) != needed:
        debug.log(f"[CTBC SEAT] No suitable block for {needed} seat(s)")
        return False

    clicked = 0
    for index in indexes:
        if index < 0 or index >= len(seat_elements):
            continue
        try:
            await seat_elements[index].click()
            clicked += 1
        except Exception:
            try:
                await seat_elements[index].apply('function(el) { el.click(); }')
                clicked += 1
            except Exception as exc:
                debug.log(f"[CTBC SEAT] Click failed at index {index}: {exc}")

    if clicked != needed:
        return False

    await tab.sleep(0.2)
    verified = await _validate_ctbc_seat_selection(tab, config_dict)
    debug.log(f"[CTBC SEAT] Selected {clicked} seat(s), verified={verified}")
    return verified


async def _get_ctbc_selected_seat_titles(tab):
    raw = await tab.evaluate('''
        (() => JSON.stringify(
            Array.from(document.querySelectorAll('#TBL td[title]'))
                .filter(seat => (seat.getAttribute('style') || '').includes('icon_chair_select'))
                .map(seat => seat.getAttribute('title') || '')
                .filter(Boolean)
                .sort()
        ))()
    ''')
    try:
        result = json.loads(raw) if isinstance(raw, str) else []
    except (TypeError, ValueError):
        result = []
    return tuple(result) if isinstance(result, list) else ()


async def _clear_ctbc_selected_seats(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    try:
        selected = await tab.query_selector_all('#TBL td[style*="icon_chair_select"]')
    except Exception:
        selected = []
    for seat in selected or []:
        try:
            await seat.click()
        except Exception:
            try:
                await seat.apply('function(el) { el.click(); }')
            except Exception as exc:
                debug.log(f"[CTBC SEAT] Failed to clear a selected seat: {exc}")
                return False
    if selected:
        await tab.sleep(0.2)
    return len(await _get_ctbc_selected_seat_titles(tab)) == 0


async def _validate_ctbc_seat_selection(tab, config_dict):
    """Mirror CTBC's cart payload and fail closed on mixed/extra seats."""
    debug = util.create_debug_logger(config_dict)
    ticket_number = int(config_dict.get("ticket_number", 1))
    type_info = _state["ticket_type"].get(_ctbc_tab_state_key(tab))
    if not type_info:
        return False

    raw = await tab.evaluate(f'''
        (() => {{
            const targetId = {json.dumps(type_info["id"])};
            const target = document.getElementById(targetId);
            if (!target || !window.seats) return JSON.stringify({{reason: 'state-missing'}});

            const counters = Array.from(document.querySelectorAll('.ticket .checkNum')).map(el => ({{
                id: el.id || '',
                count: Number((el.textContent || '').trim() || 0)
            }}));
            const submittedSeats = Object.values(window.seats).filter(
                seat => seat && String(seat.A || '') !== ''
            ).map(seat => ({{A: seat.A || '', Y: seat.Y || '', Z: seat.Z || ''}}));
            const visualCount = Array.from(document.querySelectorAll('#TBL td[title]')).filter(
                seat => getComputedStyle(seat).backgroundImage.includes('icon_chair_select_')
            ).length;
            return JSON.stringify({{
                submitted_seats: submittedSeats,
                counters: counters,
                visual_count: visualCount
            }});
        }})()
    ''')
    try:
        audit = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        audit = {}
    is_valid = _ctbc_seat_audit_is_valid(audit, ticket_number, type_info)
    if not is_valid:
        debug.log(f"[CTBC SEAT] Strict selection audit failed: {audit}")
    return is_valid


def _ctbc_seat_audit_is_valid(audit, ticket_number, type_info):
    if not isinstance(audit, dict) or not isinstance(type_info, dict):
        return False
    submitted_seats = audit.get("submitted_seats")
    counters = audit.get("counters")
    if not isinstance(submitted_seats, list) or not isinstance(counters, list):
        return False
    if len(submitted_seats) != ticket_number or audit.get("visual_count") != ticket_number:
        return False
    if any(
        seat.get("A") != "S"
        or seat.get("Y") != type_info.get("id")
        or seat.get("Z") != type_info.get("name")
        for seat in submitted_seats
        if isinstance(seat, dict)
    ) or any(not isinstance(seat, dict) for seat in submitted_seats):
        return False

    target_count = None
    other_count = 0
    total_count = 0
    for counter in counters:
        if not isinstance(counter, dict):
            return False
        try:
            count = int(counter.get("count", 0))
        except (TypeError, ValueError):
            return False
        total_count += count
        if counter.get("id") == type_info.get("id"):
            target_count = count
        else:
            other_count += count
    return target_count == ticket_number and other_count == 0 and total_count == ticket_number


async def _fill_ctbc_inline_login(tab, config_dict):
    state_raw = await tab.evaluate('''
        (() => {
            const input = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!input) return {required: false};
            const style = getComputedStyle(input);
            const required = style.display !== 'none' && style.visibility !== 'hidden' && input.offsetParent !== null;
            return {
                required: required,
                account: input.value || '',
                password: password ? password.value : ''
            };
        })()
    ''')
    state = util.parse_nodriver_result(state_raw)
    if not isinstance(state, dict) or not state.get("required", False):
        return True
    if state.get("account") and state.get("password"):
        return True

    auth_profile = get_tenant_auth_profile(str(tab.target.url))
    if not auth_profile or not auth_profile.supports_inline_login:
        return False
    accounts = config_dict.get("accounts", {})
    account = str(
        state.get("account") or accounts.get(auth_profile.account_key, "")
    ).strip()
    password = str(
        state.get("password") or accounts.get(auth_profile.password_key, "")
    ).strip()
    if not account or not password:
        return False

    result = await tab.evaluate(f'''
        (() => {{
            const expectedOrigin = {json.dumps(auth_profile.inline_login_origin)};
            const currentUrl = new URL(location.href);
            if (
                currentUrl.origin !== expectedOrigin ||
                currentUrl.username ||
                currentUrl.password
            ) return false;
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            if (!account || !password) return false;
            account.value = {json.dumps(account)};
            password.value = {json.dumps(password)};
            account.dispatchEvent(new Event('input', {{bubbles: true}}));
            password.dispatchEvent(new Event('input', {{bubbles: true}}));
            return true;
        }})()
    ''')
    return bool(result)


async def _ctbc_login_fingerprint(tab, *, expected_dialog=None):
    if expected_dialog is not None and not isinstance(
        expected_dialog, DialogSnapshot
    ):
        return ""
    expected_json = "null"
    if expected_dialog is not None:
        expected_json = json.dumps({
            "pageUrl": expected_dialog.page_url,
            "documentId": expected_dialog.document_id,
            "text": expected_dialog.text,
        }, ensure_ascii=False)
    raw = await tab.evaluate(f'''
        (() => {{
            const expectedDialog = {expected_json};
            if (expectedDialog) {{
                if (location.href !== expectedDialog.pageUrl ||
                    String(performance.timeOrigin) !==
                        expectedDialog.documentId) {{
                    return JSON.stringify({{ok: false}});
                }}
                const visible = element => {{
                    if (!element || !element.isConnected || element.hidden ||
                        element.getAttribute('aria-hidden') === 'true') {{
                        return false;
                    }}
                    const style = getComputedStyle(element);
                    return style.display !== 'none' &&
                        style.visibility !== 'hidden' &&
                        element.getClientRects().length > 0;
                }};
                const dialogs = Array.from(
                    document.querySelectorAll('#dialog-message'))
                    .map(message => ({{
                        message,
                        wrapper: message.closest('.ui-dialog')
                    }}))
                    .filter(item =>
                        visible(item.wrapper) && visible(item.message));
                if (dialogs.length !== 1 ||
                    (dialogs[0].message.textContent || '').trim() !==
                        expectedDialog.text) {{
                    return JSON.stringify({{ok: false}});
                }}
            }}
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            return JSON.stringify({{
                ok: true,
                account: account ? account.value : '',
                password: password ? password.value : ''
            }});
        }})()
    ''')
    try:
        credentials = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        credentials = {}
    if (
        credentials.get("ok") is not True
        or not credentials.get("account")
        or not credentials.get("password")
    ):
        return ""
    material = f"{credentials['account']}\0{credentials['password']}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


async def _get_ctbc_visible_dialog_text(tab):
    """Compatibility wrapper for callers that still consume dialog text."""
    snapshot = await read_dialog_snapshot(tab)
    if snapshot and snapshot.visible_count == 1:
        return snapshot.text
    return ""


async def _ctbc_dialog_is_visible(tab):
    """Fail closed when dialog observation is malformed or ambiguous."""
    snapshot = await read_dialog_snapshot(tab)
    return snapshot is None or snapshot.visible_count != 0


async def _wait_ctbc_dialog_closed(tab, snapshot):
    for _ in range(10):
        await tab.sleep(0.1)
        current = await read_dialog_snapshot(tab)
        if current is None:
            return False
        if (
            current.page_url != snapshot.page_url
            or current.document_id != snapshot.document_id
        ):
            return False
        if current.visible_count == 0:
            return True
        if current.visible_count != 1 or current.text != snapshot.text:
            return False
    return False


async def _dismiss_ctbc_dialog(tab, expected_text):
    """Compatibility wrapper around the dialog snapshot/commit seam."""
    snapshot = await read_dialog_snapshot(tab)
    if not (
        snapshot
        and snapshot.visible_count == 1
        and snapshot.text == expected_text
    ):
        return False
    if await dismiss_dialog(tab, snapshot) is not CommitResult.COMMITTED:
        return False
    return await _wait_ctbc_dialog_closed(tab, snapshot)


async def _reset_ctbc_captcha(tab, *, refresh_image, submit_key=None):
    reset_source_document_id = ""
    if refresh_image and submit_key is not None:
        identity = _ctbc_tab_identity(tab)
        current_document_id = _state["last_document"].get(identity, "")
        reset = _state["captcha_reset"].get(submit_key)
        if isinstance(reset, dict):
            phase = reset.get("phase")
            source_document_id = str(reset.get("source_document_id") or "")
            if phase == "done":
                return True
            if phase in ("dispatching", "unknown"):
                if not current_document_id or current_document_id == source_document_id:
                    return False
                _state["captcha_reset"].pop(submit_key, None)
        reset_source_document_id = current_document_id

    captcha_input = await tab.query_selector('#CHK')
    if captcha_input:
        await captcha_input.apply('function(el) { el.value = ""; }')
    if refresh_image:
        refresh = await tab.query_selector('#chang_pic')
        if refresh:
            if submit_key is not None:
                _state["captcha_reset"][submit_key] = {
                    "phase": "dispatching",
                    "source_document_id": reset_source_document_id,
                }
            try:
                await refresh.click()
            except Exception:
                if submit_key is not None:
                    _state["captcha_reset"][submit_key]["phase"] = "unknown"
                return False
            if submit_key is not None:
                _state["captcha_reset"][submit_key]["phase"] = "done"
    return True


async def _handle_ctbc_submit_dialog(
    tab, config_dict, submit_key=None, source_url=None
):
    debug = util.create_debug_logger(config_dict)
    dialog_snapshot = await read_dialog_snapshot(tab)
    if dialog_snapshot is None:
        debug.log("[CTBC] Submit dialog observation is invalid; action blocked")
        return "rejected"
    if dialog_snapshot.visible_count == 0:
        return None
    if dialog_snapshot.visible_count != 1 or not dialog_snapshot.text:
        if submit_key is None:
            submit_key = _ctbc_purchase_state_key(tab)
        if submit_key is not None:
            _state["submitted"].pop(submit_key, None)
            if _state["submit_blocked"].get(submit_key) not in (
                "success", "quota"
            ):
                _state["submit_blocked"][submit_key] = "rejected"
        debug.log("[CTBC] Submit dialog is ambiguous; action blocked")
        return "rejected"
    dialog_text = dialog_snapshot.text
    outcome = _classify_ctbc_submit_dialog(dialog_text)
    if outcome is None:
        return None

    if submit_key is None:
        submit_key = _ctbc_purchase_state_key(tab)
    if submit_key is None:
        debug.log("[CTBC] Submit dialog appeared outside a valid purchase context")
        return "rejected"
    _state["submitted"].pop(submit_key, None)
    if _state["submit_blocked"].get(submit_key) in ("unknown", "captcha_pending"):
        _state["submit_blocked"].pop(submit_key, None)
    if outcome == "success":
        if submit_key not in _state["submit_blocked"]:
            debug.log("[CTBC] Cart accepted; same-page resubmit is blocked")
        _state["submit_blocked"][submit_key] = "success"
        return outcome

    if outcome == "seat_conflict":
        identity = _ctbc_tab_identity(tab)
        page_type = _get_ctbc_page_type(source_url or str(tab.target.url))
        _state["seat_conflict_pending"].setdefault(
            submit_key,
            {
                "source_document_id": _state["last_document"].get(identity, ""),
                "page_type": page_type,
                "dialog_dismissed": False,
                "reload_dispatched": False,
            },
        )
    elif outcome == "auth":
        identity = _ctbc_tab_identity(tab)
        auth_failure = {
            "fingerprint": "",
            "source_document_id": _state["last_document"].get(identity, ""),
        }
        _state["auth_failure"][submit_key] = auth_failure
        try:
            login_fingerprint = await _ctbc_login_fingerprint(
                tab, expected_dialog=dialog_snapshot
            )
        except Exception as exc:
            debug.log(
                "[CTBC LOGIN] Credential fingerprint observation failed; "
                f"document-scoped blocker retained: {exc}"
            )
        else:
            if _state["auth_failure"].get(submit_key) is auth_failure:
                auth_failure["fingerprint"] = login_fingerprint
    elif outcome == "captcha":
        _state["submit_blocked"][submit_key] = "captcha_pending"
    elif outcome in ("quota", "rejected"):
        _state["submit_blocked"][submit_key] = outcome

    dismiss_result = await dismiss_dialog(tab, dialog_snapshot)
    if (
        dismiss_result is not CommitResult.COMMITTED
        or not await _wait_ctbc_dialog_closed(tab, dialog_snapshot)
    ):
        debug.log(f"[CTBC] Visible dialog changed before it could be dismissed: {dialog_text}")
        return outcome

    if outcome == "auth":
        debug.log("[CTBC LOGIN] Authentication rejected; waiting for credentials to change")
    elif outcome == "captcha":
        reset_complete = await _reset_ctbc_captcha(
            tab, refresh_image=True, submit_key=submit_key
        )
        if reset_complete and _state["submit_blocked"].get(submit_key) == "captcha_pending":
            _state["submit_blocked"].pop(submit_key, None)
            _state["captcha_reset"].pop(submit_key, None)
        debug.log(f"[CTBC CAPTCHA] Server rejected captcha: {dialog_text}")
    elif outcome == "seat_conflict":
        _state["seat_conflict_pending"][submit_key]["dialog_dismissed"] = True
        debug.log(f"[CTBC SEAT] Selected seats were rejected: {dialog_text}")
        pending = _state["seat_conflict_pending"][submit_key]
        if pending.get("page_type") == "seat" and not await _clear_ctbc_selected_seats(
            tab, config_dict
        ):
            debug.log("[CTBC SEAT] Local seat reset failed; reload remains pending")
        await _reset_ctbc_captcha(tab, refresh_image=False, submit_key=submit_key)
    else:
        debug.log(f"[CTBC] Submit blocked after {outcome}: {dialog_text}")
    return outcome


async def _recover_ctbc_captcha_dialog(tab, config_dict):
    submit_key = _ctbc_purchase_state_key(tab)
    if _state["submit_blocked"].get(submit_key) != "captcha_pending":
        return False
    if await _ctbc_dialog_is_visible(tab):
        return True
    reset_complete = await _reset_ctbc_captcha(
        tab, refresh_image=True, submit_key=submit_key
    )
    if reset_complete and _state["submit_blocked"].get(submit_key) == "captcha_pending":
        _state["submit_blocked"].pop(submit_key, None)
        _state["captcha_reset"].pop(submit_key, None)
    debug = util.create_debug_logger(config_dict)
    if reset_complete:
        debug.log("[CTBC CAPTCHA] Dialog disappeared; captcha was reset before retry")
    else:
        debug.log("[CTBC CAPTCHA] Captcha refresh outcome is unknown; retry remains blocked")
    return True


async def _recover_ctbc_seat_conflict(tab, config_dict):
    submit_key = _ctbc_purchase_state_key(tab)
    pending = _state["seat_conflict_pending"].get(submit_key)
    if not pending:
        return False

    identity = _ctbc_tab_identity(tab)
    current_document_id = _state["last_document"].get(identity, "")
    if current_document_id and current_document_id != pending["source_document_id"]:
        _state["seat_conflict_pending"].pop(submit_key, None)
        return False

    if not pending.get("dialog_dismissed", False):
        if await _ctbc_dialog_is_visible(tab):
            return True
        pending["dialog_dismissed"] = True

    if pending["reload_dispatched"]:
        return True

    if await _reload_with_interval(tab, config_dict, "selected seats were taken"):
        pending["source_document_id"] = _state["last_document"].get(identity, "")
        pending["reload_dispatched"] = True
    return True


def _ctbc_submit_dispatched_at(value):
    if isinstance(value, dict):
        value = value.get("dispatched_at")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ctbc_auth_failure_is_active(tab, submit_key, login_fingerprint):
    failure = _state["auth_failure"].get(submit_key)
    if not failure:
        return False
    if not isinstance(failure, dict):
        if failure == login_fingerprint:
            return True
        _state["auth_failure"].pop(submit_key, None)
        return False

    failed_fingerprint = str(failure.get("fingerprint") or "")
    if failed_fingerprint:
        if failed_fingerprint == login_fingerprint:
            return True
        _state["auth_failure"].pop(submit_key, None)
        return False

    identity = _ctbc_tab_identity(tab)
    current_document_id = _state["last_document"].get(identity, "")
    source_document_id = str(failure.get("source_document_id") or "")
    if current_document_id and current_document_id != source_document_id:
        _state["auth_failure"].pop(submit_key, None)
        return False
    return True


def _normalize_ctbc_captcha_answer(answer):
    """Normalize ddddocr output to CTBC's four uppercase ASCII characters."""
    return re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()


async def _observe_ctbc_submit_outcome(tab, config_dict, source_url, submit_key):
    """Reduce the first observable result after an add-to-cart dispatch."""
    debug = util.create_debug_logger(config_dict)
    submitted = _state["submitted"]
    await tab.sleep(1.0)
    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key, source_url=source_url
    )
    if dialog_outcome is not None:
        return dialog_outcome == "success"

    if _ctbc_submit_reached_checkout(source_url, str(tab.target.url)):
        submitted.pop(submit_key, None)
        _state["submit_blocked"][submit_key] = "success"
        return True

    destination_url = str(tab.target.url)
    destination_key = _ctbc_purchase_state_key(tab)
    destination_page_type = _get_ctbc_page_type(destination_url)
    if (
        destination_key != submit_key
        or destination_page_type not in ("seat", "general_admission")
    ):
        submitted.pop(submit_key, None)
        _state["submit_blocked"][submit_key] = "unknown"
        debug.log("[CTBC] Submit left the purchase page without verified checkout")
    return False


async def _get_ctbc_captcha_image_bytes(tab, config_dict):
    """Capture CTBC's same-origin captcha at its native 90x25 resolution."""
    debug = util.create_debug_logger(config_dict)
    try:
        data_url = await tab.evaluate('''
            (() => {
                const image = document.querySelector('img#chk_pic');
                if (!image || !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) {
                    return '';
                }
                const canvas = document.createElement('canvas');
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
                const context = canvas.getContext('2d');
                if (!context) return '';
                context.drawImage(image, 0, 0, canvas.width, canvas.height);
                return canvas.toDataURL('image/png');
            })()
        ''')
        if isinstance(data_url, str) and data_url.startswith("data:image/"):
            _header, separator, encoded = data_url.partition(",")
            if separator and encoded:
                image_bytes = base64.b64decode(encoded, validate=True)
                if image_bytes:
                    debug.log(f"[CTBC CAPTCHA] Native image: {len(image_bytes)} bytes")
                    return image_bytes
    except Exception as exc:
        debug.log(f"[CTBC CAPTCHA] Native image capture failed: {exc}")

    # Keep the shared DOM screenshot path as a compatibility fallback for
    # browsers that block canvas extraction or change the captcha origin.
    return await nodriver_get_captcha_image_from_dom_snapshot(
        tab, config_dict, preferred_image_id="chk_pic"
    )


async def _ctbc_captcha(tab, config_dict, ocr):
    debug = util.create_debug_logger(config_dict)
    if not ocr:
        debug.log("[CTBC CAPTCHA] OCR engine is unavailable")
        return False

    for _ in range(10):
        image_ready = await tab.evaluate('''
            (() => {
                const image = document.querySelector('img#chk_pic');
                return !!(image && image.src && image.complete && image.naturalWidth > 0);
            })()
        ''')
        if image_ready:
            break
        await tab.sleep(0.1)

    image_bytes = await _get_ctbc_captcha_image_bytes(tab, config_dict)
    if not image_bytes:
        debug.log("[CTBC CAPTCHA] Captcha image was not captured")
        return False
    try:
        answer = _normalize_ctbc_captcha_answer(ocr.classification(image_bytes))
    except Exception as exc:
        debug.log(f"[CTBC CAPTCHA] OCR failed: {exc}")
        return False
    if len(answer) != 4:
        debug.log(f"[CTBC CAPTCHA] OCR returned an invalid length: {len(answer)}")
        refresh = await tab.query_selector('#chang_pic')
        if refresh:
            await refresh.click()
            await tab.sleep(0.3)
        return False

    captcha_input = await tab.query_selector('#CHK')
    if not captcha_input:
        return False
    await captcha_input.click()
    await captcha_input.apply('function(el) { el.value = ""; }')
    await captcha_input.send_keys(answer)
    debug.log("[CTBC CAPTCHA] Four-character answer filled")
    return True


async def _dispatch_ctbc_submit(
    tab, config_dict, submit_key, quantity_plan=None
):
    """Dispatch once; the caller still has to observe a verified outcome."""
    debug = util.create_debug_logger(config_dict)
    button = None
    if quantity_plan is None:
        button = await tab.query_selector('button#addcart')
        if not button:
            return False
    submitted = _state["submitted"]
    if submit_key in submitted:
        return False
    dispatched_at = time.monotonic()
    dispatch_state = {
        "dispatched_at": dispatched_at,
        "phase": "dispatching",
        "source_url": str(tab.target.url),
    }
    submit_nonce = None
    if quantity_plan is not None:
        submit_nonce = uuid.uuid4().hex
        dispatch_state["nonce"] = submit_nonce
    submitted[submit_key] = dispatch_state
    try:
        if quantity_plan is None:
            await button.click()
        else:
            result = await dispatch_quantity_submit(
                tab, quantity_plan
            )
            if result is not SubmitDispatchResult.DISPATCHED:
                if submitted.get(submit_key) is not dispatch_state:
                    return False
                debug.log(
                    f"[CTBC GENERAL] Submit dispatch {result.value}; "
                    "click blocked"
                )
                if (
                    result is SubmitDispatchResult.STALE
                    and str(tab.target.url) == quantity_plan.page_url
                ):
                    try:
                        await _reload_with_interval(
                            tab, config_dict,
                            CTBC_QUANTITY_STALE_RELOAD_REASON,
                        )
                    finally:
                        if submitted.get(submit_key) is dispatch_state:
                            submitted.pop(submit_key, None)
                else:
                    submitted.pop(submit_key, None)
                return False
    except Exception as exc:
        if submitted.get(submit_key) is dispatch_state:
            submitted.pop(submit_key, None)
            _state["submit_blocked"][submit_key] = "unknown"
        debug.log(f"[CTBC] Submit dispatch outcome is unknown: {exc}")
        return False
    if submitted.get(submit_key) is not dispatch_state:
        return False
    submitted[submit_key] = {
        "dispatched_at": dispatched_at,
        "phase": "awaiting_result",
        "source_url": dispatch_state["source_url"],
        **({"nonce": submit_nonce} if submit_nonce else {}),
    }
    return True


async def _ctbc_captcha_and_submit(
    tab, config_dict, ocr, *, quantity_plan=None
):
    debug = util.create_debug_logger(config_dict)
    current_url = str(tab.target.url)
    submit_key = _ctbc_purchase_state_key(tab)
    page_type = _get_ctbc_page_type(current_url)
    if (
        submit_key is None
        or page_type not in ("seat", "general_admission")
    ):
        debug.log("[CTBC] Purchase identity is incomplete; automatic submit is blocked")
        return False
    if (
        (page_type == "general_admission" and not isinstance(
            quantity_plan, QuantityPlan
        ))
        or (page_type == "seat" and quantity_plan is not None)
    ):
        debug.log("[CTBC] Submit plan does not match the purchase page")
        return False

    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if dialog_outcome is not None:
        return dialog_outcome == "success"
    if await _recover_ctbc_captcha_dialog(tab, config_dict):
        return False
    if (
        submit_key in _state["submit_blocked"]
        or submit_key in _state["seat_conflict_pending"]
    ):
        return False

    submitted = _state["submitted"]
    dispatched_at = _ctbc_submit_dispatched_at(submitted.get(submit_key))
    if submit_key in submitted:
        if (
            not dispatched_at
            or time.monotonic() - dispatched_at >= CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS
        ):
            submitted.pop(submit_key, None)
            _state["submit_blocked"][submit_key] = "unknown"
            debug.log("[CTBC] Submit outcome is unknown; automatic resubmit is blocked")
        return False

    login_ready = await _fill_ctbc_inline_login(tab, config_dict)
    if not login_ready:
        debug.log("[CTBC LOGIN] Waiting for login or CTBC account settings")
        return False

    login_fingerprint = await _ctbc_login_fingerprint(tab)
    if _ctbc_auth_failure_is_active(tab, submit_key, login_fingerprint):
        debug.log("[CTBC LOGIN] Previous authentication failure; waiting for credentials to change")
        return False

    captcha_value = await tab.evaluate('''
        (() => {
            const input = document.querySelector('#CHK');
            return input ? input.value.trim() : '';
        })()
    ''')
    captcha_ready = isinstance(captcha_value, str) and len(captcha_value) == 4
    if not captcha_ready and config_dict.get("ocr_captcha", {}).get("enable", False):
        captcha_ready = await _ctbc_captcha(tab, config_dict, ocr)

    # With OCR disabled, keep the selections and wait for manual input.  The
    # next loop iteration detects the four-character value and continues.
    if not captcha_ready:
        return False

    # Match the existing OCR setting semantics: OCR may fill the field while
    # the final submit remains manual unless "force submit" is enabled.
    ocr_config = config_dict.get("ocr_captcha", {})
    if not ocr_config.get("enable", False) or not ocr_config.get("force_submit", False):
        return True

    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if dialog_outcome is not None:
        return dialog_outcome == "success"
    if (
        submit_key in _state["submit_blocked"]
        or submit_key in _state["seat_conflict_pending"]
    ):
        return False

    if not await _dispatch_ctbc_submit(
        tab, config_dict, submit_key, quantity_plan
    ):
        return False
    debug.log("[CTBC] Add-to-cart submitted")
    return await _observe_ctbc_submit_outcome(tab, config_dict, current_url, submit_key)


async def nodriver_ctbc_seat_main(tab, config_dict, ocr):
    if not await _ctbc_attest_fast_destination(tab, "seat", config_dict):
        return False

    submit_key = _ctbc_purchase_state_key(tab)
    if submit_key is None:
        return False
    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if await _recover_ctbc_captcha_dialog(tab, config_dict):
        return False
    if submit_key in _state["submit_blocked"]:
        return False
    if await _recover_ctbc_seat_conflict(tab, config_dict):
        return False
    if dialog_outcome is not None:
        return False

    selected_titles = await _get_ctbc_selected_seat_titles(tab)
    if not await nodriver_ctbc_ticket_type_auto_select(tab, config_dict):
        return False

    selection_ready = await _validate_ctbc_seat_selection(tab, config_dict)
    if not selection_ready:
        if selected_titles and not await _clear_ctbc_selected_seats(tab, config_dict):
            return False
        if not await nodriver_ctbc_seat_auto_select(tab, config_dict):
            await _reload_with_interval(tab, config_dict, "requested seats are unavailable")
            return False

    await nodriver_utk_sports_cancel_refresh(tab, "requested seats selected")
    # Audit again immediately before captcha/submit to block mixed or stale state.
    if not await _validate_ctbc_seat_selection(tab, config_dict):
        return False

    return await _ctbc_captcha_and_submit(tab, config_dict, ocr)


async def nodriver_ctbc_general_admission_main(tab, config_dict, ocr):
    """Select quantity on the unnumbered-seat UTK0203 page."""
    debug = util.create_debug_logger(config_dict)
    if not await _ctbc_attest_fast_destination(
        tab, "general_admission", config_dict
    ):
        return False
    submit_key = _ctbc_purchase_state_key(tab)
    if submit_key is None:
        return False
    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if await _recover_ctbc_captcha_dialog(tab, config_dict):
        return False
    if submit_key in _state["submit_blocked"]:
        return False
    if await _recover_ctbc_seat_conflict(tab, config_dict):
        return False
    if dialog_outcome is not None:
        return False
    if await _poll_ctbc_quantity_reload(tab, config_dict):
        return False

    try:
        snapshot = await read_quantity_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC GENERAL] Quantity snapshot failed: {exc}")
        return False
    if not isinstance(snapshot, QuantitySnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason
            if isinstance(snapshot, QuantitySnapshot)
            else "invalid-result"
        )
        debug.log(f"[CTBC GENERAL] Quantity page was not ready: {reason}")
        return False

    ticket_number = int(config_dict.get("ticket_number", 1))
    keyword = _get_utiki_behavior(
        config_dict, "ticket_type_keyword", "全票"
    )
    plan = select_quantity_snapshot(snapshot, keyword, ticket_number)
    if plan is None:
        debug.log(
            f"[CTBC GENERAL] No available ticket type matched: {keyword}"
        )
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "ticket type found")
    try:
        commit_result = await commit_quantity(tab, snapshot, plan)
    except Exception as exc:
        debug.log(f"[CTBC GENERAL] Quantity commit outcome is unknown: {exc}")
        return False
    if commit_result is CommitResult.STALE:
        debug.log(
            "[CTBC GENERAL] Quantity state changed during commit; "
            "waiting to reload"
        )
        await _reload_with_interval(
            tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON
        )
        return False
    if commit_result is not CommitResult.COMMITTED:
        debug.log(
            f"[CTBC GENERAL] Quantity commit {commit_result.value}; "
            "submit blocked"
        )
        return False
    debug.log(
        f"[CTBC GENERAL] Selected {plan.ticket_number} x "
        f"{plan.target_name}"
    )
    return await _ctbc_captcha_and_submit(
        tab, config_dict, ocr, quantity_plan=plan
    )


def _ctbc_fast_destination_is_valid(expected, page_type, current_url, dom_ids):
    if not isinstance(expected, dict) or not isinstance(dom_ids, dict):
        return False
    route = expected.get("route")
    expected_page_type = "seat" if route == "0205" else "general_admission" if route == "0202" else None
    if expected_page_type is None or page_type != expected_page_type:
        return False
    area_url = str(expected.get("area_url") or "")
    if (
        _get_ctbc_page_type(current_url) != expected_page_type
        or _get_ctbc_page_type(area_url) != "area"
    ):
        return False
    current = urlsplit(current_url)
    area = urlsplit(area_url)
    if not _ctbc_same_origin(current, area):
        return False
    if route == "0202" and current.path.rsplit("/", 1)[-1] not in {
        "UTK0202_", "UTK0203_"
    }:
        return False
    area_query = _ctbc_unique_query(area_url)
    if area_query is None or set(area_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}:
        return False
    if (
        area_query.get("PERFORMANCE_ID") != expected.get("performance_id")
        or area_query.get("PRODUCT_ID") != expected.get("product_id")
    ):
        return False
    query = _ctbc_unique_query(current_url)
    if query is None or set(query) != {"PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"}:
        return False
    checks = {
        "PERFORMANCE_ID": expected.get("performance_id"),
        "GROUP_ID": expected.get("group_id"),
        "PERFORMANCE_PRICE_AREA_ID": expected.get("price_area_id"),
    }
    if any(query.get(key) != value for key, value in checks.items()):
        return False
    return (
        str(dom_ids.get("performance_id") or "") == expected.get("performance_id")
        and str(dom_ids.get("product_id") or "") == expected.get("product_id")
        and str(dom_ids.get("group_id") or "") == expected.get("group_id")
        and str(dom_ids.get("price_area_id") or "") == expected.get("price_area_id")
    )


async def _ctbc_return_to_native_area(tab, expected, source_url):
    area_url = str(expected.get("area_url") or "")
    if (
        _get_ctbc_page_type(area_url) != "area"
        or _get_ctbc_page_type(source_url) not in {"seat", "general_admission"}
    ):
        return False
    area_url_json = json.dumps(area_url)
    source_url_json = json.dumps(source_url)
    try:
        return await tab.evaluate(f'''(() => {{
            // __ticketsHunterCtbcReturn
            const areaUrl = {area_url_json};
            const sourceUrl = {source_url_json};
            if (location.href !== sourceUrl) return false;
            location.assign(areaUrl);
            return true;
        }})()''')
    except Exception:
        return False


async def _ctbc_return_current_pending(tab, identity, pending, source_url):
    if not _ctbc_pending_navigation_is_current(identity, pending):
        return False
    pending["phase"] = "RETURNING"
    pending["return_source_url"] = source_url
    return await _ctbc_return_to_native_area(tab, pending, source_url)


async def _ctbc_read_destination_state(tab, page_type):
    marker_selectors = ["#TBL", ".ticket .checkNum"] if page_type == "seat" else [
        "#table_tickettype", "input.numbox[key]"
    ]
    marker_json = json.dumps(marker_selectors)
    price_area_selector_json = json.dumps(
        "#PRICE_AREA_ID" if page_type == "seat" else "#PERFORMANCE_PRICE_AREA_ID"
    )
    group_required_json = "true" if page_type == "seat" else "false"
    raw_value = await tab.evaluate(f'''
        (() => new Promise(resolve => {{
            // __ticketsHunterCtbcDestinationState
            let observer = null;
            let timer = null;
            let poller = null;
            const finish = value => {{
                if (observer) observer.disconnect();
                if (timer) clearTimeout(timer);
                if (poller) clearInterval(poller);
                resolve(JSON.stringify(value));
            }};
            const read = () => {{
                const markers = {marker_json};
                const markerReady = markers.every(selector => document.querySelector(selector));
                const performanceInput = document.querySelector('#PERFORMANCE_ID');
                const productInput = document.querySelector('#PRODUCT_ID');
                const groupInput = document.querySelector('#GROUP_ID');
                const priceAreaInput = document.querySelector({price_area_selector_json});
                const groupReady = !{group_required_json} || !!groupInput;
                if (!markerReady || !performanceInput || !productInput || !groupReady || !priceAreaInput) return false;
                finish({{ready: true, page_url: location.href,
                    performance_id: performanceInput.value || '',
                    product_id: productInput.value || '',
                    group_id: groupInput ? (groupInput.value || '') : '',
                    price_area_id: priceAreaInput.value || ''}});
                return true;
            }};
            if (read()) return;
            observer = new MutationObserver(read);
            observer.observe(document.documentElement, {{childList: true, subtree: true}});
            poller = setInterval(read, 25);
            timer = setTimeout(() => finish({{
                ready: false, page_url: location.href, document_ready: document.readyState
            }}), 2500);
        }}))()
    ''', await_promise=True)
    return _decode_ctbc_json_result(raw_value)


async def _ctbc_clear_source_navigation(
    tab, identity, pending, marker_present, marker_pending
):
    if not _ctbc_pending_navigation_is_current(identity, pending):
        return False
    if pending.get("kind") == "FAST" and marker_present:
        if not _ctbc_fast_marker_matches_pending(marker_pending, pending):
            _pop_pending_navigation_if_nonce(identity, pending.get("nonce"))
            return False
        if not await _ctbc_delete_fast_navigation_marker(
            tab, marker_pending["nonce"]
        ):
            return False
    elif pending.get("kind") == "NATIVE" and marker_present:
        if not await _ctbc_delete_fast_navigation_marker(
            tab, marker_pending["nonce"]
        ):
            return False
    return _pop_pending_navigation_if_nonce(
        identity, pending.get("nonce")
    ) is not None


async def _ctbc_reconcile_navigation_source(
    tab, current_url, page_type, config_dict
):
    identity = _ctbc_tab_identity(tab)
    observed_pending = _state["pending_navigation"].get(identity)
    marker_state = await _ctbc_read_fast_navigation_marker(tab)
    pending = _state["pending_navigation"].get(identity)
    if observed_pending:
        if not _ctbc_pending_navigation_is_current(identity, observed_pending):
            return False
        pending = observed_pending
    elif pending:
        return False
    storage_available = bool(marker_state.get("available"))
    if not storage_available and not pending:
        return True
    marker_present = storage_available and bool(marker_state.get("present"))
    marker_pending = _ctbc_pending_from_fast_marker(
        marker_state.get("marker") if marker_present else None
    )
    if storage_available and marker_present and not marker_pending:
        raw = marker_state.get("raw")
        if isinstance(raw, str):
            await _ctbc_delete_malformed_fast_marker(tab, raw)
        return False

    current_document = str(_state["last_document"].get(identity) or "")
    if not pending:
        if not marker_pending:
            return True
        pending = marker_pending
        pending["phase"] = "DISPATCH_UNKNOWN"
        pending["source_document_id"] = current_document
        current = _state["pending_navigation"].get(identity)
        if current:
            pending = current
        else:
            _state["pending_navigation"][identity] = pending

    expected_url = pending.get(
        "source_url" if page_type == "date" else "area_url"
    )
    exact_source = _ctbc_navigation_urls_match(current_url, expected_url)
    source_document = str(pending.get("source_document_id") or "")
    if not source_document and current_document:
        pending["source_document_id"] = current_document
        source_document = current_document
    document_changed = bool(
        source_document
        and current_document
        and current_document != source_document
    )
    reload_requested_at = float(pending.get("reload_requested_at") or 0)
    if document_changed and (exact_source or reload_requested_at):
        return await _ctbc_clear_source_navigation(
            tab, identity, pending, marker_present, marker_pending
        )

    pending_expired = (
        time.monotonic() - pending.get("created_at", 0)
        >= CTBC_PENDING_NAVIGATION_TTL_SECONDS
    )
    marker_active = bool(
        pending.get("kind") == "FAST"
        and marker_pending
        and marker_pending.get("marker_expires_at", 0) > time.time() * 1000
    )
    now = time.monotonic()
    if (
        not pending_expired
        or marker_active
        or (
            reload_requested_at
            and now - reload_requested_at < CTBC_PENDING_NAVIGATION_TTL_SECONDS
        )
    ):
        return False
    if not _ctbc_pending_navigation_is_current(identity, pending):
        return False
    reloaded = await _reload_with_interval(
        tab, config_dict, f"{page_type} navigation outcome unknown"
    )
    if reloaded and _ctbc_pending_navigation_is_current(identity, pending):
        pending["reload_requested_at"] = now
    return False


async def _ctbc_attest_pending_destination(
    tab, page_type, pending, debug, stale_marker_nonce=""
):
    identity = _ctbc_tab_identity(tab)
    try:
        state = await _ctbc_read_destination_state(tab, page_type)
    except Exception as exc:
        debug.log(f"[CTBC NAV] Destination attestation failed: {exc}")
        return False
    current_url = str(state.get("page_url") or "") if isinstance(state, dict) else ""
    if isinstance(state, dict) and state.get("ready") and _ctbc_fast_destination_is_valid(
        pending, page_type, current_url, state
    ):
        current = _state["pending_navigation"].get(identity)
        if not current or current.get("nonce") != pending.get("nonce"):
            return False
        marker_nonce = (
            pending["nonce"] if pending.get("kind") == "FAST"
            else stale_marker_nonce
        )
        if marker_nonce and not await _ctbc_delete_fast_navigation_marker(
            tab, marker_nonce
        ):
            return False
        removed = _pop_pending_navigation_if_nonce(identity, pending["nonce"])
        debug.log(f"[CTBC {pending.get('kind')}] Destination IDs verified")
        return removed is not None
    if isinstance(state, dict) and state.get("document_ready") != "complete":
        debug.log("[CTBC NAV] Destination is still loading")
        return False
    debug.log("[CTBC NAV] Destination mismatch; returning to native area flow")
    await _ctbc_return_current_pending(tab, identity, pending, current_url)
    return False


async def _ctbc_attest_fast_destination(tab, page_type, config_dict):
    """Reconcile FAST/NATIVE provenance before ticket-page automation."""
    identity = _ctbc_tab_identity(tab)
    debug = util.create_debug_logger(config_dict)
    observed_pending = _state["pending_navigation"].get(identity)
    marker_state = await _ctbc_read_fast_navigation_marker(tab)
    pending = _state["pending_navigation"].get(identity)
    if observed_pending:
        if not _ctbc_pending_navigation_is_current(identity, observed_pending):
            return False
        pending = observed_pending
    elif pending:
        return False
    current_url = str(marker_state.get("page_url") or str(tab.target.url))
    if not marker_state.get("available"):
        if not pending:
            return True
        if pending.get("kind") == "NATIVE":
            if pending.get("phase") == "RETURNING":
                await _ctbc_return_current_pending(
                    tab, identity, pending, pending.get("return_source_url", "")
                )
                return False
            return await _ctbc_attest_pending_destination(
                tab, page_type, pending, debug
            )
        debug.log("[CTBC FAST] Marker storage unavailable; returning to native flow")
        await _ctbc_return_current_pending(tab, identity, pending, current_url)
        return False
    marker_present = bool(marker_state.get("present"))
    marker = marker_state.get("marker") if marker_present else None
    marker_pending = _ctbc_pending_from_fast_marker(marker)
    if marker_present and not marker_pending:
        raw = marker_state.get("raw")
        if isinstance(raw, str):
            await _ctbc_delete_malformed_fast_marker(tab, raw)
        return False
    if pending and pending.get("kind") == "NATIVE":
        if pending.get("phase") == "RETURNING":
            await _ctbc_return_current_pending(
                tab, identity, pending, pending.get("return_source_url", "")
            )
            return False
        marker_nonce = str(marker.get("nonce") or "") if isinstance(marker, dict) else ""
        stale_marker_nonce = (
            marker_nonce if re.fullmatch(r"[a-f0-9]{32}", marker_nonce) else ""
        )
        return await _ctbc_attest_pending_destination(
            tab, page_type, pending, debug, stale_marker_nonce
        )
    if not pending:
        if not marker_present:
            return True
        marker_pending["source_document_id"] = str(
            _state["last_document"].get(identity) or ""
        )
        if _state["pending_navigation"].get(identity):
            return False
        _state["pending_navigation"][identity] = marker_pending
        await _ctbc_return_current_pending(
            tab, identity, marker_pending, current_url
        )
        return False
    if pending.get("phase") == "RETURNING":
        await _ctbc_return_current_pending(
            tab, identity, pending, pending.get("return_source_url", "")
        )
        return False
    marker_matches = bool(
        _ctbc_fast_marker_matches_pending(marker_pending, pending)
        and _ctbc_navigation_urls_match(
            marker_pending.get("destination_url"),
            marker_state.get("page_url") or str(tab.target.url),
        )
        and marker_pending.get("marker_expires_at", 0) >= time.time() * 1000
    )
    pending_valid = bool(
        pending.get("config_fingerprint") == _ctbc_selection_config_fingerprint(config_dict)
        and time.monotonic() - pending.get("created_at", 0)
        <= CTBC_PENDING_NAVIGATION_TTL_SECONDS
    )
    if not marker_matches or not pending_valid:
        debug.log("[CTBC FAST] Provenance mismatch or expiry; returning to native area flow")
        await _ctbc_return_current_pending(tab, identity, pending, current_url)
        return False
    return await _ctbc_attest_pending_destination(tab, page_type, pending, debug)


async def _ctbc_reconcile_area_navigation(tab, current_url, config_dict=None):
    """Block duplicate area actions until navigation reaches a new document."""
    return await _ctbc_reconcile_navigation_source(
        tab, current_url, "area", config_dict or {}
    )


async def _handle_ctbc_checkout(tab, config_dict):
    checkout_key = _ctbc_tab_state_key(tab)
    if checkout_key in _state["checkout_notified"]:
        return
    _state["checkout_notified"].add(checkout_key)
    if config_dict.get("advanced", {}).get("play_sound", {}).get("order", False):
        play_sound_while_ordering(config_dict)
    send_discord_notification(config_dict, "order", "CTBC Sports")
    send_telegram_notification(config_dict, "order", "CTBC Sports")


async def nodriver_ctbc_main(tab, url, config_dict, ocr):
    if await check_and_handle_pause(config_dict):
        await nodriver_utk_sports_cancel_refresh(
            tab, "paused", wait=True, abandon_probe=True
        )
        return tab

    guard_state = await nodriver_utk_sports_health_check(tab, url, config_dict)
    if guard_state == "blocked":
        return tab
    if guard_state == "unknown":
        return tab

    page_type = _get_ctbc_page_type(url)
    if page_type == "date" and config_dict.get("date_auto_select", {}).get("enable", False):
        await nodriver_ctbc_date_auto_select(tab, config_dict)
    elif page_type == "area":
        if config_dict.get("area_auto_select", {}).get("enable", False):
            await nodriver_ctbc_area_auto_select(tab, config_dict)
        else:
            await _ctbc_reconcile_area_navigation(tab, url, config_dict)
    elif page_type == "seat":
        await nodriver_ctbc_seat_main(tab, config_dict, ocr)
    elif page_type == "general_admission":
        await nodriver_ctbc_general_admission_main(tab, config_dict, ocr)
    elif page_type == "checkout":
        await nodriver_utk_sports_cancel_refresh(
            tab, "checkout reached", wait=True, abandon_probe=True
        )
        await _handle_ctbc_checkout(tab, config_dict)
    else:
        await nodriver_utk_sports_cancel_refresh(
            tab,
            "automatic refresh not applicable",
            wait=True,
            abandon_probe=True,
        )
    return tab
