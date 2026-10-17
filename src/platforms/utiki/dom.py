"""DOM snapshots and atomic commits for the uTiki2 ticketing family."""

import json
import re
from enum import Enum
from urllib.parse import urlsplit

import util

from .selection import (
    AreaActionPlan,
    AreaSnapshot,
    DateSnapshot,
    DialogSnapshot,
    QuantityPlan,
    QuantitySnapshot,
    SeatActionPlan,
    SeatSnapshot,
    SubmitPlan,
    date_plan_matches_snapshot,
    parse_area_snapshot,
    parse_date_snapshot,
    parse_dialog_snapshot,
    parse_quantity_snapshot,
    parse_seat_snapshot,
    seat_plan_matches_snapshot,
    submit_plan_is_valid,
)
from .profiles import (
    PageKind,
    TENANT_PROFILES,
    classify_utiki_page,
    same_origin,
    unique_query,
)
from .domain.snapshots import PresentField, parse_nonnegative_count
from .adapters.script_resources import assemble_script


CONST_SERVER_ASSIGNED_QUANTITY_PATHS = tuple(
    f"{profile.tenant_path}/UTK0201_001"
    for profile in TENANT_PROFILES.values()
)
CONST_GENERAL_ADMISSION_ROUTES = tuple(
    (f"https://{profile.host}", f"{profile.tenant_path}/UTK020{route}_")
    for profile in TENANT_PROFILES.values()
    for route in (2, 3)
)
CONST_RESERVED_SEAT_ROUTES = tuple(
    (f"https://{profile.host}", f"{profile.tenant_path}/UTK0205_")
    for profile in TENANT_PROFILES.values()
)


class CommitResult(Enum):
    COMMITTED = "committed"
    REJECTED = "rejected"
    STALE = "stale"


class SubmitDispatchResult(Enum):
    DISPATCHED = "dispatched"
    REJECTED = "rejected"
    STALE = "stale"


_CONFLICT_CLEAR_PAGE_KINDS = (
    PageKind.RESERVED_SEAT,
    PageKind.GENERAL_ADMISSION,
    PageKind.SERVER_ASSIGNED_QUANTITY,
)


def _decode_json_result(raw_value):
    """將瀏覽器回傳值解碼為 JSON 資料。"""

    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


def _valid_conflict_clear_input(page_kind, page_url, document_id, product_id):
    """驗證清除衝突選擇所需的頁面識別資料。"""

    query = unique_query(page_url)
    product_required = page_kind in (
        PageKind.RESERVED_SEAT, PageKind.SERVER_ASSIGNED_QUANTITY,
    )
    return bool(
        page_kind in _CONFLICT_CLEAR_PAGE_KINDS
        and classify_utiki_page(page_url) is page_kind
        and isinstance(document_id, str)
        and document_id
        and query is not None
        and all(
            key in query
            for key in (
                "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
            )
        )
        and isinstance(product_id, str)
        and (not product_id or re.fullmatch(r"[A-Za-z0-9_-]+", product_id))
        and (not product_required or product_id)
    )




async def clear_conflict_selection(
    tab, page_kind, page_url, document_id, product_id,
):
    """以原子操作清除一筆被拒絕的座位或票數選擇。"""
    if not _valid_conflict_clear_input(
        page_kind, page_url, document_id, product_id
    ):
        return CommitResult.REJECTED
    query = unique_query(page_url)
    intent = json.dumps({
        "pageKind": page_kind.value,
        "pageUrl": page_url,
        "documentId": document_id,
        "performanceId": query["PERFORMANCE_ID"],
        "groupId": query["GROUP_ID"],
        "priceAreaId": query["PERFORMANCE_PRICE_AREA_ID"],
        "productId": product_id,
        "reservedRoutes": CONST_RESERVED_SEAT_ROUTES,
        "generalRoutes": CONST_GENERAL_ADMISSION_ROUTES,
        "serverPaths": CONST_SERVER_ASSIGNED_QUANTITY_PATHS,
    }, ensure_ascii=False)
    script = assemble_script("conflict_clear", {
        "__TICKETS_HUNTER_CONFLICT_CLEAR_JSON__": intent,
    })
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("conflict clear outcome is unknown")


async def read_date_snapshot(tab):
    """在不改變頁面狀態下讀取官方場次表快照。"""
    raw_value = await tab.evaluate(assemble_script("date_snapshot"))
    snapshot = parse_date_snapshot(_decode_json_result(raw_value))
    if (
        isinstance(snapshot, DateSnapshot)
        and snapshot.ok
        and classify_utiki_page(snapshot.page_url) != PageKind.DATE
    ):
        return DateSnapshot(
            False, snapshot.page_url, snapshot.document_id, (), "route"
        )
    return snapshot


def _valid_date_commit_input(snapshot, plan, navigation_nonce):
    """驗證場次提交所需的快照、計畫與導覽識別。"""

    return bool(
        date_plan_matches_snapshot(snapshot, plan)
        and re.fullmatch(r"[a-f0-9]{32}", str(navigation_nonce or ""))
    )


def _valid_fast_date_destination(snapshot, plan, destination_url, marker_key, ttl_ms):
    """驗證快速場次導覽的目的地與標記設定。"""

    if not destination_url:
        return not marker_key and ttl_ms in (0, None)
    fingerprint = dict(plan.control_fingerprint)
    if (
        plan.action_kind != "normal" or not plan.fast_area_prefetch
        or fingerprint.get("tag_name") != "BUTTON"
        or fingerprint.get("id") != "buy_btn"
        or not isinstance(marker_key, str) or not 0 < len(marker_key) <= 256
        or type(ttl_ms) is not int or not 0 < ttl_ms <= 60000
    ):
        return False
    query = unique_query(destination_url)
    page_kind = classify_utiki_page(destination_url)
    try:
        source = urlsplit(snapshot.page_url)
        destination = urlsplit(destination_url)
    except ValueError:
        return False
    token = re.compile(r"[A-Za-z0-9_-]+")
    group_id = query.get("GROUP_ID", "") if query else ""
    return bool(
        page_kind in {PageKind.RESERVED_SEAT, PageKind.GENERAL_ADMISSION}
        and same_origin(source, destination)
        and query is not None
        and set(query) == {
            "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID"
        }
        and query.get("PERFORMANCE_ID") == plan.performance_id
        and token.fullmatch(query.get("PERFORMANCE_PRICE_AREA_ID", ""))
        and (not group_id or token.fullmatch(group_id))
        and (page_kind != PageKind.RESERVED_SEAT or bool(group_id))
    )




async def commit_date_action(
    tab,
    snapshot,
    plan,
    navigation_nonce,
    fast_destination_url="",
    fast_marker_key="",
    fast_marker_ttl_ms=0,
):
    """重新驗證場次計畫，並對同一文件意圖最多送出一次操作。"""
    if not _valid_date_commit_input(snapshot, plan, navigation_nonce):
        return CommitResult.REJECTED
    if not _valid_fast_date_destination(
        snapshot, plan, fast_destination_url, fast_marker_key,
        fast_marker_ttl_ms,
    ):
        return CommitResult.REJECTED
    intent_json = json.dumps({
        "pageUrl": snapshot.page_url,
        "documentId": snapshot.document_id,
        "rowIndex": plan.row_index,
        "controlIndex": plan.control_index,
        "rowText": plan.text,
        "fingerprint": dict(plan.control_fingerprint),
        "actionKind": plan.action_kind,
        "targetUrl": plan.target_url,
        "navigationNonce": navigation_nonce,
        "fastDestinationUrl": fast_destination_url,
        "fastMarkerKey": fast_marker_key,
        "fastMarkerTtlMs": fast_marker_ttl_ms,
    }, ensure_ascii=False)
    script = assemble_script("date_commit", {
        "__TICKETS_HUNTER_DATE_INTENT_JSON__": intent_json,
    })
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("date action commit outcome is unknown")


async def read_dialog_snapshot(tab):
    """在不改變頁面狀態下讀取所有可見的 uTiki 對話框。"""
    raw_value = await tab.evaluate('''
        (() => {
            // __ticketsHunterUtikiDialogSnapshot
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.getAttribute('aria-hidden') === 'true') return false;
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    element.getClientRects().length > 0;
            };
            const dialogs = Array.from(
                document.querySelectorAll('#dialog-message'))
                .map(message => ({
                    message,
                    wrapper: message.closest('.ui-dialog')
                }))
                .filter(item =>
                    visible(item.wrapper) && visible(item.message));
            return JSON.stringify({
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                visible_count: dialogs.length,
                text: dialogs.length === 1 ?
                    (dialogs[0].message.textContent || '').trim() : ''
            });
        })()
    ''')
    return parse_dialog_snapshot(_decode_json_result(raw_value))


async def dismiss_dialog(tab, snapshot):
    """重新驗證已觀察的對話框並按下其唯一操作按鈕。"""
    if not (
        isinstance(snapshot, DialogSnapshot)
        and snapshot.page_url
        and snapshot.document_id
        and snapshot.visible_count == 1
        and snapshot.text
    ):
        return CommitResult.REJECTED
    snapshot_json = json.dumps({
        "pageUrl": snapshot.page_url,
        "documentId": snapshot.document_id,
        "text": snapshot.text,
    }, ensure_ascii=False)
    raw_result = await tab.evaluate(fr'''
        (() => {{
            // __ticketsHunterUtikiDialogDismiss
            const snapshot = {snapshot_json};
            if (location.href !== snapshot.pageUrl ||
                String(performance.timeOrigin) !== snapshot.documentId) {{
                return 'stale';
            }}
            const visible = element => {{
                if (!element || !element.isConnected || element.hidden ||
                    element.getAttribute('aria-hidden') === 'true') return false;
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
                    snapshot.text) return 'stale';
            const buttons = Array.from(dialogs[0].wrapper.querySelectorAll(
                '.ui-dialog-buttonset button')).filter(visible);
            if (buttons.length !== 1 || buttons[0].disabled ||
                buttons[0].getAttribute('aria-disabled') === 'true') {{
                return 'rejected';
            }}
            buttons[0].click();
            return 'committed';
        }})()
    ''')
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("dialog dismiss outcome is unknown")


async def read_area_snapshot(tab):
    """等待可操作的區域圖後，以單次瀏覽器呼叫讀取快照。"""
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
                if (!location.pathname.includes('/UTK0204_')) {
                    return finish({ok: false, reason: 'navigation'});
                }
                const rows = Array.from(document.querySelectorAll(
                    '.seats table.f1 > tbody > tr.saleTr'));
                const areas = Array.from(document.querySelectorAll(
                    '#mapdata area[id][href]'));
                const mapReady = typeof mainMap === 'object' && mainMap &&
                    Object.keys(mainMap).length > 0;
                if (!rows.length || !areas.length || !mapReady) return false;
                finish({
                    ok: true,
                    page_url: location.href,
                    document_id: String(performance.timeOrigin),
                    rows: rows.map(row => ({
                        area: (row.querySelector(
                            'td[data-title^="票區"]')?.textContent || '').trim(),
                        price: (row.querySelector(
                            'td[data-title^="票價"]')?.textContent || '').trim(),
                        status: (row.querySelector(
                            'td[data-title^="空位"]')?.textContent || '').trim(),
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
            observer.observe(document.documentElement, {
                childList: true, subtree: true
            });
            poller = setInterval(collect, 20);
            timer = setTimeout(
                () => finish({ok: false, reason: 'timeout'}), 2500);
        }))()
    ''', await_promise=True)
    return parse_area_snapshot(_decode_json_result(raw_value))


def _valid_area_commit_input(snapshot, plan, ticket_number, navigation_nonce):
    """驗證區域提交所需的快照、計畫與票數。"""

    if not isinstance(snapshot, AreaSnapshot) or not isinstance(
        plan, AreaActionPlan
    ):
        return False
    if not snapshot.ok or not snapshot.page_url or not snapshot.document_id:
        return False
    if plan.action_name not in {"Send", "SendA"} or not plan.stable_args:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan.rel):
        return False
    if not re.fullmatch(r"[a-f0-9]{32}", str(navigation_nonce or "")):
        return False
    requested = parse_nonnegative_count(ticket_number)
    return isinstance(requested, PresentField) and requested.value > 0


async def commit_area(
    tab, snapshot, plan, ticket_number, navigation_nonce, fast_marker_key
):
    """重新驗證區域計畫，並對同一文件意圖最多點擊一次。"""
    if not _valid_area_commit_input(
        snapshot, plan, ticket_number, navigation_nonce
    ):
        return CommitResult.REJECTED

    page_url_json = json.dumps(snapshot.page_url)
    document_id_json = json.dumps(snapshot.document_id)
    rel_json = json.dumps(plan.rel)
    area_json = json.dumps(plan.area)
    price_json = json.dumps(plan.price)
    action_name_json = json.dumps(plan.action_name)
    action_args_json = json.dumps(plan.stable_args)
    ticket_number_json = json.dumps(parse_nonnegative_count(ticket_number).value)
    nonce_json = json.dumps(navigation_nonce)
    marker_key_json = json.dumps(fast_marker_key)
    raw_result = await tab.evaluate(fr'''
        (() => {{
            // __ticketsHunterCtbcNativeAreaCommit
            const pageUrl = {page_url_json};
            const documentId = {document_id_json};
            const areaId = {rel_json};
            const expectedArea = {area_json};
            const expectedPrice = {price_json};
            const expectedActionName = {action_name_json};
            const expectedActionArgs = {action_args_json};
            const ticketNumber = {ticket_number_json};
            const navigationNonce = {nonce_json};
            const normalizeArea = value => (value || '').replace(/\s+/g, '');
            const normalizePrice = value => (value || '').replace(/[\s,$]/g, '');
            const hasTickets = value => {{
                const text = (value || '').replace(/\s+/g, '');
                if (!text || text.includes('售完') ||
                    /^(0|0張|無)$/.test(text)) return false;
                if (text === '熱賣中') return true;
                const count = text.match(
                    /^(?:剩餘|尚餘|剩|餘)[:：]?([0-9]+)(?:張)?$/) ||
                    text.match(/^([0-9]+)(?:張)?$/);
                if (!count || count[1].length > 16) return false;
                const available = Number(count[1]);
                return Number.isSafeInteger(available) &&
                    available >= ticketNumber;
            }};
            const parseAction = value => {{
                const match = (value || '').match(
                    /^\s*javascript:\s*(Send|SendA)\(\s*(.*?)\s*\)\s*;?\s*$/);
                if (!match) return null;
                const args = [];
                let remaining = match[2];
                while (remaining) {{
                    const argument = remaining.match(
                        /^\s*(?:'([^'\"]*)'|"([^'\"]*)")\s*(?:,\s*|$)/);
                    if (!argument) return null;
                    args.push(argument[1] !== undefined ?
                        argument[1] : argument[2]);
                    remaining = remaining.slice(argument[0].length);
                }}
                const expectedLength = match[1] === 'Send' ? 5 : 7;
                if (args.length !== expectedLength) return null;
                return {{
                    name: match[1], stableArgs: args.slice(0, -1),
                    status: args.at(-1)
                }};
            }};
            const parseTitle = value => {{
                const match = (value || '').match(
                    /^\s*票區\s*[:：]\s*(.*?)\s+票價\s*[:：]\s*(.*?)\s+尚餘\s*[:：]\s*(.*?)\s*$/);
                if (!match) return null;
                return {{
                    area: normalizeArea(match[1]),
                    price: normalizePrice(match[2]), status: match[3]
                }};
            }};
            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-ctbc-area-commit';
            if (location.href !== pageUrl ||
                String(performance.timeOrigin) !== documentId) return 'stale';
            if (root.hasAttribute(commitMarker)) {{
                return root.getAttribute(commitMarker) === navigationNonce ?
                    'committed' : 'stale';
            }}
            try {{
                if (sessionStorage.getItem({marker_key_json}) !== null) {{
                    return 'stale';
                }}
            }} catch (_error) {{}}
            const area = document.querySelector(
                `#mapdata area#${{CSS.escape(areaId)}}`);
            if (!area) return 'rejected';
            const action = parseAction(area.getAttribute('href'));
            const mapTitle = parseTitle(area.getAttribute('title'));
            if (!action || !mapTitle || action.name !== expectedActionName ||
                action.stableArgs.length !== expectedActionArgs.length ||
                action.stableArgs.some((value, index) =>
                    value !== expectedActionArgs[index]) ||
                mapTitle.area !== normalizeArea(expectedArea) ||
                mapTitle.price !== normalizePrice(expectedPrice) ||
                !hasTickets(action.status) ||
                !hasTickets(mapTitle.status)) return 'rejected';
            const rows = Array.from(document.querySelectorAll(
                '.seats table.f1 > tbody > tr.saleTr'));
            const matchingRows = rows.filter(row => {{
                if (!(row.getAttribute('rel') || '').split(/\s+/)
                    .includes(areaId)) return false;
                const currentArea = row.querySelector(
                    'td[data-title^="票區"]')?.textContent;
                const currentPrice = row.querySelector(
                    'td[data-title^="票價"]')?.textContent;
                const status = row.querySelector(
                    'td[data-title^="空位"]')?.textContent;
                return normalizeArea(currentArea) ===
                    normalizeArea(expectedArea) &&
                    normalizePrice(currentPrice) ===
                    normalizePrice(expectedPrice) && hasTickets(status);
            }});
            if (!matchingRows.length) return 'rejected';
            root.setAttribute(commitMarker, navigationNonce);
            area.click();
            return 'committed';
        }})()
    ''')
    if raw_result is True:
        return CommitResult.COMMITTED
    if raw_result is False:
        return CommitResult.REJECTED
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("area commit outcome is unknown")




async def commit_server_assigned_area(
    tab, snapshot, plan, ticket_number, navigation_nonce, fast_marker_key
):
    """切換至電腦配位並點擊重新驗證過的區域列。"""
    if not _valid_area_commit_input(
        snapshot, plan, ticket_number, navigation_nonce
    ):
        return CommitResult.REJECTED

    intent_json = json.dumps({
        "pageUrl": snapshot.page_url,
        "documentId": snapshot.document_id,
        "areaId": plan.rel,
        "area": plan.area,
        "price": plan.price,
        "actionName": plan.action_name,
        "actionArgs": plan.stable_args,
        "ticketNumber": parse_nonnegative_count(ticket_number).value,
        "navigationNonce": navigation_nonce,
        "fastMarkerKey": fast_marker_key,
    }, ensure_ascii=False)
    script = assemble_script("server_assigned_area_commit", {
        "__TICKETS_HUNTER_INTENT_JSON__": intent_json,
    })
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError(
            "server-assigned area commit outcome is unknown"
        )




async def read_seat_snapshot(tab):
    """讀取票種與劃位座位的合併快照。"""
    raw_value = await tab.evaluate(assemble_script("seat_snapshot"))
    snapshot = parse_seat_snapshot(_decode_json_result(raw_value))
    if (
        isinstance(snapshot, SeatSnapshot)
        and snapshot.ok
        and classify_utiki_page(snapshot.page_url) != PageKind.RESERVED_SEAT
    ):
        return parse_seat_snapshot({
            "ok": False,
            "reason": "route",
            "page_url": snapshot.page_url,
            "document_id": snapshot.document_id,
        })
    return snapshot




async def commit_seat(tab, snapshot, plan):
    """以原子操作清除、選擇或稽核一筆劃位意圖。"""
    if (
        not isinstance(snapshot, SeatSnapshot)
        or not isinstance(plan, SeatActionPlan)
        or not seat_plan_matches_snapshot(snapshot, plan)
        or classify_utiki_page(plan.page_url) != PageKind.RESERVED_SEAT
    ):
        return CommitResult.REJECTED
    plan_json = json.dumps({
        "pageUrl": plan.page_url,
        "documentId": plan.document_id,
        "performanceId": plan.performance_id,
        "groupId": plan.group_id,
        "priceAreaId": plan.price_area_id,
        "productId": plan.product_id,
        "quantityLimitRaw": snapshot.quantity_limit_raw,
        "action": plan.action,
        "ticketNumber": plan.ticket_number,
        "ticketTypeFingerprint": plan.ticket_type_fingerprint,
        "ticketTypeId": plan.ticket_type_id,
        "ticketTypeName": plan.ticket_type_name,
        "selectedSeatFingerprints": plan.selected_seat_fingerprints,
        "targetSeatFingerprints": plan.target_seat_fingerprints,
        "requirePhysicalAdjacency": plan.require_physical_adjacency,
        "routes": CONST_RESERVED_SEAT_ROUTES,
    }, ensure_ascii=False)
    script = assemble_script("seat_commit", {
        "__TICKETS_HUNTER_PLAN_JSON__": plan_json,
    })
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("seat commit outcome is unknown")




async def read_quantity_snapshot(tab):
    """以單次瀏覽器呼叫讀取支援的票數頁面快照。"""
    raw_value = await tab.evaluate(assemble_script("quantity_snapshot"))
    snapshot = parse_quantity_snapshot(_decode_json_result(raw_value))
    if isinstance(snapshot, QuantitySnapshot) and snapshot.ok:
        page_kind = classify_utiki_page(snapshot.page_url)
        try:
            server_path = urlsplit(snapshot.page_url).path in (
                CONST_SERVER_ASSIGNED_QUANTITY_PATHS
            )
        except ValueError:
            server_path = False
        if page_kind == PageKind.GENERAL_ADMISSION or server_path:
            return snapshot
        return parse_quantity_snapshot({
            "ok": False,
            "reason": "route",
            "page_url": snapshot.page_url,
            "document_id": snapshot.document_id,
        })
    return snapshot


def _quantity_plan_route_is_valid(plan):
    """驗證票數計畫指定的頁面路由是否有效。"""

    if plan.page_type == "general_admission":
        return classify_utiki_page(plan.page_url) == PageKind.GENERAL_ADMISSION
    if plan.page_type != "server_assigned_quantity":
        return False
    try:
        return urlsplit(plan.page_url).path in (
            CONST_SERVER_ASSIGNED_QUANTITY_PATHS
        )
    except ValueError:
        return False


def _valid_quantity_commit_input(snapshot, plan):
    """驗證票數提交所需的快照與計畫。"""

    return bool(
        isinstance(snapshot, QuantitySnapshot)
        and isinstance(plan, QuantityPlan)
        and snapshot.ok
        and snapshot.page_url == plan.page_url
        and snapshot.document_id == plan.document_id
        and snapshot.performance_id == plan.performance_id
        and snapshot.group_id == plan.group_id
        and snapshot.price_area_id == plan.price_area_id
        and snapshot.product_id == plan.product_id
        and snapshot.performance_hidden_present
        == plan.performance_hidden_present
        and snapshot.product_hidden_present == plan.product_hidden_present
        and snapshot.group_hidden_present == plan.group_hidden_present
        and snapshot.price_area_hidden_present
        == plan.price_area_hidden_present
        and snapshot.quantity_limit_present == plan.quantity_limit_present
        and plan.page_type in {
            "general_admission", "server_assigned_quantity"
        }
        and _quantity_plan_route_is_valid(plan)
        and (
            plan.page_type != "server_assigned_quantity"
            or (bool(plan.group_id) and bool(plan.product_id))
        )
        and (
            not plan.product_hidden_present
            or re.fullmatch(r"[A-Za-z0-9_-]+", plan.product_id)
        )
        and plan.ticket_number > 0
        and re.fullmatch(r"[A-Za-z0-9_-]+", plan.target_key)
    )




async def commit_quantity(tab, snapshot, plan):
    """以原子操作將已稽核的票數計畫套用至目前文件。"""
    if not _valid_quantity_commit_input(snapshot, plan):
        return CommitResult.REJECTED
    plan_json = json.dumps({
        "pageUrl": plan.page_url,
        "documentId": plan.document_id,
        "performanceId": plan.performance_id,
        "productId": plan.product_id,
        "groupId": plan.group_id,
        "priceAreaId": plan.price_area_id,
        "quantityLimit": plan.quantity_limit,
        "targetKey": plan.target_key,
        "targetName": plan.target_name,
        "ticketNumber": plan.ticket_number,
        "pageType": plan.page_type,
        "routePaths": CONST_SERVER_ASSIGNED_QUANTITY_PATHS,
        "generalRoutes": CONST_GENERAL_ADMISSION_ROUTES,
        "performanceHiddenPresent": plan.performance_hidden_present,
        "productHiddenPresent": plan.product_hidden_present,
        "groupHiddenPresent": plan.group_hidden_present,
        "priceAreaHiddenPresent": plan.price_area_hidden_present,
        "quantityLimitPresent": plan.quantity_limit_present,
        "targetRemainingPresent": plan.target_remaining_present,
    }, ensure_ascii=False)
    script = assemble_script("quantity_commit", {
        "__TICKETS_HUNTER_PLAN_JSON__": plan_json,
    })
    raw_result = await tab.evaluate(script)
    if raw_result is True:
        return CommitResult.COMMITTED
    if raw_result is False:
        return CommitResult.REJECTED
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("quantity commit outcome is unknown")




def _quantity_submit_payload(plan):
    """建立票數選擇的送出資料。"""

    return {
        "pageUrl": plan.page_url,
        "documentId": plan.document_id,
        "performanceId": plan.performance_id,
        "productId": plan.product_id,
        "groupId": plan.group_id,
        "priceAreaId": plan.price_area_id,
        "quantityLimit": plan.quantity_limit,
        "targetKey": plan.target_key,
        "targetName": plan.target_name,
        "ticketNumber": plan.ticket_number,
        "pageType": plan.page_type,
        "quantityRoutePaths": CONST_SERVER_ASSIGNED_QUANTITY_PATHS,
        "generalRoutes": CONST_GENERAL_ADMISSION_ROUTES,
        "performanceHiddenPresent": plan.performance_hidden_present,
        "productHiddenPresent": plan.product_hidden_present,
        "groupHiddenPresent": plan.group_hidden_present,
        "priceAreaHiddenPresent": plan.price_area_hidden_present,
        "quantityLimitPresent": plan.quantity_limit_present,
        "targetRemainingPresent": plan.target_remaining_present,
    }


def _seat_submit_payload(plan):
    """建立劃位選擇的送出資料。"""

    expected = (
        plan.target_seat_fingerprints
        if plan.action == "select"
        else plan.selected_seat_fingerprints
    )
    return {
        "pageUrl": plan.page_url,
        "documentId": plan.document_id,
        "performanceId": plan.performance_id,
        "productId": plan.product_id,
        "groupId": plan.group_id,
        "priceAreaId": plan.price_area_id,
        "ticketNumber": plan.ticket_number,
        "pageType": "seat",
        "ticketTypeFingerprint": plan.ticket_type_fingerprint,
        "ticketTypeId": plan.ticket_type_id,
        "ticketTypeName": plan.ticket_type_name,
        "expectedSeatFingerprints": expected,
        "requirePhysicalAdjacency": plan.require_physical_adjacency,
        "reservedRoutes": CONST_RESERVED_SEAT_ROUTES,
    }


async def dispatch_submit(tab, plan):
    """重新驗證固定的購票意圖並送出加入購物車操作。"""
    if not isinstance(plan, SubmitPlan) or not submit_plan_is_valid(plan):
        return SubmitDispatchResult.REJECTED
    selection_plan = plan.selection_plan
    if isinstance(selection_plan, QuantityPlan):
        payload = _quantity_submit_payload(selection_plan)
    elif isinstance(selection_plan, SeatActionPlan):
        payload = _seat_submit_payload(selection_plan)
    else:
        return SubmitDispatchResult.REJECTED
    plan_json = json.dumps(payload, ensure_ascii=False)
    script = assemble_script("submit", {
        "__TICKETS_HUNTER_PLAN_JSON__": plan_json,
    })
    raw_result = await tab.evaluate(script)
    try:
        return SubmitDispatchResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("submit dispatch outcome is unknown")
