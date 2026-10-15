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


DATE_CONTROL_FINGERPRINT_FIELDS = (
    "tag_name", "id", "href", "resolved_href", "role", "type",
    "target", "download", "ping", "formaction", "has_form", "onclick",
)


@dataclass(frozen=True)
class DateSnapshot:
    ok: bool
    page_url: str
    document_id: str
    rows: tuple
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
    rows: tuple
    areas: tuple
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
    rows: tuple
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
class DialogSnapshot:
    page_url: str
    document_id: str
    visible_count: int
    text: str


def _freeze_date_control(raw_control):
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
    return actionable, fingerprint


def parse_date_snapshot(raw_snapshot):
    """Freeze one side-effect-free observation of the date table."""
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
        frozen_rows.append((row["text"], frozen_controls))
    page_url = raw_snapshot.get("page_url")
    document_id = raw_snapshot.get("document_id")
    if not isinstance(page_url, str) or not isinstance(document_id, str):
        return None
    return DateSnapshot(True, page_url, document_id, tuple(frozen_rows))


def parse_area_snapshot(raw_snapshot):
    """Freeze an area reader result before it crosses into decision code."""
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
    return AreaSnapshot(
        ok=True,
        page_url=str(raw_snapshot.get("page_url") or ""),
        document_id=str(raw_snapshot.get("document_id") or ""),
        rows=tuple(
            (
                str(row.get("area") or ""),
                str(row.get("price") or ""),
                str(row.get("status") or ""),
                str(row.get("rel") or ""),
            )
            for row in rows
        ),
        areas=tuple(
            (
                str(area.get("id") or ""),
                str(area.get("href") or ""),
                str(area.get("title") or ""),
            )
            for area in areas
        ),
    )


def _quantity_presence_fields(raw_snapshot):
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
    """Freeze a general-admission quantity reader result."""
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
    return QuantitySnapshot(
        ok=True,
        page_url=str(raw_snapshot.get("page_url") or ""),
        document_id=str(raw_snapshot.get("document_id") or ""),
        performance_id=str(raw_snapshot.get("performance_id") or ""),
        group_id=str(raw_snapshot.get("group_id") or ""),
        price_area_id=str(raw_snapshot.get("price_area_id") or ""),
        quantity_limit_raw=str(raw_snapshot.get("quantity_limit_raw") or ""),
        rows=tuple(
            (
                str(row.get("key") or ""),
                str(row.get("name") or ""),
                str(row.get("text") or ""),
                bool(row.get("disabled")),
                bool(row.get("remaining_present")),
                str(row.get("remaining_raw") or ""),
                str(row.get("value") or ""),
            )
            for row in rows
        ),
        product_id=str(raw_snapshot.get("product_id") or ""),
        **_quantity_presence_fields(raw_snapshot),
    )


def parse_dialog_snapshot(raw_snapshot):
    """Freeze one side-effect-free observation of the visible submit dialog."""
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
    """Parse the settings keyword format while accepting raw/semicolon input."""
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
    formatted_text = util.format_keyword_string(text or "")
    if isinstance(keyword_group, (list, tuple)):
        parts = [str(item).strip() for item in keyword_group if str(item).strip()]
    else:
        parts = [part for part in str(keyword_group or "").split(" ") if part]
    return bool(parts) and all(
        util.format_keyword_string(part) in formatted_text for part in parts
    )


def choose_candidate_index(candidates, keyword_string, mode, allow_fallback=False):
    """Choose an original candidate index, preserving keyword priority."""
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
    match = re.fullmatch(
        r"\s*location\.href\s*=\s*(?P<quote>['\"])(?P<url>[^'\"]+)"
        r"(?P=quote)\s*;?\s*",
        str(onclick or ""),
    )
    return _parse_date_target_url(match.group("url"), current_url) if match else None


def _parse_vip_date_token(onclick, current_url):
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
    candidates = []
    for row_index, (row_text, controls) in enumerate(snapshot.rows):
        actionable = [
            (index, fingerprint)
            for index, (is_actionable, fingerprint) in enumerate(controls)
            if is_actionable
        ]
        if len(actionable) != 1 or not row_text:
            continue
        if util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
            continue
        control_index, fingerprint = actionable[0]
        action = _classify_date_control(fingerprint, snapshot.page_url)
        if action:
            candidates.append({
                "text": row_text,
                "row_index": row_index,
                "control_index": control_index,
                "fingerprint": fingerprint,
                "action": action,
            })
    return candidates


def select_date_snapshot(snapshot, config_dict):
    """Choose one safe date action from an immutable table snapshot."""
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
    """Verify that one immutable plan is derived from the supplied snapshot."""
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
    row_text, controls = snapshot.rows[plan.row_index]
    if not 0 <= plan.control_index < len(controls):
        return False
    actionable, fingerprint = controls[plan.control_index]
    if (
        not actionable or plan.text != row_text
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
    text = re.sub(r"\s+", "", status_text or "")
    if not text or "售完" in text or text in {"0", "0張", "無"}:
        return False
    if re.fullmatch(r"\d+", text):
        return int(text) >= int(ticket_number)
    if any(marker in text for marker in ("剩餘", "尚餘", "剩", "餘")):
        match = re.search(r"(\d+)", text)
        if match and int(match.group(1)) < int(ticket_number):
            return False
    return True


def parse_area_send_action(href, expected_performance_id):
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
    return bool(
        map_title
        and map_title["area"] == re.sub(r"\s+", "", area_text)
        and map_title["price"] == re.sub(r"[\s,$]", "", price_text)
        and availability_has_tickets(map_title["status"], ticket_number)
    )


def _index_map_areas(areas):
    if not isinstance(areas, tuple) or not 0 < len(areas) <= 500:
        return None
    indexed = {}
    for area_id, href, title in areas:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", area_id) or not href:
            return None
        indexed.setdefault(area_id, []).append({"href": href, "title": title})
    return indexed


def _area_map_options(indexed_areas, rel_tokens, area_text, price_text,
                      expected_performance_id, ticket_number, require_direct):
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
    area, price, status, rel = row
    area_text = re.sub(r"\s+", " ", area).strip()
    price_text = re.sub(r"\s+", " ", price).strip()
    status_text = re.sub(r"\s+", " ", status).strip()
    rel_tokens = rel.split()
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
    """Choose an area from one batched DOM/map snapshot."""
    snapshot = parse_area_snapshot(snapshot)
    if not isinstance(snapshot, AreaSnapshot) or not snapshot.ok:
        return None
    if not 0 < len(snapshot.rows) <= 200:
        return None
    indexed_areas = _index_map_areas(snapshot.areas)
    if indexed_areas is None:
        return None
    ticket_number = int(config_dict.get("ticket_number", 1))
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


def quantity_audit_is_valid(quantities, target_key, ticket_number):
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
    try:
        requested = int(ticket_number)
    except (TypeError, ValueError):
        return None
    if requested <= 0 or not snapshot.page_url or not snapshot.document_id:
        return None
    token = re.compile(r"[A-Za-z0-9_-]+")
    if (
        not token.fullmatch(snapshot.performance_id)
        or not token.fullmatch(snapshot.price_area_id)
        or (snapshot.group_id and not token.fullmatch(snapshot.group_id))
    ):
        return None
    if snapshot.quantity_limit_raw:
        if not re.fullmatch(r"\d+", snapshot.quantity_limit_raw):
            return None
        quantity_limit = int(snapshot.quantity_limit_raw)
    else:
        quantity_limit = 0
    if quantity_limit and requested > quantity_limit:
        return None
    return requested, quantity_limit


def _quantity_candidates(snapshot, requested):
    token = re.compile(r"[A-Za-z0-9_-]+")
    if not 0 < len(snapshot.rows) <= 100:
        return None
    keys = [row[0] for row in snapshot.rows]
    if any(not token.fullmatch(key) for key in keys) or len(set(keys)) != len(keys):
        return None

    candidates = []
    for key, name, text, disabled, remaining_present, remaining_raw, _value in (
        snapshot.rows
    ):
        if disabled or "售完" in text or not name:
            continue
        if remaining_present:
            if not re.fullmatch(r"\d+", remaining_raw):
                continue
            if int(remaining_raw) < requested:
                continue
        candidates.append({
            "text": name,
            "key": key,
            "remaining_present": remaining_present,
        })
    return candidates


def _quantity_page_type_matches(page_url, page_type):
    if page_type == "general_admission":
        return classify_utiki_page(page_url) == PageKind.GENERAL_ADMISSION
    return page_type == "server_assigned_quantity"


def _quantity_snapshot_identity_is_valid(snapshot, page_type):
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
    """Choose one live quantity row from an immutable page snapshot."""
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


def is_auth_error(dialog_text):
    return (
        any(marker in (dialog_text or "") for marker in ("帳號", "密碼", "登入"))
        and any(
            marker in (dialog_text or "")
            for marker in ("錯誤", "有誤", "失敗", "不正確", "不存在", "無效")
        )
    )


def classify_submit_dialog(dialog_text):
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
