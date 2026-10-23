#encoding=utf-8
"""uTiki2 sports ticketing workflow.

The CTBC site uses the same UTK naming convention as a few older ticketing
sites. This module owns the single per-tab runtime and routes purchase work.
"""

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import TypedDict
from urllib.parse import urlencode, urlsplit, urlunsplit

import util
from nodriver_common import (
    check_and_handle_pause,
)
from . import refresh as refresh_engine
from . import purchase as purchase_engine
from . import submission as submission_engine
from .inputs import nodriver_ctbc_inject_cookies
from .dom import (
    CommitResult,
    commit_area,
    commit_date_action,
    commit_server_assigned_area,
    read_area_snapshot,
    read_date_snapshot,
)
from .profiles import (
    PageKind,
    TENANT_PROFILES,
    classify_utiki_page,
    get_utiki_tenant_profile,
    is_utiki_sports_url,
    same_origin,
    unique_query,
)
from .runtime import TabRuntime, new_tab_runtime as _new_ctbc_tab_runtime
from .selection import (
    AreaActionPlan,
    AreaSnapshot,
    DateActionPlan,
    DateSnapshot,
    select_area_snapshot as _select_ctbc_area_snapshot,
    select_date_snapshot,
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
CTBC_QUANTITY_STALE_RELOAD_REASON = (
    submission_engine.CTBC_QUANTITY_STALE_RELOAD_REASON
)
CTBC_SEAT_STALE_RELOAD_REASON = submission_engine.CTBC_SEAT_STALE_RELOAD_REASON


class _WorkflowState(TypedDict):
    tabs: dict[str, TabRuntime]
    cancel_tasks: set[asyncio.Task]


_state: _WorkflowState = {
    "tabs": {},
    "cancel_tasks": set(),
}
_UTIKI_TAB_TOKEN_ATTR = "_tickets_hunter_utiki_tab_token"


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


def _ctbc_runtime_for_identity(identity: str) -> TabRuntime:
    """依分頁識別取得或建立 uTiki 執行期。"""

    runtime = _state["tabs"].get(identity)
    if runtime is None:
        runtime = _new_ctbc_tab_runtime()
        _state["tabs"][identity] = runtime
    return runtime


def _ctbc_tab_runtime(tab) -> TabRuntime:
    """取得目前瀏覽器分頁的 uTiki 執行期。"""

    return _ctbc_runtime_for_identity(_ctbc_tab_identity(tab))


def _existing_utiki_tab_context(tab) -> tuple[str | None, TabRuntime | None]:
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
    """Use workflow's current tab identity at the original lookup point."""
    return submission_engine._ctbc_purchase_state_key(
        tab, identity_for_tab=_ctbc_tab_identity
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
    """讀取頁面儲存的快速導覽標記。"""

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
    """刪除 nonce 相符的快速導覽標記。"""

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
    """刪除仍與觀察值相同的格式錯誤快速標記。"""

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






















































































async def nodriver_ctbc_seat_main(tab, config_dict, ocr):
    """保留原座位頁導覽核對，再交由購票模組準備與提交。"""
    if not await _ctbc_attest_fast_destination(tab, "seat", config_dict):
        return False
    if _get_utiki_behavior(config_dict, "seat_selection_mode", "manual") != "manual":
        return False
    return await purchase_engine.seat_main(
        tab, config_dict, ocr,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        poll_seat_reload=_poll_ctbc_seat_reload,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
    )


async def _nodriver_utiki_quantity_main(
    tab, config_dict, ocr, page_type, expected_product_id=""
):
    """把已核對頁面的票數購票流程交給購票模組。"""
    return await purchase_engine.quantity_main(
        tab, config_dict, ocr, page_type,
        expected_product_id=expected_product_id,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
        behavior_for_key=_get_utiki_behavior,
        reload_with_interval=_reload_with_interval,
        poll_quantity_reload=_poll_ctbc_quantity_reload,
        cancel_refresh=nodriver_utk_sports_cancel_refresh,
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
            const documentToken = () => {{
                const value = Number(performance.timeOrigin ||
                    (performance.timing && performance.timing.navigationStart) || 0);
                return Number.isFinite(value) && value > 0 ? String(value) : '';
            }};
            const read = () => {{
                const markers = {marker_json};
                const markerReady = markers.every(selector => document.querySelector(selector));
                const exactInput = selector => {{
                    const inputs = Array.from(document.querySelectorAll(selector));
                    return inputs.length === 1 ? inputs[0] : null;
                }};
                const performanceInput = exactInput('#PERFORMANCE_ID');
                const productInput = exactInput('#PRODUCT_ID');
                const groupInputs = Array.from(
                    document.querySelectorAll('#GROUP_ID'));
                const groupInput = groupInputs[0] || null;
                const priceAreaInput = exactInput({price_area_selector_json});
                const groupReady = {group_required_json} ?
                    groupInputs.length === 1 : groupInputs.length <= 1;
                const documentId = documentToken();
                if (!documentId || !markerReady || !performanceInput ||
                    !productInput || !groupReady || !priceAreaInput) return false;
                finish({{ready: true, page_url: location.href,
                    document_id: documentId,
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
                ready: false, page_url: location.href,
                document_id: documentToken(), document_ready: document.readyState
            }}), 2500);
        }}))()
    ''', await_promise=True)
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




async def _reconcile_utiki_purchase(tab, current_url, page_type, config_dict):
    """在外層路由前核對既有提交結果。"""
    return await submission_engine._reconcile_utiki_purchase(
        tab, current_url, page_type, config_dict,
        runtime_for_tab=_ctbc_tab_runtime,
        purchase_key_for_tab=_ctbc_purchase_state_key,
    )


async def _handle_ctbc_checkout(tab, config_dict):
    """由既有 checkout 路由交給提交模組處理通知。"""
    return await submission_engine._handle_ctbc_checkout(
        tab, config_dict,
        runtime_for_tab=_ctbc_tab_runtime,
        tab_state_key_for_tab=_ctbc_tab_state_key,
    )


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
