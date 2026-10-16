"""Pure selection and audit rules for the uTiki2 ticketing family."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import util

from .profiles import (
    PageKind,
    classify_utiki_page,
    get_utiki_tenant_profile,
    same_origin,
    unique_query,
)
from .domain.snapshots import (
    AreaRow,
    DATE_CONTROL_FINGERPRINT_FIELDS,
    DateControl,
    DateRow,
    InvalidField,
    MapArea,
    PresentField,
    QuantityRow,
    parse_nonnegative_count,
)


@dataclass(frozen=True)
class DateSnapshot:
    ok: bool
    page_url: str
    document_id: str
    rows: tuple[DateRow, ...]
    reason: str = ""


@dataclass(frozen=True)
class DateActionPlan:
    row_index: int
    control_index: int
    text: str
    action_kind: str
    onclick: str
    control_fingerprint: tuple
    target_url: str = ""
    performance_id: str = ""
    product_id: str = ""
    tenant_path: str = ""
    host: str = ""
    map_namespace: str = ""
    fast_area_prefetch: bool = False
    action_token: str = ""


@dataclass(frozen=True)
class AreaSnapshot:
    ok: bool
    page_url: str
    document_id: str
    rows: tuple[AreaRow, ...]
    areas: tuple[MapArea, ...]
    reason: str = ""


@dataclass(frozen=True)
class AreaActionPlan:
    text: str
    area: str
    price: str
    status: str
    rel: str
    action_name: str
    route: str
    performance_id: str
    price_area_id: str
    group_id: str
    stable_args: tuple
    action_status: str
    direct_destination: bool


@dataclass(frozen=True)
class QuantitySnapshot:
    ok: bool
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    quantity_limit_raw: str
    rows: tuple[QuantityRow, ...]
    product_id: str = ""
    reason: str = ""
    performance_hidden_present: bool = False
    product_hidden_present: bool = False
    group_hidden_present: bool = False
    price_area_hidden_present: bool = False
    quantity_limit_present: bool = False


@dataclass(frozen=True)
class QuantityPlan:
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    quantity_limit: int
    target_key: str
    target_name: str
    ticket_number: int
    page_type: str = "general_admission"
    product_id: str = ""
    performance_hidden_present: bool = False
    product_hidden_present: bool = False
    group_hidden_present: bool = False
    price_area_hidden_present: bool = False
    quantity_limit_present: bool = False
    target_remaining_present: bool = False


@dataclass(frozen=True)
class SeatSnapshot:
    ok: bool
    page_url: str
    document_id: str
    performance_id: str = ""
    group_id: str = ""
    price_area_id: str = ""
    product_id: str = ""
    product_hidden_present: bool = False
    quantity_limit_present: bool = False
    quantity_limit_raw: str = ""
    ticket_types: tuple = ()
    seats: tuple = ()
    submitted_seats: tuple = ()
    counters: tuple = ()
    visual_selected_count: int = 0
    reason: str = ""


@dataclass(frozen=True)
class SeatActionPlan:
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    product_id: str
    action: str
    ticket_number: int
    ticket_type_fingerprint: tuple
    ticket_type_id: str
    ticket_type_name: str
    selected_seat_fingerprints: tuple = ()
    target_seat_fingerprints: tuple = ()
    require_physical_adjacency: bool = False


@dataclass(frozen=True)
class SubmitPlan:
    page_url: str
    document_id: str
    page_kind: PageKind
    payload_fingerprint: tuple
    selection_plan: SeatActionPlan | QuantityPlan


@dataclass(frozen=True)
class DialogSnapshot:
    page_url: str
    document_id: str
    visible_count: int
    text: str


def _freeze_date_control(raw_control):
    """將場次控制項轉成不可變的安全資料。"""

    if not isinstance(raw_control, dict):
        return None
    actionable = raw_control.get("actionable")
    has_form = raw_control.get("has_form")
    if not isinstance(actionable, bool) or not isinstance(has_form, bool):
        return None
    string_fields = set(DATE_CONTROL_FINGERPRINT_FIELDS) - {"has_form"}
    if any(not isinstance(raw_control.get(field, ""), str) for field in string_fields):
        return None
    fingerprint = tuple(
        (field, has_form if field == "has_form" else raw_control.get(field, ""))
        for field in DATE_CONTROL_FINGERPRINT_FIELDS
    )
    return DateControl(actionable, fingerprint)


def parse_date_snapshot(raw_snapshot):
    """固定一份無副作用的場次表觀察快照。"""
    if isinstance(raw_snapshot, DateSnapshot):
        return raw_snapshot
    if not isinstance(raw_snapshot, dict):
        return None
    if raw_snapshot.get("ok") is False:
        return DateSnapshot(
            ok=False,
            page_url=str(raw_snapshot.get("page_url") or ""),
            document_id=str(raw_snapshot.get("document_id") or ""),
            rows=(),
            reason=str(raw_snapshot.get("reason") or "invalid-result"),
        )
    rows = raw_snapshot.get("rows")
    if not isinstance(rows, list):
        return None
    frozen_rows = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            return None
        controls = row.get("controls")
        if not isinstance(controls, list):
            return None
        frozen_controls = tuple(_freeze_date_control(item) for item in controls)
        if any(item is None for item in frozen_controls):
            return None
        frozen_rows.append(DateRow(row["text"], frozen_controls))
    page_url = raw_snapshot.get("page_url")
    document_id = raw_snapshot.get("document_id")
    if not isinstance(page_url, str) or not isinstance(document_id, str):
        return None
    return DateSnapshot(True, page_url, document_id, tuple(frozen_rows))


def parse_area_snapshot(raw_snapshot):
    """在區域快照進入決策邏輯前轉成不可變資料。"""
    if isinstance(raw_snapshot, AreaSnapshot):
        return raw_snapshot
    if not isinstance(raw_snapshot, dict):
        return None
    if raw_snapshot.get("ok") is False:
        return AreaSnapshot(
            ok=False,
            page_url=str(raw_snapshot.get("page_url") or ""),
            document_id=str(raw_snapshot.get("document_id") or ""),
            rows=(),
            areas=(),
            reason=str(raw_snapshot.get("reason") or "invalid-result"),
        )
    rows = raw_snapshot.get("rows")
    areas = raw_snapshot.get("areas")
    if not isinstance(rows, list) or not isinstance(areas, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    if any(not isinstance(area, dict) for area in areas):
        return None
    try:
        frozen_rows = tuple(
            AreaRow(row["area"], row["price"], row["status"], row["rel"])
            for row in rows
        )
        frozen_areas = tuple(
            MapArea(area["id"], area["href"], area["title"])
            for area in areas
        )
    except (KeyError, TypeError, ValueError):
        return None
    return AreaSnapshot(
        ok=True,
        page_url=str(raw_snapshot.get("page_url") or ""),
        document_id=str(raw_snapshot.get("document_id") or ""),
        rows=frozen_rows,
        areas=frozen_areas,
    )


def _quantity_presence_fields(raw_snapshot):
    """讀取票數快照中用來判斷欄位是否存在的資訊。"""

    product_id = str(raw_snapshot.get("product_id") or "")
    limit_raw = str(raw_snapshot.get("quantity_limit_raw") or "")
    return {
        "performance_hidden_present": bool(
            raw_snapshot.get("performance_hidden_present")
        ),
        "product_hidden_present": bool(raw_snapshot.get(
            "product_hidden_present",
            bool(product_id),
        )),
        "group_hidden_present": bool(
            raw_snapshot.get("group_hidden_present")
        ),
        "price_area_hidden_present": bool(
            raw_snapshot.get("price_area_hidden_present")
        ),
        "quantity_limit_present": bool(raw_snapshot.get(
            "quantity_limit_present",
            bool(limit_raw),
        )),
    }


def parse_quantity_snapshot(raw_snapshot):
    """固定一份自由入座票數頁面的觀察快照。"""
    if isinstance(raw_snapshot, QuantitySnapshot):
        return raw_snapshot
    if not isinstance(raw_snapshot, dict):
        return None
    if raw_snapshot.get("ok") is False:
        return QuantitySnapshot(
            ok=False,
            page_url=str(raw_snapshot.get("page_url") or ""),
            document_id=str(raw_snapshot.get("document_id") or ""),
            performance_id="",
            group_id="",
            price_area_id="",
            quantity_limit_raw="",
            rows=(),
            reason=str(raw_snapshot.get("reason") or "invalid-result"),
        )
    rows = raw_snapshot.get("rows")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) for row in rows
    ):
        return None
    if type(raw_snapshot.get("quantity_limit_raw", "")) is not str:
        return None
    if (
        "quantity_limit_present" in raw_snapshot
        and type(raw_snapshot["quantity_limit_present"]) is not bool
    ):
        return None
    try:
        frozen_rows = tuple(
            QuantityRow(
                key=row["key"],
                name=row["name"],
                text=row["text"],
                disabled=row["disabled"],
                remaining_present=row["remaining_present"],
                remaining_raw=row["remaining_raw"],
                value=row["value"],
            )
            for row in rows
        )
    except (KeyError, TypeError, ValueError):
        return None
    return QuantitySnapshot(
        ok=True,
        page_url=str(raw_snapshot.get("page_url") or ""),
        document_id=str(raw_snapshot.get("document_id") or ""),
        performance_id=str(raw_snapshot.get("performance_id") or ""),
        group_id=str(raw_snapshot.get("group_id") or ""),
        price_area_id=str(raw_snapshot.get("price_area_id") or ""),
        quantity_limit_raw=str(raw_snapshot.get("quantity_limit_raw") or ""),
        rows=frozen_rows,
        product_id=str(raw_snapshot.get("product_id") or ""),
        **_quantity_presence_fields(raw_snapshot),
    )


def _freeze_scalar_tuple(value):
    """將純量序列轉成不可變元組。"""

    if not isinstance(value, (list, tuple)) or not value:
        return None
    if any(not isinstance(item, (str, int, float, bool)) for item in value):
        return None
    return tuple(value)


def _freeze_ticket_type(raw_type):
    """將票種資料轉成不可變快照。"""

    if not isinstance(raw_type, dict):
        return None
    fingerprint = _freeze_scalar_tuple(raw_type.get("fingerprint"))
    string_fields = ("text", "type_id", "type_name", "counter_raw")
    if fingerprint is None or any(
        not isinstance(raw_type.get(field), str) for field in string_fields
    ):
        return None
    if type(raw_type.get("disabled")) is not bool or type(
        raw_type.get("counter_present")
    ) is not bool:
        return None
    return (
        fingerprint, raw_type["text"], raw_type["disabled"],
        raw_type["type_id"], raw_type["type_name"],
        raw_type["counter_present"], raw_type["counter_raw"],
    )


def _freeze_seat(raw_seat):
    """將可選座位資料轉成不可變快照。"""

    if not isinstance(raw_seat, dict):
        return None
    fingerprint = _freeze_scalar_tuple(raw_seat.get("fingerprint"))
    integer_fields = ("row_index", "cell_index")
    geometry_fields = ("center_x", "center_y", "width", "height")
    if fingerprint is None or not isinstance(raw_seat.get("title"), str):
        return None
    if any(type(raw_seat.get(field)) is not int for field in integer_fields):
        return None
    if any(
        isinstance(raw_seat.get(field), bool)
        or not isinstance(raw_seat.get(field), (int, float))
        for field in geometry_fields
    ):
        return None
    if any(type(raw_seat.get(field)) is not bool for field in ("available", "selected")):
        return None
    return (
        fingerprint, raw_seat["title"], raw_seat["row_index"],
        raw_seat["cell_index"], raw_seat["center_x"], raw_seat["center_y"],
        raw_seat["width"], raw_seat["height"], raw_seat["available"],
        raw_seat["selected"],
    )


def _freeze_submitted_seat(raw_seat):
    """將已送出的座位資料轉成不可變快照。"""

    if not isinstance(raw_seat, dict):
        return None
    if any(not isinstance(raw_seat.get(field), str) for field in ("A", "Y", "Z")):
        return None
    return raw_seat["A"], raw_seat["Y"], raw_seat["Z"]


def _freeze_seat_counter(raw_counter):
    """將座位票種計數資料轉成不可變快照。"""

    if not isinstance(raw_counter, dict):
        return None
    if not isinstance(raw_counter.get("id"), str) or not isinstance(
        raw_counter.get("counter_raw"), str
    ):
        return None
    return raw_counter["id"], raw_counter["counter_raw"]


def parse_seat_snapshot(raw_snapshot):
    """固定票種與劃位座位的合併觀察快照。"""
    if isinstance(raw_snapshot, SeatSnapshot):
        return raw_snapshot
    if not isinstance(raw_snapshot, dict):
        return None
    if raw_snapshot.get("ok") is False:
        return SeatSnapshot(
            ok=False,
            page_url=str(raw_snapshot.get("page_url") or ""),
            document_id=str(raw_snapshot.get("document_id") or ""),
            reason=str(raw_snapshot.get("reason") or "invalid-result"),
        )
    collections = {}
    parsers = {
        "ticket_types": _freeze_ticket_type,
        "seats": _freeze_seat,
        "submitted_seats": _freeze_submitted_seat,
        "counters": _freeze_seat_counter,
    }
    for name, parser in parsers.items():
        raw_items = raw_snapshot.get(name)
        if not isinstance(raw_items, list):
            return None
        items = tuple(parser(item) for item in raw_items)
        if any(item is None for item in items):
            return None
        collections[name] = items
    scalar_types = {
        "page_url": str, "document_id": str, "performance_id": str,
        "group_id": str, "price_area_id": str, "product_id": str,
        "product_hidden_present": bool,
        "quantity_limit_present": bool, "quantity_limit_raw": str,
        "visual_selected_count": int,
    }
    if any(
        type(raw_snapshot.get(name)) is not expected
        for name, expected in scalar_types.items()
    ):
        return None
    if raw_snapshot["visual_selected_count"] < 0:
        return None
    return SeatSnapshot(ok=True, **collections, **{
        name: raw_snapshot[name] for name in scalar_types
    })


def parse_dialog_snapshot(raw_snapshot):
    """固定一份無副作用的可見送出對話框快照。"""
    if isinstance(raw_snapshot, DialogSnapshot):
        return raw_snapshot
    if not isinstance(raw_snapshot, dict):
        return None
    visible_count = raw_snapshot.get("visible_count")
    if (
        not isinstance(visible_count, int)
        or isinstance(visible_count, bool)
        or visible_count < 0
    ):
        return None
    page_url = raw_snapshot.get("page_url")
    document_id = raw_snapshot.get("document_id")
    text = raw_snapshot.get("text")
    if not all(isinstance(value, str) for value in (
        page_url, document_id, text
    )):
        return None
    if not page_url or not document_id:
        return None
    if visible_count == 0 and text:
        return None
    return DialogSnapshot(
        page_url=page_url,
        document_id=document_id,
        visible_count=visible_count,
        text=text,
    )


def parse_keyword_groups(keyword_string):
    """解析設定中的關鍵字格式，並相容字串或分號分組輸入。"""
    raw = (keyword_string or "").strip()
    if not raw:
        return []
    parsed = util.parse_keyword_string_to_array(raw)
    if parsed:
        return parsed
    delimiter = ";" if ";" in raw else ","
    return [
        item.strip().strip('"').strip("'")
        for item in raw.split(delimiter)
        if item.strip()
    ]


def matches_keyword_group(text, keyword_group):
    """判斷文字是否符合一組必要與排除關鍵字。"""

    formatted_text = util.format_keyword_string(text or "")
    if isinstance(keyword_group, (list, tuple)):
        parts = [str(item).strip() for item in keyword_group if str(item).strip()]
    else:
        parts = [part for part in str(keyword_group or "").split(" ") if part]
    return bool(parts) and all(
        util.format_keyword_string(part) in formatted_text for part in parts
    )


def choose_candidate_index(candidates, keyword_string, mode, allow_fallback=False):
    """依關鍵字優先序選出原始候選項目索引。"""
    if not candidates:
        return None
    indexed = list(enumerate(candidates))
    keyword_groups = parse_keyword_groups(keyword_string)
    if keyword_groups:
        for keyword_group in keyword_groups:
            matched = [
                item for item in indexed
                if matches_keyword_group(item[1].get("text", ""), keyword_group)
            ]
            if matched:
                target = util.get_target_item_from_matched_list(matched, mode)
                return target[0] if target else None
        if not allow_fallback:
            return None
    target = util.get_target_item_from_matched_list(indexed, mode)
    return target[0] if target else None


def _parse_date_target_url(target_value, current_url):
    """從場次目標解析並驗證導覽網址。"""

    if classify_utiki_page(current_url) != PageKind.DATE:
        return None
    profile = get_utiki_tenant_profile(current_url)
    current_query = unique_query(current_url)
    if profile is None or current_query is None:
        return None
    target_url = urljoin(current_url, str(target_value or ""))
    try:
        current = urlsplit(current_url)
        target = urlsplit(target_url)
    except ValueError:
        return None
    if (
        target.scheme != "https"
        or target.fragment
        or not same_origin(current, target)
        or classify_utiki_page(target_url) != PageKind.AREA
        or target.path != f"{profile.tenant_path}/UTK0204_"
    ):
        return None
    target_query = unique_query(target_url)
    if target_query is None or set(target_query) != {"PERFORMANCE_ID", "PRODUCT_ID"}:
        return None
    performance_id = target_query["PERFORMANCE_ID"]
    product_id = target_query["PRODUCT_ID"]
    token = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token.fullmatch(performance_id)
        or not token.fullmatch(product_id)
        or product_id != current_query.get("PRODUCT_ID")
    ):
        return None
    return {
        "target_url": urlunsplit(
            (target.scheme, target.netloc, target.path, target.query, "")
        ),
        "performance_id": performance_id,
        "product_id": product_id,
        "tenant_path": profile.tenant_path,
        "host": profile.host,
        "map_namespace": profile.map_namespace,
        "fast_area_prefetch": profile.fast_area_prefetch,
    }


def _parse_date_target(onclick, current_url):
    """從場次按鈕事件解析一般導覽目標。"""

    match = re.fullmatch(
        r"\s*location\.href\s*=\s*(?P<quote>['\"])(?P<url>[^'\"]+)"
        r"(?P=quote)\s*;?\s*",
        str(onclick or ""),
    )
    return _parse_date_target_url(match.group("url"), current_url) if match else None


def _parse_vip_date_token(onclick, current_url):
    """從場次按鈕事件解析 VIP 導覽識別。"""

    if classify_utiki_page(current_url) != PageKind.DATE:
        return None
    current_query = unique_query(current_url)
    product_id = (current_query or {}).get("PRODUCT_ID", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", product_id):
        return None
    match = re.fullmatch(
        r"\s*VipSellCheck\(\s*(?P<quote>['\"])"
        r"(?P<token>[A-Za-z0-9_-]{1,128})(?P=quote)\s*\)\s*;?\s*",
        str(onclick or ""),
    )
    return match.group("token") if match else None


def _safe_date_button_handler(onclick):
    """驗證場次按鈕事件是否屬於允許的安全格式。"""

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


def _classify_date_control(fingerprint, current_url):
    """依控制項指紋與目前網址判別場次操作類型。"""

    control = dict(fingerprint)
    tag_name = control.get("tag_name", "").upper()
    onclick = control.get("onclick", "")
    if tag_name == "A":
        if onclick or any(control.get(key, "") for key in ("target", "download", "ping")):
            return None
        target = _parse_date_target_url(
            control.get("resolved_href") or control.get("href"), current_url
        )
        return ("anchor_native", target, "") if target else None

    target = _parse_date_target(onclick, current_url)
    if target:
        return "normal", target, ""
    vip_token = _parse_vip_date_token(onclick, current_url)
    if vip_token:
        return "vip_native", None, vip_token
    control_type = control.get("type", "").lower()
    if (
        tag_name != "BUTTON"
        or control.get("formaction", "")
        or control_type not in ("", "button")
        or (not control_type and control.get("has_form"))
        or not _safe_date_button_handler(onclick)
    ):
        return None
    return "structural_native", None, ""


def _date_candidates(snapshot, config_dict):
    """從場次快照建立符合設定的候選清單。"""

    candidates = []
    for row_index, row in enumerate(snapshot.rows):
        actionable = [
            (index, control.fingerprint)
            for index, control in enumerate(row.controls)
            if control.actionable
        ]
        if len(actionable) != 1 or not row.text:
            continue
        if util.reset_row_text_if_match_keyword_exclude(config_dict, row.text):
            continue
        control_index, fingerprint = actionable[0]
        action = _classify_date_control(fingerprint, snapshot.page_url)
        if action:
            candidates.append({
                "text": row.text,
                "row_index": row_index,
                "control_index": control_index,
                "fingerprint": fingerprint,
                "action": action,
            })
    return candidates


def select_date_snapshot(snapshot, config_dict):
    """從不可變場次快照選出一個安全操作。"""
    snapshot = parse_date_snapshot(snapshot)
    if (
        not isinstance(snapshot, DateSnapshot)
        or not snapshot.ok
        or not snapshot.page_url
        or not snapshot.document_id
        or classify_utiki_page(snapshot.page_url) != PageKind.DATE
        or not 0 < len(snapshot.rows) <= 200
    ):
        return None
    candidates = _date_candidates(snapshot, config_dict)
    date_config = config_dict.get("date_auto_select", {})
    target_index = choose_candidate_index(
        candidates,
        date_config.get("date_keyword", "").strip(),
        date_config.get("mode", "from top to bottom"),
        config_dict.get("date_auto_fallback", False),
    )
    if target_index is None:
        return None
    target = candidates[target_index]
    action_kind, date_target, action_token = target["action"]
    date_target = date_target or {}
    return DateActionPlan(
        row_index=target["row_index"],
        control_index=target["control_index"],
        text=target["text"],
        action_kind=action_kind,
        onclick=dict(target["fingerprint"]).get("onclick", ""),
        control_fingerprint=target["fingerprint"],
        target_url=date_target.get("target_url", ""),
        performance_id=date_target.get("performance_id", ""),
        product_id=date_target.get("product_id", ""),
        tenant_path=date_target.get("tenant_path", ""),
        host=date_target.get("host", ""),
        map_namespace=date_target.get("map_namespace", ""),
        fast_area_prefetch=bool(date_target.get("fast_area_prefetch", False)),
        action_token=action_token,
    )


def date_plan_matches_snapshot(snapshot, plan):
    """驗證不可變場次計畫確實由指定快照產生。"""
    if not isinstance(snapshot, DateSnapshot) or not isinstance(
        plan, DateActionPlan
    ):
        return False
    if (
        not snapshot.ok or not snapshot.page_url or not snapshot.document_id
        or classify_utiki_page(snapshot.page_url) != PageKind.DATE
        or type(plan.row_index) is not int or type(plan.control_index) is not int
        or type(plan.fast_area_prefetch) is not bool
        or not 0 <= plan.row_index < len(snapshot.rows)
    ):
        return False
    row = snapshot.rows[plan.row_index]
    if not 0 <= plan.control_index < len(row.controls):
        return False
    control = row.controls[plan.control_index]
    fingerprint = control.fingerprint
    if (
        not control.actionable or plan.text != row.text
        or plan.control_fingerprint != fingerprint
        or tuple(key for key, _value in fingerprint)
        != DATE_CONTROL_FINGERPRINT_FIELDS
        or plan.onclick != dict(fingerprint)["onclick"]
    ):
        return False
    action = _classify_date_control(fingerprint, snapshot.page_url)
    if not action:
        return False
    action_kind, target, action_token = action
    target = target or {}
    return (
        plan.action_kind == action_kind
        and plan.action_token == action_token
        and (
            plan.target_url, plan.performance_id, plan.product_id,
            plan.tenant_path, plan.host, plan.map_namespace,
            plan.fast_area_prefetch,
        ) == (
            target.get("target_url", ""), target.get("performance_id", ""),
            target.get("product_id", ""), target.get("tenant_path", ""),
            target.get("host", ""), target.get("map_namespace", ""),
            bool(target.get("fast_area_prefetch", False)),
        )
    )


def availability_has_tickets(status_text, ticket_number=1):
    """判斷區域庫存文字是否足以供應要求票數。"""

    requested = parse_nonnegative_count(ticket_number)
    if not isinstance(requested, PresentField) or requested.value == 0:
        return False
    if type(status_text) is not str:
        return False
    text = re.sub(r"\s+", "", status_text or "")
    if not text or "售完" in text or text in {"0", "0張", "無"}:
        return False
    if text == "熱賣中":
        return True
    match = re.fullmatch(r"([0-9]+)(?:張)?", text)
    if match is None:
        match = re.fullmatch(r"(?:剩餘|尚餘|剩|餘)[:：]?([0-9]+)(?:張)?", text)
    if match is None:
        return False
    available = parse_nonnegative_count(match.group(1))
    return isinstance(available, PresentField) and available.value >= requested.value


def parse_area_send_action(href, expected_performance_id):
    """解析區域連結中的一般送出操作。"""

    match = re.fullmatch(
        r"\s*javascript:\s*Send\(\s*"
        r"(?P<q1>['\"])(?P<route>0202|0205)(?P=q1)\s*,\s*"
        r"(?P<q2>['\"])(?P<performance>[A-Za-z0-9_-]+)(?P=q2)\s*,\s*"
        r"(?P<q3>['\"])(?P<price_area>[A-Za-z0-9_-]+)(?P=q3)\s*,\s*"
        r"(?P<q4>['\"])(?P<group>[A-Za-z0-9_-]*)(?P=q4)\s*,\s*"
        r"(?P<q5>['\"])(?P<status>[^'\"]*)(?P=q5)\s*\)\s*;?\s*",
        str(href or ""),
    )
    if not match or match.group("performance") != expected_performance_id:
        return None
    if match.group("route") == "0205" and not match.group("group"):
        return None
    return {
        "action_name": "Send",
        "route": match.group("route"),
        "performance_id": match.group("performance"),
        "price_area_id": match.group("price_area"),
        "group_id": match.group("group"),
        "stable_args": [
            match.group("route"), match.group("performance"),
            match.group("price_area"), match.group("group"),
        ],
        "status": match.group("status"),
    }


def parse_area_send_a_action(href, expected_performance_id):
    """解析區域連結中的替代送出操作。"""

    match = re.fullmatch(
        r"\s*javascript:\s*SendA\(\s*"
        r"(?P<q1>['\"])(?P<route>0202|0205)(?P=q1)\s*,\s*"
        r"(?P<q2>['\"])(?P<performance>[A-Za-z0-9_-]+)(?P=q2)\s*,\s*"
        r"(?P<q3>['\"])(?P<group>[A-Za-z0-9_-]*)(?P=q3)\s*,\s*"
        r"(?P<q4>['\"])(?P<price_area>[A-Za-z0-9_-]+)(?P=q4)\s*,\s*"
        r"(?P<q5>['\"])(?P<area_group>[A-Za-z0-9_-]*)(?P=q5)\s*,\s*"
        r"(?P<q6>['\"])(?P<area_id>[A-Za-z0-9_-]*)(?P=q6)\s*,\s*"
        r"(?P<q7>['\"])(?P<status>[^'\"]*)(?P=q7)\s*\)\s*;?\s*",
        str(href or ""),
    )
    if not match or match.group("performance") != expected_performance_id:
        return None
    if match.group("route") == "0205" and not match.group("group"):
        return None
    return {
        "action_name": "SendA",
        "route": match.group("route"),
        "performance_id": match.group("performance"),
        "price_area_id": match.group("price_area"),
        "group_id": match.group("group"),
        "stable_args": [
            match.group("route"), match.group("performance"),
            match.group("group"), match.group("price_area"),
            match.group("area_group"), match.group("area_id"),
        ],
        "status": match.group("status"),
    }


def parse_map_title(title):
    """解析座位區圖標題中的區域與票價資訊。"""

    match = re.fullmatch(
        r"\s*票區\s*[:：]\s*(?P<area>.*?)\s+"
        r"票價\s*[:：]\s*(?P<price>.*?)\s+"
        r"尚餘\s*[:：]\s*(?P<status>.*?)\s*",
        str(title or ""),
    )
    if not match:
        return None
    return {
        "area": re.sub(r"\s+", "", match.group("area")),
        "price": re.sub(r"[\s,$]", "", match.group("price")),
        "status": re.sub(r"\s+", "", match.group("status")),
    }


def map_title_matches_available_row(map_title, area_text, price_text, ticket_number):
    """判斷區域圖標題是否對應有足夠庫存的區域列。"""

    return bool(
        map_title
        and map_title["area"] == re.sub(r"\s+", "", area_text)
        and map_title["price"] == re.sub(r"[\s,$]", "", price_text)
        and availability_has_tickets(map_title["status"], ticket_number)
    )


def _index_map_areas(areas):
    """依區域圖識別建立可查詢的區域索引。"""

    if not isinstance(areas, tuple) or not 0 < len(areas) <= 500:
        return None
    indexed = {}
    for area in areas:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", area.area_id) or not area.href:
            return None
        indexed.setdefault(area.area_id, []).append({"href": area.href, "title": area.title})
    return indexed


def _area_map_options(indexed_areas, rel_tokens, area_text, price_text,
                      expected_performance_id, ticket_number, require_direct):
    """建立一筆區域列可用的地圖目的地選項。"""

    options = []
    for rel in rel_tokens:
        for map_area in indexed_areas.get(rel, []):
            map_title = parse_map_title(map_area["title"])
            if not map_title_matches_available_row(
                map_title, area_text, price_text, ticket_number
            ):
                continue
            parsed = parse_area_send_action(
                map_area["href"], expected_performance_id
            )
            if parsed and availability_has_tickets(parsed["status"], ticket_number):
                options.append((rel, parsed, parsed))
                continue
            if not require_direct:
                parsed = parse_area_send_a_action(
                    map_area["href"], expected_performance_id
                )
                if parsed and availability_has_tickets(
                    parsed["status"], ticket_number
                ):
                    options.append((rel, parsed, None))
    return options


def _area_candidate(row, indexed_areas, config_dict, expected_performance_id,
                    ticket_number, require_direct):
    """依設定與庫存將區域列轉成候選項目。"""

    area_text = re.sub(r"\s+", " ", row.area).strip()
    price_text = re.sub(r"\s+", " ", row.price).strip()
    status_text = re.sub(r"\s+", " ", row.status).strip()
    rel_tokens = row.rel.split()
    if not area_text or not rel_tokens or not availability_has_tickets(
        status_text, ticket_number
    ):
        return None
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in rel_tokens):
        return None
    match_text = f"{area_text} {price_text}".strip()
    if util.reset_row_text_if_match_keyword_exclude(config_dict, match_text):
        return None
    options = _area_map_options(
        indexed_areas, rel_tokens, area_text, price_text,
        expected_performance_id, ticket_number, require_direct,
    )
    if not options:
        return None
    if require_direct:
        identities = {
            (item[2]["route"], item[2]["performance_id"],
             item[2]["price_area_id"], item[2]["group_id"])
            for item in options
        }
        if len(identities) != 1:
            return None
    rel, action, destination = options[0]
    return {
        "text": match_text, "area": area_text, "price": price_text,
        "status": status_text, "rel": rel, "map_action": action,
        "destination": destination,
    }


def select_area_snapshot(snapshot, config_dict, expected_performance_id,
                         require_direct_destination=True):
    """從批次 DOM 與地圖快照選出一個區域。"""
    snapshot = parse_area_snapshot(snapshot)
    if not isinstance(snapshot, AreaSnapshot) or not snapshot.ok:
        return None
    if not 0 < len(snapshot.rows) <= 200:
        return None
    indexed_areas = _index_map_areas(snapshot.areas)
    if indexed_areas is None:
        return None
    requested = parse_nonnegative_count(config_dict.get("ticket_number", 1))
    if not isinstance(requested, PresentField) or requested.value == 0:
        return None
    ticket_number = requested.value
    candidates = []
    for row in snapshot.rows:
        candidate = _area_candidate(
            row, indexed_areas, config_dict, expected_performance_id,
            ticket_number, require_direct_destination,
        )
        if candidate is False:
            return None
        if candidate:
            candidates.append(candidate)
    area_config = config_dict.get("area_auto_select", {})
    target_index = choose_candidate_index(
        candidates,
        area_config.get("area_keyword", "").strip(),
        area_config.get("mode", "from top to bottom"),
        config_dict.get("area_auto_fallback", False),
    )
    if target_index is None:
        return None
    target = candidates[target_index]
    action = target["map_action"]
    return AreaActionPlan(
        text=target["text"],
        area=target["area"],
        price=target["price"],
        status=target["status"],
        rel=target["rel"],
        action_name=action["action_name"],
        route=action["route"],
        performance_id=action["performance_id"],
        price_area_id=action["price_area_id"],
        group_id=action["group_id"],
        stable_args=tuple(action["stable_args"]),
        action_status=action["status"],
        direct_destination=target["destination"] is not None,
    )


def seats_are_physically_adjacent(first, second):
    """判斷 uTiki 水平或旋轉垂直座位圖中的兩席是否相鄰。"""
    geometry = ("center_x", "center_y", "width", "height")
    if all(
        isinstance(seat.get(field), (int, float))
        for seat in (first, second) for field in geometry
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


def _seat_number(seat):
    """解析座位資料中的數字座號。"""

    match = re.search(r"-(\d+)排-(\d+)號\s*$", str(seat.get("title") or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _seat_row_edges(seats, strict_titles):
    """找出各排座位在目前清單中的邊界。"""

    edges = {}
    for seat in seats:
        numbers = _seat_number(seat)
        if numbers is None:
            if strict_titles and str(seat.get("title") or "").strip():
                return None
            continue
        row_number, seat_number = numbers
        row_edges = edges.setdefault(row_number, [seat_number, seat_number])
        row_edges[0] = min(row_edges[0], seat_number)
        row_edges[1] = max(row_edges[1], seat_number)
    return edges


def _seat_block_candidates(available, needed, row_edges):
    """建立符合張數需求的連續座位區塊候選。"""

    rows = {}
    for seat in available:
        numbers = _seat_number(seat)
        if numbers is None:
            continue
        parsed = dict(seat, _row_number=numbers[0], _seat_number=numbers[1])
        rows.setdefault(numbers[0], []).append(parsed)
    candidates = []
    for row_number, row in rows.items():
        ordered = sorted(row, key=lambda item: item["_seat_number"])
        for start in range(len(ordered) - needed + 1):
            block = ordered[start:start + needed]
            if not all(
                seats_are_physically_adjacent(block[index], block[index + 1])
                for index in range(len(block) - 1)
            ):
                continue
            low = min(item["_seat_number"] for item in block)
            high = max(item["_seat_number"] for item in block)
            candidates.append({
                "seats": block, "row_number": row_number,
                "min_seat_number": low, "max_seat_number": high,
                "dom_index": min(item.get("index", 0) for item in block),
                "touches_aisle": low == row_edges[row_number][0]
                or high == row_edges[row_number][1],
            })
    return candidates


def _sort_seat_blocks(candidates, seat_sorting):
    """依座位排序設定排列連續座位區塊。"""

    if seat_sorting == "row_asc_aisle":
        candidates = [item for item in candidates if item["touches_aisle"]]
        key = lambda item: (item["row_number"], item["dom_index"])
    elif seat_sorting == "display":
        key = lambda item: item["dom_index"]
    elif seat_sorting == "row_asc_seat_desc":
        key = lambda item: (
            item["row_number"], -item["max_seat_number"], item["dom_index"]
        )
    elif seat_sorting == "row_desc_seat_asc":
        key = lambda item: (
            -item["row_number"], item["min_seat_number"], item["dom_index"]
        )
    else:
        key = lambda item: (
            item["row_number"], item["min_seat_number"], item["dom_index"]
        )
    return sorted(candidates, key=key)


def _numeric_seat_key(seat, seat_sorting):
    """建立數字座號的排序鍵。"""

    numbers = _seat_number(seat)
    if numbers is None:
        return (1, 0, 0, seat.get("row_index", 0), seat.get("cell_index", 0))
    row_number, seat_number = numbers
    if seat_sorting == "row_asc_seat_desc":
        seat_number = -seat_number
    elif seat_sorting == "row_desc_seat_asc":
        row_number = -row_number
    return (
        0, row_number, seat_number,
        seat.get("row_index", 0), seat.get("cell_index", 0),
    )


def select_seat_indexes(seats, needed, allow_non_adjacent=False,
                        seat_sorting="row_seat"):
    """保留既有優先規則後選出劃位座位索引。"""
    if needed <= 0:
        return []
    numeric_sortings = {
        "row_seat", "row_asc_aisle", "row_asc_seat_desc",
        "row_desc_seat_asc",
    }
    if seat_sorting not in numeric_sortings | {"display"}:
        seat_sorting = "row_seat"
    row_edges = _seat_row_edges(seats, seat_sorting == "row_asc_aisle")
    if row_edges is None:
        return []
    available = [
        seat for seat in seats
        if seat.get("available") and not seat.get("selected")
    ]
    if len(available) < needed:
        return []
    blocks = _sort_seat_blocks(
        _seat_block_candidates(available, needed, row_edges), seat_sorting
    )
    if blocks:
        target = sorted(blocks[0]["seats"], key=lambda item: item.get("index", 0))
        return [seat["index"] for seat in target]
    if seat_sorting == "row_asc_aisle" or not allow_non_adjacent:
        return []
    if seat_sorting in numeric_sortings:
        available = sorted(
            available, key=lambda seat: _numeric_seat_key(seat, seat_sorting)
        )
    return [seat["index"] for seat in available[:needed]]


def _seat_snapshot_route_is_valid(snapshot):
    """驗證座位快照所屬的頁面路由。"""

    query = unique_query(snapshot.page_url)
    token = re.compile(r"[A-Za-z0-9_-]+")
    identities = {
        "PERFORMANCE_ID": snapshot.performance_id,
        "GROUP_ID": snapshot.group_id,
        "PERFORMANCE_PRICE_AREA_ID": snapshot.price_area_id,
    }
    return bool(
        snapshot.ok and snapshot.document_id and query is not None
        and classify_utiki_page(snapshot.page_url) == PageKind.RESERVED_SEAT
        and all(token.fullmatch(value) for value in identities.values())
        and all(query.get(key) == value for key, value in identities.items())
        and snapshot.product_hidden_present
        and token.fullmatch(snapshot.product_id)
    )


def _counter_value(raw_value):
    """將票種計數值正規化為整數。"""

    if not re.fullmatch(r"\d+", str(raw_value or "")):
        return None
    return int(raw_value)


def _seat_snapshot_structure_is_valid(snapshot):
    """驗證座位快照必要欄位與結構。"""

    if not _seat_snapshot_route_is_valid(snapshot):
        return False
    if not snapshot.quantity_limit_present or _counter_value(
        snapshot.quantity_limit_raw
    ) is None:
        return False
    if not 0 < len(snapshot.ticket_types) <= 100 or len(snapshot.seats) > 5000:
        return False
    seat_fingerprints = [seat[0] for seat in snapshot.seats]
    counter_ids = [counter[0] for counter in snapshot.counters]
    if len(set(seat_fingerprints)) != len(seat_fingerprints):
        return False
    if any(not item for item in counter_ids) or len(set(counter_ids)) != len(counter_ids):
        return False
    if any(_counter_value(raw) is None for _type_id, raw in snapshot.counters):
        return False
    selected = [seat for seat in snapshot.seats if seat[9]]
    return (
        all(not (seat[8] and seat[9]) for seat in snapshot.seats)
        and len(selected) == snapshot.visual_selected_count
        and len(snapshot.submitted_seats) <= len(snapshot.seats)
    )


def _target_seat_ticket_type(snapshot, keyword):
    """依關鍵字從座位快照選出目標票種。"""

    candidates = []
    seen_fingerprints = set()
    seen_type_ids = set()
    counter_map = dict(snapshot.counters)
    for ticket_type in snapshot.ticket_types:
        fingerprint, text, disabled, type_id, type_name, present, raw = ticket_type
        if fingerprint in seen_fingerprints:
            return None, "invalid-snapshot"
        seen_fingerprints.add(fingerprint)
        if disabled:
            continue
        if (
            not text or not type_id or not type_name or not present
            or type_id in seen_type_ids or _counter_value(raw) is None
            or counter_map.get(type_id) != raw
        ):
            return None, "invalid-snapshot"
        seen_type_ids.add(type_id)
        candidates.append({"text": text, "ticket_type": ticket_type})
    target_index = choose_candidate_index(
        candidates, keyword, "from top to bottom", allow_fallback=False
    )
    if target_index is None:
        return None, "ticket-type-unavailable"
    return candidates[target_index]["ticket_type"], ""


def seat_selection_is_valid(snapshot, ticket_number, ticket_type_id,
                            ticket_type_name):
    """稽核畫面座位、送出資料與所有票種計數。"""
    snapshot = parse_seat_snapshot(snapshot)
    try:
        requested = int(ticket_number)
    except (TypeError, ValueError):
        return False
    if not isinstance(snapshot, SeatSnapshot) or not snapshot.ok or requested <= 0:
        return False
    selected = [seat for seat in snapshot.seats if seat[9]]
    if len(selected) != requested or snapshot.visual_selected_count != requested:
        return False
    if len(snapshot.submitted_seats) != requested or any(
        payload != ("S", ticket_type_id, ticket_type_name)
        for payload in snapshot.submitted_seats
    ):
        return False
    counts = [(type_id, _counter_value(raw)) for type_id, raw in snapshot.counters]
    if any(value is None for _type_id, value in counts):
        return False
    targets = [value for type_id, value in counts if type_id == ticket_type_id]
    return (
        len(targets) == 1 and targets[0] == requested
        and sum(value for _type_id, value in counts) == requested
    )


def _seat_request(snapshot, ticket_number):
    """建立座位選擇所需的張數與目前狀態。"""

    try:
        requested = int(ticket_number)
    except (TypeError, ValueError):
        return None
    limit = _counter_value(snapshot.quantity_limit_raw)
    if requested <= 0 or limit is None or (limit and requested > limit):
        return None
    return requested


def _seat_dicts(snapshot):
    """將座位快照項目整理成可供決策的字典清單。"""

    return [
        {
            "index": index, "title": seat[1], "row_index": seat[2],
            "cell_index": seat[3], "center_x": seat[4], "center_y": seat[5],
            "width": seat[6], "height": seat[7], "available": seat[8],
            "selected": seat[9],
        }
        for index, seat in enumerate(snapshot.seats)
    ]


def _seat_targets_are_adjacent(snapshot, fingerprints):
    """判斷目標座位指紋是否形成連續座位。"""

    by_fingerprint = dict(zip(
        (seat[0] for seat in snapshot.seats), _seat_dicts(snapshot)
    ))
    targets = [by_fingerprint.get(fingerprint) for fingerprint in fingerprints]
    return bool(all(targets)) and all(
        seats_are_physically_adjacent(targets[index], targets[index + 1])
        for index in range(len(targets) - 1)
    )


def _make_seat_plan(snapshot, ticket_type, action, requested,
                    selected=(), targets=(), require_adjacency=False):
    """建立包含身分與相鄰限制的座位操作計畫。"""

    return SeatActionPlan(
        page_url=snapshot.page_url,
        document_id=snapshot.document_id,
        performance_id=snapshot.performance_id,
        group_id=snapshot.group_id,
        price_area_id=snapshot.price_area_id,
        product_id=snapshot.product_id,
        action=action,
        ticket_number=requested,
        ticket_type_fingerprint=ticket_type[0],
        ticket_type_id=ticket_type[3],
        ticket_type_name=ticket_type[4],
        selected_seat_fingerprints=tuple(selected),
        target_seat_fingerprints=tuple(targets),
        require_physical_adjacency=require_adjacency,
    )


def _seat_state_is_clean(snapshot):
    """判斷座位頁是否尚未殘留任何選擇。"""

    return (
        snapshot.visual_selected_count == 0
        and not snapshot.submitted_seats
        and not any(seat[9] for seat in snapshot.seats)
        and all(_counter_value(raw) == 0 for _type_id, raw in snapshot.counters)
    )


def select_seat_snapshot(snapshot, keyword, ticket_number,
                         allow_non_adjacent=False, seat_sorting="row_seat"):
    """從劃位快照規劃清除、選擇或就緒操作。"""
    snapshot = parse_seat_snapshot(snapshot)
    if not isinstance(snapshot, SeatSnapshot) or not _seat_snapshot_structure_is_valid(snapshot):
        return None, "invalid-snapshot"
    requested = _seat_request(snapshot, ticket_number)
    if requested is None:
        return None, "seats-unavailable"
    ticket_type, reason = _target_seat_ticket_type(snapshot, keyword)
    if ticket_type is None:
        return None, reason
    selected = tuple(seat[0] for seat in snapshot.seats if seat[9])
    require_adjacency = (
        not allow_non_adjacent or seat_sorting == "row_asc_aisle"
    )
    if seat_selection_is_valid(
        snapshot, requested, ticket_type[3], ticket_type[4]
    ):
        if require_adjacency and not _seat_targets_are_adjacent(
            snapshot, selected
        ):
            return _make_seat_plan(
                snapshot, ticket_type, "clear", requested, selected=selected,
                require_adjacency=True,
            ), ""
        return _make_seat_plan(
            snapshot, ticket_type, "ready", requested, selected=selected,
            require_adjacency=require_adjacency,
        ), ""
    if selected:
        return _make_seat_plan(
            snapshot, ticket_type, "clear", requested, selected=selected
        ), ""
    if not _seat_state_is_clean(snapshot):
        return None, "invalid-snapshot"
    seats = _seat_dicts(snapshot)
    indexes = select_seat_indexes(
        seats, requested, allow_non_adjacent, seat_sorting
    )
    if len(indexes) != requested:
        return None, "seats-unavailable"
    targets = tuple(snapshot.seats[index][0] for index in indexes)
    return _make_seat_plan(
        snapshot, ticket_type, "select", requested, targets=targets,
        require_adjacency=require_adjacency,
    ), ""


def _seat_plan_identity_matches(snapshot, plan):
    """驗證座位計畫與快照的頁面身分一致。"""

    if (
        plan.page_url != snapshot.page_url
        or plan.document_id != snapshot.document_id
        or plan.performance_id != snapshot.performance_id
        or plan.group_id != snapshot.group_id
        or plan.price_area_id != snapshot.price_area_id
        or plan.product_id != snapshot.product_id
        or plan.action not in {"clear", "select", "ready"}
        or type(plan.require_physical_adjacency) is not bool
    ):
        return False
    matching_types = [
        item for item in snapshot.ticket_types
        if item[0] == plan.ticket_type_fingerprint
    ]
    counter_map = dict(snapshot.counters)
    return bool(
        len(matching_types) == 1
        and matching_types[0][3] == plan.ticket_type_id
        and matching_types[0][4] == plan.ticket_type_name
        and not matching_types[0][2]
        and matching_types[0][5]
        and _counter_value(matching_types[0][6]) is not None
        and counter_map.get(plan.ticket_type_id) == matching_types[0][6]
    )


def seat_plan_matches_snapshot(snapshot, plan):
    """在瀏覽器端原子驗證前檢查座位操作文法。"""
    snapshot = parse_seat_snapshot(snapshot)
    if (
        not isinstance(snapshot, SeatSnapshot)
        or not isinstance(plan, SeatActionPlan)
        or not _seat_snapshot_structure_is_valid(snapshot)
        or not _seat_plan_identity_matches(snapshot, plan)
        or _seat_request(snapshot, plan.ticket_number) is None
    ):
        return False
    selected = tuple(seat[0] for seat in snapshot.seats if seat[9])
    if plan.selected_seat_fingerprints != selected:
        return False
    valid = seat_selection_is_valid(
        snapshot, plan.ticket_number, plan.ticket_type_id, plan.ticket_type_name
    )
    if plan.action == "ready":
        if not valid or plan.target_seat_fingerprints:
            return False
        return (
            not plan.require_physical_adjacency
            or _seat_targets_are_adjacent(snapshot, selected)
        )
    if plan.action == "clear":
        if not selected or plan.target_seat_fingerprints:
            return False
        if plan.require_physical_adjacency:
            return valid and not _seat_targets_are_adjacent(snapshot, selected)
        return not valid
    if selected or not _seat_state_is_clean(snapshot):
        return False
    by_fingerprint = {seat[0]: seat for seat in snapshot.seats}
    targets = plan.target_seat_fingerprints
    targets_match = bool(
        len(targets) == plan.ticket_number == len(set(targets))
        and all(
            fingerprint in by_fingerprint
            and by_fingerprint[fingerprint][8]
            and not by_fingerprint[fingerprint][9]
            for fingerprint in targets
        )
    )
    if not targets_match or not plan.require_physical_adjacency:
        return targets_match
    return _seat_targets_are_adjacent(snapshot, targets)


def quantity_audit_is_valid(quantities, target_key, ticket_number):
    """稽核目標票種與各票數欄位是否符合要求。"""

    if not isinstance(quantities, list) or not target_key:
        return False
    target_matches = [
        item for item in quantities
        if isinstance(item, dict) and item.get("key") == target_key
    ]
    if len(target_matches) != 1:
        return False
    try:
        if int(target_matches[0].get("value", 0)) != int(ticket_number):
            return False
        return all(
            item is target_matches[0] or int(item.get("value", 0)) == 0
            for item in quantities if isinstance(item, dict)
        ) and all(isinstance(item, dict) for item in quantities)
    except (TypeError, ValueError):
        return False


def _parse_quantity_request(snapshot, ticket_number):
    """從票數快照解析要求張數與既有選擇。"""

    observed_request = parse_nonnegative_count(ticket_number)
    if not isinstance(observed_request, PresentField):
        return None
    requested = observed_request.value
    if requested <= 0 or not snapshot.page_url or not snapshot.document_id:
        return None
    token = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token.fullmatch(snapshot.performance_id)
        or not token.fullmatch(snapshot.price_area_id)
        or (snapshot.group_id and not token.fullmatch(snapshot.group_id))
    ):
        return None
    if snapshot.quantity_limit_present:
        observed_limit = parse_nonnegative_count(snapshot.quantity_limit_raw)
        if not isinstance(observed_limit, PresentField):
            return None
        quantity_limit = observed_limit.value
    else:
        if snapshot.quantity_limit_raw:
            return None
        quantity_limit = 0
    if quantity_limit and requested > quantity_limit:
        return None
    return requested, quantity_limit


def _quantity_candidates(snapshot, requested):
    """建立符合張數需求的票種候選清單。"""

    token = re.compile(r"[A-Za-z0-9_-]+")
    if not 0 < len(snapshot.rows) <= 100:
        return None
    keys = [row.key for row in snapshot.rows]
    if any(not token.fullmatch(key) for key in keys) or len(set(keys)) != len(keys):
        return None

    candidates = []
    for row in snapshot.rows:
        if row.disabled or "售完" in row.text or not row.name:
            continue
        remaining = row.remaining_observation
        if isinstance(remaining, InvalidField):
            continue
        if isinstance(remaining, PresentField) and remaining.value < requested:
            continue
        candidates.append({
            "text": row.name,
            "key": row.key,
            "remaining_present": row.remaining_present,
        })
    return candidates


def _quantity_page_type_matches(page_url, page_type):
    """判斷網址是否符合預期的票數頁面類型。"""

    if page_type == "general_admission":
        return classify_utiki_page(page_url) == PageKind.GENERAL_ADMISSION
    return page_type == "server_assigned_quantity"


def _quantity_snapshot_identity_is_valid(snapshot, page_type):
    """驗證票數快照的文件與商品身分。"""

    token = r"[A-Za-z0-9_-]+"
    if not _quantity_page_type_matches(snapshot.page_url, page_type):
        return False
    if snapshot.product_hidden_present and not re.fullmatch(
        token, snapshot.product_id
    ):
        return False
    if (
        bool(snapshot.product_id) != snapshot.product_hidden_present
        or (bool(snapshot.quantity_limit_raw) and not snapshot.quantity_limit_present)
    ):
        return False
    return bool(
        page_type != "server_assigned_quantity"
        or re.fullmatch(token, snapshot.product_id)
    )


def select_quantity_snapshot(snapshot, keyword, ticket_number,
                             mode="from top to bottom",
                             page_type="general_admission"):
    """從不可變票數快照選出仍可購買的票種列。"""
    snapshot = parse_quantity_snapshot(snapshot)
    if not isinstance(snapshot, QuantitySnapshot) or not snapshot.ok:
        return None
    if not _quantity_snapshot_identity_is_valid(snapshot, page_type):
        return None
    request = _parse_quantity_request(snapshot, ticket_number)
    if request is None:
        return None
    requested, quantity_limit = request
    candidates = _quantity_candidates(snapshot, requested)
    if candidates is None:
        return None
    target_index = choose_candidate_index(
        candidates, keyword, mode, allow_fallback=False
    )
    if target_index is None:
        return None
    target = candidates[target_index]
    return QuantityPlan(
        page_url=snapshot.page_url,
        document_id=snapshot.document_id,
        performance_id=snapshot.performance_id,
        group_id=snapshot.group_id,
        price_area_id=snapshot.price_area_id,
        quantity_limit=quantity_limit,
        target_key=target["key"],
        target_name=target["text"],
        ticket_number=requested,
        page_type=page_type,
        product_id=snapshot.product_id,
        performance_hidden_present=snapshot.performance_hidden_present,
        product_hidden_present=snapshot.product_hidden_present,
        group_hidden_present=snapshot.group_hidden_present,
        price_area_hidden_present=snapshot.price_area_hidden_present,
        quantity_limit_present=snapshot.quantity_limit_present,
        target_remaining_present=target["remaining_present"],
    )


def _seat_submit_payload(plan):
    """建立劃位計畫的最終送出資料。"""

    if not isinstance(plan, SeatActionPlan) or plan.action not in {
        "select", "ready"
    }:
        return None
    expected = (
        plan.target_seat_fingerprints
        if plan.action == "select"
        else plan.selected_seat_fingerprints
    )
    unused = (
        plan.selected_seat_fingerprints
        if plan.action == "select"
        else plan.target_seat_fingerprints
    )
    fingerprints = (plan.ticket_type_fingerprint, *expected)
    token = r"[A-Za-z0-9_-]+"
    if (
        classify_utiki_page(plan.page_url) != PageKind.RESERVED_SEAT
        or not isinstance(plan.document_id, str) or not plan.document_id
        or type(plan.ticket_number) is not int or plan.ticket_number <= 0
        or not isinstance(plan.selected_seat_fingerprints, tuple)
        or not isinstance(plan.target_seat_fingerprints, tuple) or unused
        or len(expected) != plan.ticket_number
        or any(not isinstance(item, tuple) or not item for item in fingerprints)
        or any(
            any(not isinstance(value, (str, int, float, bool)) for value in item)
            for item in fingerprints
        )
        or len(set(expected)) != len(expected)
        or any(
            not isinstance(value, str) or not re.fullmatch(token, value)
            for value in (
                plan.performance_id, plan.group_id,
                plan.price_area_id, plan.product_id,
            )
        )
        or not isinstance(plan.ticket_type_id, str) or not plan.ticket_type_id
        or not isinstance(plan.ticket_type_name, str) or not plan.ticket_type_name
        or type(plan.require_physical_adjacency) is not bool
    ):
        return None
    return (
        "seat", plan.ticket_number, plan.ticket_type_fingerprint,
        plan.ticket_type_id, plan.ticket_type_name, expected,
        plan.require_physical_adjacency,
    )


def _quantity_submit_payload(plan):
    """建立票數計畫的最終送出資料。"""

    if not isinstance(plan, QuantityPlan):
        return None
    page_kinds = {
        "general_admission": PageKind.GENERAL_ADMISSION,
        "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
    }
    page_kind = page_kinds.get(plan.page_type)
    token = r"[A-Za-z0-9_-]+"
    bool_fields = (
        plan.performance_hidden_present, plan.product_hidden_present,
        plan.group_hidden_present, plan.price_area_hidden_present,
        plan.quantity_limit_present, plan.target_remaining_present,
    )
    if (
        page_kind is None or classify_utiki_page(plan.page_url) != page_kind
        or not isinstance(plan.document_id, str) or not plan.document_id
        or type(plan.ticket_number) is not int or plan.ticket_number <= 0
        or type(plan.quantity_limit) is not int or plan.quantity_limit < 0
        or not isinstance(plan.target_name, str) or not plan.target_name
        or not isinstance(plan.performance_id, str)
        or not re.fullmatch(token, plan.performance_id)
        or not isinstance(plan.price_area_id, str)
        or not re.fullmatch(token, plan.price_area_id)
        or not isinstance(plan.target_key, str)
        or not re.fullmatch(token, plan.target_key)
        or not isinstance(plan.group_id, str)
        or (plan.group_id and not re.fullmatch(token, plan.group_id))
        or not isinstance(plan.product_id, str)
        or (plan.product_id and not re.fullmatch(token, plan.product_id))
        or bool(plan.product_id) != plan.product_hidden_present
        or (
            page_kind is PageKind.SERVER_ASSIGNED_QUANTITY
            and (not plan.group_id or not plan.product_id)
        )
        or any(type(value) is not bool for value in bool_fields)
    ):
        return None
    return (
        "quantity", plan.page_type, plan.target_key, plan.target_name,
        plan.ticket_number, plan.quantity_limit, bool_fields,
    )


def build_submit_plan(selection_plan):
    """從選擇計畫固定一份不含敏感資料的最終送出意圖。"""
    if isinstance(selection_plan, SeatActionPlan):
        page_kind = PageKind.RESERVED_SEAT
        payload = _seat_submit_payload(selection_plan)
    elif isinstance(selection_plan, QuantityPlan):
        page_kind = {
            "general_admission": PageKind.GENERAL_ADMISSION,
            "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
        }.get(selection_plan.page_type)
        payload = _quantity_submit_payload(selection_plan)
    else:
        return None
    if page_kind is None or payload is None:
        return None
    return SubmitPlan(
        page_url=selection_plan.page_url,
        document_id=selection_plan.document_id,
        page_kind=page_kind,
        payload_fingerprint=payload,
        selection_plan=selection_plan,
    )


def submit_plan_is_valid(plan):
    """重建純資料指紋以拒絕遭竄改的送出資訊。"""
    if not isinstance(plan, SubmitPlan):
        return False
    expected = build_submit_plan(plan.selection_plan)
    return bool(
        expected is not None
        and plan.page_url == expected.page_url
        and plan.document_id == expected.document_id
        and plan.page_kind is expected.page_kind
        and plan.payload_fingerprint == expected.payload_fingerprint
    )


def is_auth_error(dialog_text):
    """判斷對話框文字是否表示登入狀態失效。"""

    return (
        any(marker in (dialog_text or "") for marker in ("帳號", "密碼", "登入"))
        and any(
            marker in (dialog_text or "")
            for marker in ("錯誤", "有誤", "失敗", "不正確", "不存在", "無效")
        )
    )


def classify_submit_dialog(dialog_text):
    """依對話框文字判別送出結果與後續處理類型。"""

    text = str(dialog_text or "").strip()
    if not text:
        return None
    if is_auth_error(text):
        return "auth"
    if "驗證碼" in text and any(
        marker in text for marker in ("錯誤", "失敗", "不正確")
    ):
        return "captcha"
    if "請再重新選取座位" in text or (
        any(subject in text for subject in ("座位", "位置"))
        and any(
            marker in text
            for marker in ("已被選走", "已被選取", "已被訂走", "已被預訂")
        )
    ):
        return "seat_conflict"
    if (
        "限購" in text and any(marker in text for marker in ("超過", "超出", "已達", "上限"))
    ) or (
        "購買額度" in text
        and any(marker in text for marker in ("超過", "超出", "已達", "上限"))
    ):
        return "quota"
    if "成功" in text and "加入購物車" in text:
        return "success"
    return "rejected"
