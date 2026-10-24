# encoding=utf-8
"""Existing uTiki navigation dispatch, pending lifecycle, and attestation."""

import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

import util
from .dom import (
    CommitResult,
    commit_area,
    commit_date_action,
    commit_server_assigned_area,
)
from .profiles import (
    PageKind,
    classify_utiki_page,
    get_utiki_tenant_profile,
    same_origin,
    unique_query,
)
from .selection import AreaActionPlan, DateActionPlan, DateSnapshot

UTIKI_PENDING_NAVIGATION_TTL_SECONDS = 15.0
UTIKI_FAST_MARKER_CLOCK_SKEW_SECONDS = 1.0
# Keep the historical storage key so an existing tab can reconcile its marker.
UTIKI_FAST_MARKER_KEY = "ticketsHunter.ctbc.fast-navigation.v1"


def _get_utiki_page_type(url):
    """判別已支援 uTiki 網址的頁面類型。"""

    page_kind = classify_utiki_page(url)
    return None if page_kind is PageKind.UNKNOWN else page_kind.value


def _utiki_unique_query(url):
    """讀取沒有重複鍵的 uTiki 網址查詢參數。"""

    return unique_query(url)


def _utiki_area_action_payload(plan):
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
    }


def _build_utiki_destination_url(area_url, target):
    """依區域網址與目標建立可驗證的目的地網址。"""

    if _get_utiki_page_type(area_url) != "area":
        return None
    source = urlsplit(area_url)
    if target.get("route") not in {"0202", "0205"}:
        return None
    area_query = _utiki_unique_query(area_url)
    if (area_query or {}).get("PERFORMANCE_ID") != target.get("performance_id"):
        return None
    path = f"{source.path.rsplit('/', 1)[0]}/UTK{target['route']}_"
    query = urlencode(
        {
            "PERFORMANCE_ID": target["performance_id"],
            "GROUP_ID": target["group_id"],
            "PERFORMANCE_PRICE_AREA_ID": target["price_area_id"],
        }
    )
    return urlunsplit(("https", source.netloc, path, query, ""))


def _build_utiki_server_assignment_destination_url(area_url, target):
    """建立 uTiki 電腦配位的票數頁目的地網址。"""

    if _get_utiki_page_type(area_url) != "area":
        return None
    try:
        source = urlsplit(area_url)
    except ValueError:
        return None
    area_query = _utiki_unique_query(area_url)
    token = re.compile(r"[A-Za-z0-9_-]+")
    performance_id = str(target.get("performance_id") or "")
    group_id = str(target.get("group_id") or "")
    price_area_id = str(target.get("price_area_id") or "")
    if (
        area_query is None
        or area_query.get("PERFORMANCE_ID") != performance_id
        or not all(
            token.fullmatch(value)
            for value in (performance_id, group_id, price_area_id)
        )
    ):
        return None
    path = f"{source.path.rsplit('/', 1)[0]}/UTK0201_001"
    query = urlencode(
        {
            "PERFORMANCE_ID": performance_id,
            "GROUP_ID": group_id,
            "PERFORMANCE_PRICE_AREA_ID": price_area_id,
            "ATYPE": "N",
        }
    )
    destination = urlunsplit(("https", source.netloc, path, query, ""))
    if _get_utiki_page_type(destination) != "server_assigned_quantity":
        return None
    return destination


def _utiki_same_origin(first, second):
    """判斷兩個 uTiki 售票網址是否同源。"""

    return same_origin(first, second)


def _pop_pending_navigation_if_nonce(identity, nonce, *, runtime_for_identity):
    """僅在 nonce 相符時移除待處理導覽。"""

    transition = runtime_for_identity(identity)["transition"]
    current = transition["pending_navigation"]
    if current and current.get("nonce") == nonce:
        transition["pending_navigation"] = None
        return current
    return None


def _utiki_pending_navigation_is_current(identity, pending, *, runtime_for_identity):
    """判斷待處理導覽是否仍屬於目前分頁狀態。"""

    current = runtime_for_identity(identity)["transition"][
        "pending_navigation"
    ]
    return bool(current and pending and current.get("nonce") == pending.get("nonce"))


def _decode_utiki_json_result(raw_value):
    """將 uTiki 頁面的回傳值解碼為 JSON 資料。"""

    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _utiki_selection_config_fingerprint(config_dict, *, behavior_for_key):
    """建立影響選位結果之設定的穩定指紋。"""

    area_config = config_dict.get("area_auto_select", {})
    payload = {
        "ticket_number": config_dict.get("ticket_number", 1),
        "keyword_exclude": config_dict.get("keyword_exclude", ""),
        "area_auto_fallback": config_dict.get("area_auto_fallback", False),
        "area_enable": area_config.get("enable", False),
        "area_keyword": area_config.get("area_keyword", ""),
        "area_mode": area_config.get("mode", "from top to bottom"),
        "seat_selection_mode": behavior_for_key(
            config_dict, "seat_selection_mode", "manual"
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utiki_navigation_urls_match(first_url, second_url):
    """判斷兩個導覽網址是否代表同一標準目的地。"""

    try:
        first = urlsplit(str(first_url or ""))
        second = urlsplit(str(second_url or ""))
    except ValueError:
        return False
    first_query = _utiki_unique_query(first_url)
    second_query = _utiki_unique_query(second_url)
    return bool(
        not first.fragment
        and not second.fragment
        and _utiki_same_origin(first, second)
        and first.path == second.path
        and first_query is not None
        and first_query == second_query
    )


def _utiki_new_pending_navigation(
    kind,
    source_url,
    area_url,
    target,
    config_dict,
    selection_mode="manual",
    *,
    identity,
    document_for_identity,
    behavior_for_key,
):
    """建立跨文件追蹤用的待處理導覽紀錄。"""

    if selection_mode == "computer":
        destination_url = _build_utiki_server_assignment_destination_url(
            area_url, target
        )
        route = "0201_001"
    elif selection_mode == "manual":
        destination_url = _build_utiki_destination_url(area_url, target)
        route = target.get("route")
    else:
        return None
    area_query = _utiki_unique_query(area_url)
    if (
        not destination_url
        or area_query is None
        or set(area_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}
        or not re.fullmatch(r"[A-Za-z0-9_-]+", area_query.get("PRODUCT_ID", ""))
    ):
        return None
    document = document_for_identity(identity)
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
        "config_fingerprint": _utiki_selection_config_fingerprint(
            config_dict, behavior_for_key=behavior_for_key
        ),
        "created_at": time.monotonic(),
    }


def _utiki_new_date_native_pending(
    snapshot, plan, *, identity, document_for_identity,
):
    """為已原子提交的原生場次點擊建立跨文件防護。"""
    if not isinstance(snapshot, DateSnapshot) or not isinstance(plan, DateActionPlan):
        return None
    source_url = snapshot.page_url
    source_query = _utiki_unique_query(source_url)
    profile = get_utiki_tenant_profile(source_url)
    product_id = str((source_query or {}).get("PRODUCT_ID") or "")
    if (
        _get_utiki_page_type(source_url) != "date"
        or not profile
        or not re.fullmatch(r"[A-Za-z0-9_-]+", product_id)
        or plan.action_kind
        not in {
            "normal",
            "anchor_native",
            "vip_native",
            "structural_native",
        }
    ):
        return None
    area_url = str(plan.target_url or "")
    if area_url and (
        plan.action_kind not in {"normal", "anchor_native"}
        or _get_utiki_page_type(area_url) != "area"
        or (_utiki_unique_query(area_url) or {}).get("PRODUCT_ID") != product_id
    ):
        return None
    document = document_for_identity(identity)
    return {
        "kind": "DATE_NATIVE",
        "phase": "PREPARED",
        "nonce": uuid.uuid4().hex,
        "source_url": source_url,
        "source_document_id": document["last_document"],
        "area_url": area_url,
        "product_id": product_id,
        "tenant_path": profile.tenant_path,
        "host": profile.host,
        "action_kind": plan.action_kind,
        "created_at": time.monotonic(),
    }


def _utiki_date_native_area_matches(pending, current_url):
    """判斷原生場次導覽是否到達預期區域頁。"""

    if not isinstance(pending, dict) or pending.get("kind") != "DATE_NATIVE":
        return False
    source_url = str(pending.get("source_url") or "")
    source_profile = get_utiki_tenant_profile(source_url)
    current_profile = get_utiki_tenant_profile(current_url)
    current_query = _utiki_unique_query(current_url)
    try:
        source = urlsplit(source_url)
        current = urlsplit(current_url)
    except ValueError:
        return False
    if (
        _get_utiki_page_type(current_url) != "area"
        or not source_profile
        or not current_profile
        or source_profile.host != pending.get("host")
        or source_profile.tenant_path != pending.get("tenant_path")
        or current_profile.host != pending.get("host")
        or current_profile.tenant_path != pending.get("tenant_path")
        or not _utiki_same_origin(source, current)
        or current_query is None
        or set(current_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}
        or current_query.get("PRODUCT_ID") != pending.get("product_id")
    ):
        return False
    area_url = str(pending.get("area_url") or "")
    return (
        _utiki_navigation_urls_match(current_url, area_url)
        if area_url
        else current.path == f"{pending.get('tenant_path', '')}/UTK0204_"
    )


def _utiki_pending_from_fast_marker(marker):
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
    source_query = _utiki_unique_query(source_url)
    destination_query = _utiki_unique_query(destination_url)
    page_type = _get_utiki_page_type(destination_url)
    token = re.compile(r"[A-Za-z0-9_-]+")
    max_expiry = time.time() * 1000 + 1000 * (
        UTIKI_PENDING_NAVIGATION_TTL_SECONDS + UTIKI_FAST_MARKER_CLOCK_SKEW_SECONDS
    )
    if (
        not re.fullmatch(r"[a-f0-9]{32}", nonce)
        or not 0 < expires_at <= max_expiry
        or _get_utiki_page_type(source_url) != "date"
        or page_type not in {"seat", "general_admission"}
        or not _utiki_same_origin(source, destination)
        or source.path.rsplit("/", 1)[0] != destination.path.rsplit("/", 1)[0]
        or source_query is None
        or destination_query is None
        or set(destination_query)
        != {"PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"}
    ):
        return None
    product_id = str(source_query.get("PRODUCT_ID") or "")
    performance_id = str(destination_query.get("PERFORMANCE_ID") or "")
    group_id = str(destination_query.get("GROUP_ID") or "")
    price_area_id = str(destination_query.get("PERFORMANCE_PRICE_AREA_ID") or "")
    if (
        not all(
            token.fullmatch(value)
            for value in (product_id, performance_id, price_area_id)
        )
        or (group_id and not token.fullmatch(group_id))
        or (page_type == "seat" and not group_id)
    ):
        return None
    area_path = f"{source.path.rsplit('/', 1)[0]}/UTK0204_"
    area_url = urlunsplit(
        (
            source.scheme,
            source.netloc,
            area_path,
            urlencode(
                {
                    "PERFORMANCE_ID": performance_id,
                    "PRODUCT_ID": product_id,
                }
            ),
            "",
        )
    )
    created_at = (
        time.monotonic()
        + (expires_at - time.time() * 1000) / 1000
        - UTIKI_PENDING_NAVIGATION_TTL_SECONDS
    )
    return {
        "kind": "FAST",
        "phase": "RETURNING",
        "nonce": nonce,
        "source_url": source_url,
        "source_document_id": "",
        "area_url": area_url,
        "destination_url": destination_url,
        "route": "0205" if page_type == "seat" else "0202",
        "performance_id": performance_id,
        "product_id": product_id,
        "group_id": group_id,
        "price_area_id": price_area_id,
        "marker_expires_at": expires_at,
        "created_at": created_at,
    }


def _utiki_fast_marker_matches_pending(marker_pending, pending):
    """判斷快速標記與待處理導覽是否屬於同一意圖。"""

    if not marker_pending or not pending or pending.get("kind") != "FAST":
        return False
    if marker_pending.get("nonce") != pending.get("nonce"):
        return False
    for key in ("route", "performance_id", "product_id", "group_id", "price_area_id"):
        if marker_pending.get(key) != pending.get(key):
            return False
    return all(
        _utiki_navigation_urls_match(marker_pending.get(key), pending.get(key))
        for key in ("source_url", "area_url", "destination_url")
    )


async def _utiki_read_fast_navigation_marker(tab):
    """讀取頁面儲存的快速導覽標記。"""

    marker_key_json = json.dumps(UTIKI_FAST_MARKER_KEY)
    try:
        raw_value = await tab.evaluate(
            f"""(() => {{
            // __ticketsHunterUtikiFastMarkerRead
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
        }})()"""
        )
    except Exception:
        return {"available": False, "present": False, "marker": None}
    result = _decode_utiki_json_result(raw_value)
    return (
        result
        if isinstance(result, dict)
        else {
            "available": False,
            "present": False,
            "marker": None,
        }
    )


async def _utiki_delete_fast_navigation_marker(tab, nonce):
    """刪除 nonce 相符的快速導覽標記。"""

    marker_key_json = json.dumps(UTIKI_FAST_MARKER_KEY)
    nonce_json = json.dumps(nonce)
    try:
        return await tab.evaluate(
            f"""(() => {{
            // __ticketsHunterUtikiFastMarkerDelete
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
        }})()"""
        )
    except Exception:
        return False


async def _utiki_delete_malformed_fast_marker(tab, expected_raw):
    """刪除仍與觀察值相同的格式錯誤快速標記。"""

    marker_key_json = json.dumps(UTIKI_FAST_MARKER_KEY)
    expected_raw_json = json.dumps(expected_raw)
    try:
        return await tab.evaluate(
            f"""(() => {{
            // __ticketsHunterUtikiMalformedMarkerDelete
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
        }})()"""
        )
    except Exception:
        return False


async def _utiki_commit_native_date(
    tab, snapshot, plan, debug, *, identity, transition, runtime_for_identity,
):
    """先安裝跨文件意圖，再送出一次原生場次點擊。"""
    if transition["pending_navigation"]:
        return False
    pending = _utiki_new_date_native_pending(
        snapshot, plan, identity=identity,
        document_for_identity=lambda key: runtime_for_identity(key)["document"],
    )
    if not pending or transition["pending_navigation"]:
        return False
    transition["pending_navigation"] = pending
    try:
        committed = await commit_date_action(tab, snapshot, plan, pending["nonce"])
    except Exception as exc:
        if _utiki_pending_navigation_is_current(
            identity, pending, runtime_for_identity=runtime_for_identity
        ):
            pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[UTIKI DATE] Click failed: {exc}")
        return False
    if committed is CommitResult.REJECTED:
        _pop_pending_navigation_if_nonce(
            identity, pending["nonce"], runtime_for_identity=runtime_for_identity
        )
        debug.log(f"[UTIKI DATE] Native commit rejected ({plan.action_kind})")
        return False
    if _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        pending["phase"] = "DISPATCH_UNKNOWN"
    if committed is CommitResult.COMMITTED:
        debug.log(f"[UTIKI DATE] Selected ({plan.action_kind}): {plan.text}")
        return True
    outcome = committed.value if isinstance(committed, CommitResult) else "unknown"
    debug.log(f"[UTIKI DATE] Native commit {outcome} ({plan.action_kind})")
    return False


def _utiki_server_assignment_pending_is_expected(pending):
    """判斷待處理導覽是否為預期的電腦配位流程。"""

    return bool(
        pending
        and pending.get("kind") == "NATIVE"
        and pending.get("route") == "0201_001"
        and pending.get("selection_mode") == "computer"
        and re.fullmatch(r"[A-Za-z0-9_-]+", str(pending.get("product_id") or ""))
    )


def _utiki_server_assignment_is_authorized(
    tab, transition, *, purchase_key_for_tab,
):
    """判斷目前分頁是否獲准進入電腦配位票數頁。"""

    purchase_key = purchase_key_for_tab(tab)
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


def _utiki_destination_is_valid(expected, page_type, current_url, dom_ids):
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
    if (route == "0201_001" and expected.get("selection_mode") != "computer") or (
        route != "0201_001" and expected.get("selection_mode", "manual") != "manual"
    ):
        return False
    area_url = str(expected.get("area_url") or "")
    if (
        _get_utiki_page_type(current_url) != expected_page_type
        or _get_utiki_page_type(area_url) != "area"
    ):
        return False
    current = urlsplit(current_url)
    area = urlsplit(area_url)
    if not _utiki_same_origin(current, area):
        return False
    if route == "0202" and current.path.rsplit("/", 1)[-1] not in {
        "UTK0202_",
        "UTK0203_",
    }:
        return False
    area_query = _utiki_unique_query(area_url)
    if area_query is None or set(area_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}:
        return False
    if area_query.get("PERFORMANCE_ID") != expected.get(
        "performance_id"
    ) or area_query.get("PRODUCT_ID") != expected.get("product_id"):
        return False
    query = _utiki_unique_query(current_url)
    expected_query_keys = {"PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"}
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
    # The server-assigned page may carry GROUP_ID only in its exact URL.
    # If a DOM input exists, it still has to agree with that URL identity.
    group_matches = str(dom_ids.get("group_id") or "") == expected.get("group_id")
    if route == "0201_001" and dom_ids.get("group_input_present") is False:
        group_matches = not dom_ids.get("group_id")
    return (
        str(dom_ids.get("performance_id") or "") == expected.get("performance_id")
        and str(dom_ids.get("product_id") or "") == expected.get("product_id")
        and group_matches
        and str(dom_ids.get("price_area_id") or "") == expected.get("price_area_id")
    )


def _utiki_date_native_direct_pending(pending, page_type):
    """判斷原生場次導覽是否直接到達票券頁。"""

    return bool(
        isinstance(pending, dict)
        and pending.get("kind") == "DATE_NATIVE"
        and not pending.get("area_url")
        and pending.get("action_kind") in {"vip_native", "structural_native"}
        and page_type in {"seat", "general_admission"}
    )


def _utiki_date_native_direct_state_is_valid(pending, page_type, state):
    """驗證原生場次直接導覽的目的地文件狀態。"""

    if not isinstance(state, dict) or not state.get("ready"):
        return False
    source_url = str(pending.get("source_url") or "")
    current_url = str(state.get("page_url") or "")
    source_profile = get_utiki_tenant_profile(source_url)
    current_profile = get_utiki_tenant_profile(current_url)
    source_query = _utiki_unique_query(source_url)
    query = _utiki_unique_query(current_url)
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
        not source_profile
        or not current_profile
        or source_query is None
        or query is None
        or _get_utiki_page_type(source_url) != "date"
        or _get_utiki_page_type(current_url) != page_type
        or current.path not in expected_paths[page_type]
        or not _utiki_same_origin(source, current)
        or any(
            profile.host != pending.get("host")
            or profile.tenant_path != pending.get("tenant_path")
            for profile in (source_profile, current_profile)
        )
        or source_query.get("PRODUCT_ID") != pending.get("product_id")
        or set(query) != {"PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"}
    ):
        return False
    url_ids = tuple(
        query.get(key, "")
        for key in ("PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID")
    )
    token = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token.fullmatch(str(pending.get("product_id") or ""))
        or not token.fullmatch(url_ids[0])
        or not token.fullmatch(url_ids[2])
        or (url_ids[1] and not token.fullmatch(url_ids[1]))
        or (page_type == "seat" and not url_ids[1])
    ):
        return False
    return url_ids == tuple(
        str(state.get(key) or "")
        for key in ("performance_id", "group_id", "price_area_id")
    ) and str(state.get("product_id") or "") == pending.get("product_id")


async def _utiki_return_to_native_area(tab, expected, source_url):
    """返回來源區域頁，讓原生導覽流程重新接手。"""

    area_url = str(expected.get("area_url") or "")
    if _get_utiki_page_type(area_url) != "area" or _get_utiki_page_type(
        source_url
    ) not in {"seat", "general_admission", "server_assigned_quantity"}:
        return False
    area_url_json = json.dumps(area_url)
    source_url_json = json.dumps(source_url)
    try:
        return await tab.evaluate(
            f"""(() => {{
            // __ticketsHunterUtikiReturn
            const areaUrl = {area_url_json};
            const sourceUrl = {source_url_json};
            if (location.href !== sourceUrl) return false;
            location.assign(areaUrl);
            return true;
        }})()"""
        )
    except Exception:
        return False


async def _utiki_return_current_pending(
    tab, identity, pending, source_url, *, runtime_for_identity,
):
    """將目前待處理導覽保留至返回來源頁後繼續。"""

    if not _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        return False
    pending["phase"] = "RETURNING"
    pending["return_source_url"] = source_url
    return await _utiki_return_to_native_area(tab, pending, source_url)


async def _utiki_read_destination_state(tab, page_type):
    """讀取目的地頁面的文件與商品身分狀態。"""

    marker_selectors = (
        ["#TBL", ".ticket .checkNum"]
        if page_type == "seat"
        else ["#table_tickettype", "input.numbox[key]"]
    )
    marker_json = json.dumps(marker_selectors)
    price_area_selector_json = json.dumps(
        "#PRICE_AREA_ID" if page_type == "seat" else "#PERFORMANCE_PRICE_AREA_ID"
    )
    group_required_json = "true" if page_type == "seat" else "false"
    raw_value = await tab.evaluate(
        f"""
        (() => new Promise(resolve => {{
            // __ticketsHunterUtikiDestinationState
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
                    group_input_present: !!groupInput,
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
    """,
        await_promise=True,
    )
    return _decode_utiki_json_result(raw_value)


async def _utiki_clear_source_navigation(
    tab, identity, pending, marker_present, marker_pending, *, runtime_for_identity
):
    """清除來源頁待處理導覽與相符快速標記。"""

    if not _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        return False
    if pending.get("kind") == "FAST" and marker_present:
        if not _utiki_fast_marker_matches_pending(marker_pending, pending):
            _pop_pending_navigation_if_nonce(
                identity, pending.get("nonce"),
                runtime_for_identity=runtime_for_identity,
            )
            return False
        if not await _utiki_delete_fast_navigation_marker(tab, marker_pending["nonce"]):
            return False
    elif pending.get("kind") == "NATIVE" and marker_present:
        if not await _utiki_delete_fast_navigation_marker(tab, marker_pending["nonce"]):
            return False
    return (
        _pop_pending_navigation_if_nonce(
            identity, pending.get("nonce"),
            runtime_for_identity=runtime_for_identity,
        )
        is not None
    )


def _utiki_reduce_date_native_navigation(
    identity,
    pending,
    current_url,
    page_type,
    document_changed,
    exact_source,
    reload_requested_at,
    marker_present,
    *,
    runtime_for_identity,
):
    """歸納原生場次來源狀態，判斷是否仍需期限內復原。"""
    if page_type == "area":
        if (
            document_changed
            and not marker_present
            and _utiki_date_native_area_matches(pending, current_url)
        ):
            return (
                _pop_pending_navigation_if_nonce(
                    identity, pending.get("nonce"),
                    runtime_for_identity=runtime_for_identity,
                )
                is not None
            )
        return False
    if (
        page_type == "date"
        and document_changed
        and exact_source
        and reload_requested_at
        and not marker_present
    ):
        return (
            _pop_pending_navigation_if_nonce(
                identity, pending.get("nonce"),
                runtime_for_identity=runtime_for_identity,
            )
            is not None
        )
    return None


async def _utiki_reconcile_navigation_source(
    tab, current_url, page_type, config_dict, *,
    identity, runtime, runtime_for_identity, reload_with_interval,
):
    """協調目前網址與待處理導覽的來源狀態。"""

    transition = runtime["transition"]
    observed_pending = transition["pending_navigation"]
    marker_state = await _utiki_read_fast_navigation_marker(tab)
    pending = transition["pending_navigation"]
    if observed_pending:
        if not _utiki_pending_navigation_is_current(
            identity, observed_pending,
            runtime_for_identity=runtime_for_identity,
        ):
            return False
        pending = observed_pending
    elif pending:
        return False
    storage_available = bool(marker_state.get("available"))
    if not storage_available and not pending:
        return True
    marker_present = storage_available and bool(marker_state.get("present"))
    marker_pending = _utiki_pending_from_fast_marker(
        marker_state.get("marker") if marker_present else None
    )
    if storage_available and marker_present and not marker_pending:
        raw = marker_state.get("raw")
        if isinstance(raw, str):
            await _utiki_delete_malformed_fast_marker(tab, raw)
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

    expected_url = pending.get("source_url" if page_type == "date" else "area_url")
    exact_source = _utiki_navigation_urls_match(current_url, expected_url)
    source_document = str(pending.get("source_document_id") or "")
    if not source_document and current_document:
        pending["source_document_id"] = current_document
        source_document = current_document
    document_changed = bool(
        source_document and current_document and current_document != source_document
    )
    reload_requested_at = float(pending.get("reload_requested_at") or 0)
    if pending.get("kind") == "DATE_NATIVE":
        date_result = _utiki_reduce_date_native_navigation(
            identity,
            pending,
            current_url,
            page_type,
            document_changed,
            exact_source,
            reload_requested_at,
            marker_present,
            runtime_for_identity=runtime_for_identity,
        )
        if date_result is not None or page_type == "area":
            return bool(date_result)
    elif document_changed and (exact_source or reload_requested_at):
        return await _utiki_clear_source_navigation(
            tab, identity, pending, marker_present, marker_pending,
            runtime_for_identity=runtime_for_identity,
        )

    pending_expired = (
        time.monotonic() - pending.get("created_at", 0)
        >= UTIKI_PENDING_NAVIGATION_TTL_SECONDS
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
            and now - reload_requested_at < UTIKI_PENDING_NAVIGATION_TTL_SECONDS
        )
    ):
        return False
    if not _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        return False
    reloaded = await reload_with_interval(
        tab, config_dict, f"{page_type} navigation outcome unknown"
    )
    if reloaded and _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        pending["reload_requested_at"] = now
    return False


async def _utiki_attest_pending_destination(
    tab, page_type, pending, debug, stale_marker_nonce="", *,
    identity, runtime_for_identity,
):
    """驗證待處理導覽是否抵達可信任的目的地。"""

    runtime = runtime_for_identity(identity)
    try:
        state = await _utiki_read_destination_state(tab, page_type)
    except Exception as exc:
        debug.log(f"[UTIKI NAV] Destination attestation failed: {exc}")
        return False
    current_url = str(state.get("page_url") or "") if isinstance(state, dict) else ""
    current_document = str(runtime["document"]["last_document"] or "")
    state_document = (
        str(state.get("document_id") or "") if isinstance(state, dict) else ""
    )
    source_document = str(pending.get("source_document_id") or "")
    if (
        isinstance(state, dict)
        and state.get("ready")
        and state_document
        and state_document == current_document
        and source_document
        and state_document != source_document
        and _utiki_destination_is_valid(pending, page_type, current_url, state)
    ):
        current = runtime["transition"]["pending_navigation"]
        if not current or current.get("nonce") != pending.get("nonce"):
            return False
        marker_nonce = (
            pending["nonce"] if pending.get("kind") == "FAST" else stale_marker_nonce
        )
        if marker_nonce and not await _utiki_delete_fast_navigation_marker(
            tab, marker_nonce
        ):
            return False
        removed = _pop_pending_navigation_if_nonce(
            identity, pending["nonce"],
            runtime_for_identity=runtime_for_identity,
        )
        debug.log(f"[UTIKI {pending.get('kind')}] Destination IDs verified")
        return removed is not None
    if isinstance(state, dict) and state.get("document_ready") != "complete":
        debug.log("[UTIKI NAV] Destination is still loading")
        return False
    debug.log("[UTIKI NAV] Destination mismatch; returning to native area flow")
    await _utiki_return_current_pending(
        tab, identity, pending, current_url,
        runtime_for_identity=runtime_for_identity,
    )
    return False


async def _utiki_attest_date_native_direct_destination(
    tab, page_type, pending, debug, *, identity, runtime_for_identity,
):
    """驗證原生場次導覽直接抵達的票券頁。"""

    if not _utiki_date_native_direct_pending(pending, page_type):
        return None
    runtime = runtime_for_identity(identity)
    try:
        state = await _utiki_read_destination_state(tab, page_type)
    except Exception as exc:
        debug.log(f"[UTIKI DATE] Direct destination attestation failed: {exc}")
        return False
    if not _utiki_pending_navigation_is_current(
        identity, pending, runtime_for_identity=runtime_for_identity
    ):
        return False
    current_document = str(runtime["document"]["last_document"] or "")
    source_document = str(pending.get("source_document_id") or "")
    state_document = (
        str(state.get("document_id") or "") if isinstance(state, dict) else ""
    )
    if (
        state_document
        and source_document
        and current_document == state_document
        and state_document != source_document
        and _utiki_date_native_direct_state_is_valid(pending, page_type, state)
    ):
        removed = _pop_pending_navigation_if_nonce(
            identity, pending["nonce"],
            runtime_for_identity=runtime_for_identity,
        )
        debug.log("[UTIKI DATE] Direct destination IDs verified")
        return removed is not None
    if isinstance(state, dict) and state.get("document_ready") != "complete":
        debug.log("[UTIKI DATE] Direct destination is still loading")
    else:
        debug.log("[UTIKI DATE] Direct destination mismatch; pending retained")
    return False


async def _utiki_attest_destination(
    tab, page_type, config_dict, *,
    identity, runtime, runtime_for_identity, behavior_for_key,
):
    """在票券頁自動化前協調快速與原生導覽來源。"""
    transition = runtime["transition"]
    debug = util.create_debug_logger(config_dict)
    observed_pending = transition["pending_navigation"]
    marker_state = await _utiki_read_fast_navigation_marker(tab)
    pending = transition["pending_navigation"]
    if observed_pending:
        if not _utiki_pending_navigation_is_current(
            identity, observed_pending,
            runtime_for_identity=runtime_for_identity,
        ):
            return False
        pending = observed_pending
    elif pending:
        return False
    current_url = str(marker_state.get("page_url") or str(tab.target.url))
    direct_result = await _utiki_attest_date_native_direct_destination(
        tab, page_type, pending, debug,
        identity=identity, runtime_for_identity=runtime_for_identity,
    )
    if direct_result is not None:
        return direct_result
    if not marker_state.get("available"):
        if not pending:
            return True
        if pending.get("kind") == "NATIVE":
            if pending.get("phase") == "RETURNING":
                await _utiki_return_current_pending(
                    tab,
                    identity,
                    pending,
                    pending.get("return_source_url", ""),
                    runtime_for_identity=runtime_for_identity,
                )
                return False
            return await _utiki_attest_pending_destination(
                tab, page_type, pending, debug,
                identity=identity, runtime_for_identity=runtime_for_identity,
            )
        debug.log("[UTIKI FAST MARKER] Marker storage unavailable; returning to native flow")
        await _utiki_return_current_pending(
            tab, identity, pending, current_url,
            runtime_for_identity=runtime_for_identity,
        )
        return False
    marker_present = bool(marker_state.get("present"))
    marker = marker_state.get("marker") if marker_present else None
    marker_pending = _utiki_pending_from_fast_marker(marker)
    if marker_present and not marker_pending:
        raw = marker_state.get("raw")
        if isinstance(raw, str):
            await _utiki_delete_malformed_fast_marker(tab, raw)
        return False
    if pending and pending.get("kind") == "NATIVE":
        if pending.get("phase") == "RETURNING":
            await _utiki_return_current_pending(
                tab,
                identity,
                pending,
                pending.get("return_source_url", ""),
                runtime_for_identity=runtime_for_identity,
            )
            return False
        marker_nonce = (
            str(marker.get("nonce") or "") if isinstance(marker, dict) else ""
        )
        stale_marker_nonce = (
            marker_nonce if re.fullmatch(r"[a-f0-9]{32}", marker_nonce) else ""
        )
        return await _utiki_attest_pending_destination(
            tab, page_type, pending, debug, stale_marker_nonce,
            identity=identity, runtime_for_identity=runtime_for_identity,
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
        await _utiki_return_current_pending(
            tab, identity, marker_pending, current_url,
            runtime_for_identity=runtime_for_identity,
        )
        return False
    if pending.get("phase") == "RETURNING":
        await _utiki_return_current_pending(
            tab, identity, pending, pending.get("return_source_url", ""),
            runtime_for_identity=runtime_for_identity,
        )
        return False
    marker_matches = bool(
        _utiki_fast_marker_matches_pending(marker_pending, pending)
        and _utiki_navigation_urls_match(
            marker_pending.get("destination_url"),
            marker_state.get("page_url") or str(tab.target.url),
        )
        and marker_pending.get("marker_expires_at", 0) >= time.time() * 1000
    )
    pending_valid = bool(
        pending.get("config_fingerprint")
        == _utiki_selection_config_fingerprint(
            config_dict, behavior_for_key=behavior_for_key
        )
        and time.monotonic() - pending.get("created_at", 0)
        <= UTIKI_PENDING_NAVIGATION_TTL_SECONDS
    )
    if not marker_matches or not pending_valid:
        debug.log(
            "[UTIKI FAST MARKER] Provenance mismatch or expiry; returning to native area flow"
        )
        await _utiki_return_current_pending(
            tab, identity, pending, current_url,
            runtime_for_identity=runtime_for_identity,
        )
        return False
    return await _utiki_attest_pending_destination(
        tab, page_type, pending, debug,
        identity=identity, runtime_for_identity=runtime_for_identity,
    )


async def _utiki_reconcile_area_navigation(
    tab, current_url, config_dict=None, *,
    identity, runtime, runtime_for_identity, reload_with_interval,
):
    """在抵達新文件前阻止重複送出區域操作。"""
    return await _utiki_reconcile_navigation_source(
        tab, current_url, "area", config_dict or {},
        identity=identity, runtime=runtime,
        runtime_for_identity=runtime_for_identity,
        reload_with_interval=reload_with_interval,
    )


async def _ctbc_dispatch_fast_area_navigation(
    tab,
    config_dict,
    date_snapshot,
    date_plan,
    date_target,
    target,
    started,
    debug,
    identity,
    transition,
    *,
    runtime_for_identity,
    behavior_for_key,
):
    """送出已選定的 fast 目的地並追蹤結果。"""
    target_action = _utiki_area_action_payload(target)
    pending = _utiki_new_pending_navigation(
        "FAST",
        date_snapshot.page_url,
        date_target["url"],
        target_action,
        config_dict,
        identity=identity,
        document_for_identity=lambda key: runtime_for_identity(key)["document"],
        behavior_for_key=behavior_for_key,
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
            fast_marker_key=UTIKI_FAST_MARKER_KEY,
            fast_marker_ttl_ms=int(UTIKI_PENDING_NAVIGATION_TTL_SECONDS * 1000),
        )
    except Exception as exc:
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[CTBC FAST] Dispatch outcome unknown: {exc}")
        return True
    if committed is CommitResult.REJECTED:
        await _utiki_delete_fast_navigation_marker(tab, pending["nonce"])
        _pop_pending_navigation_if_nonce(
            identity, pending["nonce"], runtime_for_identity=runtime_for_identity
        )
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
        f"[CTBC FAST] Selected: {target.text} ({target.status}) " f"in {elapsed_ms} ms"
    )
    return True


async def _utiki_commit_native_area(
    tab,
    config_dict,
    snapshot,
    target,
    seat_selection_mode,
    debug,
    *,
    identity,
    transition,
    runtime_for_identity,
    behavior_for_key,
):
    """先安裝 pending，再提交已選定的原生區域操作。"""
    page_url = snapshot.page_url
    if transition["pending_navigation"]:
        return False
    pending = _utiki_new_pending_navigation(
        "NATIVE",
        page_url,
        page_url,
        _utiki_area_action_payload(target),
        config_dict,
        selection_mode=seat_selection_mode,
        identity=identity,
        document_for_identity=lambda key: runtime_for_identity(key)["document"],
        behavior_for_key=behavior_for_key,
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
            tab,
            snapshot,
            target,
            config_dict.get("ticket_number", 1),
            pending["nonce"],
            UTIKI_FAST_MARKER_KEY,
        )
        if commit_result is CommitResult.REJECTED or (
            seat_selection_mode == "manual" and commit_result is CommitResult.STALE
        ):
            _pop_pending_navigation_if_nonce(
                identity, pending["nonce"], runtime_for_identity=runtime_for_identity
            )
            debug.log(
                "[UTIKI AREA] Area commit "
                f"{commit_result.value}; no navigation row click was sent"
            )
            return False
        if seat_selection_mode == "computer" and commit_result is CommitResult.STALE:
            pending["phase"] = "DISPATCH_UNKNOWN"
            debug.log(
                "[UTIKI AREA] Computer-assignment preparation changed "
                "document; navigation outcome is unknown"
            )
            return False
        if commit_result is not CommitResult.COMMITTED:
            raise RuntimeError("area commit returned an unknown result")
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[UTIKI AREA] Selected: {target.text} ({target.status})")
        return True
    except Exception as exc:
        pending["phase"] = "DISPATCH_UNKNOWN"
        debug.log(f"[UTIKI AREA] Click failed: {exc}")
        return False


async def _utiki_server_assigned_authorization(
    tab, config_dict, *, identity, runtime, runtime_for_identity,
    purchase_key_for_tab, behavior_for_key,
):
    """只為原電腦配位票數頁維持既有來源紀錄。"""
    transition = runtime["transition"]
    current_url = str(tab.target.url)
    profile = get_utiki_tenant_profile(current_url)
    if (
        behavior_for_key(config_dict, "seat_selection_mode", "manual")
        != "computer"
        or not profile
        or not profile.supports_server_assignment
    ):
        transition["server_assignment_authorized"] = None
        return None
    pending = transition["pending_navigation"]
    authorization = _utiki_server_assignment_is_authorized(
        tab, transition, purchase_key_for_tab=purchase_key_for_tab
    )
    if pending:
        if not _utiki_server_assignment_pending_is_expected(pending):
            transition["server_assignment_authorized"] = None
            return None
        if not await _utiki_attest_destination(
            tab, "server_assigned_quantity", config_dict,
            identity=identity, runtime=runtime,
            runtime_for_identity=runtime_for_identity,
            behavior_for_key=behavior_for_key,
        ):
            return None
        purchase_key = purchase_key_for_tab(tab)
        if purchase_key is None:
            return None
        authorization = {
            "purchase_key": purchase_key,
            "product_id": pending["product_id"],
        }
        transition["server_assignment_authorized"] = authorization
    elif authorization is None:
        return None
    return authorization


def _utiki_clear_server_assignment_for_other_page(transition):
    transition["server_assignment_authorized"] = None
