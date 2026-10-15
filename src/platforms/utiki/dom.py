"""DOM snapshots and atomic commits for the uTiki2 ticketing family."""

import json
import re
from enum import Enum

import util

from .selection import AreaActionPlan, AreaSnapshot, parse_area_snapshot


class CommitResult(Enum):
    COMMITTED = "committed"
    REJECTED = "rejected"
    STALE = "stale"


def _decode_json_result(raw_value):
    if isinstance(raw_value, str):
        try:
            return json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return util.parse_nodriver_result(raw_value)


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
