# encoding=utf-8
"""Existing uTiki date/area candidate selection and fast prefetch."""

import json
import re
import time

import util
from . import navigation
from .dom import read_area_snapshot, read_date_snapshot
from .profiles import get_utiki_tenant_profile
from .selection import (
    AreaSnapshot,
    DateSnapshot,
    select_area_snapshot as _select_utiki_area_snapshot,
    select_date_snapshot,
)

CTBC_FAST_PATH_TTL_SECONDS = 1.5


async def _ctbc_prefetch_area_snapshot(tab, date_target):
    """以目前瀏覽器工作階段預取區域頁 HTML 與精確地圖。"""
    area_url_json = json.dumps(date_target["url"])
    performance_json = json.dumps(date_target["performance_id"])
    product_json = json.dumps(date_target["product_id"])
    map_namespace_json = json.dumps(date_target["map_namespace"])
    raw_value = await tab.evaluate(
        f"""
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
    """,
        await_promise=True,
    )
    return navigation._decode_utiki_json_result(raw_value)


async def _ctbc_try_fast_area_navigation(
    tab,
    config_dict,
    date_snapshot,
    date_plan,
    *,
    identity_for_tab,
    runtime_for_identity,
    behavior_for_key,
):
    """預取區域候選，未送出時沿原條件回到 native 流程。"""
    debug = util.create_debug_logger(config_dict)
    identity = identity_for_tab(tab)
    transition = runtime_for_identity(identity)["transition"]
    if transition["pending_navigation"]:
        return True
    date_target = navigation._ctbc_date_target_payload(date_plan)
    if not date_target or not date_plan.fast_area_prefetch_eligible:
        debug.log("[UTK SPORTS] Native date button click required")
        return False
    started = time.monotonic()
    try:
        snapshot = await _ctbc_prefetch_area_snapshot(tab, date_target)
    except Exception as exc:
        debug.log(f"[CTBC FAST] Prefetch failed: {exc}")
        return False
    if not isinstance(snapshot, dict) or not snapshot.get("ok"):
        reason = (
            snapshot.get("reason", "invalid-result")
            if isinstance(snapshot, dict)
            else "invalid-result"
        )
        debug.log(f"[CTBC FAST] Native area-page fallback: {reason}")
        return False
    if time.monotonic() - started > CTBC_FAST_PATH_TTL_SECONDS:
        debug.log("[CTBC FAST] Native area-page fallback: snapshot expired")
        return False
    target = _select_utiki_area_snapshot(
        snapshot,
        config_dict,
        date_target["performance_id"],
        require_direct_destination=True,
    )
    if not target or not target.direct_destination:
        debug.log("[CTBC FAST] Native area-page fallback: no attested area matched")
        return False
    return await navigation._ctbc_dispatch_fast_area_navigation(
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
        runtime_for_identity=runtime_for_identity,
        behavior_for_key=behavior_for_key,
    )


async def nodriver_utiki_date_auto_select(
    tab, config_dict, *, identity_for_tab, runtime_for_identity,
    behavior_for_key, reload_with_interval, cancel_refresh,
):
    """依設定自動選擇已支援 uTiki 租戶的售票場次。"""

    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    if navigation._get_utiki_page_type(current_url) != "date":
        return False
    identity = identity_for_tab(tab)
    runtime = runtime_for_identity(identity)
    if not await navigation._utiki_reconcile_navigation_source(
        tab, current_url, "date", config_dict,
        identity=identity, runtime=runtime,
        runtime_for_identity=runtime_for_identity,
        reload_with_interval=reload_with_interval,
    ):
        return False
    seat_selection_mode = behavior_for_key(
        config_dict, "seat_selection_mode", "manual"
    )
    if seat_selection_mode == "computer":
        profile = get_utiki_tenant_profile(current_url)
        if not profile or not profile.supports_server_assignment:
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
            debug.log(f"[UTIKI DATE] Failed to inspect date table: {exc}")
            snapshot = None
        if isinstance(snapshot, DateSnapshot) and snapshot.ok and snapshot.rows:
            break
        await tab.sleep(0.2)
    if not isinstance(snapshot, DateSnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason if isinstance(snapshot, DateSnapshot) else "invalid-result"
        )
        if (
            navigation._get_utiki_page_type(str(getattr(tab.target, "url", "")))
            != "date"
        ):
            return False
        await reload_with_interval(
            tab, config_dict, f"date table was not ready ({reason})"
        )
        return False
    if (
        snapshot.page_url != current_url
        or navigation._get_utiki_page_type(snapshot.page_url) != "date"
    ):
        return False

    plan = select_date_snapshot(snapshot, config_dict)
    if plan is None:
        await reload_with_interval(
            tab, config_dict, "no purchasable date matched"
        )
        return False

    await cancel_refresh(tab, "purchasable date found")
    try:
        if (
            seat_selection_mode == "manual"
            and plan.action_kind == "normal"
            and config_dict.get("area_auto_select", {}).get("enable", False)
        ):
            if await _ctbc_try_fast_area_navigation(
                tab, config_dict, snapshot, plan,
                identity_for_tab=identity_for_tab,
                runtime_for_identity=runtime_for_identity,
                behavior_for_key=behavior_for_key,
            ):
                return True
        identity = identity_for_tab(tab)
        return await navigation._utiki_commit_native_date(
            tab, snapshot, plan, debug, identity=identity,
            transition=runtime_for_identity(identity)["transition"],
            runtime_for_identity=runtime_for_identity,
        )
    except Exception as exc:
        debug.log(f"[UTIKI DATE] Click failed: {exc}")
        return False


async def nodriver_utiki_area_auto_select(
    tab, config_dict, *, identity_for_tab, runtime_for_identity,
    behavior_for_key, reload_with_interval, cancel_refresh,
):
    """依設定自動選擇已支援 uTiki 租戶的售票區域。"""

    debug = util.create_debug_logger(config_dict)
    current_url = str(getattr(getattr(tab, "target", None), "url", ""))
    identity = identity_for_tab(tab)
    runtime = runtime_for_identity(identity)
    if not await navigation._utiki_reconcile_area_navigation(
        tab, current_url, config_dict, identity=identity, runtime=runtime,
        runtime_for_identity=runtime_for_identity,
        reload_with_interval=reload_with_interval,
    ):
        return False
    seat_selection_mode = behavior_for_key(
        config_dict, "seat_selection_mode", "manual"
    )
    if seat_selection_mode == "computer":
        profile = get_utiki_tenant_profile(current_url)
        if not profile or not profile.supports_server_assignment:
            debug.log(
                "[UTK SPORTS] Website computer assignment is not enabled "
                "for this platform"
            )
            return False
    try:
        snapshot = await read_area_snapshot(tab)
    except Exception as exc:
        debug.log(f"[UTIKI AREA] Action-ready wait failed: {exc}")
        snapshot = None
    if not isinstance(snapshot, AreaSnapshot) or not snapshot.ok:
        reason = (
            snapshot.reason if isinstance(snapshot, AreaSnapshot) else "invalid-result"
        )
        await reload_with_interval(
            tab, config_dict, f"area map was not ready ({reason})"
        )
        return False

    page_url = snapshot.page_url
    query = navigation._utiki_unique_query(page_url)
    performance_id = query.get("PERFORMANCE_ID", "") if query else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", performance_id):
        return False
    target = _select_utiki_area_snapshot(
        snapshot, config_dict, performance_id, require_direct_destination=False
    )
    if target is None:
        await reload_with_interval(
            tab, config_dict, "no available area matched"
        )
        return False

    await cancel_refresh(tab, "available area found")
    identity = identity_for_tab(tab)
    return await navigation._utiki_commit_native_area(
        tab, config_dict, snapshot, target, seat_selection_mode, debug,
        identity=identity,
        transition=runtime_for_identity(identity)["transition"],
        runtime_for_identity=runtime_for_identity,
        behavior_for_key=behavior_for_key,
    )
