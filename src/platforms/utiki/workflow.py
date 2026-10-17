#encoding=utf-8
"""uTiki2 sports ticketing workflow.

The CTBC site uses the same UTK naming convention as a few older ticketing
sites. This module owns the single per-tab runtime and purchase workflow.
"""

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

from zendriver import cdp

import util
from nodriver_common import (
    check_and_handle_pause,
    nodriver_get_captcha_image_from_dom_snapshot,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
)
from . import refresh as refresh_engine
from .adapters.script_resources import assemble_script
from .auth import (
    cookie_domain_is_in_scope,
    get_tenant_auth_profile,
    parse_ctbc_cookie_string,
)
from .dom import (
    CommitResult,
    SubmitDispatchResult,
    clear_conflict_selection,
    commit_area,
    commit_date_action,
    commit_seat,
    commit_server_assigned_area,
    commit_quantity,
    dismiss_dialog,
    dispatch_submit,
    read_area_snapshot,
    read_date_snapshot,
    read_dialog_snapshot,
    read_quantity_snapshot,
    read_seat_snapshot,
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
    DateActionPlan,
    DateSnapshot,
    DialogSnapshot,
    QuantityPlan,
    QuantitySnapshot,
    SeatSnapshot,
    SubmitPlan,
    build_submit_plan,
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    select_area_snapshot as _select_ctbc_area_snapshot,
    select_date_snapshot,
    select_quantity_snapshot,
    select_seat_snapshot,
    submit_plan_is_valid,
)


__all__ = [
    "is_utk_sports_url",
    "nodriver_utk_sports_cancel_refresh",
    "nodriver_utk_sports_cleanup_tab",
    "nodriver_utk_sports_pause_refresh",
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
CTBC_AREA_ROW_SELECTOR = ".seats table.f1 > tbody > tr.saleTr"
CTBC_FAST_PATH_TTL_SECONDS = 1.5
CTBC_PENDING_NAVIGATION_TTL_SECONDS = 15.0
CTBC_FAST_MARKER_CLOCK_SKEW_SECONDS = 1.0
CTBC_FAST_MARKER_KEY = "ticketsHunter.ctbc.fast-navigation.v1"
CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS = 10.0
CTBC_QUANTITY_STALE_RELOAD_REASON = "quantity state changed during commit"
CTBC_SEAT_STALE_RELOAD_REASON = "requested seats are unavailable"
_state = {
    "tabs": {},
    "cancel_tasks": set(),
}
_UTIKI_TAB_TOKEN_ATTR = "_tickets_hunter_utiki_tab_token"


def _new_ctbc_tab_runtime():
    """建立由單一瀏覽器分頁持有的可變執行期。"""
    return {
        "document": {
            "snapshot": None,
            "last_document": "",  # Canonical non-zero performance time origin.
            "unknown_document": None,
        },
        "action": {},
        "transition": {
            "pending_navigation": None,
            "server_assignment_authorized": None,
        },
        "purchase": {
            "active_submit": None,
            "outcomes": {},
            "last_submit": None,
            "checkout_notified": set(),
        },
        "refresh": {
            "reload_request": None,
            "rate_limit_observation": None,
            "rate_limit_retry": 0.0,
            "recovery_probe": None,
            "reload_event": False,
        },
    }


def _get_utiki_behavior(config_dict, key, default):
    """讀取標準租戶行為設定，並提供一期舊設定相容。"""
    canonical = config_dict.get("utiki")
    if isinstance(canonical, dict) and key in canonical:
        return canonical[key]
    legacy = config_dict.get("ctbc")
    if isinstance(legacy, dict) and key in legacy:
        return legacy[key]
    return default


def _ctbc_tab_identity(tab):
    """取得不保留分頁物件參照的穩定純量識別。"""
    identity = str(getattr(tab, _UTIKI_TAB_TOKEN_ATTR, "") or "")
    if identity:
        return identity
    target_id = str(
        getattr(getattr(tab, "target", None), "target_id", "") or ""
    ).strip()
    if target_id:
        identity = f"utiki-target-{target_id}"
    else:
        identity = f"utiki-tab-{uuid.uuid4().hex}"
    try:
        setattr(tab, _UTIKI_TAB_TOKEN_ATTR, identity)
    except (AttributeError, TypeError) as exc:
        raise TypeError("uTiki tab does not support a stable identity token") from exc
    return identity


def _ctbc_runtime_for_identity(identity):
    """依分頁識別取得或建立 uTiki 執行期。"""

    runtime = _state["tabs"].get(identity)
    if runtime is None:
        runtime = _new_ctbc_tab_runtime()
        _state["tabs"][identity] = runtime
    return runtime


def _ctbc_tab_runtime(tab):
    """取得目前瀏覽器分頁的 uTiki 執行期。"""

    return _ctbc_runtime_for_identity(_ctbc_tab_identity(tab))


def _existing_utiki_tab_context(tab):
    """取得既有分頁執行期，且不配置或固定新識別。"""
    identity = str(getattr(tab, _UTIKI_TAB_TOKEN_ATTR, "") or "")
    if not identity:
        target_id = str(
            getattr(getattr(tab, "target", None), "target_id", "") or ""
        ).strip()
        if not target_id:
            return None, None
        identity = f"utiki-target-{target_id}"
    return identity, _state["tabs"].get(identity)


def _ctbc_purchase_runtime(tab):
    """取得目前分頁的購票流程狀態。"""

    return _ctbc_tab_runtime(tab)["purchase"]


def _ctbc_tab_state_key(tab):
    """建立目前分頁在相容狀態表中的索引鍵。"""

    identity = _ctbc_tab_identity(tab)
    return (identity, str(tab.target.url))


def _pop_pending_navigation_if_nonce(identity, nonce):
    """僅在 nonce 相符時移除待處理導覽。"""

    transition = _ctbc_runtime_for_identity(identity)["transition"]
    current = transition["pending_navigation"]
    if current and current.get("nonce") == nonce:
        transition["pending_navigation"] = None
        return current
    return None


def _ctbc_pending_navigation_is_current(identity, pending):
    """判斷待處理導覽是否仍屬於目前分頁狀態。"""

    current = _ctbc_runtime_for_identity(identity)["transition"][
        "pending_navigation"
    ]
    return bool(
        current
        and pending
        and current.get("nonce") == pending.get("nonce")
    )


async def nodriver_ctbc_inject_cookies(tab, config_dict):
    """注入複製的中信售票 Cookie，並保留帳號密碼備援。"""
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
    """判斷網址是否屬於支援的 uTiki 棒球售票站。"""

    return is_utiki_sports_url(url)


is_utk_sports_url = is_utiki_sports_url


def _get_utk_sports_profile(url):
    """取得網址所屬的 uTiki 棒球租戶設定。"""

    profile = get_utiki_tenant_profile(url)
    return profile.as_legacy_dict() if profile else None


def _get_ctbc_page_type(url):
    """判別網址所屬的中信相容售票頁面類型。"""

    page_kind = classify_utiki_page(url)
    return None if page_kind is PageKind.UNKNOWN else page_kind.value


def _ctbc_unique_query(url):
    """讀取沒有重複鍵的中信相容網址查詢參數。"""

    return unique_query(url)


def _ctbc_area_action_payload(plan):
    """將區域計畫轉成瀏覽器提交資料。"""

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


def _ctbc_date_target_payload(plan):
    """將場次計畫轉成導覽目標資料。"""

    if not isinstance(plan, DateActionPlan) or not plan.target_url:
        return None
    return {
        "url": plan.target_url,
        "performance_id": plan.performance_id,
        "product_id": plan.product_id,
        "tenant_path": plan.tenant_path,
        "host": plan.host,
        "map_namespace": plan.map_namespace,
        "fast_area_prefetch": plan.fast_area_prefetch,
    }


def _build_ctbc_destination_url(area_url, target):
    """依區域網址與目標建立可驗證的目的地網址。"""

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


def _build_utiki_server_assignment_destination_url(area_url, target):
    """建立 uTiki 電腦配位的票數頁目的地網址。"""

    if _get_ctbc_page_type(area_url) != "area":
        return None
    try:
        source = urlsplit(area_url)
    except ValueError:
        return None
    area_query = _ctbc_unique_query(area_url)
    token = re.compile(r"[A-Za-z0-9_-]+")
    performance_id = str(target.get("performance_id") or "")
    group_id = str(target.get("group_id") or "")
    price_area_id = str(target.get("price_area_id") or "")
    if (
        area_query is None
        or area_query.get("PERFORMANCE_ID") != performance_id
        or not all(token.fullmatch(value) for value in (
            performance_id, group_id, price_area_id
        ))
    ):
        return None
    path = f"{source.path.rsplit('/', 1)[0]}/UTK0201_001"
    query = urlencode({
        "PERFORMANCE_ID": performance_id,
        "GROUP_ID": group_id,
        "PERFORMANCE_PRICE_AREA_ID": price_area_id,
        "ATYPE": "N",
    })
    destination = urlunsplit(("https", source.netloc, path, query, ""))
    if _get_ctbc_page_type(destination) != "server_assigned_quantity":
        return None
    return destination


def _ctbc_same_origin(first, second):
    """判斷兩個中信相容售票網址是否同源。"""

    return same_origin(first, second)


def _ctbc_purchase_state_key(tab):
    """以購票身分而非外觀網址建立送出狀態索引鍵。"""
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
    if (
        page_type not in (
            "seat", "general_admission", "server_assigned_quantity"
        )
        or query is None
        or not profile
    ):
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
        or (page_type in {"seat", "server_assigned_quantity"} and not group_id)
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
        query.get("ATYPE", ""),
    )


def _ctbc_submit_reached_checkout(source_url, destination_url):
    """僅接受導向來源租戶結帳路由的送出結果。"""
    try:
        source = urlsplit(source_url)
        destination = urlsplit(destination_url)
    except ValueError:
        return False

    source_without_fragment = urlunsplit(
        (source.scheme, source.netloc, source.path, source.query, "")
    )
    return (
        _get_ctbc_page_type(source_without_fragment) in (
            "seat", "general_admission", "server_assigned_quantity"
        )
        and _get_ctbc_page_type(destination_url) == "checkout"
        and _ctbc_same_origin(source, destination)
    )


def _utk_refresh_context(tab):
    """取得目前分頁的刷新執行期與背景工作集合。"""

    identity = _ctbc_tab_identity(tab)
    return (
        _ctbc_runtime_for_identity(identity),
        refresh_engine.build_refresh_identity(identity),
    )


def _utk_refresh_identity(tab):
    """取得目前分頁穩定且程序內唯一的刷新佇列識別。"""
    return refresh_engine.build_refresh_identity(_ctbc_tab_identity(tab))


async def nodriver_utk_sports_cancel_refresh(
    tab,
    reason="",
    *,
    wait=False,
    abandon_probe=False,
):
    """透過標準 uTiki 刷新引擎取消分頁刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.cancel_refresh(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        reason,
        wait=wait,
        abandon_probe=abandon_probe,
    )


async def nodriver_utk_sports_cleanup_tab(tab, reason=""):
    """解除並清理單一分頁所持有的既有執行期。"""
    identity, runtime = _existing_utiki_tab_context(tab)
    if runtime is None or _state["tabs"].get(identity) is not runtime:
        return False
    _state["tabs"].pop(identity, None)
    try:
        await refresh_engine.cancel_refresh(
            tab,
            runtime,
            refresh_engine.build_refresh_identity(identity),
            _state["cancel_tasks"],
            reason,
            wait=True,
            abandon_probe=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        pass
    return True


async def nodriver_utk_sports_pause_refresh(tab, reason="paused"):
    """暫停分頁的佇列或預約刷新，且不重設意圖。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.pause_refresh(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        reason,
    )


def nodriver_utk_sports_consume_reload_event(tab):
    """通知外層路由同網址刷新需要重新偵測 Cloudflare。"""
    return refresh_engine.consume_reload_event(_ctbc_tab_runtime(tab))


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
    """透過標準 uTiki 共用刷新引擎推進一次刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.reload(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        config_dict,
        reason,
        apply_local_interval=apply_local_interval,
        allow_unknown_document=allow_unknown_document,
        allow_blocked_recovery=allow_blocked_recovery,
        dispatch_deadline=dispatch_deadline,
    )


async def nodriver_utk_sports_reload_wait(
    tab,
    config_dict,
    reason,
    *,
    apply_local_interval=False,
    allow_unknown_document=True,
    allow_blocked_recovery=True,
):
    """透過有界等待器完成一次 uTiki 共用刷新。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.reload_wait(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        config_dict,
        reason,
        apply_local_interval=apply_local_interval,
        allow_unknown_document=allow_unknown_document,
        allow_blocked_recovery=allow_blocked_recovery,
    )


async def nodriver_utk_sports_health_check(tab, url, config_dict):
    """僅執行限流復原，不選擇任何票券控制項。"""
    runtime, refresh_identity = _utk_refresh_context(tab)
    return await refresh_engine.health_check(
        tab,
        runtime,
        refresh_identity,
        _state["cancel_tasks"],
        url,
        config_dict,
    )


async def _reload_with_interval(tab, config_dict, reason):
    """依設定的刷新間隔排程一次頁面刷新。"""

    return await nodriver_utk_sports_reload(
        tab,
        config_dict,
        reason,
        apply_local_interval=True,
        allow_unknown_document=False,
    )


async def _poll_ctbc_quantity_reload(tab, config_dict):
    """延續既有票數頁刷新，且不重設等待間隔。"""
    request = _ctbc_tab_runtime(tab)["refresh"]["reload_request"]
    if not request or request.get("reason") != CTBC_QUANTITY_STALE_RELOAD_REASON:
        return False
    await _reload_with_interval(
        tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON
    )
    return True


async def _poll_ctbc_seat_reload(tab, config_dict):
    """延續既有劃位頁刷新，且不重設等待間隔。"""
    request = _ctbc_tab_runtime(tab)["refresh"]["reload_request"]
    if not request or request.get("reason") != CTBC_SEAT_STALE_RELOAD_REASON:
        return False
    await _reload_with_interval(tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON)
    return True


def _decode_ctbc_json_result(raw_value):
    """將中信相容頁面的回傳值解碼為 JSON 資料。"""

    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _ctbc_selection_config_fingerprint(config_dict):
    """建立影響選位結果之設定的穩定指紋。"""

    area_config = config_dict.get("area_auto_select", {})
    payload = {
        "ticket_number": config_dict.get("ticket_number", 1),
        "keyword_exclude": config_dict.get("keyword_exclude", ""),
        "area_auto_fallback": config_dict.get("area_auto_fallback", False),
        "area_enable": area_config.get("enable", False),
        "area_keyword": area_config.get("area_keyword", ""),
        "area_mode": area_config.get("mode", "from top to bottom"),
        "seat_selection_mode": _get_utiki_behavior(
            config_dict, "seat_selection_mode", "manual"
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ctbc_navigation_urls_match(first_url, second_url):
    """判斷兩個導覽網址是否代表同一標準目的地。"""

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
    tab, kind, source_url, area_url, target, config_dict,
    selection_mode="manual",
):
    """建立跨文件追蹤用的待處理導覽紀錄。"""

    if selection_mode == "computer":
        destination_url = _build_utiki_server_assignment_destination_url(
            area_url, target
        )
        route = "0201_001"
    elif selection_mode == "manual":
        destination_url = _build_ctbc_destination_url(area_url, target)
        route = target.get("route")
    else:
        return None
    area_query = _ctbc_unique_query(area_url)
    if (
        not destination_url
        or area_query is None
        or set(area_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}
        or not re.fullmatch(r"[A-Za-z0-9_-]+", area_query.get("PRODUCT_ID", ""))
    ):
        return None
    identity = _ctbc_tab_identity(tab)
    document = _ctbc_runtime_for_identity(identity)["document"]
    return {
        "kind": kind,
        "phase": "PREPARED",
        "nonce": uuid.uuid4().hex,
        "source_url": source_url,
        "source_document_id": document["last_document"],
        "area_url": area_url,
        "destination_url": destination_url,
        "route": route,
        "selection_mode": selection_mode,
        "performance_id": target.get("performance_id"),
        "product_id": area_query.get("PRODUCT_ID"),
        "group_id": target.get("group_id"),
        "price_area_id": target.get("price_area_id"),
        "config_fingerprint": _ctbc_selection_config_fingerprint(config_dict),
        "created_at": time.monotonic(),
    }


def _ctbc_new_date_native_pending(tab, snapshot, plan):
    """為已原子提交的原生場次點擊建立跨文件防護。"""
    if not isinstance(snapshot, DateSnapshot) or not isinstance(plan, DateActionPlan):
        return None
    source_url = snapshot.page_url
    source_query = _ctbc_unique_query(source_url)
    profile = _get_utk_sports_profile(source_url)
    product_id = str((source_query or {}).get("PRODUCT_ID") or "")
    if (
        _get_ctbc_page_type(source_url) != "date"
        or not profile
        or not re.fullmatch(r"[A-Za-z0-9_-]+", product_id)
        or plan.action_kind not in {
            "normal", "anchor_native", "vip_native", "structural_native",
        }
    ):
        return None
    area_url = str(plan.target_url or "")
    if area_url and (
        plan.action_kind not in {"normal", "anchor_native"}
        or _get_ctbc_page_type(area_url) != "area"
        or (_ctbc_unique_query(area_url) or {}).get("PRODUCT_ID") != product_id
    ):
        return None
    identity = _ctbc_tab_identity(tab)
    document = _ctbc_runtime_for_identity(identity)["document"]
    return {
        "kind": "DATE_NATIVE",
        "phase": "PREPARED",
        "nonce": uuid.uuid4().hex,
        "source_url": source_url,
        "source_document_id": document["last_document"],
        "area_url": area_url,
        "product_id": product_id,
        "tenant_path": profile["tenant_path"],
        "host": profile["host"],
        "action_kind": plan.action_kind,
        "created_at": time.monotonic(),
    }


def _ctbc_date_native_area_matches(pending, current_url):
    """判斷原生場次導覽是否到達預期區域頁。"""

    if not isinstance(pending, dict) or pending.get("kind") != "DATE_NATIVE":
        return False
    source_url = str(pending.get("source_url") or "")
    source_profile = _get_utk_sports_profile(source_url)
    current_profile = _get_utk_sports_profile(current_url)
    current_query = _ctbc_unique_query(current_url)
    try:
        source = urlsplit(source_url)
        current = urlsplit(current_url)
    except ValueError:
        return False
    if (
        _get_ctbc_page_type(current_url) != "area"
        or not source_profile
        or not current_profile
        or source_profile["host"] != pending.get("host")
        or source_profile["tenant_path"] != pending.get("tenant_path")
        or current_profile["host"] != pending.get("host")
        or current_profile["tenant_path"] != pending.get("tenant_path")
        or not _ctbc_same_origin(source, current)
        or current_query is None
        or set(current_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}
        or current_query.get("PRODUCT_ID") != pending.get("product_id")
    ):
        return False
    area_url = str(pending.get("area_url") or "")
    return (
        _ctbc_navigation_urls_match(current_url, area_url)
        if area_url
        else current.path == f"{pending.get('tenant_path', '')}/UTK0204_"
    )


def _ctbc_pending_from_fast_marker(marker):
    """從快速導覽標記還原待處理導覽。"""

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
    """判斷快速標記與待處理導覽是否屬於同一意圖。"""

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
    """以目前瀏覽器工作階段預取區域頁 HTML 與精確地圖。"""
    area_url_json = json.dumps(date_target["url"])
    performance_json = json.dumps(date_target["performance_id"])
    product_json = json.dumps(date_target["product_id"])
    map_namespace_json = json.dumps(date_target["map_namespace"])
    raw_value = await tab.evaluate(
        assemble_script("prefetch_area_snapshot", {
            "__UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_0__": area_url_json,
            "__UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_1__": performance_json,
            "__UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_2__": product_json,
            "__UTIKI_PREFETCH_AREA_SNAPSHOT_ARG_3__": map_namespace_json,
        }),
        await_promise=True,
    )
    return _decode_ctbc_json_result(raw_value)


async def _ctbc_read_fast_navigation_marker(tab):
    """讀取頁面儲存的快速導覽標記。"""

    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    try:
        raw_value = await tab.evaluate(assemble_script("fast_marker_read", {
            "__UTIKI_FAST_MARKER_READ_ARG_0__": marker_key_json,
        }))
    except Exception:
        return {"available": False, "present": False, "marker": None}
    result = _decode_ctbc_json_result(raw_value)
    return result if isinstance(result, dict) else {
        "available": False, "present": False, "marker": None,
    }


async def _ctbc_delete_fast_navigation_marker(tab, nonce):
    """刪除 nonce 相符的快速導覽標記。"""

    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    nonce_json = json.dumps(nonce)
    try:
        return await tab.evaluate(assemble_script("fast_marker_delete", {
            "__UTIKI_FAST_MARKER_DELETE_ARG_0__": nonce_json,
            "__UTIKI_FAST_MARKER_DELETE_ARG_1__": marker_key_json,
            "__UTIKI_FAST_MARKER_DELETE_ARG_2__": marker_key_json,
        }))
    except Exception:
        return False


async def _ctbc_delete_malformed_fast_marker(tab, expected_raw):
    """刪除仍與觀察值相同的格式錯誤快速標記。"""

    marker_key_json = json.dumps(CTBC_FAST_MARKER_KEY)
    expected_raw_json = json.dumps(expected_raw)
    try:
        return await tab.evaluate(assemble_script("fast_marker_malformed_delete", {
            "__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_0__": expected_raw_json,
            "__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_1__": marker_key_json,
            "__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_2__": marker_key_json,
            "__UTIKI_FAST_MARKER_MALFORMED_DELETE_ARG_3__": marker_key_json,
        }))
    except Exception:
        return False


async def _ctbc_try_fast_area_navigation(tab, config_dict, date_snapshot, date_plan):
    """嘗試已驗證的快速區域導覽，失敗時允許原生流程接手。"""
    debug = util.create_debug_logger(config_dict)
    identity = _ctbc_tab_identity(tab)
    transition = _ctbc_runtime_for_identity(identity)["transition"]
    if transition["pending_navigation"]:
        return True
    date_target = _ctbc_date_target_payload(date_plan)
    if not date_target or not date_plan.fast_area_prefetch:
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
        tab, "FAST", date_snapshot.page_url, date_target["url"],
        target_action, config_dict,
    )
    if not pending:
        return False
    if transition["pending_navigation"]:
        return True
    transition["pending_navigation"] = pending
    try:
        committed = await commit_date_action(
            tab,
            date_snapshot,
            date_plan,
            pending["nonce"],
            fast_destination_url=pending["destination_url"],
            fast_marker_key=CTBC_FAST_MARKER_KEY,
            fast_marker_ttl_ms=int(CTBC_PENDING_NAVIGATION_TTL_SECONDS * 1000),
        )
    except Exception as exc:
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC FAST] Dispatch outcome unknown: {exc}")
        return True
    if committed is CommitResult.REJECTED:
        await _ctbc_delete_fast_navigation_marker(tab, pending["nonce"])
        _pop_pending_navigation_if_nonce(identity, pending["nonce"])
        return bool(transition["pending_navigation"])
    if committed is CommitResult.STALE:
        pending["phase"] = "DISPATCH_UNKNOWN"
        return True
    if committed is not CommitResult.COMMITTED:
        pending["phase"] = "DISPATCH_UNKNOWN"
        raise RuntimeError("date fast commit returned an unknown result")
    pending["phase"] = "DISPATCH_UNKNOWN"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    debug.log(
        f"[CTBC FAST] Selected: {target.text} ({target.status}) "
        f"in {elapsed_ms} ms"
    )
    return True


async def _ctbc_commit_native_date(tab, snapshot, plan, debug):
    """先安裝跨文件意圖，再送出一次原生場次點擊。"""
    identity = _ctbc_tab_identity(tab)
    transition = _ctbc_runtime_for_identity(identity)["transition"]
    if transition["pending_navigation"]:
        return False
    pending = _ctbc_new_date_native_pending(tab, snapshot, plan)
    if not pending or transition["pending_navigation"]:
        return False
    transition["pending_navigation"] = pending
    try:
        committed = await commit_date_action(
            tab, snapshot, plan, pending["nonce"]
        )
    except Exception as exc:
        if _ctbc_pending_navigation_is_current(identity, pending):
            pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC DATE] Click failed: {exc}")
        return False
    if committed is CommitResult.REJECTED:
        _pop_pending_navigation_if_nonce(identity, pending["nonce"])
        debug.log(f"[CTBC DATE] Native commit rejected ({plan.action_kind})")
        return False
    if _ctbc_pending_navigation_is_current(identity, pending):
        pending["phase"] = "DISPATCH_UNKNOWN"
    if committed is CommitResult.COMMITTED:
        debug.log(f"[CTBC DATE] Selected ({plan.action_kind}): {plan.text}")
        return True
    outcome = committed.value if isinstance(committed, CommitResult) else "unknown"
    debug.log(f"[CTBC DATE] Native commit {outcome} ({plan.action_kind})")
    return False


async def nodriver_ctbc_date_auto_select(tab, config_dict):
    """依設定自動選擇中信相容售票場次。"""

    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    if _get_ctbc_page_type(current_url) != "date":
        return False
    if not await _ctbc_reconcile_navigation_source(
        tab, current_url, "date", config_dict
    ):
        return False
    seat_selection_mode = _get_utiki_behavior(
        config_dict, "seat_selection_mode", "manual"
    )
    if seat_selection_mode == "computer":
        profile = _get_utk_sports_profile(current_url)
        if not profile or not profile.get("supports_server_assignment", False):
            debug.log(
                "[UTK SPORTS] Website computer assignment is not enabled "
                "for this platform"
            )
            return False

    snapshot = None
    for _ in range(10):
        try:
            snapshot = await read_date_snapshot(tab)
        except Exception as exc:
            debug.log(f"[CTBC DATE] Failed to inspect date table: {exc}")
            snapshot = None
        if isinstance(snapshot, DateSnapshot) and snapshot.ok and snapshot.rows:
            break
        await tab.sleep(0.2)
    if not isinstance(snapshot, DateSnapshot) or not snapshot.ok:
        reason = snapshot.reason if isinstance(snapshot, DateSnapshot) else "invalid-result"
        if _get_ctbc_page_type(str(getattr(tab.target, "url", ""))) != "date":
            return False
        await _reload_with_interval(
            tab, config_dict, f"date table was not ready ({reason})"
        )
        return False
    if snapshot.page_url != current_url or _get_ctbc_page_type(snapshot.page_url) != "date":
        return False

    plan = select_date_snapshot(snapshot, config_dict)
    if plan is None:
        await _reload_with_interval(tab, config_dict, "no purchasable date matched")
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "purchasable date found")
    try:
        if (
            seat_selection_mode == "manual"
            and plan.action_kind == "normal"
            and config_dict.get("area_auto_select", {}).get("enable", False)
        ):
            if await _ctbc_try_fast_area_navigation(
                tab, config_dict, snapshot, plan
            ):
                return True
        return await _ctbc_commit_native_date(tab, snapshot, plan, debug)
    except Exception as exc:
        debug.log(f"[CTBC DATE] Click failed: {exc}")
        return False


async def nodriver_ctbc_area_auto_select(tab, config_dict):
    """依設定自動選擇中信相容售票區域。"""

    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    if not await _ctbc_reconcile_area_navigation(
        tab, current_url, config_dict
    ):
        return False
    seat_selection_mode = _get_utiki_behavior(
        config_dict, "seat_selection_mode", "manual"
    )
    if seat_selection_mode == "computer":
        profile = _get_utk_sports_profile(current_url)
        if not profile or not profile.get("supports_server_assignment", False):
            debug.log(
                "[UTK SPORTS] Website computer assignment is not enabled "
                "for this platform"
            )
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
    transition = _ctbc_runtime_for_identity(identity)["transition"]
    if transition["pending_navigation"]:
        return False
    pending = _ctbc_new_pending_navigation(
        tab, "NATIVE", page_url, page_url,
        _ctbc_area_action_payload(target), config_dict,
        selection_mode=seat_selection_mode,
    )
    if not pending:
        return False
    transition["pending_navigation"] = pending
    try:
        committer = (
            commit_server_assigned_area
            if seat_selection_mode == "computer"
            else commit_area
        )
        commit_result = await committer(
            tab, snapshot, target, config_dict.get("ticket_number", 1),
            pending["nonce"], CTBC_FAST_MARKER_KEY,
        )
        if (
            commit_result is CommitResult.REJECTED
            or (
                seat_selection_mode == "manual"
                and commit_result is CommitResult.STALE
            )
        ):
            _pop_pending_navigation_if_nonce(identity, pending["nonce"])
            debug.log(
                "[CTBC AREA] Area commit "
                f"{commit_result.value}; no navigation row click was sent"
            )
            return False
        if (
            seat_selection_mode == "computer"
            and commit_result is CommitResult.STALE
        ):
            pending["phase"] = "DISPATCH_UNKNOWN"
            debug.log(
                "[CTBC AREA] Computer-assignment preparation changed "
                "document; navigation outcome is unknown"
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


async def _fill_ctbc_inline_login(tab, config_dict):
    """在支援的租戶頁面填入並送出登入資料。"""

    state_raw = await tab.evaluate(assemble_script("inline_login_state"))
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

    result = await tab.evaluate(assemble_script("inline_login_fill", {
        "__UTIKI_INLINE_LOGIN_FILL_ARG_0__": json.dumps(auth_profile.inline_login_origin),
        "__UTIKI_INLINE_LOGIN_FILL_ARG_1__": json.dumps(account),
        "__UTIKI_INLINE_LOGIN_FILL_ARG_2__": json.dumps(password),
    }))
    return bool(result)


async def _ctbc_login_fingerprint(tab, *, expected_dialog=None):
    """建立登入對話框與目前文件的識別指紋。"""

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
    raw = await tab.evaluate(assemble_script("login_fingerprint", {
        "__UTIKI_LOGIN_FINGERPRINT_ARG_0__": expected_json,
    }))
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
    """為舊呼叫端讀取目前可見對話框文字。"""
    snapshot = await read_dialog_snapshot(tab)
    if snapshot and snapshot.visible_count == 1:
        return snapshot.text
    return ""


async def _ctbc_dialog_is_visible(tab):
    """以保守規則判斷對話框是否仍可見。"""
    snapshot = await read_dialog_snapshot(tab)
    return snapshot is None or snapshot.visible_count != 0


async def _wait_ctbc_dialog_closed(tab, snapshot):
    """等待指定對話框關閉或文件狀態改變。"""

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
    """透過快照與提交邊界關閉指定對話框。"""
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


def _ctbc_purchase_outcome(
    kind, *, text="", reason="", source_url="", source_document_id="",
    page_type="", nonce="", dom_document_id="", product_id="",
):
    """建立不含敏感資料的標準購票結果紀錄。"""
    normalized_text = " ".join(str(text or "").split())
    record = {
        "kind": str(kind or "unknown"),
        "text": normalized_text,
        "reason": str(reason or ""),
        "source_url": str(source_url or ""),
        "source_document_id": str(source_document_id or ""),
        "nonce": str(nonce or ""),
    }
    if dom_document_id:
        record["dom_document_id"] = str(dom_document_id)
    if product_id:
        record["product_id"] = str(product_id)
    if page_type:
        record["page_type"] = str(page_type)
    return record


def _ctbc_source_document_id(context):
    """取得流程與 DOM 共用的標準文件時間來源識別。"""
    if "source_document_id" in context:
        return str(context.get("source_document_id") or "")
    return str(context.get("document_id") or "")


def _ctbc_dialog_outcome_record(
    outcome, text, source_url, source_document_id, page_type, nonce,
    dom_document_id="", product_id="",
):
    """將已分類對話框純化為標準阻擋結果紀錄。"""
    kind = "unknown" if outcome == "rejected" else outcome
    reason = "dialog" if kind == "unknown" else ""
    record = _ctbc_purchase_outcome(
        kind,
        text=text,
        reason=reason,
        source_url=source_url,
        source_document_id=source_document_id,
        page_type=page_type if kind == "seat_conflict" else "",
        nonce=nonce,
        dom_document_id=dom_document_id if kind == "seat_conflict" else "",
        product_id=product_id if kind == "seat_conflict" else "",
    )
    if kind == "seat_conflict":
        record.update({
            "dialog_dismissed": False,
            "clear_phase": "",
            "clear_result": "",
            "reload_dispatched": False,
            "recovery_document_id": "",
        })
    elif kind == "auth":
        record["fingerprint"] = ""
    elif kind == "captcha":
        record.update({
            "reset_phase": "",
            "reset_source_document_id": "",
            "reset_before_token": "",
        })
    return record


def _ctbc_recent_submit_context(purchase, fallback_key=None):
    """在不猜測歷史結果的前提下判定對話框所屬送出操作。"""
    active = purchase.get("active_submit")
    if isinstance(active, dict) and active.get("purchase_key") is not None:
        return active.get("purchase_key"), active
    outcomes = purchase.get("outcomes", {})
    fallback = outcomes.get(fallback_key)
    pointer = purchase.get("last_submit")
    pointer_key = None
    pointer_outcome = None
    if isinstance(pointer, dict):
        pointer_key = pointer.get("purchase_key")
        pointer_outcome = outcomes.get(pointer_key)
        if (
            not isinstance(pointer_outcome, dict)
            or pointer_outcome.get("nonce") != pointer.get("nonce")
        ):
            pointer_key, pointer_outcome = None, None
    if isinstance(fallback, dict):
        if pointer_outcome is not None and pointer_key != fallback_key:
            return None, None
        return fallback_key, fallback
    if pointer_outcome is not None:
        return pointer_key, pointer_outcome
    if fallback_key is not None:
        return fallback_key, None
    return None, None


def _ctbc_store_purchase_outcome(purchase, purchase_key, record, active=None):
    """套用購票結果並安全清理相符的進行中送出狀態。"""
    if purchase_key is None or not isinstance(record, dict):
        return False
    current = purchase.get("active_submit")
    if active is not None and current is not active:
        return False
    if active is not None:
        purchase["active_submit"] = None
    purchase["outcomes"][purchase_key] = record
    if active is not None:
        purchase["last_submit"] = {
            "purchase_key": purchase_key,
            "nonce": record.get("nonce", ""),
        }
    pointer = purchase.get("last_submit")
    if isinstance(pointer, dict) and pointer.get("purchase_key") == purchase_key:
        pointer["nonce"] = record.get("nonce", "")
    return True


def _ctbc_captcha_image_token(image_bytes):
    """在不保留圖片位元組下建立驗證碼圖片識別。"""
    if not isinstance(image_bytes, (bytes, bytearray, memoryview)) or not image_bytes:
        return ""
    return hashlib.sha256(bytes(image_bytes)).hexdigest()


async def _read_ctbc_captcha_image_token(tab, config_dict):
    """讀取目前驗證碼圖片的非敏感識別。"""

    try:
        image_bytes = await _get_ctbc_captcha_image_bytes(tab, config_dict)
    except Exception:
        return ""
    return _ctbc_captcha_image_token(image_bytes)


def _ctbc_captcha_reset_is_current(
    tab, submit_key, outcome, source_document_id,
):
    """判斷驗證碼重設工作是否仍屬於目前送出意圖。"""

    runtime = _ctbc_tab_runtime(tab)
    return bool(
        source_document_id
        and runtime["purchase"]["outcomes"].get(submit_key) is outcome
        and _ctbc_purchase_state_key(tab) == submit_key
        and runtime["document"]["last_document"] == source_document_id
    )


def _ctbc_captcha_phase_is_current(
    tab, submit_key, outcome, source_document_id, phase,
):
    """判斷驗證碼工作是否仍處於指定階段。"""

    return bool(
        _ctbc_captcha_reset_is_current(
            tab, submit_key, outcome, source_document_id
        )
        and outcome.get("reset_phase") == phase
    )


def _release_ctbc_captcha_preflight(
    tab, submit_key, outcome,
):
    """釋放驗證碼重設前置檢查所持有的狀態。"""

    if (
        _ctbc_outcome_is_current(tab, submit_key, outcome)
        and outcome.get("reset_phase") == "preflighting"
    ):
        outcome["reset_phase"] = ""
    return False


async def _observe_ctbc_captcha_refresh(
    tab, config_dict, submit_key, outcome,
):
    """觀察驗證碼刷新是否已產生新的圖片。"""

    current_document_id = _ctbc_tab_runtime(tab)["document"]["last_document"]
    source_document_id = str(outcome.get("reset_source_document_id") or "")
    if not _ctbc_outcome_is_current(tab, submit_key, outcome):
        return False
    if _ctbc_purchase_state_key(tab) != submit_key:
        return False
    if not source_document_id:
        return False
    if current_document_id and current_document_id != source_document_id:
        outcome["reset_phase"] = "done"
        return True
    if current_document_id != source_document_id:
        return False
    before_token = str(outcome.get("reset_before_token") or "")
    if not before_token:
        return False
    current_token = await _read_ctbc_captcha_image_token(tab, config_dict)
    if not _ctbc_captcha_reset_is_current(
        tab, submit_key, outcome, source_document_id
    ):
        return False
    if current_token and current_token != before_token:
        outcome["reset_phase"] = "done"
        return True
    return False


async def _dispatch_ctbc_captcha_refresh(
    tab, config_dict, submit_key, outcome, source_document_id,
):
    """送出驗證碼圖片刷新並登記觀察狀態。"""

    before_token = await _read_ctbc_captcha_image_token(tab, config_dict)
    if not _ctbc_captcha_phase_is_current(
        tab, submit_key, outcome, source_document_id, "preflighting"
    ):
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome
        )
    if not before_token:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome
        )
    outcome["reset_before_token"] = before_token
    try:
        refresh = await tab.query_selector('#chang_pic')
    except Exception:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome
        )
    if not _ctbc_captcha_phase_is_current(
        tab, submit_key, outcome, source_document_id, "preflighting"
    ):
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome
        )
    if not refresh:
        return _release_ctbc_captcha_preflight(
            tab, submit_key, outcome
        )
    outcome["reset_phase"] = "dispatching"
    try:
        await refresh.click()
    except Exception:
        if not _ctbc_captcha_phase_is_current(
            tab, submit_key, outcome, source_document_id, "dispatching"
        ):
            return False
        outcome["reset_phase"] = "unknown"
        return await _observe_ctbc_captcha_refresh(
            tab, config_dict, submit_key, outcome
        )
    if not _ctbc_captcha_phase_is_current(
        tab, submit_key, outcome, source_document_id, "dispatching"
    ):
        return False
    outcome["reset_phase"] = "awaiting_change"
    return await _observe_ctbc_captcha_refresh(
        tab, config_dict, submit_key, outcome
    )


async def _reset_ctbc_captcha(tab, *, refresh_image, submit_key=None, config_dict=None):
    """重設驗證碼輸入，並視需要刷新圖片。"""

    config_dict = config_dict or {}
    runtime = _ctbc_tab_runtime(tab)
    purchase = runtime["purchase"]
    outcome = purchase["outcomes"].get(submit_key)
    if refresh_image and submit_key is not None and (
        not isinstance(outcome, dict) or outcome.get("kind") != "captcha"
    ):
        return False
    if refresh_image and submit_key is not None:
        if not _ctbc_outcome_is_current(tab, submit_key, outcome):
            return False
        if _ctbc_purchase_state_key(tab) != submit_key:
            return False
        phase = outcome.get("reset_phase")
        prior_source = str(outcome.get("reset_source_document_id") or "")
        current_document_id = str(runtime["document"]["last_document"] or "")
        if (
            prior_source
            and current_document_id
            and current_document_id != prior_source
        ):
            outcome["reset_phase"] = "done"
            return True
        if phase == "done":
            return True
        if phase == "preflighting":
            return False
        if phase:
            return await _observe_ctbc_captcha_refresh(
                tab, config_dict, submit_key, outcome
            )
        source_document_id = current_document_id
        if not source_document_id:
            return False
        outcome["reset_source_document_id"] = source_document_id
        outcome["reset_before_token"] = ""
        outcome["reset_phase"] = "preflighting"

    try:
        captcha_input = await tab.query_selector('#CHK')
        if refresh_image and not _ctbc_captcha_phase_is_current(
            tab, submit_key, outcome, source_document_id, "preflighting"
        ):
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome
            )
        if refresh_image and not captcha_input:
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome
            )
        if captcha_input:
            await captcha_input.apply('function(el) { el.value = ""; }')
            if refresh_image and not _ctbc_captcha_phase_is_current(
                tab, submit_key, outcome, source_document_id, "preflighting"
            ):
                return _release_ctbc_captcha_preflight(
                    tab, submit_key, outcome
                )
    except Exception:
        if refresh_image:
            return _release_ctbc_captcha_preflight(
                tab, submit_key, outcome
            )
        return False
    if refresh_image:
        return await _dispatch_ctbc_captcha_refresh(
            tab, config_dict, submit_key, outcome, source_document_id
        )
    return True


def _ctbc_conflict_page_kind(page_type):
    """將頁面類型轉成衝突清理所需的選擇種類。"""

    return {
        "seat": PageKind.RESERVED_SEAT,
        "general_admission": PageKind.GENERAL_ADMISSION,
        "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
    }.get(page_type)


def _ctbc_outcome_is_current(tab, submit_key, record):
    """判斷購票結果是否仍屬於目前文件與送出意圖。"""

    return _ctbc_purchase_runtime(tab)["outcomes"].get(submit_key) is record


def _ctbc_conflict_is_current(tab, submit_key, record):
    """判斷座位或票數衝突是否仍可安全清理。"""

    return bool(
        _ctbc_outcome_is_current(tab, submit_key, record)
        and _ctbc_purchase_state_key(tab) == submit_key
    )


async def _clear_ctbc_conflict_selection(
    tab, config_dict, submit_key, record,
):
    """清除目前送出衝突所對應的座位或票數選擇。"""

    if not _ctbc_conflict_is_current(tab, submit_key, record):
        return False
    clear_phase = record.get("clear_phase")
    if clear_phase == "done":
        return True
    if clear_phase:
        return False
    record["clear_phase"] = "dispatching"
    page_kind = _ctbc_conflict_page_kind(record.get("page_type"))
    try:
        result = await clear_conflict_selection(
            tab,
            page_kind,
            str(record.get("source_url") or ""),
            str(record.get("dom_document_id") or ""),
            str(record.get("product_id") or ""),
        )
    except Exception as exc:
        result = None
        util.create_debug_logger(config_dict).log(
            f"[CTBC] Conflict clear outcome is unknown: {exc}"
        )
    if not _ctbc_outcome_is_current(tab, submit_key, record):
        return False
    record["clear_phase"] = "done"
    record["clear_result"] = (
        result.value if isinstance(result, CommitResult) else "unknown"
    )
    return _ctbc_conflict_is_current(tab, submit_key, record)


async def _apply_ctbc_dialog_effects(
    tab, config_dict, submit_key, outcome, record
):
    """依對話框分類套用關閉、清理或重試效果。"""

    debug = util.create_debug_logger(config_dict)
    purchase = _ctbc_purchase_runtime(tab)
    if purchase["outcomes"].get(submit_key) is not record:
        return
    if outcome == "auth":
        debug.log("[CTBC LOGIN] Authentication rejected; waiting for credentials to change")
    elif outcome == "captcha":
        reset_complete = await _reset_ctbc_captcha(
            tab,
            refresh_image=True,
            submit_key=submit_key,
            config_dict=config_dict,
        )
        if reset_complete and purchase["outcomes"].get(submit_key) is record:
            purchase["outcomes"].pop(submit_key, None)
        debug.log(f"[CTBC CAPTCHA] Server rejected captcha: {record['text']}")
    elif outcome == "seat_conflict":
        if not _ctbc_conflict_is_current(tab, submit_key, record):
            return
        record["dialog_dismissed"] = True
        debug.log(f"[CTBC SEAT] Selected seats were rejected: {record['text']}")
        if not await _clear_ctbc_conflict_selection(
            tab, config_dict, submit_key, record
        ):
            return
        if record.get("clear_result") != CommitResult.COMMITTED.value:
            debug.log("[CTBC] Local selection clear was not committed; reload remains pending")
        if not _ctbc_conflict_is_current(tab, submit_key, record):
            return
        await _reset_ctbc_captcha(tab, refresh_image=False, submit_key=submit_key)
        if not _ctbc_conflict_is_current(tab, submit_key, record):
            return
    elif outcome == "success":
        debug.log("[CTBC] Cart accepted; same-page resubmit is blocked")
    else:
        debug.log(f"[CTBC] Submit blocked after {outcome}: {record['text']}")


async def _handle_ctbc_submit_dialog(
    tab, config_dict, submit_key=None, source_url=None, dialog_snapshot=None
):
    """分類並處理加入購物車後出現的售票對話框。"""

    debug = util.create_debug_logger(config_dict)
    runtime = _ctbc_tab_runtime(tab)
    purchase = runtime["purchase"]
    if dialog_snapshot is None:
        dialog_snapshot = await read_dialog_snapshot(tab)
    if dialog_snapshot is None:
        debug.log("[CTBC] Submit dialog observation is invalid; action blocked")
        return "rejected"
    if dialog_snapshot.visible_count == 0:
        return None

    current_key = submit_key if submit_key is not None else _ctbc_purchase_state_key(tab)
    submit_key, context = _ctbc_recent_submit_context(purchase, current_key)
    if submit_key is None:
        debug.log("[CTBC] Submit dialog appeared outside a valid purchase context")
        return "rejected"
    active = context if context is purchase.get("active_submit") else None
    context_source = context.get("source_url", "") if isinstance(context, dict) else ""
    context_document = ""
    context_dom_document = ""
    context_product_id = ""
    if isinstance(context, dict):
        context_document = _ctbc_source_document_id(context)
        context_dom_document = str(
            context.get("dom_document_id")
            or context.get("document_id")
            or ""
        )
        context_product_id = str(context.get("product_id") or "")
    source_url = str(source_url or context_source or tab.target.url)
    page_type = _get_ctbc_page_type(source_url)
    dialog_text = dialog_snapshot.text
    outcome = (
        _classify_ctbc_submit_dialog(dialog_text)
        if dialog_snapshot.visible_count == 1 and dialog_text
        else "rejected"
    )
    record = _ctbc_dialog_outcome_record(
        outcome,
        dialog_text,
        source_url,
        (
            context_document
            if isinstance(context, dict)
            else runtime["document"]["last_document"]
        ),
        page_type,
        context.get("nonce", "") if isinstance(context, dict) else "",
        context_dom_document,
        context_product_id,
    )
    if not _ctbc_store_purchase_outcome(
        purchase, submit_key, record, active=active
    ):
        return "rejected"
    if outcome == "rejected":
        debug.log("[CTBC] Unknown or ambiguous dialog preserved; action blocked")
        return outcome

    if outcome == "auth":
        try:
            fingerprint = await _ctbc_login_fingerprint(
                tab, expected_dialog=dialog_snapshot
            )
        except Exception as exc:
            debug.log(
                "[CTBC LOGIN] Credential fingerprint observation failed; "
                f"document-scoped blocker retained: {exc}"
            )
        else:
            if purchase["outcomes"].get(submit_key) is record:
                record["fingerprint"] = fingerprint
    dismiss_result = await dismiss_dialog(tab, dialog_snapshot)
    if (
        dismiss_result is not CommitResult.COMMITTED
        or not await _wait_ctbc_dialog_closed(tab, dialog_snapshot)
    ):
        debug.log(
            f"[CTBC] Visible dialog changed before it could be dismissed: {dialog_text}"
        )
        return "rejected" if outcome == "success" else outcome
    await _apply_ctbc_dialog_effects(
        tab, config_dict, submit_key, outcome, record
    )
    return outcome


async def _recover_ctbc_captcha_dialog(tab, config_dict):
    """從驗證碼錯誤對話框恢復至可再次送出的狀態。"""

    submit_key = _ctbc_purchase_state_key(tab)
    purchase = _ctbc_purchase_runtime(tab)
    outcome = purchase["outcomes"].get(submit_key)
    if not isinstance(outcome, dict) or outcome.get("kind") != "captcha":
        return False
    if await _ctbc_dialog_is_visible(tab):
        return True
    reset_complete = await _reset_ctbc_captcha(
        tab,
        refresh_image=True,
        submit_key=submit_key,
        config_dict=config_dict,
    )
    if reset_complete and purchase["outcomes"].get(submit_key) is outcome:
        purchase["outcomes"].pop(submit_key, None)
    debug = util.create_debug_logger(config_dict)
    if reset_complete:
        debug.log("[CTBC CAPTCHA] Dialog disappeared; captcha was reset before retry")
    else:
        debug.log("[CTBC CAPTCHA] Captcha refresh outcome is unknown; retry remains blocked")
    return True


async def _recover_ctbc_seat_conflict(tab, config_dict):
    """從座位或票數衝突恢復至可重新選擇的狀態。"""

    submit_key = _ctbc_purchase_state_key(tab)
    runtime = _ctbc_tab_runtime(tab)
    outcomes = runtime["purchase"]["outcomes"]
    pending = outcomes.get(submit_key)
    if not isinstance(pending, dict) or pending.get("kind") != "seat_conflict":
        return False

    current_document_id = runtime["document"]["last_document"]
    if not pending.get("dialog_dismissed", False):
        if await _ctbc_dialog_is_visible(tab):
            return True
        if not _ctbc_conflict_is_current(tab, submit_key, pending):
            return False
        pending["dialog_dismissed"] = True

    if not await _clear_ctbc_conflict_selection(
        tab, config_dict, submit_key, pending
    ):
        return False

    if pending["reload_dispatched"]:
        recovery_document = str(pending.get("recovery_document_id") or "")
        if (
            recovery_document
            and current_document_id
            and current_document_id != recovery_document
        ):
            outcomes.pop(submit_key, None)
            return False
        return True

    if not _ctbc_conflict_is_current(tab, submit_key, pending):
        return False
    reload_reason = "selected seats were taken"
    reload_completed = await _reload_with_interval(
        tab, config_dict, reload_reason
    )
    if not _ctbc_conflict_is_current(tab, submit_key, pending):
        return False
    reload_request = runtime["refresh"]["reload_request"]
    request_source_document = str(
        reload_request.get("source_document_token")
        if isinstance(reload_request, dict) else ""
    )
    request_dispatched = bool(
        reload_request
        and reload_request.get("reason") == reload_reason
        and reload_request.get("phase") in {"awaiting_document", "completed"}
        and request_source_document
        and request_source_document == current_document_id
    )
    if reload_completed or request_dispatched:
        pending["recovery_document_id"] = (
            request_source_document if request_dispatched
            else str(current_document_id or "")
        )
        pending["reload_dispatched"] = True
    return True


def _ctbc_submit_dispatched_at(value):
    """將送出時間轉成可比較的時間戳。"""

    if isinstance(value, dict):
        value = value.get("dispatched_at")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ctbc_unknown_submit_outcome(active, reason):
    """將已送出意圖轉成需保留的未知結果。"""
    return _ctbc_purchase_outcome(
        "unknown",
        reason=reason,
        source_url=active.get("source_url", ""),
        source_document_id=_ctbc_source_document_id(active),
        nonce=active.get("nonce", ""),
        dom_document_id=active.get("document_id", ""),
        product_id=active.get("product_id", ""),
    )


def _ctbc_finish_active_unknown(purchase, active, reason):
    """以未知結果結束目前進行中的送出意圖。"""

    if purchase.get("active_submit") is not active:
        return False
    purchase_key = active.get("purchase_key")
    record = _ctbc_unknown_submit_outcome(active, reason)
    if not _ctbc_store_purchase_outcome(
        purchase, purchase_key, record, active=active
    ):
        return False
    return True


def _ctbc_auth_failure_is_active(tab, submit_key, login_fingerprint):
    """判斷登入失敗是否仍屬於目前文件與送出操作。"""

    runtime = _ctbc_tab_runtime(tab)
    outcomes = runtime["purchase"]["outcomes"]
    failure = outcomes.get(submit_key)
    if not isinstance(failure, dict) or failure.get("kind") != "auth":
        return False

    failed_fingerprint = str(failure.get("fingerprint") or "")
    if failed_fingerprint:
        current_fingerprint = str(login_fingerprint or "")
        if not current_fingerprint or failed_fingerprint == current_fingerprint:
            return True
        outcomes.pop(submit_key, None)
        return False

    current_document_id = runtime["document"]["last_document"]
    source_document_id = str(failure.get("source_document_id") or "")
    if not source_document_id:
        return True
    if current_document_id and current_document_id != source_document_id:
        outcomes.pop(submit_key, None)
        return False
    return True


def _normalize_ctbc_captcha_answer(answer):
    """將 OCR 結果正規化為四個大寫 ASCII 字元。"""
    return re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()


async def _observe_ctbc_submit_outcome(tab, config_dict, source_url, submit_key):
    """歸納加入購物車送出後第一個可觀察結果。"""
    await tab.sleep(1.0)
    result = await _reconcile_utiki_purchase(
        tab, str(tab.target.url), _get_ctbc_page_type(str(tab.target.url)), config_dict
    )
    return result == "success"


async def _reconcile_utiki_purchase(tab, current_url, page_type, config_dict):
    """在執行頁面效果前協調延遲出現的送出結果。"""
    debug = util.create_debug_logger(config_dict)
    runtime = _ctbc_tab_runtime(tab)
    purchase = runtime["purchase"]
    snapshot = await read_dialog_snapshot(tab)
    if snapshot is None:
        debug.log("[CTBC] Submit dialog observation is invalid; action blocked")
        return "blocked"
    if snapshot.visible_count:
        current_key = _ctbc_purchase_state_key(tab)
        submit_key, context = _ctbc_recent_submit_context(purchase, current_key)
        source_url = context.get("source_url", "") if isinstance(context, dict) else ""
        outcome = await _handle_ctbc_submit_dialog(
            tab,
            config_dict,
            submit_key=submit_key,
            source_url=source_url,
            dialog_snapshot=snapshot,
        )
        return "success" if outcome == "success" else "blocked"

    active = purchase.get("active_submit")
    context_key = None
    context = None
    if isinstance(active, dict):
        context_key, context = active.get("purchase_key"), active
    else:
        context_key, context = _ctbc_recent_submit_context(
            purchase, _ctbc_purchase_state_key(tab)
        )
    if not isinstance(context, dict) or context_key is None:
        return None
    source_url = str(context.get("source_url") or "")
    if _ctbc_submit_reached_checkout(source_url, current_url):
        if active is None and not (
            context.get("kind") == "unknown"
            and context.get("reason") in {"submit_outcome", "dispatch_unknown"}
        ):
            return "success" if context.get("kind") == "success" else "blocked"
        record = _ctbc_purchase_outcome(
            "success",
            source_url=source_url,
            source_document_id=_ctbc_source_document_id(context),
            nonce=context.get("nonce", ""),
        )
        _ctbc_store_purchase_outcome(
            purchase, context_key, record, active=active
        )
        return "success"
    destination_key = _ctbc_purchase_state_key(tab)
    if active is None:
        if destination_key != context_key:
            return None
        return (
            None
            if context.get("kind") in {"auth", "captcha", "seat_conflict"}
            else "blocked"
        )
    dispatched_at = _ctbc_submit_dispatched_at(active)
    if (
        dispatched_at
        and time.monotonic() - dispatched_at < CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS
    ):
        return "pending"
    _ctbc_finish_active_unknown(purchase, active, "submit_outcome")
    debug.log("[CTBC] Submit outcome is unknown; automatic resubmit is blocked")
    return "blocked"


async def _get_ctbc_captcha_image_bytes(tab, config_dict):
    """以原始 90×25 解析度擷取同源驗證碼圖片。"""
    debug = util.create_debug_logger(config_dict)
    try:
        data_url = await tab.evaluate(assemble_script("captcha_image"))
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
    """讀取並辨識目前頁面的中信相容驗證碼。"""

    debug = util.create_debug_logger(config_dict)
    if not ocr:
        debug.log("[CTBC CAPTCHA] OCR engine is unavailable")
        return False

    for _ in range(10):
        image_ready = await tab.evaluate(assemble_script("captcha_image_ready"))
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


async def _dispatch_ctbc_submit(tab, config_dict, submit_key, submit_plan):
    """送出一次加入購物車操作，並等待後續確認結果。"""
    debug = util.create_debug_logger(config_dict)
    runtime = _ctbc_tab_runtime(tab)
    purchase = runtime["purchase"]
    if purchase.get("active_submit") is not None:
        return False
    dispatched_at = time.monotonic()
    dispatch_state = {
        "purchase_key": submit_key,
        "nonce": uuid.uuid4().hex,
        "dispatched_at": dispatched_at,
        "phase": "dispatching",
        "source_url": str(tab.target.url),
        "source_document_id": str(
            runtime["document"]["last_document"] or ""
        ),
        "document_id": submit_plan.document_id,
        "page_kind": submit_plan.page_kind.value,
        "product_id": str(
            getattr(submit_plan.selection_plan, "product_id", "") or ""
        ),
        "payload_fingerprint": submit_plan.payload_fingerprint,
    }
    purchase["active_submit"] = dispatch_state
    try:
        result = await dispatch_submit(tab, submit_plan)
        if result is not SubmitDispatchResult.DISPATCHED:
            if purchase.get("active_submit") is not dispatch_state:
                return False
            debug.log(
                f"[CTBC] Submit dispatch {result.value}; click blocked"
            )
            if (
                result is SubmitDispatchResult.STALE
                and str(tab.target.url) == submit_plan.page_url
            ):
                reason = (
                    CTBC_SEAT_STALE_RELOAD_REASON
                    if submit_plan.page_kind is PageKind.RESERVED_SEAT
                    else CTBC_QUANTITY_STALE_RELOAD_REASON
                )
                try:
                    await _reload_with_interval(tab, config_dict, reason)
                finally:
                    if purchase.get("active_submit") is dispatch_state:
                        purchase["active_submit"] = None
            else:
                if purchase.get("active_submit") is dispatch_state:
                    purchase["active_submit"] = None
            return False
    except Exception as exc:
        _ctbc_finish_active_unknown(purchase, dispatch_state, "dispatch_unknown")
        debug.log(f"[CTBC] Submit dispatch outcome is unknown: {exc}")
        return False
    if purchase.get("active_submit") is not dispatch_state:
        return False
    purchase["last_submit"] = None
    dispatch_state["phase"] = "awaiting_result"
    return True


async def _ctbc_captcha_and_submit(
    tab, config_dict, ocr, *, submit_plan=None
):
    """完成驗證碼辨識後送出已驗證的購票計畫。"""

    debug = util.create_debug_logger(config_dict)
    purchase = _ctbc_purchase_runtime(tab)
    current_url = str(tab.target.url)
    submit_key = _ctbc_purchase_state_key(tab)
    page_type = _get_ctbc_page_type(current_url)
    if (
        submit_key is None
        or page_type not in (
            "seat", "general_admission", "server_assigned_quantity"
        )
    ):
        debug.log("[CTBC] Purchase identity is incomplete; automatic submit is blocked")
        return False
    if (
        not isinstance(submit_plan, SubmitPlan)
        or not submit_plan_is_valid(submit_plan)
        or submit_plan.page_kind.value != page_type
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
    active = purchase.get("active_submit")
    if isinstance(active, dict):
        dispatched_at = _ctbc_submit_dispatched_at(active)
        if (
            not dispatched_at
            or time.monotonic() - dispatched_at >= CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS
        ):
            _ctbc_finish_active_unknown(purchase, active, "submit_outcome")
            debug.log("[CTBC] Submit outcome is unknown; automatic resubmit is blocked")
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False

    try:
        login_ready = await _fill_ctbc_inline_login(tab, config_dict)
        if not login_ready:
            debug.log("[CTBC LOGIN] Waiting for login or CTBC account settings")
            return False
        login_fingerprint = await _ctbc_login_fingerprint(tab)
    except Exception:
        debug.log(
            "[CTBC LOGIN] Credential observation failed; "
            "previous authentication blocker retained"
        )
        return False
    if _ctbc_auth_failure_is_active(tab, submit_key, login_fingerprint):
        debug.log("[CTBC LOGIN] Previous authentication failure; waiting for credentials to change")
        return False

    captcha_value = await tab.evaluate(assemble_script("captcha_value"))
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
    if purchase.get("active_submit") is not None:
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict):
        return False

    if not await _dispatch_ctbc_submit(
        tab, config_dict, submit_key, submit_plan
    ):
        return False
    debug.log("[CTBC] Add-to-cart submitted")
    return await _observe_ctbc_submit_outcome(tab, config_dict, current_url, submit_key)


async def nodriver_ctbc_seat_main(tab, config_dict, ocr):
    """執行中信相容售票的劃位頁主要流程。"""

    if not await _ctbc_attest_fast_destination(tab, "seat", config_dict):
        return False
    if _get_utiki_behavior(
        config_dict, "seat_selection_mode", "manual"
    ) != "manual":
        return False

    submit_key = _ctbc_purchase_state_key(tab)
    if submit_key is None:
        return False
    purchase = _ctbc_purchase_runtime(tab)
    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if await _recover_ctbc_captcha_dialog(tab, config_dict):
        return False
    if await _recover_ctbc_seat_conflict(tab, config_dict):
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False
    if dialog_outcome is not None:
        return False
    if await _poll_ctbc_seat_reload(tab, config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    try:
        snapshot = await read_seat_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC SEAT] Seat snapshot failed: {exc}")
        return False
    if not isinstance(snapshot, SeatSnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason
            if isinstance(snapshot, SeatSnapshot)
            else "invalid-result"
        )
        debug.log(f"[CTBC SEAT] Reserved-seat page was not ready: {reason}")
        return False

    ticket_number = int(config_dict.get("ticket_number", 1))
    keyword = _get_utiki_behavior(
        config_dict, "ticket_type_keyword", "全票"
    )
    plan, reason = select_seat_snapshot(
        snapshot,
        keyword,
        ticket_number,
        config_dict.get("advanced", {}).get("disable_adjacent_seat", False),
        _get_utiki_behavior(config_dict, "seat_sorting", "row_seat"),
    )
    if plan is None:
        if reason == "seats-unavailable":
            await _reload_with_interval(
                tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON
            )
        elif reason == "ticket-type-unavailable":
            debug.log(f"[CTBC SEAT] No ticket type matched: {keyword}")
        else:
            debug.log(f"[CTBC SEAT] Reserved-seat snapshot rejected: {reason}")
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "reserved-seat plan ready")
    try:
        commit_result = await commit_seat(tab, snapshot, plan)
    except Exception as exc:
        debug.log(f"[CTBC SEAT] Seat commit outcome is unknown: {exc}")
        await _reload_with_interval(
            tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON
        )
        return False
    if not await _reduce_utiki_seat_commit(
        tab, config_dict, plan, commit_result, debug
    ):
        return False
    submit_plan = build_submit_plan(plan)
    if submit_plan is None:
        debug.log("[CTBC SEAT] Reserved-seat submit plan was rejected")
        return False
    return await _ctbc_captcha_and_submit(
        tab, config_dict, ocr, submit_plan=submit_plan
    )


async def _reduce_utiki_seat_commit(
    tab, config_dict, plan, commit_result, debug
):
    """歸納劃位提交結果，避免過早進入送出流程。"""
    if commit_result in {CommitResult.STALE, CommitResult.REJECTED}:
        outcome = commit_result.value
        debug.log(
            f"[CTBC SEAT] Seat commit {outcome}; waiting to reload"
        )
        await _reload_with_interval(
            tab, config_dict, CTBC_SEAT_STALE_RELOAD_REASON
        )
        return False
    if commit_result is not CommitResult.COMMITTED:
        debug.log("[CTBC SEAT] Seat commit outcome was unknown; submit blocked")
        return False
    if plan.action == "clear":
        debug.log("[CTBC SEAT] Cleared an invalid reserved-seat selection")
        return False
    debug.log(
        f"[CTBC SEAT] Reserved-seat selection ready: "
        f"{plan.ticket_number} x {plan.ticket_type_name}"
    )
    return True


async def _reduce_utiki_quantity_commit(
    tab, config_dict, plan, commit_result, debug, label
):
    """歸納票數提交結果，避免過早進入送出流程。"""
    if commit_result is CommitResult.STALE:
        debug.log(
            f"[CTBC {label}] Quantity state changed during commit; "
            "waiting to reload"
        )
        await _reload_with_interval(
            tab, config_dict, CTBC_QUANTITY_STALE_RELOAD_REASON
        )
        return False
    if commit_result is not CommitResult.COMMITTED:
        outcome = (
            commit_result.value
            if isinstance(commit_result, CommitResult)
            else "unknown"
        )
        debug.log(
            f"[CTBC {label}] Quantity commit {outcome}; submit blocked"
        )
        return False
    debug.log(
        f"[CTBC {label}] Selected {plan.ticket_number} x "
        f"{plan.target_name}"
    )
    return True


async def _nodriver_utiki_quantity_main(
    tab, config_dict, ocr, page_type, expected_product_id=""
):
    """在路由驗證後執行共用票數頁流程。"""
    debug = util.create_debug_logger(config_dict)
    label = (
        "SERVER ASSIGNMENT"
        if page_type == "server_assigned_quantity"
        else "GENERAL"
    )
    submit_key = _ctbc_purchase_state_key(tab)
    if submit_key is None:
        return False
    purchase = _ctbc_purchase_runtime(tab)
    dialog_outcome = await _handle_ctbc_submit_dialog(
        tab, config_dict, submit_key=submit_key
    )
    if await _recover_ctbc_captcha_dialog(tab, config_dict):
        return False
    if await _recover_ctbc_seat_conflict(tab, config_dict):
        return False
    outcome = purchase["outcomes"].get(submit_key)
    if isinstance(outcome, dict) and outcome.get("kind") != "auth":
        return False
    if dialog_outcome is not None:
        return False
    if await _poll_ctbc_quantity_reload(tab, config_dict):
        return False

    try:
        snapshot = await read_quantity_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC {label}] Quantity snapshot failed: {exc}")
        return False
    if not isinstance(snapshot, QuantitySnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason
            if isinstance(snapshot, QuantitySnapshot)
            else "invalid-result"
        )
        debug.log(f"[CTBC {label}] Quantity page was not ready: {reason}")
        return False
    if (
        page_type == "server_assigned_quantity"
        and snapshot.product_id != expected_product_id
    ):
        debug.log(
            "[CTBC SERVER ASSIGNMENT] Product identity changed after "
            "destination attestation"
        )
        return False

    ticket_number = int(config_dict.get("ticket_number", 1))
    keyword = _get_utiki_behavior(
        config_dict, "ticket_type_keyword", "全票"
    )
    plan = select_quantity_snapshot(
        snapshot, keyword, ticket_number, page_type=page_type
    )
    if plan is None:
        debug.log(
            f"[CTBC {label}] No available ticket type matched: {keyword}"
        )
        return False

    await nodriver_utk_sports_cancel_refresh(tab, "ticket type found")
    try:
        commit_result = await commit_quantity(tab, snapshot, plan)
    except Exception as exc:
        debug.log(
            f"[CTBC {label}] Quantity commit outcome is unknown: {exc}"
        )
        return False
    if not await _reduce_utiki_quantity_commit(
        tab, config_dict, plan, commit_result, debug, label
    ):
        return False
    submit_plan = build_submit_plan(plan)
    if submit_plan is None:
        debug.log(f"[CTBC {label}] Quantity submit plan was rejected")
        return False
    return await _ctbc_captcha_and_submit(
        tab, config_dict, ocr, submit_plan=submit_plan
    )


async def nodriver_ctbc_general_admission_main(tab, config_dict, ocr):
    """在不劃位的 UTK0202／UTK0203 頁面選擇票數。"""
    if not await _ctbc_attest_fast_destination(
        tab, "general_admission", config_dict
    ):
        return False
    if _get_utiki_behavior(
        config_dict, "seat_selection_mode", "manual"
    ) != "manual":
        return False
    return await _nodriver_utiki_quantity_main(
        tab, config_dict, ocr, "general_admission"
    )


def _ctbc_server_assignment_pending_is_expected(pending):
    """判斷待處理導覽是否為預期的電腦配位流程。"""

    return bool(
        pending
        and pending.get("kind") == "NATIVE"
        and pending.get("route") == "0201_001"
        and pending.get("selection_mode") == "computer"
        and re.fullmatch(
            r"[A-Za-z0-9_-]+", str(pending.get("product_id") or "")
        )
    )


def _ctbc_server_assignment_is_authorized(tab):
    """判斷目前分頁是否獲准進入電腦配位票數頁。"""

    purchase_key = _ctbc_purchase_state_key(tab)
    transition = _ctbc_tab_runtime(tab)["transition"]
    authorized = transition["server_assignment_authorized"]
    if (
        purchase_key is not None
        and isinstance(authorized, dict)
        and authorized.get("purchase_key") == purchase_key
        and re.fullmatch(
            r"[A-Za-z0-9_-]+",
            str(authorized.get("product_id") or ""),
        )
    ):
        return authorized
    transition["server_assignment_authorized"] = None
    return None


async def nodriver_utiki_server_assigned_quantity_main(
    tab, config_dict, ocr
):
    """在已驗證的原生電腦配位點擊後選擇票數。"""
    transition = _ctbc_tab_runtime(tab)["transition"]
    current_url = str(tab.target.url)
    profile = _get_utk_sports_profile(current_url)
    if (
        _get_utiki_behavior(
            config_dict, "seat_selection_mode", "manual"
        ) != "computer"
        or not profile
        or not profile.get("supports_server_assignment", False)
    ):
        transition["server_assignment_authorized"] = None
        return False
    pending = transition["pending_navigation"]
    authorization = _ctbc_server_assignment_is_authorized(tab)
    if pending:
        if not _ctbc_server_assignment_pending_is_expected(pending):
            transition["server_assignment_authorized"] = None
            return False
        if not await _ctbc_attest_fast_destination(
            tab, "server_assigned_quantity", config_dict
        ):
            return False
        purchase_key = _ctbc_purchase_state_key(tab)
        if purchase_key is None:
            return False
        authorization = {
            "purchase_key": purchase_key,
            "product_id": pending["product_id"],
        }
        transition["server_assignment_authorized"] = authorization
    elif authorization is None:
        return False
    return await _nodriver_utiki_quantity_main(
        tab, config_dict, ocr, "server_assigned_quantity",
        expected_product_id=authorization["product_id"],
    )


def _ctbc_fast_destination_is_valid(expected, page_type, current_url, dom_ids):
    """驗證快速導覽目的地的路由、網址與 DOM 身分。"""

    if not isinstance(expected, dict) or not isinstance(dom_ids, dict):
        return False
    route = expected.get("route")
    expected_page_type = {
        "0205": "seat",
        "0202": "general_admission",
        "0201_001": "server_assigned_quantity",
    }.get(route)
    if expected_page_type is None or page_type != expected_page_type:
        return False
    if (
        (route == "0201_001" and expected.get("selection_mode") != "computer")
        or (
            route != "0201_001"
            and expected.get("selection_mode", "manual") != "manual"
        )
    ):
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
    expected_query_keys = {
        "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
    }
    if route == "0201_001":
        expected_query_keys.add("ATYPE")
    if query is None or set(query) != expected_query_keys:
        return False
    checks = {
        "PERFORMANCE_ID": expected.get("performance_id"),
        "GROUP_ID": expected.get("group_id"),
        "PERFORMANCE_PRICE_AREA_ID": expected.get("price_area_id"),
    }
    if any(query.get(key) != value for key, value in checks.items()):
        return False
    if route == "0201_001" and query.get("ATYPE") != "N":
        return False
    return (
        str(dom_ids.get("performance_id") or "") == expected.get("performance_id")
        and str(dom_ids.get("product_id") or "") == expected.get("product_id")
        and str(dom_ids.get("group_id") or "") == expected.get("group_id")
        and str(dom_ids.get("price_area_id") or "") == expected.get("price_area_id")
    )


def _ctbc_date_native_direct_pending(pending, page_type):
    """判斷原生場次導覽是否直接到達票券頁。"""

    return bool(
        isinstance(pending, dict)
        and pending.get("kind") == "DATE_NATIVE"
        and not pending.get("area_url")
        and pending.get("action_kind") in {"vip_native", "structural_native"}
        and page_type in {"seat", "general_admission"}
    )


def _ctbc_date_native_direct_state_is_valid(pending, page_type, state):
    """驗證原生場次直接導覽的目的地文件狀態。"""

    if not isinstance(state, dict) or not state.get("ready"):
        return False
    source_url = str(pending.get("source_url") or "")
    current_url = str(state.get("page_url") or "")
    source_profile = _get_utk_sports_profile(source_url)
    current_profile = _get_utk_sports_profile(current_url)
    source_query = _ctbc_unique_query(source_url)
    query = _ctbc_unique_query(current_url)
    try:
        source = urlsplit(source_url)
        current = urlsplit(current_url)
    except ValueError:
        return False
    expected_paths = {
        "seat": {f"{pending.get('tenant_path', '')}/UTK0205_"},
        "general_admission": {
            f"{pending.get('tenant_path', '')}/UTK0202_",
            f"{pending.get('tenant_path', '')}/UTK0203_",
        },
    }
    if (
        not source_profile or not current_profile or source_query is None
        or query is None or _get_ctbc_page_type(source_url) != "date"
        or _get_ctbc_page_type(current_url) != page_type
        or current.path not in expected_paths[page_type]
        or not _ctbc_same_origin(source, current)
        or any(profile.get("host") != pending.get("host") or
               profile.get("tenant_path") != pending.get("tenant_path")
               for profile in (source_profile, current_profile))
        or source_query.get("PRODUCT_ID") != pending.get("product_id")
        or set(query) != {
            "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
        }
    ):
        return False
    url_ids = tuple(query.get(key, "") for key in (
        "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
    ))
    token = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token.fullmatch(str(pending.get("product_id") or ""))
        or not token.fullmatch(url_ids[0]) or not token.fullmatch(url_ids[2])
        or (url_ids[1] and not token.fullmatch(url_ids[1]))
        or (page_type == "seat" and not url_ids[1])
    ):
        return False
    return url_ids == tuple(str(state.get(key) or "") for key in (
        "performance_id", "group_id", "price_area_id"
    )) and str(state.get("product_id") or "") == pending.get("product_id")


async def _ctbc_return_to_native_area(tab, expected, source_url):
    """返回來源區域頁，讓原生導覽流程重新接手。"""

    area_url = str(expected.get("area_url") or "")
    if (
        _get_ctbc_page_type(area_url) != "area"
        or _get_ctbc_page_type(source_url) not in {
            "seat", "general_admission", "server_assigned_quantity"
        }
    ):
        return False
    area_url_json = json.dumps(area_url)
    source_url_json = json.dumps(source_url)
    try:
        return await tab.evaluate(assemble_script("return_to_native_area", {
            "__UTIKI_RETURN_TO_NATIVE_AREA_ARG_0__": area_url_json,
            "__UTIKI_RETURN_TO_NATIVE_AREA_ARG_1__": source_url_json,
        }))
    except Exception:
        return False


async def _ctbc_return_current_pending(tab, identity, pending, source_url):
    """將目前待處理導覽保留至返回來源頁後繼續。"""

    if not _ctbc_pending_navigation_is_current(identity, pending):
        return False
    pending["phase"] = "RETURNING"
    pending["return_source_url"] = source_url
    return await _ctbc_return_to_native_area(tab, pending, source_url)


async def _ctbc_read_destination_state(tab, page_type):
    """讀取目的地頁面的文件與商品身分狀態。"""

    marker_selectors = ["#TBL", ".ticket .checkNum"] if page_type == "seat" else [
        "#table_tickettype", "input.numbox[key]"
    ]
    marker_json = json.dumps(marker_selectors)
    price_area_selector_json = json.dumps(
        "#PRICE_AREA_ID" if page_type == "seat" else "#PERFORMANCE_PRICE_AREA_ID"
    )
    group_required_json = (
        "true"
        if page_type in {"seat", "server_assigned_quantity"}
        else "false"
    )
    raw_value = await tab.evaluate(
        assemble_script("destination_state", {
            "__UTIKI_DESTINATION_STATE_ARG_0__": marker_json,
            "__UTIKI_DESTINATION_STATE_ARG_1__": price_area_selector_json,
            "__UTIKI_DESTINATION_STATE_ARG_2__": group_required_json,
        }),
        await_promise=True,
    )
    return _decode_ctbc_json_result(raw_value)


async def _ctbc_clear_source_navigation(
    tab, identity, pending, marker_present, marker_pending
):
    """清除來源頁待處理導覽與相符快速標記。"""

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


def _ctbc_reduce_date_native_navigation(
    identity, pending, current_url, page_type, document_changed,
    exact_source, reload_requested_at, marker_present,
):
    """歸納原生場次來源狀態，判斷是否仍需期限內復原。"""
    if page_type == "area":
        if (
            document_changed
            and not marker_present
            and _ctbc_date_native_area_matches(pending, current_url)
        ):
            return _pop_pending_navigation_if_nonce(
                identity, pending.get("nonce")
            ) is not None
        return False
    if (
        page_type == "date"
        and document_changed
        and exact_source
        and reload_requested_at
        and not marker_present
    ):
        return _pop_pending_navigation_if_nonce(
            identity, pending.get("nonce")
        ) is not None
    return None


async def _ctbc_reconcile_navigation_source(
    tab, current_url, page_type, config_dict
):
    """協調目前網址與待處理導覽的來源狀態。"""

    identity = _ctbc_tab_identity(tab)
    runtime = _ctbc_runtime_for_identity(identity)
    transition = runtime["transition"]
    observed_pending = transition["pending_navigation"]
    marker_state = await _ctbc_read_fast_navigation_marker(tab)
    pending = transition["pending_navigation"]
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

    current_document = str(runtime["document"]["last_document"] or "")
    if not pending:
        if not marker_pending:
            return True
        pending = marker_pending
        pending["phase"] = "DISPATCH_UNKNOWN"
        pending["source_document_id"] = current_document
        current = transition["pending_navigation"]
        if current:
            pending = current
        else:
            transition["pending_navigation"] = pending

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
    if pending.get("kind") == "DATE_NATIVE":
        date_result = _ctbc_reduce_date_native_navigation(
            identity, pending, current_url, page_type, document_changed,
            exact_source, reload_requested_at, marker_present,
        )
        if date_result is not None or page_type == "area":
            return bool(date_result)
    elif document_changed and (exact_source or reload_requested_at):
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
    """驗證待處理導覽是否抵達可信任的目的地。"""

    identity = _ctbc_tab_identity(tab)
    runtime = _ctbc_runtime_for_identity(identity)
    try:
        state = await _ctbc_read_destination_state(tab, page_type)
    except Exception as exc:
        debug.log(f"[CTBC NAV] Destination attestation failed: {exc}")
        return False
    current_url = str(state.get("page_url") or "") if isinstance(state, dict) else ""
    current_document = str(runtime["document"]["last_document"] or "")
    state_document = str(state.get("document_id") or "") if isinstance(
        state, dict
    ) else ""
    source_document = str(pending.get("source_document_id") or "")
    if (
        isinstance(state, dict)
        and state.get("ready")
        and state_document
        and state_document == current_document
        and source_document
        and state_document != source_document
        and _ctbc_fast_destination_is_valid(
            pending, page_type, current_url, state
        )
    ):
        current = runtime["transition"]["pending_navigation"]
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


async def _ctbc_attest_date_native_direct_destination(
    tab, page_type, pending, debug
):
    """驗證原生場次導覽直接抵達的票券頁。"""

    if not _ctbc_date_native_direct_pending(pending, page_type):
        return None
    identity = _ctbc_tab_identity(tab)
    runtime = _ctbc_runtime_for_identity(identity)
    try:
        state = await _ctbc_read_destination_state(tab, page_type)
    except Exception as exc:
        debug.log(f"[CTBC DATE] Direct destination attestation failed: {exc}")
        return False
    if not _ctbc_pending_navigation_is_current(identity, pending):
        return False
    current_document = str(runtime["document"]["last_document"] or "")
    source_document = str(pending.get("source_document_id") or "")
    state_document = str(state.get("document_id") or "") if isinstance(
        state, dict
    ) else ""
    if (
        state_document
        and source_document
        and current_document == state_document
        and state_document != source_document
        and _ctbc_date_native_direct_state_is_valid(pending, page_type, state)
    ):
        removed = _pop_pending_navigation_if_nonce(identity, pending["nonce"])
        debug.log("[CTBC DATE] Direct destination IDs verified")
        return removed is not None
    if isinstance(state, dict) and state.get("document_ready") != "complete":
        debug.log("[CTBC DATE] Direct destination is still loading")
    else:
        debug.log("[CTBC DATE] Direct destination mismatch; pending retained")
    return False


async def _ctbc_attest_fast_destination(tab, page_type, config_dict):
    """在票券頁自動化前協調快速與原生導覽來源。"""
    identity = _ctbc_tab_identity(tab)
    runtime = _ctbc_runtime_for_identity(identity)
    transition = runtime["transition"]
    debug = util.create_debug_logger(config_dict)
    observed_pending = transition["pending_navigation"]
    marker_state = await _ctbc_read_fast_navigation_marker(tab)
    pending = transition["pending_navigation"]
    if observed_pending:
        if not _ctbc_pending_navigation_is_current(identity, observed_pending):
            return False
        pending = observed_pending
    elif pending:
        return False
    current_url = str(marker_state.get("page_url") or str(tab.target.url))
    direct_result = await _ctbc_attest_date_native_direct_destination(
        tab, page_type, pending, debug
    )
    if direct_result is not None:
        return direct_result
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
            runtime["document"]["last_document"] or ""
        )
        if transition["pending_navigation"]:
            return False
        transition["pending_navigation"] = marker_pending
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
    """在抵達新文件前阻止重複送出區域操作。"""
    return await _ctbc_reconcile_navigation_source(
        tab, current_url, "area", config_dict or {}
    )


async def _handle_ctbc_checkout(tab, config_dict):
    """處理中信相容售票的結帳頁狀態。"""

    purchase = _ctbc_purchase_runtime(tab)
    purchase_key, context = _ctbc_recent_submit_context(purchase)
    if (
        purchase_key is not None
        and isinstance(context, dict)
        and context.get("nonce")
    ):
        checkout_key = ("submit", purchase_key, str(context["nonce"]))
    else:
        checkout_key = ("location", _ctbc_tab_state_key(tab))
    checkout_notified = purchase["checkout_notified"]
    if checkout_key in checkout_notified:
        return
    checkout_notified.add(checkout_key)
    if config_dict.get("advanced", {}).get("play_sound", {}).get("order", False):
        play_sound_while_ordering(config_dict)
    send_discord_notification(config_dict, "order", "CTBC Sports")
    send_telegram_notification(config_dict, "order", "CTBC Sports")


async def nodriver_ctbc_main(tab, url, config_dict, ocr):
    """依頁面類型分派中信相容棒球售票自動化流程。"""

    page_type = _get_ctbc_page_type(url)
    if page_type != "server_assigned_quantity":
        _ctbc_tab_runtime(tab)["transition"][
            "server_assignment_authorized"
        ] = None
    if await check_and_handle_pause(config_dict):
        await nodriver_utk_sports_pause_refresh(tab, "paused")
        return tab

    guard_state = await nodriver_utk_sports_health_check(tab, url, config_dict)
    if guard_state == "blocked":
        return tab
    if guard_state == "unknown":
        return tab

    purchase_result = await _reconcile_utiki_purchase(
        tab, url, page_type, config_dict
    )
    if purchase_result in {"blocked", "pending"}:
        return tab
    if purchase_result == "success" and page_type != "checkout":
        return tab

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
    elif page_type == "server_assigned_quantity":
        await nodriver_utiki_server_assigned_quantity_main(
            tab, config_dict, ocr
        )
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
