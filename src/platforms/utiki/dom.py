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
    date_plan_matches_snapshot,
    parse_area_snapshot,
    parse_date_snapshot,
    parse_dialog_snapshot,
    parse_quantity_snapshot,
)
from .profiles import (
    PageKind,
    TENANT_PROFILES,
    classify_utiki_page,
    same_origin,
    unique_query,
)


CONST_SERVER_ASSIGNED_QUANTITY_PATHS = tuple(
    f"{profile.tenant_path}/UTK0201_001"
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


def _decode_json_result(raw_value):
    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


CONST_DATE_SNAPSHOT_SCRIPT = r'''
        (() => {
            // __ticketsHunterUtikiDateSnapshot
            const roots = Array.from(document.querySelectorAll(
                '#PerformanceListTable'));
            const pageUrl = location.href;
            const documentId = String(performance.timeOrigin);
            if (roots.length !== 1) {
                return JSON.stringify({
                    ok: false, reason: 'scope', page_url: pageUrl,
                    document_id: documentId
                });
            }
            const rows = Array.from(roots[0].querySelectorAll(
                'table > tbody > tr'));
            if (!rows.length) {
                return JSON.stringify({
                    ok: false, reason: 'not-ready', page_url: pageUrl,
                    document_id: documentId
                });
            }
            const visible = element => {
                if (!element || !element.isConnected ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return element.getClientRects().length > 0 &&
                    style.display !== 'none' && style.visibility !== 'hidden' &&
                    style.pointerEvents !== 'none';
            };
            const fingerprint = element => ({
                tag_name: (element.tagName || '').toUpperCase(),
                id: element.getAttribute('id') || '',
                href: element.getAttribute('href') || '',
                resolved_href: element.href || '',
                role: element.getAttribute('role') || '',
                type: element.getAttribute('type') || '',
                target: element.getAttribute('target') || '',
                download: element.getAttribute('download') || '',
                ping: element.getAttribute('ping') || '',
                formaction: element.getAttribute('formaction') || '',
                has_form: !!element.closest('form'),
                onclick: element.getAttribute('onclick') || ''
            });
            return JSON.stringify({
                ok: true,
                page_url: pageUrl,
                document_id: documentId,
                rows: rows.map(row => {
                    const cell = row.lastElementChild;
                    const controls = cell && cell.tagName === 'TD' ?
                        Array.from(cell.querySelectorAll(
                            'button, a[href], [role="button"]')) : [];
                    return {
                        text: (row.innerText || row.textContent || '')
                            .replace(/\s+/g, ' ').trim(),
                        controls: controls.map(control => ({
                            actionable: control.closest('tr') === row &&
                                control.closest('td') === cell &&
                                visible(control) && !control.matches(':disabled') &&
                                control.getAttribute('aria-disabled') !== 'true',
                            ...fingerprint(control)
                        }))
                    };
                })
            });
        })()'''


async def read_date_snapshot(tab):
    """Read the official performance table without changing browser state."""
    raw_value = await tab.evaluate(CONST_DATE_SNAPSHOT_SCRIPT)
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
    return bool(
        date_plan_matches_snapshot(snapshot, plan)
        and re.fullmatch(r"[a-f0-9]{32}", str(navigation_nonce or ""))
    )


def _valid_fast_date_destination(snapshot, plan, destination_url, marker_key, ttl_ms):
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


CONST_DATE_COMMIT_SCRIPT = r'''
        (() => {
            // __ticketsHunterUtikiDateCommit
            const intent = __TICKETS_HUNTER_DATE_INTENT_JSON__;
            if (location.href !== intent.pageUrl ||
                String(performance.timeOrigin) !== intent.documentId) {
                return 'stale';
            }
            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-utiki-date-commit';
            const priorCommit = root.getAttribute(commitMarker);
            if (priorCommit) {
                return priorCommit === intent.navigationNonce ?
                    'committed' : 'stale';
            }
            const tables = Array.from(document.querySelectorAll(
                '#PerformanceListTable'));
            if (tables.length !== 1) return 'rejected';
            const rows = Array.from(tables[0].querySelectorAll(
                'table > tbody > tr'));
            const row = rows[intent.rowIndex];
            if (!row) return 'rejected';
            const rowText = (row.innerText || row.textContent || '')
                .replace(/\s+/g, ' ').trim();
            if (rowText !== intent.rowText) return 'stale';
            const cell = row.lastElementChild;
            if (!cell || cell.tagName !== 'TD') return 'rejected';
            const controls = Array.from(cell.querySelectorAll(
                'button, a[href], [role="button"]'));
            const control = controls[intent.controlIndex];
            if (!control) return 'rejected';
            const visible = element => {
                if (!element || !element.isConnected ||
                    element.closest('[hidden], [inert], [aria-hidden="true"]')) {
                    return false;
                }
                const style = getComputedStyle(element);
                return element.getClientRects().length > 0 &&
                    style.display !== 'none' && style.visibility !== 'hidden' &&
                    style.pointerEvents !== 'none';
            };
            const actionable = candidate =>
                candidate.closest('tr') === row &&
                candidate.closest('td') === cell && visible(candidate) &&
                !candidate.matches(':disabled') &&
                candidate.getAttribute('aria-disabled') !== 'true';
            const actionableControls = controls.filter(actionable);
            if (actionableControls.length !== 1 ||
                actionableControls[0] !== control) return 'rejected';
            const liveFingerprint = {
                tag_name: (control.tagName || '').toUpperCase(),
                id: control.getAttribute('id') || '',
                href: control.getAttribute('href') || '',
                resolved_href: control.href || '',
                role: control.getAttribute('role') || '',
                type: control.getAttribute('type') || '',
                target: control.getAttribute('target') || '',
                download: control.getAttribute('download') || '',
                ping: control.getAttribute('ping') || '',
                formaction: control.getAttribute('formaction') || '',
                has_form: !!control.closest('form'),
                onclick: control.getAttribute('onclick') || ''
            };
            if (Object.keys(intent.fingerprint).some(key =>
                liveFingerprint[key] !== intent.fingerprint[key])) {
                return 'stale';
            }
            if (intent.fastDestinationUrl) {
                const fastMatches = Array.from(tables[0].querySelectorAll(
                    'button#buy_btn')).filter(candidate =>
                        (candidate.getAttribute('onclick') || '') ===
                            intent.fingerprint.onclick);
                if (fastMatches.length !== 1 || fastMatches[0] !== control) {
                    return 'rejected';
                }
            }
            const cleanupOwnFastMarker = () => {
                try {
                    const raw = sessionStorage.getItem(intent.fastMarkerKey);
                    if (raw === null) return true;
                    const marker = JSON.parse(raw);
                    if (!marker || marker.nonce !== intent.navigationNonce) {
                        return false;
                    }
                    sessionStorage.removeItem(intent.fastMarkerKey);
                    return sessionStorage.getItem(intent.fastMarkerKey) === null;
                } catch (_error) {
                    return false;
                }
            };
            if (intent.fastDestinationUrl) {
                let existingRaw;
                try {
                    existingRaw = sessionStorage.getItem(intent.fastMarkerKey);
                } catch (_error) {
                    return 'rejected';
                }
                if (existingRaw !== null) return 'stale';
                const marker = {
                    version: 1,
                    nonce: intent.navigationNonce,
                    source: intent.pageUrl,
                    destination: intent.fastDestinationUrl,
                    expires_at: Date.now() + intent.fastMarkerTtlMs
                };
                try {
                    sessionStorage.setItem(
                        intent.fastMarkerKey, JSON.stringify(marker));
                    const stored = JSON.parse(
                        sessionStorage.getItem(intent.fastMarkerKey) || 'null');
                    if (!stored || stored.version !== marker.version ||
                        stored.nonce !== marker.nonce ||
                        stored.source !== marker.source ||
                        stored.destination !== marker.destination ||
                        stored.expires_at !== marker.expires_at) {
                        return cleanupOwnFastMarker() ? 'rejected' : 'stale';
                    }
                } catch (_error) {
                    return cleanupOwnFastMarker() ? 'rejected' : 'stale';
                }
            }
            root.setAttribute(commitMarker, intent.navigationNonce);
            if (intent.fastDestinationUrl) {
                location.assign(intent.fastDestinationUrl);
            } else if (intent.actionKind === 'anchor_native') {
                location.assign(intent.targetUrl);
            } else {
                control.click();
            }
            return 'committed';
        })()'''


async def commit_date_action(
    tab,
    snapshot,
    plan,
    navigation_nonce,
    fast_destination_url="",
    fast_marker_key="",
    fast_marker_ttl_ms=0,
):
    """Atomically revalidate and dispatch one date action per document intent."""
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
    script = CONST_DATE_COMMIT_SCRIPT.replace(
        "__TICKETS_HUNTER_DATE_INTENT_JSON__", intent_json, 1
    )
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("date action commit outcome is unknown")


async def read_dialog_snapshot(tab):
    """Read all visible uTiki dialog messages without changing the page."""
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
    """Revalidate and click the only action for one observed dialog."""
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
    """Wait for an actionable area map and read it in one browser call."""
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
    try:
        return int(ticket_number) > 0
    except (TypeError, ValueError):
        return False


async def commit_area(
    tab, snapshot, plan, ticket_number, navigation_nonce, fast_marker_key
):
    """Revalidate one area plan and dispatch at most one click per document intent."""
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
    ticket_number_json = json.dumps(int(ticket_number))
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
                if (/^\d+$/.test(text)) {{
                    return Number(text) >= ticketNumber;
                }}
                if (/(?:剩餘|尚餘|剩|餘)/.test(text)) {{
                    const count = text.match(/(\d+)/);
                    if (count && Number(count[1]) < ticketNumber) return false;
                }}
                return true;
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


CONST_SERVER_ASSIGNED_AREA_COMMIT_SCRIPT = r'''
        (() => {
            // __ticketsHunterUtikiServerAssignedAreaCommit
            const intent = __TICKETS_HUNTER_INTENT_JSON__;
            const visible = element => {
                if (!element || !element.isConnected || element.hidden ||
                    element.getAttribute('aria-hidden') === 'true') {
                    return false;
                }
                const style = getComputedStyle(element);
                return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.pointerEvents !== 'none' &&
                    element.getClientRects().length > 0;
            };
            const actionable = element => visible(element) &&
                !element.matches(':disabled') &&
                element.getAttribute('aria-disabled') !== 'true';
            const identityValid = () =>
                location.href === intent.pageUrl &&
                String(performance.timeOrigin) === intent.documentId;
            const uniqueVisible = selector => {
                const matches = Array.from(document.querySelectorAll(selector))
                    .filter(visible);
                return matches.length === 1 ? matches[0] : null;
            };
            const controls = () => {
                const root = uniqueVisible('#SeatOptions');
                const server = uniqueVisible('#byServer');
                const manual = uniqueVisible('#byhend');
                const checkboxRoot = uniqueVisible('#ABdiv_ATYPE_CB');
                const checkboxes = checkboxRoot ? Array.from(
                    checkboxRoot.querySelectorAll('#ATYPE_CB')) : [];
                const checkbox = checkboxes.length === 1 ? checkboxes[0] : null;
                if (!root || !server || !manual || !checkboxRoot || !checkbox ||
                    !root.contains(server) || !root.contains(manual) ||
                    !root.contains(checkboxRoot) ||
                    !checkboxRoot.contains(checkbox) ||
                    !visible(root) || !visible(checkboxRoot) ||
                    !actionable(server) || !actionable(manual) ||
                    !checkbox.isConnected || checkbox.disabled ||
                    checkbox.getAttribute('aria-disabled') === 'true' ||
                    checkbox.type !== 'checkbox') {
                    return null;
                }
                return {root, server, manual, checkbox};
            };
            const modeIsUnambiguous = state =>
                state.server.classList.contains('active') !==
                    state.manual.classList.contains('active');
            const normalizeArea = value => (value || '').replace(/\s+/g, '');
            const normalizePrice = value =>
                (value || '').replace(/[\s,$]/g, '');
            const hasTickets = value => {
                const text = (value || '').replace(/\s+/g, '');
                if (!text || text.includes('售完') ||
                    /^(0|0張|無)$/.test(text)) return false;
                if (text === '熱賣中') return true;
                if (/^\d+$/.test(text)) {
                    return Number(text) >= intent.ticketNumber;
                }
                const count = text.match(
                    /^(?:剩餘|尚餘|剩|餘)[:：]?(\d+)(?:張)?$/);
                if (count) return Number(count[1]) >= intent.ticketNumber;
                return false;
            };
            const parseAction = value => {
                const match = (value || '').match(
                    /^\s*(?:javascript:\s*)?(Send|SendA)\(\s*(.*?)\s*\)\s*;?\s*$/);
                if (!match) return null;
                const args = [];
                let remaining = match[2];
                while (remaining) {
                    const argument = remaining.match(
                        /^\s*(?:'([^'\"]*)'|"([^'\"]*)")\s*(?:,\s*|$)/);
                    if (!argument) return null;
                    args.push(argument[1] !== undefined ?
                        argument[1] : argument[2]);
                    remaining = remaining.slice(argument[0].length);
                }
                const expectedLength = match[1] === 'Send' ? 5 : 7;
                if (args.length !== expectedLength) return null;
                return {
                    name: match[1], stableArgs: args.slice(0, -1),
                    status: args.at(-1)
                };
            };
            const parseTitle = value => {
                const match = (value || '').match(
                    /^\s*票區\s*[:：]\s*(.*?)\s+票價\s*[:：]\s*(.*?)\s+尚餘\s*[:：]\s*(.*?)\s*$/);
                return match ? {
                    area: normalizeArea(match[1]),
                    price: normalizePrice(match[2]), status: match[3]
                } : null;
            };
            const targetRow = () => {
                const area = document.querySelector(
                    `#mapdata area#${CSS.escape(intent.areaId)}`);
                if (!area) return null;
                const action = parseAction(area.getAttribute('href'));
                const title = parseTitle(area.getAttribute('title'));
                if (!action || !title || action.name !== intent.actionName ||
                    action.stableArgs.length !== intent.actionArgs.length ||
                    action.stableArgs.some((value, index) =>
                        value !== intent.actionArgs[index]) ||
                    title.area !== normalizeArea(intent.area) ||
                    title.price !== normalizePrice(intent.price) ||
                    !hasTickets(action.status) || !hasTickets(title.status)) {
                    return null;
                }
                const rows = Array.from(document.querySelectorAll(
                    '.seats table.f1 > tbody > tr.saleTr')).filter(row => {
                    if (!(row.getAttribute('rel') || '').split(/\s+/)
                        .includes(intent.areaId) || !actionable(row)) return false;
                    const areaText = row.querySelector(
                        'td[data-title^="票區"]')?.textContent;
                    const priceText = row.querySelector(
                        'td[data-title^="票價"]')?.textContent;
                    const status = row.querySelector(
                        'td[data-title^="空位"]')?.textContent;
                    const rowAction = parseAction(
                        row.getAttribute('onclick'));
                    return normalizeArea(areaText) ===
                        normalizeArea(intent.area) &&
                        normalizePrice(priceText) ===
                        normalizePrice(intent.price) && hasTickets(status) &&
                        rowAction && rowAction.name === intent.actionName &&
                        rowAction.stableArgs.length ===
                            intent.actionArgs.length &&
                        rowAction.stableArgs.every((value, index) =>
                            value === intent.actionArgs[index]) &&
                        hasTickets(rowAction.status);
                });
                return rows.length === 1 ? rows[0] : null;
            };

            const root = document.documentElement;
            const commitMarker = 'data-tickets-hunter-ctbc-area-commit';
            const modePrepareMarker =
                'data-tickets-hunter-utiki-server-mode-prepare';
            const checkboxPrepareMarker =
                'data-tickets-hunter-utiki-server-checkbox-prepare';
            const beginPreparation = markerName => {
                if (root.getAttribute(markerName) === intent.documentId) {
                    return false;
                }
                root.setAttribute(markerName, intent.documentId);
                return true;
            };
            if (!identityValid()) return 'stale';
            if (root.hasAttribute(commitMarker)) {
                return root.getAttribute(commitMarker) ===
                    intent.navigationNonce ? 'committed' : 'stale';
            }
            try {
                if (sessionStorage.getItem(intent.fastMarkerKey) !== null) {
                    return 'stale';
                }
            } catch (_error) {}

            let state = controls();
            if (!state || !modeIsUnambiguous(state)) return 'rejected';
            if (!state.server.classList.contains('active')) {
                if (!beginPreparation(modePrepareMarker)) return 'rejected';
                state.server.click();
                if (!identityValid()) return 'stale';
                state = controls();
            }
            if (!state || !state.server.classList.contains('active') ||
                state.manual.classList.contains('active')) return 'rejected';
            if (state.checkbox.checked) {
                if (!beginPreparation(checkboxPrepareMarker)) return 'rejected';
                state.checkbox.click();
                if (!identityValid()) return 'stale';
                state = controls();
            }
            if (!state || !state.server.classList.contains('active') ||
                state.manual.classList.contains('active') ||
                state.checkbox.checked) return 'rejected';

            const row = targetRow();
            if (!row) return 'rejected';
            root.setAttribute(commitMarker, intent.navigationNonce);
            row.click();
            return 'committed';
        })()

    '''


async def commit_server_assigned_area(
    tab, snapshot, plan, ticket_number, navigation_nonce, fast_marker_key
):
    """Switch to native server assignment and click one revalidated area row."""
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
        "ticketNumber": int(ticket_number),
        "navigationNonce": navigation_nonce,
        "fastMarkerKey": fast_marker_key,
    }, ensure_ascii=False)
    script = CONST_SERVER_ASSIGNED_AREA_COMMIT_SCRIPT.replace(
        "__TICKETS_HUNTER_INTENT_JSON__", intent_json, 1
    )
    raw_result = await tab.evaluate(script)
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError(
            "server-assigned area commit outcome is unknown"
        )


async def read_quantity_snapshot(tab):
    """Read a general-admission quantity page in one browser call."""
    raw_value = await tab.evaluate(r'''
        (() => {
            const roots = Array.from(document.querySelectorAll(
                '[id="table_tickettype"]'));
            const scopedInputs = Array.from(new Set(roots.flatMap(root =>
                Array.from(root.querySelectorAll('input.numbox[key]')))));
            const inputs = Array.from(document.querySelectorAll(
                'input.numbox[key]'));
            if (!roots.length || !inputs.length) {
                return JSON.stringify({
                    ok: false, reason: 'not-ready', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const scopedSet = new Set(scopedInputs);
            if (inputs.length !== scopedInputs.length ||
                inputs.some(input => !scopedSet.has(input))) {
                return JSON.stringify({
                    ok: false, reason: 'scope', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const query = new URLSearchParams(location.search);
            const identity = (selector, key) => {
                const values = query.getAll(key);
                if (values.length !== 1) return null;
                const hidden = document.querySelector(selector);
                if (hidden && (hidden.value || '') !== values[0]) return null;
                return values[0];
            };
            const performanceId = identity(
                '#PERFORMANCE_ID', 'PERFORMANCE_ID');
            const groupId = identity('#GROUP_ID', 'GROUP_ID');
            const priceAreaId = identity(
                '#PERFORMANCE_PRICE_AREA_ID',
                'PERFORMANCE_PRICE_AREA_ID');
            const productInputs = Array.from(
                document.querySelectorAll('#PRODUCT_ID'));
            const productId = productInputs.length === 1 ?
                (productInputs[0].value || '') :
                (productInputs.length ? '__invalid__' : '');
            if (performanceId === null || groupId === null ||
                priceAreaId === null) {
                return JSON.stringify({
                    ok: false, reason: 'identity', page_url: location.href,
                    document_id: String(performance.timeOrigin)
                });
            }
            const limit = document.querySelector('#QUANTITY_LIMIT');
            return JSON.stringify({
                ok: true,
                page_url: location.href,
                document_id: String(performance.timeOrigin),
                performance_id: performanceId,
                product_id: productId,
                group_id: groupId,
                price_area_id: priceAreaId,
                quantity_limit_raw: limit ? (limit.value || '').trim() : '',
                rows: inputs.map(input => {
                    const row = input.closest('tr') ||
                        input.closest('[id="table_tickettype"]');
                    const remaining = Array.from(
                        row.querySelectorAll('input[key="LAST_AMOUNT"]'));
                    const nameCell = row.querySelector('td.fill');
                    return {
                        key: input.getAttribute('key') || '',
                        name: (nameCell?.textContent || '').trim(),
                        text: (row.innerText || row.textContent || '')
                            .replace(/\s+/g, ' ').trim(),
                        disabled: !!input.disabled ||
                            input.getAttribute('aria-disabled') === 'true',
                        remaining_present: remaining.length > 0,
                        remaining_raw: remaining.length === 1 ?
                            (remaining[0].value || '').trim() : '__invalid__',
                        value: (input.value || '').trim()
                    };
                })
            });
        })()
    ''')
    return parse_quantity_snapshot(_decode_json_result(raw_value))


def _quantity_plan_route_is_valid(plan):
    if plan.page_type == "general_admission":
        return True
    if plan.page_type != "server_assigned_quantity":
        return False
    try:
        return urlsplit(plan.page_url).path in (
            CONST_SERVER_ASSIGNED_QUANTITY_PATHS
        )
    except ValueError:
        return False


def _valid_quantity_commit_input(snapshot, plan):
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
        and plan.page_type in {
            "general_admission", "server_assigned_quantity"
        }
        and _quantity_plan_route_is_valid(plan)
        and (
            plan.page_type != "server_assigned_quantity"
            or (bool(plan.group_id) and bool(plan.product_id))
        )
        and plan.ticket_number > 0
        and re.fullmatch(r"[A-Za-z0-9_-]+", plan.target_key)
    )


CONST_QUANTITY_COMMIT_SCRIPT = r'''
        (() => {
            // __ticketsHunterUtikiQuantityCommit
            const plan = __TICKETS_HUNTER_PLAN_JSON__;
            const identityValid = () => {
                if (location.href !== plan.pageUrl ||
                    String(performance.timeOrigin) !== plan.documentId) {
                    return false;
                }
                const query = new URLSearchParams(location.search);
                const exactQuery = (key, expected) => {
                    const values = query.getAll(key);
                    return values.length === 1 && values[0] === expected;
                };
                const hiddenMatches = (selector, expected) => {
                    const input = document.querySelector(selector);
                    return !input || (input.value || '') === expected;
                };
                const exactHiddenMatches = (selector, expected) => {
                    const inputs = Array.from(
                        document.querySelectorAll(selector));
                    return inputs.length === 1 &&
                        (inputs[0].value || '') === expected;
                };
                const serverRouteValid = () => {
                    if (plan.pageType === 'general_admission') return true;
                    if (plan.pageType !== 'server_assigned_quantity') {
                        return false;
                    }
                    const keys = Array.from(query.keys());
                    return plan.routePaths.includes(location.pathname) &&
                        keys.length === 4 &&
                        new Set(keys).size === 4 &&
                        exactQuery('ATYPE', 'N') &&
                        exactHiddenMatches(
                            '#PERFORMANCE_ID', plan.performanceId) &&
                        exactHiddenMatches('#PRODUCT_ID', plan.productId) &&
                        exactHiddenMatches('#GROUP_ID', plan.groupId) &&
                        exactHiddenMatches(
                            '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId);
                };
                return exactQuery('PERFORMANCE_ID', plan.performanceId) &&
                    exactQuery('GROUP_ID', plan.groupId) &&
                    exactQuery('PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) &&
                    hiddenMatches('#PERFORMANCE_ID', plan.performanceId) &&
                    hiddenMatches('#GROUP_ID', plan.groupId) &&
                    hiddenMatches(
                        '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) &&
                    serverRouteValid();
            };
            const inspectLive = () => {
                if (!identityValid()) return {status: 'stale'};
                const roots = Array.from(document.querySelectorAll(
                    '[id="table_tickettype"]'));
                const scoped = Array.from(new Set(roots.flatMap(root =>
                    Array.from(root.querySelectorAll('input.numbox[key]')))));
                const inputs = Array.from(document.querySelectorAll(
                    'input.numbox[key]'));
                const scopedSet = new Set(scoped);
                const keys = inputs.map(input =>
                    input.getAttribute('key') || '');
                if (!roots.length || !inputs.length ||
                    inputs.length !== scoped.length ||
                    inputs.some(input => !scopedSet.has(input)) ||
                    keys.some(key => !/^[A-Za-z0-9_-]+$/.test(key)) ||
                    new Set(keys).size !== keys.length) {
                    return {status: 'rejected'};
                }
                const targets = scoped.filter(input =>
                    input.getAttribute('key') === plan.targetKey);
                if (targets.length !== 1) return {status: 'rejected'};
                const limitInput = document.querySelector('#QUANTITY_LIMIT');
                const limitRaw = limitInput ?
                    (limitInput.value || '').trim() : '';
                if (limitRaw && !/^\d+$/.test(limitRaw)) {
                    return {status: 'rejected'};
                }
                const liveLimit = limitRaw ? Number(limitRaw) : 0;
                if (liveLimit !== plan.quantityLimit ||
                    (liveLimit && plan.ticketNumber > liveLimit)) {
                    return {status: 'rejected'};
                }
                const target = targets[0];
                const row = target.closest('tr') ||
                    target.closest('[id="table_tickettype"]');
                const name = (row.querySelector('td.fill')?.textContent || '')
                    .trim();
                const text = (row.innerText || row.textContent || '')
                    .replace(/\s+/g, ' ').trim();
                const remaining = Array.from(
                    row.querySelectorAll('input[key="LAST_AMOUNT"]'));
                if (name !== plan.targetName || text.includes('售完') ||
                    target.disabled ||
                    target.getAttribute('aria-disabled') === 'true') {
                    return {status: 'rejected'};
                }
                if (remaining.length > 1) return {status: 'rejected'};
                if (remaining.length === 1) {
                    const raw = (remaining[0].value || '').trim();
                    if (!/^\d+$/.test(raw) ||
                        Number(raw) < plan.ticketNumber) {
                        return {status: 'rejected'};
                    }
                }
                return {status: 'ok', inputs, target};
            };
            const markerName = 'data-tickets-hunter-utiki-quantity-commit';
            const intent = JSON.stringify([
                plan.targetKey, plan.ticketNumber]);
            const marker = document.documentElement.getAttribute(markerName);
            let live = inspectLive();
            if (live.status !== 'ok') return marker ? 'stale' : live.status;
            const auditValid = state => state.inputs.every(input =>
                Number(input.value || 0) ===
                    (input === state.target ? plan.ticketNumber : 0));
            if (marker) {
                return marker === intent && auditValid(live) ?
                    'committed' : 'stale';
            }
            document.documentElement.setAttribute(markerName, intent);
            for (const input of live.inputs) {
                input.value = '0';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
            }
            live.target.value = String(plan.ticketNumber);
            live.target.dispatchEvent(new Event('input', {bubbles: true}));
            live.target.dispatchEvent(new Event('change', {bubbles: true}));
            live.target.dispatchEvent(new Event('blur', {bubbles: true}));
            live = inspectLive();
            return live.status === 'ok' && auditValid(live) ?
                'committed' : 'stale';
        })()'''


async def commit_quantity(tab, snapshot, plan):
    """Atomically apply one audited quantity plan to the current document."""
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
    }, ensure_ascii=False)
    script = CONST_QUANTITY_COMMIT_SCRIPT.replace(
        "__TICKETS_HUNTER_PLAN_JSON__", plan_json, 1
    )
    raw_result = await tab.evaluate(script)
    if raw_result is True:
        return CommitResult.COMMITTED
    if raw_result is False:
        return CommitResult.REJECTED
    try:
        return CommitResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("quantity commit outcome is unknown")


def _valid_quantity_submit_input(plan):
    token = r"[A-Za-z0-9_-]+"
    return bool(
        isinstance(plan, QuantityPlan)
        and plan.page_url
        and plan.document_id
        and re.fullmatch(token, plan.performance_id)
        and re.fullmatch(token, plan.price_area_id)
        and (not plan.group_id or re.fullmatch(token, plan.group_id))
        and re.fullmatch(token, plan.target_key)
        and plan.page_type in {
            "general_admission", "server_assigned_quantity"
        }
        and _quantity_plan_route_is_valid(plan)
        and (
            plan.page_type != "server_assigned_quantity"
            or (bool(plan.group_id) and re.fullmatch(token, plan.product_id))
        )
        and plan.ticket_number > 0
        and plan.quantity_limit >= 0
    )


CONST_QUANTITY_SUBMIT_SCRIPT = r'''
        (() => {
            // __ticketsHunterUtikiQuantitySubmit
            const plan = __TICKETS_HUNTER_PLAN_JSON__;
            if (location.href !== plan.pageUrl ||
                String(performance.timeOrigin) !== plan.documentId) {
                return 'stale';
            }
            const query = new URLSearchParams(location.search);
            const exactQuery = (key, expected) => {
                const values = query.getAll(key);
                return values.length === 1 && values[0] === expected;
            };
            const hiddenMatches = (selector, expected) => {
                const input = document.querySelector(selector);
                return !input || (input.value || '') === expected;
            };
            const exactHiddenMatches = (selector, expected) => {
                const inputs = Array.from(document.querySelectorAll(selector));
                return inputs.length === 1 &&
                    (inputs[0].value || '') === expected;
            };
            const queryKeys = Array.from(query.keys());
            const serverRouteValid =
                plan.pageType === 'general_admission' ||
                (plan.pageType === 'server_assigned_quantity' &&
                    plan.routePaths.includes(location.pathname) &&
                    queryKeys.length === 4 &&
                    new Set(queryKeys).size === 4 &&
                    exactQuery('ATYPE', 'N') &&
                    exactHiddenMatches(
                        '#PERFORMANCE_ID', plan.performanceId) &&
                    exactHiddenMatches('#PRODUCT_ID', plan.productId) &&
                    exactHiddenMatches('#GROUP_ID', plan.groupId) &&
                    exactHiddenMatches(
                        '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId));
            if (!exactQuery('PERFORMANCE_ID', plan.performanceId) ||
                !exactQuery('GROUP_ID', plan.groupId) ||
                !exactQuery(
                    'PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) ||
                !hiddenMatches('#PERFORMANCE_ID', plan.performanceId) ||
                !hiddenMatches('#GROUP_ID', plan.groupId) ||
                !hiddenMatches(
                    '#PERFORMANCE_PRICE_AREA_ID', plan.priceAreaId) ||
                !serverRouteValid) {
                return 'stale';
            }
            const quantityIntent = JSON.stringify([
                plan.targetKey, plan.ticketNumber]);
            if (document.documentElement.getAttribute(
                'data-tickets-hunter-utiki-quantity-commit'
            ) !== quantityIntent) return 'stale';
            const roots = Array.from(document.querySelectorAll(
                '[id="table_tickettype"]'));
            const scoped = Array.from(new Set(roots.flatMap(root =>
                Array.from(root.querySelectorAll('input.numbox[key]')))));
            const inputs = Array.from(document.querySelectorAll(
                'input.numbox[key]'));
            const scopedSet = new Set(scoped);
            const keys = inputs.map(input =>
                input.getAttribute('key') || '');
            if (!roots.length || !inputs.length ||
                inputs.length !== scoped.length ||
                inputs.some(input => !scopedSet.has(input)) ||
                keys.some(key => !/^[A-Za-z0-9_-]+$/.test(key)) ||
                new Set(keys).size !== keys.length) return 'stale';
            const targets = scoped.filter(input =>
                input.getAttribute('key') === plan.targetKey);
            if (targets.length !== 1) return 'stale';
            const limitInput = document.querySelector('#QUANTITY_LIMIT');
            const limitRaw = limitInput ?
                (limitInput.value || '').trim() : '';
            if (limitRaw && !/^\d+$/.test(limitRaw)) return 'stale';
            const liveLimit = limitRaw ? Number(limitRaw) : 0;
            if (liveLimit !== plan.quantityLimit ||
                (liveLimit && plan.ticketNumber > liveLimit)) {
                return 'stale';
            }
            const target = targets[0];
            const row = target.closest('tr') ||
                target.closest('[id="table_tickettype"]');
            const name = (row.querySelector('td.fill')?.textContent || '')
                .trim();
            const text = (row.innerText || row.textContent || '')
                .replace(/\s+/g, ' ').trim();
            const remaining = Array.from(
                row.querySelectorAll('input[key="LAST_AMOUNT"]'));
            if (name !== plan.targetName || text.includes('售完') ||
                target.disabled ||
                target.getAttribute('aria-disabled') === 'true' ||
                remaining.length > 1) return 'stale';
            if (remaining.length === 1) {
                const raw = (remaining[0].value || '').trim();
                if (!/^\d+$/.test(raw) ||
                    Number(raw) < plan.ticketNumber) return 'stale';
            }
            const quantitiesValid = inputs.every(input =>
                Number(input.value || 0) ===
                    (input === target ? plan.ticketNumber : 0));
            if (!quantitiesValid) return 'stale';
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
            if (dialogs.length) return 'rejected';
            const loginIds = Array.from(
                document.querySelectorAll('#LOGIN_ID')).filter(visible);
            const loginPasswords = Array.from(
                document.querySelectorAll('#LOGIN_PWD')).filter(visible);
            if (loginIds.length || loginPasswords.length) {
                if (loginIds.length !== 1 || loginPasswords.length !== 1 ||
                    !(loginIds[0].value || '').trim() ||
                    !(loginPasswords[0].value || '')) return 'rejected';
            }
            const captchas = Array.from(
                document.querySelectorAll('input#CHK')).filter(visible);
            if (captchas.length !== 1 ||
                captchas[0].disabled ||
                captchas[0].getAttribute('aria-disabled') === 'true' ||
                captchas[0].readOnly ||
                (captchas[0].value || '').trim().length !== 4) {
                return 'rejected';
            }
            const buttons = Array.from(
                document.querySelectorAll('button#addcart')).filter(visible);
            if (buttons.length !== 1 || buttons[0].disabled ||
                buttons[0].getAttribute('aria-disabled') === 'true') {
                return 'rejected';
            }
            if (plan.pageType === 'server_assigned_quantity') {
                const form = buttons[0].form;
                if (!form) return 'rejected';
                const method = (
                    buttons[0].hasAttribute('formmethod') ?
                        buttons[0].formMethod : form.method
                ).toLowerCase();
                if (method !== 'post') {
                    return 'rejected';
                }
                const action = new URL(
                    buttons[0].hasAttribute('formaction') ?
                        buttons[0].formAction : form.action,
                    location.href
                );
                const expectedPath = location.pathname.replace(/\/+$/, '') +
                    '/ADD_SHOPPING_CAR';
                if (action.origin !== location.origin ||
                    action.pathname !== expectedPath ||
                    action.search || action.hash) return 'rejected';
            }
            buttons[0].click();
            return 'dispatched';
        })()'''


async def dispatch_quantity_submit(tab, plan):
    """Atomically revalidate quantity state and dispatch add-to-cart once."""
    if not _valid_quantity_submit_input(plan):
        return SubmitDispatchResult.REJECTED
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
    }, ensure_ascii=False)
    script = CONST_QUANTITY_SUBMIT_SCRIPT.replace(
        "__TICKETS_HUNTER_PLAN_JSON__", plan_json, 1
    )
    raw_result = await tab.evaluate(script)
    try:
        return SubmitDispatchResult(raw_result)
    except (TypeError, ValueError):
        raise RuntimeError("quantity submit dispatch outcome is unknown")
