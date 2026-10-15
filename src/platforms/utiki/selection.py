"""Pure selection and audit rules for the uTiki2 ticketing family."""

import re

import util


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
    if not isinstance(areas, list) or not 0 < len(areas) <= 500:
        return None
    indexed = {}
    for area in areas:
        if not isinstance(area, dict):
            return None
        area_id = str(area.get("id") or "")
        href = str(area.get("href") or "")
        title = str(area.get("title") or "")
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
    if not isinstance(row, dict):
        return False
    area_text = re.sub(r"\s+", " ", str(row.get("area") or "")).strip()
    price_text = re.sub(r"\s+", " ", str(row.get("price") or "")).strip()
    status_text = re.sub(r"\s+", " ", str(row.get("status") or "")).strip()
    rel_tokens = str(row.get("rel") or "").split()
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
    if not isinstance(snapshot, dict):
        return None
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or not 0 < len(rows) <= 200:
        return None
    indexed_areas = _index_map_areas(snapshot.get("areas"))
    if indexed_areas is None:
        return None
    ticket_number = int(config_dict.get("ticket_number", 1))
    candidates = []
    for row in rows:
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
    return candidates[target_index] if target_index is not None else None


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
