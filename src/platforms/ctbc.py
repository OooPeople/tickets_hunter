#encoding=utf-8
"""CTBC Sports ticketing platform support.

The CTBC site uses the same UTK naming convention as a few older ticketing
sites, but its URLs and DOM are different enough that sharing their page
router is unsafe.  Keep the selectors and state handling local to this module.
"""

import base64
import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from zendriver import cdp

import util
from nodriver_common import (
    check_and_handle_pause,
    nodriver_get_captcha_image_from_dom_snapshot,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
)


__all__ = [
    "is_utk_sports_url",
    "nodriver_ctbc_inject_cookies",
    "nodriver_ctbc_main",
]


CTBC_DOMAIN = "tix.ctbcsports.com"
CTBC_MAP_DOMAIN = "imgs2.utiki.com.tw"
UTK_SPORTS_PROFILES = {
    CTBC_DOMAIN: {
        "name": "CTBC Sports",
        "map_namespace": "CTBC_SPORTS",
        "fast_area_prefetch": True,
    },
    "tix.wdragons.com": {
        "name": "Wei Chuan Dragons",
        "map_namespace": "WDRAGONS",
        # WDragons' native Send() first calls GET_PRICE_AREA_NOTICE and may
        # display a confirmation.  Keep the optimized native map flow instead
        # of bypassing that server-side notice check.
        "fast_area_prefetch": False,
    },
}
CTBC_DATE_ROW_SELECTOR = "#PerformanceListTable table > tbody > tr"
CTBC_AREA_ROW_SELECTOR = ".seats table.f1 > tbody > tr.saleTr"
CTBC_FAST_PATH_TTL_SECONDS = 1.5
_state = {
    "submitted": {},
    "checkout_notified": set(),
    "ticket_type": {},
    "auth_failure": {},
    "fast_target": {},
    "tab_tokens": [],
}


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


def parse_ctbc_cookie_string(raw_value):
    """Return CTBC session cookie pairs from a Cookie header or JSON export."""
    raw = str(raw_value or "").strip()
    if not raw:
        return []

    cookie_pairs = []
    if raw.startswith("{") or raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("cookies"), list):
                    parsed = parsed["cookies"]
                else:
                    parsed = [{"name": name, "value": value} for name, value in parsed.items()]
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        cookie_pairs.append((str(item.get("name", "")).strip(), str(item.get("value", ""))))
        except (TypeError, ValueError, json.JSONDecodeError):
            cookie_pairs = []

    if not cookie_pairs:
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1].strip()
        for part in raw.replace("\r", ";").replace("\n", ";").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator:
                cookie_pairs.append((name.strip(), value.strip()))

    # ASP.NET antiforgery and analytics cookies are intentionally excluded.
    # The page generates a fresh antiforgery token after the injected session
    # is reloaded, while stale antiforgery cookies would make POSTs fail.
    result = []
    seen = set()
    for name, value in cookie_pairs:
        if not name.startswith("CTBC_") or "|" not in name or not value or name in seen:
            continue
        result.append((name, value))
        seen.add(name)
    return result


async def nodriver_ctbc_inject_cookies(tab, config_dict):
    """Inject a copied CTBC cookie set; account/password remains the fallback."""
    debug = util.create_debug_logger(config_dict)
    raw_value = config_dict.get("accounts", {}).get("ctbc_cookie", "")
    cookies = parse_ctbc_cookie_string(raw_value)
    if not cookies:
        if str(raw_value or "").strip():
            debug.log("[CTBC COOKIE] No valid CTBC session cookies were found")
        return False

    try:
        existing_cookies = await tab.browser.cookies.get_all()
        for cookie in existing_cookies:
            if cookie.name.startswith("CTBC_") and cookie.domain.lstrip(".").endswith("ctbcsports.com"):
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
                domain=CTBC_DOMAIN,
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
    try:
        return (urlsplit(str(url or "")).hostname or "").lower() in UTK_SPORTS_PROFILES
    except ValueError:
        return False


def is_utk_sports_url(url):
    return _is_ctbc_url(url)


def _get_utk_sports_profile(url):
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return None
    profile = UTK_SPORTS_PROFILES.get(host)
    return dict(profile, host=host) if profile else None


def _get_ctbc_page_type(url):
    lower_url = (url or "").lower()
    if not _is_ctbc_url(lower_url):
        return None
    if "/utk0201_" in lower_url and "product_id=" in lower_url:
        return "date"
    if "/utk0204_" in lower_url and "performance_id=" in lower_url:
        return "area"
    if "/utk0205_" in lower_url and "performance_id=" in lower_url:
        return "seat"
    if any(path in lower_url for path in ("/utk0202_", "/utk0203_")) and "performance_id=" in lower_url:
        return "general_admission"
    if "/utk0206_" in lower_url:
        return "checkout"
    return None


def _parse_keyword_groups(keyword_string):
    """Parse the settings keyword format while accepting raw/semicolon input."""
    raw = (keyword_string or "").strip()
    if not raw:
        return []

    parsed = util.parse_keyword_string_to_array(raw)
    if parsed:
        return parsed

    # Settings normally stores JSON fragments ("A","B"), but accepting raw
    # values makes direct config editing and older profiles less surprising.
    delimiter = ";" if ";" in raw else ","
    return [item.strip().strip('"').strip("'") for item in raw.split(delimiter) if item.strip()]


def _matches_keyword_group(text, keyword_group):
    formatted_text = util.format_keyword_string(text or "")
    if isinstance(keyword_group, (list, tuple)):
        parts = [str(item).strip() for item in keyword_group if str(item).strip()]
    else:
        parts = [part for part in str(keyword_group or "").split(" ") if part]
    return bool(parts) and all(util.format_keyword_string(part) in formatted_text for part in parts)


def _choose_candidate_index(candidates, keyword_string, mode, allow_fallback=False):
    """Choose an original candidate index, preserving keyword priority."""
    if not candidates:
        return None

    indexed = list(enumerate(candidates))
    keyword_groups = _parse_keyword_groups(keyword_string)
    if keyword_groups:
        for keyword_group in keyword_groups:
            matched = [item for item in indexed if _matches_keyword_group(item[1].get("text", ""), keyword_group)]
            if matched:
                target = util.get_target_item_from_matched_list(matched, mode)
                return target[0] if target else None
        if not allow_fallback:
            return None

    target = util.get_target_item_from_matched_list(indexed, mode)
    return target[0] if target else None


def _ctbc_availability_has_tickets(status_text, ticket_number=1):
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


def _ctbc_unique_query(url):
    """Return a query dict only when every key occurs exactly once."""
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    if len({key for key, _value in pairs}) != len(pairs):
        return None
    return dict(pairs)


def _parse_ctbc_date_target(onclick, current_url):
    """Strictly parse the site's ordinary date-button navigation target."""
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
        or target.fragment
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


def _parse_ctbc_map_send(href, expected_performance_id):
    """Parse the allowlisted five-argument Send call without evaluating JavaScript."""
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
        "route": match.group("route"),
        "performance_id": match.group("performance"),
        "price_area_id": match.group("price_area"),
        "group_id": match.group("group"),
        "status": match.group("status"),
    }


def _parse_ctbc_map_title(title):
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


def _ctbc_native_map_href_is_allowed(href, expected_performance_id):
    if _parse_ctbc_map_send(href, expected_performance_id):
        return True
    match = re.fullmatch(
        r"\s*javascript:\s*SendA\(\s*"
        r"(?P<q1>['\"])(?:0202|0205)(?P=q1)\s*,\s*"
        r"(?P<q2>['\"])(?P<performance>[A-Za-z0-9_-]+)(?P=q2)\s*"
        r"(?:,\s*['\"][^'\"]*['\"]\s*){5}\)\s*;?\s*",
        str(href or ""),
    )
    return bool(match and match.group("performance") == expected_performance_id)


def _build_ctbc_destination_url(area_url, target):
    source = urlsplit(area_url)
    if source.scheme != "https" or not _get_utk_sports_profile(area_url):
        return None
    if target.get("route") not in {"0202", "0205"}:
        return None
    path = f"{source.path.rsplit('/', 1)[0]}/UTK{target['route']}_"
    query = urlencode({
        "PERFORMANCE_ID": target["performance_id"],
        "GROUP_ID": target["group_id"],
        "PERFORMANCE_PRICE_AREA_ID": target["price_area_id"],
    })
    return urlunsplit(("https", source.netloc, path, query, ""))


def _select_ctbc_area_snapshot(snapshot, config_dict, expected_performance_id, strict_map=True):
    """Choose an area from one batched DOM/map snapshot using shared keyword semantics."""
    if not isinstance(snapshot, dict):
        return None
    rows = snapshot.get("rows")
    areas = snapshot.get("areas")
    if not isinstance(rows, list) or not isinstance(areas, list):
        return None
    if not 0 < len(rows) <= 200 or not 0 < len(areas) <= 500:
        return None

    areas_by_id = {}
    for area in areas:
        if not isinstance(area, dict):
            return None
        area_id = str(area.get("id") or "")
        href = str(area.get("href") or "")
        title = str(area.get("title") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", area_id) or not href:
            return None
        areas_by_id.setdefault(area_id, []).append({"href": href, "title": title})

    candidates = []
    ticket_number = int(config_dict.get("ticket_number", 1))
    for row in rows:
        if not isinstance(row, dict):
            return None
        area_text = re.sub(r"\s+", " ", str(row.get("area") or "")).strip()
        price_text = re.sub(r"\s+", " ", str(row.get("price") or "")).strip()
        status_text = re.sub(r"\s+", " ", str(row.get("status") or "")).strip()
        rel_tokens = str(row.get("rel") or "").split()
        if not area_text or not rel_tokens or not _ctbc_availability_has_tickets(status_text, ticket_number):
            continue
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", token) for token in rel_tokens):
            continue
        match_text = f"{area_text} {price_text}".strip()
        if util.reset_row_text_if_match_keyword_exclude(config_dict, match_text):
            continue

        map_options = []
        for rel in rel_tokens:
            for map_area in areas_by_id.get(rel, []):
                href = map_area["href"]
                map_title = _parse_ctbc_map_title(map_area["title"])
                fingerprint_matches = bool(
                    map_title
                    and map_title["area"] == re.sub(r"\s+", "", area_text)
                    and map_title["price"] == re.sub(r"[\s,$]", "", price_text)
                    and map_title["status"] == re.sub(r"\s+", "", status_text)
                )
                if not fingerprint_matches:
                    continue
                parsed = _parse_ctbc_map_send(href, expected_performance_id)
                if parsed:
                    status_matches = (
                        re.sub(r"\s+", "", parsed["status"])
                        == re.sub(r"\s+", "", status_text)
                    )
                    if _ctbc_availability_has_tickets(parsed["status"], ticket_number) and (
                        status_matches or not strict_map
                    ):
                        map_options.append((rel, href, parsed))
                elif not strict_map and _ctbc_native_map_href_is_allowed(href, expected_performance_id):
                    map_options.append((rel, href, None))
        if not map_options:
            continue
        if strict_map:
            identities = {
                (option[2]["route"], option[2]["performance_id"], option[2]["price_area_id"], option[2]["group_id"])
                for option in map_options
            }
            if len(identities) != 1:
                continue
        rel, map_href, destination = map_options[0]
        candidates.append({
            "text": match_text,
            "status": status_text,
            "rel": rel,
            "map_href": map_href,
            "destination": destination,
        })

    area_config = config_dict.get("area_auto_select", {})
    target_index = _choose_candidate_index(
        candidates,
        area_config.get("area_keyword", "").strip(),
        area_config.get("mode", "from top to bottom"),
        config_dict.get("area_auto_fallback", False),
    )
    return candidates[target_index] if target_index is not None else None


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
        "row_asc_seat_desc",
        "row_desc_seat_asc",
    }
    if seat_sorting not in numeric_sortings | {"display"}:
        seat_sorting = "row_seat"

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
                block_candidates.append({
                    "seats": block,
                    "row_number": row_number,
                    "min_seat_number": min(item["_seat_number"] for item in block),
                    "max_seat_number": max(item["_seat_number"] for item in block),
                    "dom_index": min(item.get("index", 0) for item in block),
                })

    if block_candidates:
        if seat_sorting == "display":
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

    if not allow_non_adjacent:
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


async def _reload_with_interval(tab, config_dict, reason):
    debug = util.create_debug_logger(config_dict)
    debug.log(f"[CTBC] {reason}; waiting before reload")
    interval = config_dict.get("advanced", {}).get("auto_reload_page_interval", 0.0)
    await tab.sleep(interval if interval and interval > 0 else 1.0)
    try:
        await tab.reload()
    except Exception as exc:
        debug.log(f"[CTBC] Reload failed: {exc}")


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


async def _ctbc_try_fast_area_navigation(tab, config_dict, date_target, onclick, source_url):
    """Use the attested normal-mode shortcut; return False for native-flow fallback."""
    debug = util.create_debug_logger(config_dict)
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
        snapshot, config_dict, date_target["performance_id"], strict_map=True
    )
    if not target or not target.get("destination"):
        debug.log("[CTBC FAST] Native area-page fallback: no attested area matched")
        return False
    destination_url = _build_ctbc_destination_url(date_target["url"], target["destination"])
    if not destination_url:
        return False

    source_json = json.dumps(source_url)
    onclick_json = json.dumps(onclick)
    destination_json = json.dumps(destination_url)
    committed = await tab.evaluate(f'''
        (() => {{
            const sourceUrl = {source_json};
            const expectedOnclick = {onclick_json};
            const destinationUrl = {destination_json};
            if (location.href !== sourceUrl || window.__ticketsHunterCtbcDateCommit) return false;
            const matches = Array.from(document.querySelectorAll('#PerformanceListTable button#buy_btn'))
                .filter(button => (button.getAttribute('onclick') || '') === expectedOnclick);
            if (matches.length !== 1) return false;
            const button = matches[0];
            if (button.disabled || button.getAttribute('aria-disabled') === 'true' ||
                !(button.textContent || '').includes('購買')) return false;
            window.__ticketsHunterCtbcDateCommit = destinationUrl;
            setTimeout(() => location.assign(destinationUrl), 0);
            return true;
        }})()
    ''')
    if committed is not True:
        return False

    expected = dict(target["destination"])
    expected.update({
        "product_id": date_target["product_id"],
        "area_url": date_target["url"],
        "destination_url": destination_url,
        "map_url": snapshot.get("map_url", ""),
        "config_fingerprint": _ctbc_selection_config_fingerprint(config_dict),
        "created_at": time.monotonic(),
    })
    _state["fast_target"][_ctbc_tab_identity(tab)] = expected
    elapsed_ms = int((time.monotonic() - started) * 1000)
    debug.log(f"[CTBC FAST] Selected: {target['text']} ({target['status']}) in {elapsed_ms} ms")
    return True


async def _ctbc_wait_area_snapshot(tab):
    """Wait for the native map to become actionable and return one batched snapshot."""
    raw_value = await tab.evaluate('''
        (() => new Promise(resolve => {
            let observer = null;
            let timer = null;
            let poller = null;
            const finish = value => {
                if (observer) observer.disconnect();
                if (timer) clearTimeout(timer);
                if (poller) clearInterval(poller);
                resolve(JSON.stringify(value));
                return true;
            };
            const collect = () => {
                if (!location.pathname.includes('/UTK0204_')) return finish({ok: false, reason: 'navigation'});
                const rows = Array.from(document.querySelectorAll('.seats table.f1 > tbody > tr.saleTr'));
                const areas = Array.from(document.querySelectorAll('#mapdata area[id][href]'));
                const mapReady = typeof mainMap === 'object' && mainMap && Object.keys(mainMap).length > 0;
                if (!rows.length || !areas.length || !mapReady) return false;
                finish({
                    ok: true,
                    page_url: location.href,
                    rows: rows.map(row => ({
                        area: (row.querySelector('td[data-title^="票區"]')?.textContent || '').trim(),
                        price: (row.querySelector('td[data-title^="票價"]')?.textContent || '').trim(),
                        status: (row.querySelector('td[data-title^="空位"]')?.textContent || '').trim(),
                        rel: row.getAttribute('rel') || ''
                    })),
                    areas: areas.map(area => ({
                        id: area.getAttribute('id') || '',
                        href: area.getAttribute('href') || '',
                        title: area.getAttribute('title') || ''
                    }))
                });
                return true;
            };
            if (collect()) return;
            observer = new MutationObserver(collect);
            observer.observe(document.documentElement, {childList: true, subtree: true});
            poller = setInterval(collect, 20);
            timer = setTimeout(() => finish({ok: false, reason: 'timeout'}), 2500);
        }))()
    ''', await_promise=True)
    return _decode_ctbc_json_result(raw_value)


async def nodriver_ctbc_date_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
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
            button = await row.query_selector('button#buy_btn')
            if not button:
                continue
            disabled = await button.apply('function(el) { return !!el.disabled; }')
            onclick = await button.apply('function(el) { return el.getAttribute("onclick") || ""; }')
            button_text = await _element_text(button)
            if disabled or "購買" not in button_text:
                continue
            row_text = await _element_text(row)
            if not row_text or util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                continue
            candidates.append({"text": row_text, "element": button, "onclick": onclick or ""})
        except Exception as exc:
            debug.log(f"[CTBC DATE] Failed to inspect row: {exc}")

    date_config = config_dict.get("date_auto_select", {})
    keyword = date_config.get("date_keyword", "").strip()
    target_index = _choose_candidate_index(
        candidates,
        keyword,
        date_config.get("mode", "from top to bottom"),
        config_dict.get("date_auto_fallback", False),
    )

    if target_index is None:
        await _reload_with_interval(tab, config_dict, "no purchasable date matched")
        return False

    target = candidates[target_index]
    try:
        current_url = str(getattr(getattr(tab, "target", None), "url", ""))
        date_target = _parse_ctbc_date_target(target.get("onclick"), current_url)
        if date_target and config_dict.get("area_auto_select", {}).get("enable", False):
            if await _ctbc_try_fast_area_navigation(
                tab, config_dict, date_target, target["onclick"], current_url
            ):
                return True
        await target["element"].apply('function(el) { el.click(); return true; }')
        debug.log(f"[CTBC DATE] Selected: {target['text']}")
        return True
    except Exception as exc:
        debug.log(f"[CTBC DATE] Click failed: {exc}")
        return False


async def nodriver_ctbc_area_auto_select(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    try:
        snapshot = await _ctbc_wait_area_snapshot(tab)
    except Exception as exc:
        debug.log(f"[CTBC AREA] Action-ready wait failed: {exc}")
        snapshot = None
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        reason = snapshot.get("reason", "invalid-result") if isinstance(snapshot, dict) else "invalid-result"
        await _reload_with_interval(tab, config_dict, f"area map was not ready ({reason})")
        return False

    page_url = str(snapshot.get("page_url") or "")
    query = _ctbc_unique_query(page_url)
    performance_id = query.get("PERFORMANCE_ID", "") if query else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", performance_id):
        return False
    target = _select_ctbc_area_snapshot(
        snapshot, config_dict, performance_id, strict_map=False
    )
    if target is None:
        await _reload_with_interval(tab, config_dict, "no available area matched")
        return False

    try:
        page_url_json = json.dumps(page_url)
        rel_json = json.dumps(target["rel"])
        map_href_json = json.dumps(target["map_href"])
        status_json = json.dumps(target["status"])
        committed = await tab.evaluate(f'''
            (() => {{
                const pageUrl = {page_url_json};
                const areaId = {rel_json};
                const expectedHref = {map_href_json};
                const expectedStatus = {status_json};
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                if (location.href !== pageUrl || window.__ticketsHunterCtbcAreaCommit) return false;
                const area = document.querySelector(`#mapdata area#${{CSS.escape(areaId)}}`);
                if (!area || (area.getAttribute('href') || '') !== expectedHref) return false;
                const rows = Array.from(document.querySelectorAll('.seats table.f1 > tbody > tr.saleTr'));
                const matchingRows = rows.filter(row => (row.getAttribute('rel') || '').split(/\\s+/).includes(areaId));
                if (!matchingRows.length) return false;
                const status = normalize(matchingRows[0].querySelector('td[data-title^="空位"]')?.textContent);
                if (status !== expectedStatus || status.includes('售完') || /^(0|0張|無)$/.test(status)) return false;
                window.__ticketsHunterCtbcAreaCommit = {{pageUrl, areaId, expectedHref}};
                setTimeout(() => area.click(), 0);
                return true;
            }})()
        ''')
        if committed is not True:
            debug.log("[CTBC AREA] Area changed before commit; no click was sent")
            return False
        debug.log(f"[CTBC AREA] Selected: {target['text']} ({target['status']})")
        return True
    except Exception as exc:
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

    ctbc_config = config_dict.get("ctbc", {})
    keyword = ctbc_config.get("ticket_type_keyword", "全票")
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
        config_dict.get("ctbc", {}).get("seat_sorting", "row_seat"),
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

    accounts = config_dict.get("accounts", {})
    account = str(state.get("account") or accounts.get("ctbc_account", "")).strip()
    password = str(state.get("password") or accounts.get("ctbc_password", "")).strip()
    if not account or not password:
        return False

    result = await tab.evaluate(f'''
        (() => {{
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


async def _ctbc_login_fingerprint(tab):
    raw = await tab.evaluate('''
        (() => {
            const account = document.querySelector('#LOGIN_ID');
            const password = document.querySelector('#LOGIN_PWD');
            return JSON.stringify({
                account: account ? account.value : '',
                password: password ? password.value : ''
            });
        })()
    ''')
    try:
        credentials = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        credentials = {}
    if not credentials.get("account") or not credentials.get("password"):
        return ""
    material = f"{credentials['account']}\0{credentials['password']}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _is_ctbc_auth_error(dialog_text):
    return (
        any(marker in (dialog_text or "") for marker in ("帳號", "密碼", "登入"))
        and any(marker in (dialog_text or "") for marker in ("錯誤", "有誤", "失敗", "不正確", "不存在", "無效"))
    )


async def _ctbc_check_captcha_error(tab, config_dict):
    debug = util.create_debug_logger(config_dict)
    dialog_raw = await tab.evaluate('''
        (() => {
            const dialog = document.querySelector('#dialog-message');
            if (!dialog) return '';
            const style = getComputedStyle(dialog);
            const visible = style.display !== 'none' && style.visibility !== 'hidden' && dialog.offsetParent !== null;
            return visible ? dialog.textContent.trim() : '';
        })()
    ''')
    dialog_text = dialog_raw if isinstance(dialog_raw, str) else ""
    if _is_ctbc_auth_error(dialog_text):
        submit_key = _ctbc_tab_state_key(tab)
        login_fingerprint = await _ctbc_login_fingerprint(tab)
        if login_fingerprint:
            _state["auth_failure"][submit_key] = login_fingerprint
        _state["submitted"].pop(submit_key, None)
        debug.log("[CTBC LOGIN] Authentication rejected; waiting for credentials to change")
        button = await tab.query_selector('.ui-dialog-buttonset button')
        if button:
            await button.click()
        return "auth"

    if "驗證碼" not in dialog_text or "錯誤" not in dialog_text:
        return None

    _state["submitted"].pop(_ctbc_tab_state_key(tab), None)
    debug.log(f"[CTBC CAPTCHA] Server rejected captcha: {dialog_text}")
    button = await tab.query_selector('.ui-dialog-buttonset button')
    if button:
        await button.click()
    captcha_input = await tab.query_selector('#CHK')
    if captcha_input:
        await captcha_input.apply('function(el) { el.value = ""; }')
    refresh = await tab.query_selector('#chang_pic')
    if refresh:
        await refresh.click()
    return "captcha"


def _normalize_ctbc_captcha_answer(answer):
    """Normalize ddddocr output to CTBC's four uppercase ASCII characters."""
    return re.sub(r"[^A-Za-z0-9]", "", str(answer or "")).upper()


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


async def _ctbc_captcha_and_submit(tab, config_dict, ocr):
    debug = util.create_debug_logger(config_dict)
    login_ready = await _fill_ctbc_inline_login(tab, config_dict)
    if not login_ready:
        debug.log("[CTBC LOGIN] Waiting for login or CTBC account settings")
        return False

    current_url = str(tab.target.url)
    submit_key = _ctbc_tab_state_key(tab)
    login_fingerprint = await _ctbc_login_fingerprint(tab)
    failed_fingerprint = _state["auth_failure"].get(submit_key)
    if failed_fingerprint and failed_fingerprint == login_fingerprint:
        debug.log("[CTBC LOGIN] Previous authentication failure; waiting for credentials to change")
        return False
    if failed_fingerprint and failed_fingerprint != login_fingerprint:
        _state["auth_failure"].pop(submit_key, None)

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

    submitted = _state["submitted"]
    submitted_at = submitted.get(submit_key)
    if submitted_at and time.monotonic() - submitted_at < 10.0:
        return False

    button = await tab.query_selector('button#addcart')
    if not button:
        return False
    await button.click()
    submitted[submit_key] = time.monotonic()
    debug.log("[CTBC] Add-to-cart submitted")

    await tab.sleep(1.0)
    if str(tab.target.url) != current_url:
        return True

    dialog_raw = await tab.evaluate('''
        (() => {
            const dialog = document.querySelector('#dialog-message');
            if (!dialog) return '';
            const style = getComputedStyle(dialog);
            const visible = style.display !== 'none' && style.visibility !== 'hidden' && dialog.offsetParent !== null;
            return visible ? dialog.textContent.trim() : '';
        })()
    ''')
    dialog_text = dialog_raw if isinstance(dialog_raw, str) else ""
    is_auth_error = _is_ctbc_auth_error(dialog_text)
    if is_auth_error:
        _state["auth_failure"][submit_key] = login_fingerprint
        submitted.pop(submit_key, None)
        debug.log("[CTBC LOGIN] Authentication rejected; automatic retry is blocked")
    elif "驗證碼" in dialog_text and any(marker in dialog_text for marker in ("錯誤", "失敗", "不正確")):
        submitted.pop(submit_key, None)
    elif dialog_text and "加入購物車" not in dialog_text:
        debug.log(f"[CTBC] Submit rejected: {dialog_text}")
    return "加入購物車" in dialog_text


async def nodriver_ctbc_seat_main(tab, config_dict, ocr):
    if not await _ctbc_attest_fast_destination(tab, "seat", config_dict):
        return False
    if await _ctbc_check_captcha_error(tab, config_dict) == "auth":
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

    # Audit again immediately before captcha/submit to block mixed or stale state.
    if not await _validate_ctbc_seat_selection(tab, config_dict):
        return False

    return await _ctbc_captcha_and_submit(tab, config_dict, ocr)


async def nodriver_ctbc_general_admission_main(tab, config_dict, ocr):
    """Select quantity on the unnumbered-seat UTK0203 page."""
    debug = util.create_debug_logger(config_dict)
    if not await _ctbc_attest_fast_destination(tab, "general_admission", config_dict):
        return False
    if await _ctbc_check_captcha_error(tab, config_dict) == "auth":
        return False

    current_url = str(tab.target.url).lower()
    row_selector = (
        '[id="table_tickettype"]'
        if "/utk0202_" in current_url
        else '#table_tickettype > tbody > tr'
    )
    rows = []
    for _ in range(10):
        try:
            rows = await tab.query_selector_all(row_selector)
        except Exception:
            rows = []
        if rows:
            break
        await tab.sleep(0.2)

    candidates = []
    ticket_number = int(config_dict.get("ticket_number", 1))
    for row in rows or []:
        try:
            amount = await row.query_selector('input.numbox[key]')
            if not amount:
                continue
            disabled = await amount.apply('function(el) { return !!el.disabled; }')
            row_text = await _element_text(row)
            if disabled or "售完" in row_text:
                continue
            remaining_input = await row.query_selector('input[key="LAST_AMOUNT"]')
            if remaining_input:
                remaining_raw = await remaining_input.apply('function(el) { return el.value || "0"; }')
                try:
                    if int(remaining_raw) < ticket_number:
                        continue
                except (TypeError, ValueError):
                    pass
            cells = await row.query_selector_all('td.fill')
            name = await _element_text(cells[0]) if cells else row_text
            if not name:
                continue
            candidates.append({"text": name, "element": amount})
        except Exception as exc:
            debug.log(f"[CTBC GENERAL] Failed to inspect ticket type: {exc}")

    keyword = config_dict.get("ctbc", {}).get("ticket_type_keyword", "全票")
    target_index = _choose_candidate_index(candidates, keyword, "from top to bottom", False)
    if target_index is None:
        debug.log(f"[CTBC GENERAL] No ticket type matched: {keyword}")
        return False

    quantity_limit_raw = await tab.evaluate('''
        (() => {
            const input = document.querySelector('#QUANTITY_LIMIT');
            return input ? parseInt(input.value || '0', 10) : 0;
        })()
    ''')
    quantity_limit = int(quantity_limit_raw or 0)
    if quantity_limit and ticket_number > quantity_limit:
        debug.log(f"[CTBC GENERAL] Requested {ticket_number}, limit is {quantity_limit}")
        return False

    amount = candidates[target_index]["element"]
    target_key = await amount.apply('function(el) { return el.getAttribute("key") || ""; }')
    if not target_key:
        return False
    try:
        all_amounts = await tab.query_selector_all('input.numbox[key]')
        for other_amount in all_amounts or []:
            await other_amount.apply('''function(el) {
                el.value = "0";
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }''')
        await amount.apply(f'''function(el) {{
            el.value = "{ticket_number}";
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('blur', {{bubbles: true}}));
        }}''')
        debug.log(f"[CTBC GENERAL] Selected {ticket_number} x {candidates[target_index]['text']}")
    except Exception as exc:
        debug.log(f"[CTBC GENERAL] Quantity assignment failed: {exc}")
        return False

    quantities_raw = await tab.evaluate('''
        (() => {
            const inputs = Array.from(document.querySelectorAll('input.numbox[key]'));
            return JSON.stringify(inputs.map(input => ({
                key: input.getAttribute('key') || '',
                value: Number(input.value || 0)
            })));
        })()
    ''')
    try:
        quantities = json.loads(quantities_raw) if isinstance(quantities_raw, str) else []
    except (TypeError, ValueError):
        quantities = []
    if not _ctbc_general_quantity_audit_is_valid(quantities, target_key, ticket_number):
        debug.log("[CTBC GENERAL] Quantity verification failed; submit blocked")
        return False

    return await _ctbc_captcha_and_submit(tab, config_dict, ocr)


def _ctbc_general_quantity_audit_is_valid(quantities, target_key, ticket_number):
    if not isinstance(quantities, list) or not target_key:
        return False
    target_matches = [item for item in quantities if isinstance(item, dict) and item.get("key") == target_key]
    if len(target_matches) != 1:
        return False
    try:
        if int(target_matches[0].get("value", 0)) != int(ticket_number):
            return False
        return all(
            item is target_matches[0] or int(item.get("value", 0)) == 0
            for item in quantities
            if isinstance(item, dict)
        ) and all(isinstance(item, dict) for item in quantities)
    except (TypeError, ValueError):
        return False


def _ctbc_fast_destination_is_valid(expected, page_type, current_url, dom_ids):
    if not isinstance(expected, dict) or not isinstance(dom_ids, dict):
        return False
    route = expected.get("route")
    expected_page_type = "seat" if route == "0205" else "general_admission" if route == "0202" else None
    if page_type != expected_page_type:
        return False
    current = urlsplit(current_url)
    area = urlsplit(expected.get("area_url", ""))
    if (
        current.scheme != "https"
        or current.hostname != area.hostname
        or not _get_utk_sports_profile(current_url)
        or current.fragment
    ):
        return False
    tenant_path = area.path.rsplit('/', 1)[0]
    allowed_paths = {f"{tenant_path}/UTK{route}_"}
    if route == "0202":
        allowed_paths.add(f"{tenant_path}/UTK0203_")
    if current.path not in allowed_paths:
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


async def _ctbc_return_to_native_area(tab, expected):
    area_url = str(expected.get("area_url") or "")
    parsed = urlsplit(area_url)
    if parsed.scheme != "https" or not _get_utk_sports_profile(area_url) or "/UTK0204_" not in parsed.path:
        return
    area_url_json = json.dumps(area_url)
    try:
        await tab.evaluate(f'''(() => {{
            const areaUrl = {area_url_json};
            if (location.href !== areaUrl && !window.__ticketsHunterCtbcReturn) {{
                window.__ticketsHunterCtbcReturn = true;
                setTimeout(() => location.assign(areaUrl), 0);
            }}
            return true;
        }})()''')
    except Exception:
        pass


async def _ctbc_attest_fast_destination(tab, page_type, config_dict):
    """Fail closed until a direct-navigation destination proves its hidden IDs."""
    identity = _ctbc_tab_identity(tab)
    expected = _state["fast_target"].get(identity)
    if not expected:
        return True
    debug = util.create_debug_logger(config_dict)
    if expected.get("returning"):
        await _ctbc_return_to_native_area(tab, expected)
        return False
    if expected.get("config_fingerprint") != _ctbc_selection_config_fingerprint(config_dict):
        expected["returning"] = True
        debug.log("[CTBC FAST] Selection settings changed; returning to native area flow")
        await _ctbc_return_to_native_area(tab, expected)
        return False
    if time.monotonic() - expected.get("created_at", 0) > 15.0:
        expected["returning"] = True
        debug.log("[CTBC FAST] Destination attestation expired")
        await _ctbc_return_to_native_area(tab, expected)
        return False

    marker_selectors = ["#TBL", ".ticket .checkNum"] if page_type == "seat" else [
        "#table_tickettype", "input.numbox[key]"
    ]
    marker_json = json.dumps(marker_selectors)
    price_area_selector_json = json.dumps(
        "#PRICE_AREA_ID" if page_type == "seat" else "#PERFORMANCE_PRICE_AREA_ID"
    )
    group_required_json = "true" if page_type == "seat" else "false"
    try:
        raw_value = await tab.evaluate(f'''
            (() => new Promise(resolve => {{
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
                    finish({{
                        ready: true,
                        page_url: location.href,
                        performance_id: performanceInput.value || '',
                        product_id: productInput.value || '',
                        group_id: groupInput ? (groupInput.value || '') : '',
                        price_area_id: priceAreaInput.value || ''
                    }});
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
        state = _decode_ctbc_json_result(raw_value)
    except Exception as exc:
        debug.log(f"[CTBC FAST] Destination attestation failed: {exc}")
        return False

    current_url = str(state.get("page_url") or "") if isinstance(state, dict) else ""
    if isinstance(state, dict) and state.get("ready") and _ctbc_fast_destination_is_valid(
        expected, page_type, current_url, state
    ):
        _state["fast_target"].pop(identity, None)
        debug.log("[CTBC FAST] Destination IDs verified")
        return True
    if isinstance(state, dict) and state.get("document_ready") != "complete":
        debug.log("[CTBC FAST] Destination is still loading")
        return False

    debug.log("[CTBC FAST] Destination mismatch; returning to native area flow")
    expected["returning"] = True
    await _ctbc_return_to_native_area(tab, expected)
    return False


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
        return tab

    page_type = _get_ctbc_page_type(url)
    if page_type == "date" and config_dict.get("date_auto_select", {}).get("enable", False):
        await nodriver_ctbc_date_auto_select(tab, config_dict)
    elif page_type == "area" and config_dict.get("area_auto_select", {}).get("enable", False):
        _state["fast_target"].pop(_ctbc_tab_identity(tab), None)
        await nodriver_ctbc_area_auto_select(tab, config_dict)
    elif page_type == "seat":
        await nodriver_ctbc_seat_main(tab, config_dict, ocr)
    elif page_type == "general_admission":
        await nodriver_ctbc_general_admission_main(tab, config_dict, ocr)
    elif page_type == "checkout":
        await _handle_ctbc_checkout(tab, config_dict)
    return tab
