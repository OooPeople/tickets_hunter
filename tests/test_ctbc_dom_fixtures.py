import asyncio
import json
import os
import tempfile
import threading
import types
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import zendriver as uc

from tests.ctbc_support import ctbc_identity_runtime, ctbc_tab_runtime, config

from platforms.utiki.dom import (
    CommitResult,
    SubmitDispatchResult,
    commit_area,
    commit_date_action,
    commit_server_assigned_area,
    commit_quantity,
    commit_seat,
    dismiss_dialog,
    dispatch_submit,
    read_area_snapshot,
    read_date_snapshot,
    read_dialog_snapshot,
    read_quantity_snapshot,
    read_seat_snapshot,
)
from platforms.utiki.selection import (
    build_submit_plan,
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    select_area_snapshot,
    select_date_snapshot,
    select_quantity_snapshot,
    seat_selection_is_valid,
    select_seat_snapshot,
)
from platforms.utiki.profiles import PageKind
from platforms.utiki.workflow import (
    CTBC_SEAT_STALE_RELOAD_REASON,
    _ctbc_captcha_and_submit,
    _ctbc_delete_fast_navigation_marker,
    _ctbc_login_fingerprint,
    _ctbc_purchase_state_key,
    _ctbc_read_destination_state,
    _ctbc_read_fast_navigation_marker,
    _ctbc_tab_identity,
    _dismiss_ctbc_dialog,
    _state,
    nodriver_ctbc_general_admission_main,
)


pytestmark = pytest.mark.browser

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "ctbc"


def test_production_dom_contracts_in_local_browser():
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_run_browser_fixtures())


async def _run_browser_fixtures():
    browser = None
    profile = tempfile.TemporaryDirectory(
        prefix="tickets-hunter-ctbc-fixture-"
    )
    _clear_ctbc_test_state()
    try:
        browser = await uc.start(
            user_data_dir=profile.name,
            headless=True,
            browser_args=[
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        )
        for assertion in (
            _assert_date_fixture,
            _assert_area_fixture,
            _assert_seat_fixture,
            _assert_quantity_fixture,
            _assert_server_assigned_quantity_fixture,
            _assert_dialog_fixture,
            _assert_fast_marker_fixture,
        ):
            if assertion is _assert_seat_fixture:
                seat_path = str(
                    (FIXTURE_ROOT / "UTK0205_seat.html").resolve()
                )
                fixture_routes = tuple(
                    (origin, seat_path) for origin in ("file://", "null")
                )
                with (
                    patch(
                        "platforms.utiki.dom.classify_utiki_page",
                        return_value=PageKind.RESERVED_SEAT,
                    ),
                    patch(
                        "platforms.utiki.selection.classify_utiki_page",
                        return_value=PageKind.RESERVED_SEAT,
                    ),
                    patch(
                        "platforms.utiki.dom.CONST_RESERVED_SEAT_ROUTES",
                        fixture_routes,
                    ),
                ):
                    await assertion(browser)
            elif assertion is _assert_quantity_fixture:
                general_paths = tuple(
                    str((FIXTURE_ROOT / filename).resolve())
                    for filename in (
                        "UTK0202_quantity.html",
                        "UTK0203_quantity.html",
                    )
                )
                fixture_routes = tuple(
                    (origin, path)
                    for origin in ("file://", "null")
                    for path in general_paths
                )
                with (
                    patch(
                        "platforms.utiki.dom.classify_utiki_page",
                        return_value=PageKind.GENERAL_ADMISSION,
                    ),
                    patch(
                        "platforms.utiki.selection.classify_utiki_page",
                        return_value=PageKind.GENERAL_ADMISSION,
                    ),
                    patch(
                        "platforms.utiki.dom.CONST_GENERAL_ADMISSION_ROUTES",
                        fixture_routes,
                    ),
                ):
                    await assertion(browser)
            elif assertion is _assert_server_assigned_quantity_fixture:
                with patch(
                    "platforms.utiki.selection.classify_utiki_page",
                    return_value=PageKind.SERVER_ASSIGNED_QUANTITY,
                ):
                    await assertion(browser)
            else:
                await assertion(browser)
            _clear_ctbc_test_state()
    finally:
        try:
            if browser is not None:
                await browser.stop()
        finally:
            profile.cleanup()
            _clear_ctbc_test_state()


def _clear_ctbc_test_state():
    for bucket in _state.values():
        if isinstance(bucket, (dict, list, set)):
            bucket.clear()


async def _open_fixture(browser, filename, query=""):
    fixture_url = (FIXTURE_ROOT / filename).resolve().as_uri() + query
    tab = await browser.get(fixture_url)
    for _ in range(50):
        if await tab.evaluate("document.readyState === 'complete'"):
            actual_url = await tab.evaluate("location.href")
            assert actual_url == str(tab.target.url)
            return tab
        await tab.sleep(0.02)
    raise AssertionError(f"fixture did not finish loading: {filename}")


async def _open_fixture_url(browser, url):
    tab = await browser.get(url)
    for _ in range(50):
        if await tab.evaluate("document.readyState === 'complete'"):
            return tab
        await tab.sleep(0.02)
    raise AssertionError(f"fixture did not finish loading: {url}")


async def _assert_date_fixture(browser):
    async def open_snapshot():
        tab = await _open_fixture(
            browser,
            "UTK0201_date.html",
            "?PRODUCT_ID=PRODUCT_FIXTURE",
        )
        with patch(
            "platforms.utiki.dom.classify_utiki_page",
            return_value=PageKind.DATE,
        ):
            snapshot = await read_date_snapshot(tab)
        assert snapshot.ok
        return tab, snapshot

    def plan_for(snapshot):
        with patch(
            "platforms.utiki.selection.classify_utiki_page",
            return_value=PageKind.DATE,
        ):
            plan = select_date_snapshot(
                snapshot, config(date_keyword='"9/10"')
            )
        assert plan is not None
        return plan

    async def commit(tab, snapshot, plan, nonce):
        with (
            patch(
                "platforms.utiki.dom.classify_utiki_page",
                return_value=PageKind.DATE,
            ),
            patch(
                "platforms.utiki.selection.classify_utiki_page",
                return_value=PageKind.DATE,
            ),
        ):
            return await commit_date_action(tab, snapshot, plan, nonce)

    tab, snapshot = await open_snapshot()
    assert snapshot.document_id
    assert len(snapshot.rows) == 2
    assert snapshot.rows[0][1][0][0]
    assert not snapshot.rows[1][1][0][0]
    assert await tab.evaluate("Number(document.body.dataset.dateClicks || 0)") == 0

    plan = plan_for(snapshot)
    nonce = "d" * 32
    assert await commit(tab, snapshot, plan, nonce) is CommitResult.COMMITTED
    assert await tab.evaluate("document.body.dataset.selectedDate || ''") == "DATE_FRONT"
    assert await tab.evaluate("Number(document.body.dataset.dateClicks || 0)") == 1
    assert await commit(tab, snapshot, plan, nonce) is CommitResult.COMMITTED
    assert await tab.evaluate("Number(document.body.dataset.dateClicks || 0)") == 1
    assert await commit(
        tab, snapshot, plan, "e" * 32
    ) is CommitResult.STALE
    assert await tab.evaluate("Number(document.body.dataset.dateClicks || 0)") == 1

    url_tab, url_snapshot = await open_snapshot()
    await url_tab.evaluate(
        "history.replaceState({}, '', location.pathname + '?changed=1')"
    )
    assert await commit(
        url_tab, url_snapshot, plan_for(url_snapshot), "1" * 32
    ) is CommitResult.STALE
    assert await url_tab.evaluate(
        "Number(document.body.dataset.dateClicks || 0)"
    ) == 0

    document_tab, document_snapshot = await open_snapshot()
    await document_tab.sleep(0.05)
    await document_tab.reload()
    for _ in range(50):
        if await document_tab.evaluate("document.readyState === 'complete'"):
            break
        await document_tab.sleep(0.02)
    assert await commit(
        document_tab,
        document_snapshot,
        plan_for(document_snapshot),
        "2" * 32,
    ) is CommitResult.STALE
    assert await document_tab.evaluate(
        "Number(document.body.dataset.dateClicks || 0)"
    ) == 0

    row_tab, row_snapshot = await open_snapshot()
    await row_tab.evaluate(
        "document.querySelector('#PerformanceListTable tr td').textContent = 'changed'"
    )
    assert await commit(
        row_tab, row_snapshot, plan_for(row_snapshot), "3" * 32
    ) is CommitResult.STALE
    assert await row_tab.evaluate(
        "Number(document.body.dataset.dateClicks || 0)"
    ) == 0

    ambiguous_tab, ambiguous_snapshot = await open_snapshot()
    await ambiguous_tab.evaluate('''
        (() => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('aria-label', 'duplicate');
            document.querySelector('#PerformanceListTable tr td:last-child')
                .append(button);
        })()
    ''')
    assert await commit(
        ambiguous_tab,
        ambiguous_snapshot,
        plan_for(ambiguous_snapshot),
        "4" * 32,
    ) is CommitResult.REJECTED
    assert await ambiguous_tab.evaluate(
        "Number(document.body.dataset.dateClicks || 0)"
    ) == 0


async def _assert_area_fixture(browser):
    tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    cfg = config(area_keyword='"109區前排"')
    snapshot = await read_area_snapshot(tab)
    assert snapshot.ok
    assert snapshot.document_id
    assert await tab.evaluate("Number(document.body.dataset.areaClicks || 0)") == 0
    target = select_area_snapshot(
        snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    assert target.rel == "front"

    result = await commit_area(
        tab, snapshot, target, cfg["ticket_number"], "f" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    )
    assert result is CommitResult.COMMITTED
    assert await tab.evaluate("document.body.dataset.selectedArea || ''") == "PRICE_FRONT"
    assert await tab.evaluate("Number(document.body.dataset.areaClicks || 0)") == 1
    assert await commit_area(
        tab, snapshot, target, cfg["ticket_number"], "f" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.COMMITTED
    assert await tab.evaluate("Number(document.body.dataset.areaClicks || 0)") == 1

    stale_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    stale_snapshot = await read_area_snapshot(stale_tab)
    stale_target = select_area_snapshot(
        stale_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    stale_snapshot = replace(
        stale_snapshot, document_id=f"{stale_snapshot.document_id}-old"
    )
    assert await commit_area(
        stale_tab, stale_snapshot, stale_target, cfg["ticket_number"],
        "e" * 32, "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.STALE
    assert await stale_tab.evaluate(
        "Number(document.body.dataset.areaClicks || 0)"
    ) == 0

    changed_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    changed_snapshot = await read_area_snapshot(changed_tab)
    changed_target = select_area_snapshot(
        changed_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await changed_tab.evaluate("""
        document.querySelector('#mapdata area#front').setAttribute(
            'href',
            "javascript:Send('0205','OTHER','PRICE_FRONT','GROUP_FRONT','45')"
        )
    """)
    assert await commit_area(
        changed_tab, changed_snapshot, changed_target, cfg["ticket_number"],
        "d" * 32, "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await changed_tab.evaluate(
        "Number(document.body.dataset.areaClicks || 0)"
    ) == 0

    availability_mutations = (
        "document.querySelector('tr.saleTr[rel=\"front\"] "
        "td[data-title^=\"空位\"]').textContent = '1'",
        "document.querySelector('#mapdata area#front').setAttribute("
        "'title', '票區:109區前排 票價：1600 尚餘：1')",
        "document.querySelector('#mapdata area#front').setAttribute("
        "'href', \"javascript:Send('0205','P16D0B8Z','PRICE_FRONT',"
        "'GROUP_FRONT','1')\")",
    )
    for index, mutation in enumerate(availability_mutations):
        unavailable_tab = await _open_fixture(
            browser,
            "UTK0204_area.html",
            "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
        )
        unavailable_snapshot = await read_area_snapshot(unavailable_tab)
        unavailable_target = select_area_snapshot(
            unavailable_snapshot, cfg, "P16D0B8Z",
            require_direct_destination=False,
        )
        await unavailable_tab.evaluate(mutation)
        assert await commit_area(
            unavailable_tab, unavailable_snapshot, unavailable_target,
            cfg["ticket_number"], f"{index + 1:x}" * 32,
            "ticketsHunter.ctbc.fast-navigation.v1",
        ) is CommitResult.REJECTED
        assert await unavailable_tab.evaluate(
            "Number(document.body.dataset.areaClicks || 0)"
        ) == 0

    marker_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    marker_snapshot = await read_area_snapshot(marker_tab)
    marker_target = select_area_snapshot(
        marker_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    marker_raw = '{"nonce":"racing-fast-marker"}'
    await marker_tab.evaluate(
        "sessionStorage.setItem("
        "'ticketsHunter.ctbc.fast-navigation.v1', "
        f"{marker_raw!r})"
    )
    assert await commit_area(
        marker_tab, marker_snapshot, marker_target, cfg["ticket_number"],
        "a" * 32, "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.STALE
    assert await marker_tab.evaluate(
        "sessionStorage.getItem('ticketsHunter.ctbc.fast-navigation.v1')"
    ) == marker_raw
    assert await marker_tab.evaluate(
        "Number(document.body.dataset.areaClicks || 0)"
    ) == 0

    server_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    server_snapshot = await read_area_snapshot(server_tab)
    server_target = select_area_snapshot(
        server_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await server_tab.evaluate(
        "sessionStorage.removeItem("
        "'ticketsHunter.ctbc.fast-navigation.v1')"
    )
    assert await commit_server_assigned_area(
        server_tab, server_snapshot, server_target, cfg["ticket_number"],
        "c" * 32, "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.COMMITTED
    assert await server_tab.evaluate(
        "document.querySelector('#byServer').classList.contains('active')"
    )
    assert not await server_tab.evaluate(
        "document.querySelector('#byhend').classList.contains('active')"
    )
    assert not await server_tab.evaluate(
        "document.querySelector('#ATYPE_CB').checked"
    )
    assert await server_tab.evaluate(
        "Number(document.body.dataset.serverModeClicks || 0)"
    ) == 1
    assert await server_tab.evaluate(
        "Number(document.body.dataset.checkboxClicks || 0)"
    ) == 1
    assert await server_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 1
    assert await server_tab.evaluate(
        "document.body.dataset.selectedArea || ''"
    ) == "PRICE_FRONT"
    assert await commit_server_assigned_area(
        server_tab, server_snapshot, server_target, cfg["ticket_number"],
        "c" * 32, "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.COMMITTED
    assert await server_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 1

    ambiguous_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    ambiguous_snapshot = await read_area_snapshot(ambiguous_tab)
    ambiguous_target = select_area_snapshot(
        ambiguous_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await ambiguous_tab.evaluate(
        "document.querySelector('#byServer').classList.add('active')"
    )
    assert await commit_server_assigned_area(
        ambiguous_tab, ambiguous_snapshot, ambiguous_target,
        cfg["ticket_number"], "9" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await ambiguous_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0

    post_click_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    post_click_snapshot = await read_area_snapshot(post_click_tab)
    post_click_target = select_area_snapshot(
        post_click_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await post_click_tab.evaluate("""
        (() => {
            const oldButton = document.querySelector('#byServer');
            const button = oldButton.cloneNode(true);
            oldButton.replaceWith(button);
            button.addEventListener('click', () => {
                button.classList.add('active');
                document.body.dataset.serverModeClicks = String(
                    Number(document.body.dataset.serverModeClicks || 0) + 1
                );
            });
        })()
    """)
    assert await commit_server_assigned_area(
        post_click_tab, post_click_snapshot, post_click_target,
        cfg["ticket_number"], "8" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await post_click_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0
    assert await commit_server_assigned_area(
        post_click_tab, post_click_snapshot, post_click_target,
        cfg["ticket_number"], "5" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await post_click_tab.evaluate(
        "Number(document.body.dataset.serverModeClicks || 0)"
    ) == 1

    stale_row_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    stale_row_snapshot = await read_area_snapshot(stale_row_tab)
    stale_row_target = select_area_snapshot(
        stale_row_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await stale_row_tab.evaluate("""
        (() => {
            const oldButton = document.querySelector('#byServer');
            const button = oldButton.cloneNode(true);
            oldButton.replaceWith(button);
            button.addEventListener('click', () => {
                button.classList.add('active');
                document.querySelector('#byhend').classList.remove('active');
                document.querySelector(
                    'tr.saleTr[rel="front"] td[data-title^="空位"]'
                ).textContent = '0';
            });
        })()
    """)
    assert await commit_server_assigned_area(
        stale_row_tab, stale_row_snapshot, stale_row_target,
        cfg["ticket_number"], "7" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await stale_row_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0

    stale_action_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    stale_action_snapshot = await read_area_snapshot(stale_action_tab)
    stale_action_target = select_area_snapshot(
        stale_action_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await stale_action_tab.evaluate("""
        document.querySelector('tr.saleTr[rel="front"]').setAttribute(
            'onclick',
            "Send('0205','P16D0B8Z','PRICE_BACK','GROUP_BACK','45')"
        )
    """)
    assert await commit_server_assigned_area(
        stale_action_tab, stale_action_snapshot, stale_action_target,
        cfg["ticket_number"], "4" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await stale_action_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0

    checkbox_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    checkbox_snapshot = await read_area_snapshot(checkbox_tab)
    checkbox_target = select_area_snapshot(
        checkbox_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await checkbox_tab.evaluate("""
        document.querySelector('#ATYPE_CB').addEventListener(
            'click', event => event.preventDefault())
    """)
    assert await commit_server_assigned_area(
        checkbox_tab, checkbox_snapshot, checkbox_target,
        cfg["ticket_number"], "3" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await commit_server_assigned_area(
        checkbox_tab, checkbox_snapshot, checkbox_target,
        cfg["ticket_number"], "2" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await checkbox_tab.evaluate(
        "Number(document.body.dataset.checkboxClicks || 0)"
    ) == 1
    assert await checkbox_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0

    rerender_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    rerender_snapshot = await read_area_snapshot(rerender_tab)
    rerender_target = select_area_snapshot(
        rerender_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await rerender_tab.evaluate("""
        (() => {
            const oldButton = document.querySelector('#byServer');
            const button = oldButton.cloneNode(true);
            oldButton.replaceWith(button);
            button.addEventListener('click', () => {
                button.classList.add('active');
                document.querySelector('#byhend').classList.remove('active');
                document.body.dataset.serverModeClicks = String(
                    Number(document.body.dataset.serverModeClicks || 0) + 1
                );
                window.fixtureCheckboxRoot =
                    document.querySelector('#ABdiv_ATYPE_CB');
                window.fixtureCheckboxRoot.remove();
            });
        })()
    """)
    assert await commit_server_assigned_area(
        rerender_tab, rerender_snapshot, rerender_target,
        cfg["ticket_number"], "1" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    await rerender_tab.evaluate("""
        document.querySelector('#SeatOptions').append(
            window.fixtureCheckboxRoot)
    """)
    assert await commit_server_assigned_area(
        rerender_tab, rerender_snapshot, rerender_target,
        cfg["ticket_number"], "0" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.COMMITTED
    assert await rerender_tab.evaluate(
        "Number(document.body.dataset.serverModeClicks || 0)"
    ) == 1
    assert await rerender_tab.evaluate(
        "Number(document.body.dataset.checkboxClicks || 0)"
    ) == 1
    assert await rerender_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 1

    duplicate_tab = await _open_fixture(
        browser,
        "UTK0204_area.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    duplicate_snapshot = await read_area_snapshot(duplicate_tab)
    duplicate_target = select_area_snapshot(
        duplicate_snapshot, cfg, "P16D0B8Z", require_direct_destination=False
    )
    await duplicate_tab.evaluate("""
        document.querySelector('#SeatOptions').append(
            document.querySelector('#byServer').cloneNode(true))
    """)
    assert await commit_server_assigned_area(
        duplicate_tab, duplicate_snapshot, duplicate_target,
        cfg["ticket_number"], "6" * 32,
        "ticketsHunter.ctbc.fast-navigation.v1",
    ) is CommitResult.REJECTED
    assert await duplicate_tab.evaluate(
        "Number(document.body.dataset.rowClicks || 0)"
    ) == 0

    unknown_availability_mutations = (
        "document.querySelector('tr.saleTr[rel=\"front\"] "
        "td[data-title^=\"空位\"]').textContent = '暫停銷售'",
        "document.querySelector('#mapdata area#front').setAttribute("
        "'title', '票區:109區前排 票價：1600 尚餘：暫停銷售')",
        "document.querySelector('#mapdata area#front').setAttribute("
        "'href', \"javascript:Send('0205','P16D0B8Z','PRICE_FRONT',"
        "'GROUP_FRONT','暫停銷售')\")",
        "document.querySelector('tr.saleTr[rel=\"front\"]').setAttribute("
        "'onclick', \"Send('0205','P16D0B8Z','PRICE_FRONT',"
        "'GROUP_FRONT','暫停銷售')\")",
        "document.querySelector('tr.saleTr[rel=\"front\"] "
        "td[data-title^=\"空位\"]').textContent = "
        "'暫停銷售，剩餘2筆資料待同步'",
    )
    for index, mutation in enumerate(unknown_availability_mutations):
        unknown_tab = await _open_fixture(
            browser,
            "UTK0204_area.html",
            "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
        )
        unknown_snapshot = await read_area_snapshot(unknown_tab)
        unknown_target = select_area_snapshot(
            unknown_snapshot, cfg, "P16D0B8Z",
            require_direct_destination=False,
        )
        await unknown_tab.evaluate(mutation)
        assert await commit_server_assigned_area(
            unknown_tab, unknown_snapshot, unknown_target,
            cfg["ticket_number"], f"{index + 10:x}" * 32,
            "ticketsHunter.ctbc.fast-navigation.v1",
        ) is CommitResult.REJECTED
        assert await unknown_tab.evaluate(
            "Number(document.body.dataset.rowClicks || 0)"
        ) == 0


async def _assert_seat_fixture(browser):
    query = (
        "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
        "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
    )
    cfg = config()

    async def read_plan(tab):
        snapshot = await read_seat_snapshot(tab)
        plan, reason = select_seat_snapshot(
            snapshot,
            cfg["utiki"]["ticket_type_keyword"],
            cfg["ticket_number"],
            cfg["advanced"]["disable_adjacent_seat"],
            cfg["utiki"]["seat_sorting"],
        )
        assert reason == ""
        assert plan is not None
        return snapshot, plan

    async def click_counts(tab):
        return await tab.evaluate('''({
            ticketType: Number(document.body.dataset.ticketTypeClicks || 0),
            seats: Number(document.body.dataset.seatClicks || 0)
        })''')

    async def addcart_clicks(tab):
        return await tab.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        )

    async def open_committed_submit():
        submit_tab = await _open_fixture(
            browser, "UTK0205_seat.html", query
        )
        submit_snapshot, selection_plan = await read_plan(submit_tab)
        assert await commit_seat(
            submit_tab, submit_snapshot, selection_plan
        ) is CommitResult.COMMITTED
        submit_plan = build_submit_plan(selection_plan)
        assert submit_plan is not None
        return submit_tab, submit_plan

    tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    before_html = await tab.evaluate("document.body.innerHTML")
    snapshot = await read_seat_snapshot(tab)
    repeated = await read_seat_snapshot(tab)
    assert snapshot == repeated
    assert snapshot.ok
    assert snapshot.page_url == await tab.evaluate("location.href")
    assert await tab.evaluate("document.body.innerHTML") == before_html
    assert await click_counts(tab) == {"ticketType": 0, "seats": 0}
    plan, reason = select_seat_snapshot(
        snapshot,
        cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
        cfg["advanced"]["disable_adjacent_seat"],
        cfg["utiki"]["seat_sorting"],
    )
    assert reason == ""
    assert plan.action == "select"
    assert await commit_seat(tab, snapshot, plan) is CommitResult.COMMITTED
    assert await click_counts(tab) == {"ticketType": 1, "seats": 2}
    committed_snapshot = await read_seat_snapshot(tab)
    assert seat_selection_is_valid(
        committed_snapshot,
        plan.ticket_number,
        plan.ticket_type_id,
        plan.ticket_type_name,
    )
    submit_plan = build_submit_plan(plan)
    assert submit_plan is not None
    assert await dispatch_submit(
        tab, submit_plan
    ) is SubmitDispatchResult.DISPATCHED
    assert await addcart_clicks(tab) == 1
    ready_plan, reason = select_seat_snapshot(
        committed_snapshot,
        cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
        cfg["advanced"]["disable_adjacent_seat"],
        cfg["utiki"]["seat_sorting"],
    )
    assert reason == ""
    assert ready_plan.action == "ready"
    assert await commit_seat(
        tab, committed_snapshot, ready_plan
    ) is CommitResult.COMMITTED
    assert await click_counts(tab) == {"ticketType": 1, "seats": 2}
    selected = await tab.evaluate(
        "Array.from(document.querySelectorAll('#TBL td[title]'))"
        ".filter(seat => seat.dataset.selected === '1')"
        ".map(seat => seat.title)"
    )
    assert selected == ["A區-1排-1號", "A區-1排-2號"]
    assert await commit_seat(tab, snapshot, plan) is CommitResult.COMMITTED
    assert await click_counts(tab) == {"ticketType": 1, "seats": 2}

    unlimited_tab = await _open_fixture(
        browser, "UTK0205_seat.html", query
    )
    await unlimited_tab.evaluate(
        "document.querySelector('#QUANTITY_LIMIT').value = '0'"
    )
    unlimited_snapshot, unlimited_plan = await read_plan(unlimited_tab)
    assert unlimited_snapshot.quantity_limit_raw == "0"
    assert unlimited_plan.action == "select"
    assert await commit_seat(
        unlimited_tab, unlimited_snapshot, unlimited_plan
    ) is CommitResult.COMMITTED
    unlimited_submit = build_submit_plan(unlimited_plan)
    assert unlimited_submit is not None
    assert await dispatch_submit(
        unlimited_tab, unlimited_submit
    ) is SubmitDispatchResult.DISPATCHED
    assert await addcart_clicks(unlimited_tab) == 1

    nonadjacent_tab = await _open_fixture(
        browser, "UTK0205_seat.html", query
    )
    await nonadjacent_tab.evaluate('''(() => {
        document.querySelector('#TBL td[data-seat-id="S1"]').click();
        document.querySelector('#TBL td[data-seat-id="S2"]').click();
        document.querySelector(
            '#TBL td[data-seat-id="S2"]'
        ).style.transform = 'translateX(120px)';
    })()''')
    nonadjacent_snapshot, nonadjacent_plan = await read_plan(nonadjacent_tab)
    assert seat_selection_is_valid(
        nonadjacent_snapshot,
        nonadjacent_plan.ticket_number,
        nonadjacent_plan.ticket_type_id,
        nonadjacent_plan.ticket_type_name,
    )
    assert nonadjacent_plan.action == "clear"
    assert nonadjacent_plan.require_physical_adjacency
    assert await commit_seat(
        nonadjacent_tab, nonadjacent_snapshot, nonadjacent_plan
    ) is CommitResult.COMMITTED
    assert await nonadjacent_tab.evaluate(
        "Object.keys(window.seats).length"
    ) == 0
    assert await nonadjacent_tab.evaluate(
        "document.querySelector('#FULL').textContent"
    ) == "0"
    assert not await nonadjacent_tab.evaluate(
        "document.documentElement.hasAttribute("
        "'data-tickets-hunter-utiki-seat-commit')"
    )

    forged_marker_tab = await _open_fixture(
        browser, "UTK0205_seat.html", query
    )
    await forged_marker_tab.evaluate('''(() => {
        document.querySelector('#TBL td[data-seat-id="S1"]').click();
        document.querySelector('#TBL td[data-seat-id="S2"]').click();
    })()''')
    forged_snapshot, forged_plan = await read_plan(forged_marker_tab)
    assert forged_plan.action == "ready"
    assert forged_plan.require_physical_adjacency
    forged_intent = json.dumps([
        "selection",
        forged_plan.ticket_type_fingerprint,
        forged_plan.ticket_type_id,
        forged_plan.ticket_type_name,
        forged_plan.ticket_number,
        forged_plan.selected_seat_fingerprints,
        False,
    ], ensure_ascii=False)
    await forged_marker_tab.evaluate(
        "document.documentElement.setAttribute("
        "'data-tickets-hunter-utiki-seat-commit', "
        f"{json.dumps(forged_intent)})"
    )
    assert await commit_seat(
        forged_marker_tab, forged_snapshot, forged_plan
    ) is CommitResult.STALE
    assert await addcart_clicks(forged_marker_tab) == 0

    clear_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    await clear_tab.evaluate(
        "document.querySelector('#TBL td[data-seat-id=\"S1\"]').click()"
    )
    clear_snapshot, clear_plan = await read_plan(clear_tab)
    assert clear_plan.action == "clear"
    assert await commit_seat(
        clear_tab, clear_snapshot, clear_plan
    ) is CommitResult.COMMITTED
    assert await click_counts(clear_tab) == {"ticketType": 0, "seats": 2}
    assert await clear_tab.evaluate("Object.keys(window.seats).length") == 0
    assert not await clear_tab.evaluate(
        "document.documentElement.hasAttribute("
        "'data-tickets-hunter-utiki-seat-commit')"
    )
    clean_snapshot, clean_plan = await read_plan(clear_tab)
    assert clean_plan.action == "select"
    assert await commit_seat(
        clear_tab, clean_snapshot, clean_plan
    ) is CommitResult.COMMITTED
    assert await click_counts(clear_tab) == {"ticketType": 1, "seats": 4}
    assert seat_selection_is_valid(
        await read_seat_snapshot(clear_tab),
        clean_plan.ticket_number,
        clean_plan.ticket_type_id,
        clean_plan.ticket_type_name,
    )

    route_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    route_snapshot, route_plan = await read_plan(route_tab)
    await route_tab.evaluate('''(() => {
        const url = new URL(location.href);
        url.searchParams.set('PERFORMANCE_ID', 'OTHER');
        history.replaceState({}, '', url.href);
    })()''')
    assert await commit_seat(
        route_tab, route_snapshot, route_plan
    ) is CommitResult.REJECTED
    assert await click_counts(route_tab) == {"ticketType": 0, "seats": 0}

    document_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    document_snapshot, document_plan = await read_plan(document_tab)
    forged_document = f"{document_snapshot.document_id}-old"
    assert await commit_seat(
        document_tab,
        replace(document_snapshot, document_id=forged_document),
        replace(document_plan, document_id=forged_document),
    ) is CommitResult.REJECTED
    assert await click_counts(document_tab) == {
        "ticketType": 0, "seats": 0,
    }

    identity_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    identity_snapshot, identity_plan = await read_plan(identity_tab)
    await identity_tab.evaluate(
        "document.querySelector('#PRODUCT_ID').value = 'OTHER_PRODUCT'"
    )
    assert await commit_seat(
        identity_tab, identity_snapshot, identity_plan
    ) is CommitResult.REJECTED
    assert await click_counts(identity_tab) == {
        "ticketType": 0, "seats": 0,
    }

    ticket_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    ticket_snapshot, ticket_plan = await read_plan(ticket_tab)
    await ticket_tab.evaluate(
        "document.querySelector('.ticket button').disabled = true"
    )
    assert await commit_seat(
        ticket_tab, ticket_snapshot, ticket_plan
    ) is CommitResult.STALE
    assert await click_counts(ticket_tab) == {
        "ticketType": 0, "seats": 0,
    }

    seat_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    seat_snapshot, seat_plan = await read_plan(seat_tab)
    await seat_tab.evaluate('''document.querySelector(
        '#TBL td[data-seat-id="S1"]'
    ).setAttribute('title', 'A區-9排-9號')''')
    assert await commit_seat(
        seat_tab, seat_snapshot, seat_plan
    ) is CommitResult.REJECTED
    assert await click_counts(seat_tab) == {"ticketType": 0, "seats": 0}

    geometry_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    geometry_snapshot, geometry_plan = await read_plan(geometry_tab)
    assert geometry_plan.require_physical_adjacency
    await geometry_tab.evaluate('''document.querySelector(
        '#TBL td[data-seat-id="S2"]'
    ).style.transform = 'translateX(120px)' ''')
    assert await commit_seat(
        geometry_tab, geometry_snapshot, geometry_plan
    ) in {CommitResult.REJECTED, CommitResult.STALE}
    assert await click_counts(geometry_tab) == {
        "ticketType": 0, "seats": 0,
    }

    race_tab = await _open_fixture(browser, "UTK0205_seat.html", query)
    await race_tab.evaluate('''(() => {
        const first = document.querySelector(
            '#TBL td[data-seat-id="S1"]');
        first.setAttribute('onclick',
            "toggleSeat(this, 'S1');" +
            "const second=document.querySelector('[data-seat-id=\\"S2\\"]');" +
            "second.style.cursor='default';" +
            "second.style.backgroundImage='none';");
    })()''')
    race_snapshot, race_plan = await read_plan(race_tab)
    assert await commit_seat(
        race_tab, race_snapshot, race_plan
    ) is CommitResult.STALE
    assert await click_counts(race_tab) == {"ticketType": 1, "seats": 1}
    race_live = await read_seat_snapshot(race_tab)
    assert not seat_selection_is_valid(
        race_live,
        race_plan.ticket_number,
        race_plan.ticket_type_id,
        race_plan.ticket_type_name,
    )

    negative_mutations = (
        (
            "dialog",
            "document.body.insertAdjacentHTML('beforeend', "
            "'<div id=\"dialog-message\">警告</div>')",
            SubmitDispatchResult.REJECTED,
        ),
        (
            "login",
            "document.body.insertAdjacentHTML('beforeend', "
            "'<input id=\"LOGIN_ID\"><input id=\"LOGIN_PWD\">')",
            SubmitDispatchResult.REJECTED,
        ),
        (
            "captcha",
            "document.querySelector('#CHK').value = ''",
            SubmitDispatchResult.REJECTED,
        ),
        (
            "button",
            "document.querySelector('#addcart').disabled = true",
            SubmitDispatchResult.REJECTED,
        ),
        (
            "seat",
            "document.querySelector('#TBL td[data-seat-id=\"S1\"]').click()",
            SubmitDispatchResult.STALE,
        ),
        (
            "hidden-identity",
            "document.querySelector('#PRODUCT_ID').value = 'OTHER_PRODUCT'",
            SubmitDispatchResult.STALE,
        ),
        (
            "seat-payload",
            "Object.assign(window.seats[Object.keys(window.seats)[0]], "
            "{Y: 'OTHER', Z: '其他票'})",
            SubmitDispatchResult.STALE,
        ),
        (
            "ticket-counter",
            "document.querySelector('#FULL').textContent = '1'",
            SubmitDispatchResult.STALE,
        ),
        (
            "strict-adjacency",
            "document.querySelector("
            "'#TBL td[data-seat-id=\"S2\"]').style.transform = "
            "'translateX(120px)'",
            SubmitDispatchResult.STALE,
        ),
    )
    for label, mutation, expected in negative_mutations:
        invalid_tab, invalid_plan = await open_committed_submit()
        await invalid_tab.evaluate(mutation)
        assert await dispatch_submit(
            invalid_tab, invalid_plan
        ) is expected, label
        assert await addcart_clicks(invalid_tab) == 0, label

    document_tab, document_plan = await open_committed_submit()
    await document_tab.sleep(0.05)
    await document_tab.reload()
    for _ in range(50):
        if await document_tab.evaluate("document.readyState === 'complete'"):
            break
        await document_tab.sleep(0.02)
    assert await dispatch_submit(
        document_tab, document_plan
    ) is SubmitDispatchResult.STALE
    assert await addcart_clicks(document_tab) == 0

    wait_tab, wait_plan = await open_committed_submit()
    await wait_tab.evaluate("document.querySelector('#CHK').value = ''")
    wait_cfg = config()
    wait_cfg["ocr_captcha"].update({
        "enable": True, "force_submit": True,
    })
    dispatch_results = []

    async def mutate_seat_during_ocr(*_args):
        await wait_tab.evaluate(
            "document.querySelector("
            "'#TBL td[data-seat-id=\"S1\"]').click()"
        )
        await wait_tab.evaluate(
            "document.querySelector('#CHK').value = 'ABCD'"
        )
        return True

    async def capture_seat_dispatch(*args):
        result = await dispatch_submit(*args)
        dispatch_results.append(result)
        return result

    with (
        patch(
            "platforms.utiki.workflow._ctbc_captcha",
            AsyncMock(side_effect=mutate_seat_during_ocr),
        ) as captcha_solver,
        patch(
            "platforms.utiki.workflow.dispatch_submit",
            AsyncMock(side_effect=capture_seat_dispatch),
        ),
        patch(
            "platforms.utiki.workflow._ctbc_purchase_state_key",
            return_value=("fixture-seat-wait",),
        ),
        patch(
            "platforms.utiki.workflow._get_ctbc_page_type",
            return_value="seat",
        ),
        patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page,
    ):
        assert not await _ctbc_captcha_and_submit(
            wait_tab, wait_cfg, ocr=None, submit_plan=wait_plan
        )
    captcha_solver.assert_awaited_once()
    assert dispatch_results == [SubmitDispatchResult.STALE]
    reload_page.assert_awaited_once_with(
        wait_tab, wait_cfg, CTBC_SEAT_STALE_RELOAD_REASON
    )
    assert await addcart_clicks(wait_tab) == 0


async def _assert_quantity_fixture(browser):
    query = (
        "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
        "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
    )
    cfg = config()

    destination_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    await destination_tab.evaluate('''(() => {
        const product = document.createElement('input');
        product.id = 'PRODUCT_ID';
        product.value = 'PRODUCT_FIXTURE';
        product.hidden = true;
        document.body.prepend(product);
    })()''')
    destination_url = await destination_tab.evaluate("location.href")
    time_origin = await destination_tab.evaluate('''Number(
        performance.timeOrigin ||
        (performance.timing && performance.timing.navigationStart) || 0
    )''')
    assert isinstance(time_origin, (int, float)) and time_origin > 0
    time_origin_text = (
        str(int(time_origin))
        if float(time_origin).is_integer()
        else str(time_origin)
    )
    destination_state = await _ctbc_read_destination_state(
        destination_tab, "general_admission"
    )
    assert destination_state["ready"]
    assert destination_state["page_url"] == destination_url
    assert destination_state["document_id"] == (
        f"{destination_url}|{time_origin_text}"
    )
    await destination_tab.evaluate('''document.body.appendChild(
        document.querySelector('#PRODUCT_ID').cloneNode(true)
    )''')
    duplicate_destination_state = await _ctbc_read_destination_state(
        destination_tab, "general_admission"
    )
    assert not duplicate_destination_state["ready"]

    tab = await _open_fixture(browser, "UTK0203_quantity.html", query)
    snapshot = await read_quantity_snapshot(tab)
    plan = select_quantity_snapshot(
        snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert plan.target_key == "FULL"
    assert plan.ticket_number == 2
    assert await commit_quantity(
        tab, snapshot, plan
    ) is CommitResult.COMMITTED
    assert await _read_fixture_quantities(tab) == {"FULL": "2", "OTHER": "0"}
    event_count = await tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    )
    assert event_count == 7
    assert await commit_quantity(
        tab, snapshot, plan
    ) is CommitResult.COMMITTED
    assert await tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == event_count
    assert await tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0
    await tab.evaluate(
        "document.querySelector('input.numbox[key=\"OTHER\"]').value = '1'"
    )
    assert await commit_quantity(
        tab, snapshot, plan
    ) is CommitResult.STALE
    assert await tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == event_count
    assert await tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    flow_tab = await _open_fixture(browser, "UTK0203_quantity.html", query)
    flow_cfg = config()
    flow_cfg["ocr_captcha"].update({
        "enable": True, "force_submit": True,
    })
    dispatch_results = []

    async def capture_flow_dispatch(*args):
        result = await dispatch_submit(*args)
        dispatch_results.append(result)
        return result

    with patch(
        "platforms.utiki.workflow._ctbc_attest_fast_destination",
        AsyncMock(return_value=True),
    ), patch(
        "platforms.utiki.workflow._ctbc_purchase_state_key",
        return_value=("fixture-purchase",),
    ), patch(
        "platforms.utiki.workflow._get_ctbc_page_type",
        return_value="general_admission",
    ), patch(
        "platforms.utiki.workflow.dispatch_submit",
        AsyncMock(side_effect=capture_flow_dispatch),
    ), patch(
        "platforms.utiki.workflow._observe_ctbc_submit_outcome",
        AsyncMock(return_value=False),
    ) as observe:
        assert not await nodriver_ctbc_general_admission_main(
            flow_tab, flow_cfg, ocr=None
        )
        flow_event_count = await flow_tab.evaluate(
            "Number(document.body.dataset.quantityEvents || 0)"
        )
        assert not await nodriver_ctbc_general_admission_main(
            flow_tab, flow_cfg, ocr=None
        )
    assert dispatch_results == [SubmitDispatchResult.DISPATCHED]
    observe.assert_awaited_once()
    assert await flow_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == flow_event_count
    assert await flow_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 1
    active_submit = ctbc_tab_runtime(flow_tab)["purchase"]["active_submit"]
    assert active_submit["purchase_key"] == ("fixture-purchase",)
    assert active_submit["phase"] == "awaiting_result"
    assert await _read_fixture_quantities(flow_tab) == {
        "FULL": "2", "OTHER": "0"
    }

    rejected_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    rejected_snapshot = await read_quantity_snapshot(rejected_tab)
    rejected_plan = select_quantity_snapshot(
        rejected_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await rejected_tab.evaluate('''(() => {
        const url = new URL(location.href);
        url.searchParams.set('PERFORMANCE_ID', 'OTHER_PERFORMANCE');
        history.replaceState({}, '', url.href);
    })()''')
    assert await rejected_tab.evaluate("location.href") != (
        rejected_snapshot.page_url
    )
    assert await commit_quantity(
        rejected_tab, rejected_snapshot, rejected_plan
    ) is CommitResult.STALE
    assert await rejected_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0
    assert await rejected_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    for duplicate_kind, mutation in (
        (
            "identity",
            "document.body.appendChild(document.querySelector("
            "'#PERFORMANCE_ID').cloneNode(true))",
        ),
        (
            "product",
            "for (let index = 0; index < 2; index += 1) {"
            " const input = document.createElement('input');"
            " input.id = 'PRODUCT_ID'; input.value = 'PRODUCT_FIXTURE';"
            " input.hidden = true; document.body.appendChild(input); }",
        ),
        (
            "quantity-limit",
            "document.body.appendChild(document.querySelector("
            "'#QUANTITY_LIMIT').cloneNode(true))",
        ),
    ):
        duplicate_tab = await _open_fixture(
            browser, "UTK0203_quantity.html", query
        )
        await duplicate_tab.evaluate(f"(() => {{{mutation}}})()")
        duplicate_snapshot = await read_quantity_snapshot(duplicate_tab)
        assert not duplicate_snapshot.ok, duplicate_kind
        assert select_quantity_snapshot(
            duplicate_snapshot, cfg["utiki"]["ticket_type_keyword"],
            cfg["ticket_number"],
        ) is None, duplicate_kind

    product_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    await product_tab.evaluate('''(() => {
        const input = document.createElement('input');
        input.id = 'PRODUCT_ID';
        input.value = 'PRODUCT_FIXTURE';
        input.hidden = true;
        document.body.appendChild(input);
    })()''')
    product_snapshot = await read_quantity_snapshot(product_tab)
    product_plan = select_quantity_snapshot(
        product_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await product_tab.evaluate(
        "document.querySelector('#PRODUCT_ID').value = 'OTHER_PRODUCT'"
    )
    assert await commit_quantity(
        product_tab, product_snapshot, product_plan
    ) is CommitResult.STALE
    assert await product_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0

    remaining_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    remaining_snapshot = await read_quantity_snapshot(remaining_tab)
    remaining_plan = select_quantity_snapshot(
        remaining_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await remaining_tab.evaluate('''document.querySelector(
        'input.numbox[key="FULL"]'
    ).closest('tr').querySelector('input[key="LAST_AMOUNT"]').remove()''')
    assert await commit_quantity(
        remaining_tab, remaining_snapshot, remaining_plan
    ) is CommitResult.REJECTED
    assert await remaining_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0

    limit_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    limit_snapshot = await read_quantity_snapshot(limit_tab)
    limit_plan = select_quantity_snapshot(
        limit_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await limit_tab.evaluate('''document.body.appendChild(
        document.querySelector('#QUANTITY_LIMIT').cloneNode(true)
    )''')
    assert await commit_quantity(
        limit_tab, limit_snapshot, limit_plan
    ) is CommitResult.REJECTED
    assert await limit_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0
    await limit_tab.evaluate(
        "document.querySelectorAll('#QUANTITY_LIMIT')[1].remove()"
    )
    assert await commit_quantity(
        limit_tab, limit_snapshot, limit_plan
    ) is CommitResult.COMMITTED
    await limit_tab.evaluate('''document.body.appendChild(
        document.querySelector('#QUANTITY_LIMIT').cloneNode(true)
    )''')
    assert await dispatch_submit(
        limit_tab, build_submit_plan(limit_plan)
    ) is SubmitDispatchResult.STALE
    assert await limit_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    dispatch_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    dispatch_snapshot = await read_quantity_snapshot(dispatch_tab)
    dispatch_plan = select_quantity_snapshot(
        dispatch_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert await commit_quantity(
        dispatch_tab, dispatch_snapshot, dispatch_plan
    ) is CommitResult.COMMITTED
    assert await dispatch_submit(
        dispatch_tab, build_submit_plan(dispatch_plan)
    ) is SubmitDispatchResult.DISPATCHED
    assert await dispatch_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 1
    assert await dispatch_submit(
        dispatch_tab, build_submit_plan(dispatch_plan)
    ) is SubmitDispatchResult.DISPATCHED
    assert await dispatch_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 2

    marker_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    marker_snapshot = await read_quantity_snapshot(marker_tab)
    marker_plan = select_quantity_snapshot(
        marker_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert await commit_quantity(
        marker_tab, marker_snapshot, marker_plan
    ) is CommitResult.COMMITTED
    await marker_tab.evaluate(
        "document.documentElement.removeAttribute("
        "'data-tickets-hunter-utiki-quantity-commit')"
    )
    assert await dispatch_submit(
        marker_tab, build_submit_plan(marker_plan)
    ) is SubmitDispatchResult.STALE
    assert await marker_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    dialog_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    dialog_snapshot = await read_quantity_snapshot(dialog_tab)
    dialog_plan = select_quantity_snapshot(
        dialog_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert await commit_quantity(
        dialog_tab, dialog_snapshot, dialog_plan
    ) is CommitResult.COMMITTED
    await dialog_tab.evaluate('''
        (() => {
            const wrapper = document.createElement('div');
            wrapper.className = 'ui-dialog';
            wrapper.innerHTML = '<div id="dialog-message">警告</div>';
            document.body.appendChild(wrapper);
        })()
    ''')
    assert await dispatch_submit(
        dialog_tab, build_submit_plan(dialog_plan)
    ) is SubmitDispatchResult.REJECTED
    assert await dialog_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    for mutation in ("quantity", "identity"):
        wait_tab = await _open_fixture(
            browser, "UTK0203_quantity.html", query
        )
        wait_snapshot = await read_quantity_snapshot(wait_tab)
        wait_plan = select_quantity_snapshot(
            wait_snapshot, cfg["utiki"]["ticket_type_keyword"],
            cfg["ticket_number"],
        )
        assert await commit_quantity(
            wait_tab, wait_snapshot, wait_plan
        ) is CommitResult.COMMITTED
        await wait_tab.evaluate(
            "document.querySelector('#CHK').value = ''"
        )

        async def mutate_during_ocr(*_args, kind=mutation):
            if kind == "quantity":
                await wait_tab.evaluate(
                    "document.querySelector("
                    "'input.numbox[key=\"OTHER\"]').value = '1'"
                )
            else:
                await wait_tab.evaluate(
                    "document.querySelector('#PERFORMANCE_ID').value = "
                    "'OTHER'"
                )
            await wait_tab.evaluate(
                "document.querySelector('#CHK').value = 'ABCD'"
            )
            return True

        wait_cfg = config()
        wait_cfg["ocr_captcha"].update({
            "enable": True, "force_submit": True,
        })
        dispatch_results = []

        async def capture_dispatch(*args):
            result = await dispatch_submit(*args)
            dispatch_results.append(result)
            return result

        with patch(
            "platforms.utiki.workflow._ctbc_captcha",
            AsyncMock(side_effect=mutate_during_ocr),
        ) as captcha_solver, patch(
            "platforms.utiki.workflow.dispatch_submit",
            AsyncMock(side_effect=capture_dispatch),
        ), patch(
            "platforms.utiki.workflow._ctbc_purchase_state_key",
            return_value=(f"fixture-wait-{mutation}",),
        ), patch(
            "platforms.utiki.workflow._get_ctbc_page_type",
            return_value="general_admission",
        ), patch(
            "platforms.utiki.workflow._observe_ctbc_submit_outcome",
            AsyncMock(return_value=False),
        ) as observe, patch(
            "platforms.utiki.workflow._reload_with_interval",
            AsyncMock(return_value=False),
        ) as reload_page:
            assert not await _ctbc_captcha_and_submit(
                wait_tab, wait_cfg, ocr=None,
                submit_plan=build_submit_plan(wait_plan),
            )
        captcha_solver.assert_awaited_once()
        assert dispatch_results == [SubmitDispatchResult.STALE], mutation
        observe.assert_not_awaited()
        assert reload_page.await_count == 1, mutation
        assert await wait_tab.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        ) == 0
        assert ctbc_tab_runtime(wait_tab)["purchase"]["active_submit"] is None

    race_tab = await _open_fixture(browser, "UTK0203_quantity.html", query)
    await race_tab.evaluate("document.body.dataset.reboundOther = '1'")
    race_snapshot = await read_quantity_snapshot(race_tab)
    race_plan = select_quantity_snapshot(
        race_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert await commit_quantity(
        race_tab, race_snapshot, race_plan
    ) is CommitResult.STALE
    assert await _read_fixture_quantities(race_tab) == {
        "FULL": "2", "OTHER": "1"
    }
    race_event_count = await race_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    )
    assert await commit_quantity(
        race_tab, race_snapshot, race_plan
    ) is CommitResult.STALE
    assert await race_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == race_event_count
    assert await race_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    stale_tab = await _open_fixture(browser, "UTK0203_quantity.html", query)
    stale_snapshot = await read_quantity_snapshot(stale_tab)
    stale_plan = select_quantity_snapshot(
        stale_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    stale_document_id = f"{stale_snapshot.document_id}-old"
    stale_snapshot = replace(stale_snapshot, document_id=stale_document_id)
    stale_plan = replace(stale_plan, document_id=stale_document_id)
    assert await commit_quantity(
        stale_tab, stale_snapshot, stale_plan
    ) is CommitResult.STALE
    assert await stale_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0

    identity_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    identity_snapshot = await read_quantity_snapshot(identity_tab)
    identity_plan = select_quantity_snapshot(
        identity_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await identity_tab.evaluate(
        "document.querySelector('#PERFORMANCE_ID').value = 'OTHER'"
    )
    assert await commit_quantity(
        identity_tab, identity_snapshot, identity_plan
    ) is CommitResult.STALE
    assert await identity_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == 0

    mutation_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    mutation_snapshot = await read_quantity_snapshot(mutation_tab)
    mutation_plan = select_quantity_snapshot(
        mutation_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    await mutation_tab.evaluate("document.body.dataset.addExtra = '1'")
    assert await commit_quantity(
        mutation_tab, mutation_snapshot, mutation_plan
    ) is CommitResult.STALE
    mutation_event_count = await mutation_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    )
    assert await commit_quantity(
        mutation_tab, mutation_snapshot, mutation_plan
    ) is CommitResult.STALE
    assert await mutation_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == mutation_event_count
    assert await mutation_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0

    recovery_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    await recovery_tab.evaluate("document.body.dataset.addExtra = '1'")
    recovery_cfg = config()
    recovery_cfg["advanced"]["auto_reload_page_interval"] = 5
    reload_calls = 0

    async def recovery_reload(reload_tab, _config, reason):
        nonlocal reload_calls
        reload_calls += 1
        identity = _ctbc_tab_identity(reload_tab)
        refresh = ctbc_identity_runtime(identity)["refresh"]
        if reload_calls == 1:
            refresh["reload_request"] = {"reason": reason}
            return False
        refresh["reload_request"] = None
        return True

    with patch(
        "platforms.utiki.workflow._ctbc_attest_fast_destination",
        AsyncMock(return_value=True),
    ), patch(
        "platforms.utiki.workflow._ctbc_purchase_state_key",
        return_value=("fixture-stale-quantity",),
    ), patch(
        "platforms.utiki.workflow._reload_with_interval",
        AsyncMock(side_effect=recovery_reload),
    ) as reload_page, patch(
        "platforms.utiki.workflow._ctbc_captcha_and_submit",
        AsyncMock(return_value=True),
    ) as submit_gate:
        assert not await nodriver_ctbc_general_admission_main(
            recovery_tab, recovery_cfg, ocr=None
        )
        recovery_event_count = await recovery_tab.evaluate(
            "Number(document.body.dataset.quantityEvents || 0)"
        )
        assert not await nodriver_ctbc_general_admission_main(
            recovery_tab, recovery_cfg, ocr=None
        )
    assert reload_page.await_count == 2
    submit_gate.assert_not_awaited()
    assert await recovery_tab.evaluate(
        "Number(document.body.dataset.quantityEvents || 0)"
    ) == recovery_event_count

    malformed_tab = await _open_fixture(
        browser, "UTK0203_quantity.html", query
    )
    await malformed_tab.evaluate(
        "document.querySelector('input[key=\"LAST_AMOUNT\"]').value = "
        "'unknown'"
    )
    malformed_snapshot = await read_quantity_snapshot(malformed_tab)
    assert select_quantity_snapshot(
        malformed_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    ) is None

    multi_tab = await _open_fixture(
        browser, "UTK0202_quantity.html", query
    )
    multi_snapshot = await read_quantity_snapshot(multi_tab)
    assert len(multi_snapshot.rows) == 2
    multi_plan = select_quantity_snapshot(
        multi_snapshot, cfg["utiki"]["ticket_type_keyword"],
        cfg["ticket_number"],
    )
    assert multi_plan.target_key == "FULL"
    assert await commit_quantity(
        multi_tab, multi_snapshot, multi_plan
    ) is CommitResult.COMMITTED
    assert await _read_fixture_quantities(multi_tab) == {
        "OTHER": "0", "FULL": "2"
    }


async def _read_fixture_quantities(tab):
    return await tab.evaluate(
        "Object.fromEntries(Array.from("
        "document.querySelectorAll('input.numbox[key]')"
        ").map(input => [input.getAttribute('key'), input.value]))"
    )


async def _assert_server_assigned_quantity_fixture(browser):
    class QuantityHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            routes = (
                "/BROTHERS/UTK0201_001",
                "/BROTHERS/nested/UTK0201_001",
            )
            for route in routes:
                if self.path == route or self.path.startswith(route + "?"):
                    self.path = "/UTK0201_001" + self.path[len(route):]
                    break
            super().do_GET()

        def guess_type(self, path):
            if path.endswith("/UTK0201_001"):
                return "text/html"
            return super().guess_type(path)

    handler = partial(QuantityHandler, directory=str(FIXTURE_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query = (
        "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=GROUP_FRONT&"
        "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT&ATYPE=N"
    )
    url = (
        f"http://127.0.0.1:{server.server_port}"
        f"/BROTHERS/UTK0201_001{query}"
    )
    try:
        tab = await _open_fixture_url(browser, url)
        snapshot = await read_quantity_snapshot(tab)
        plan = select_quantity_snapshot(
            snapshot, "全票", 2, page_type="server_assigned_quantity"
        )
        assert plan is not None, snapshot
        assert await commit_quantity(
            tab, snapshot, plan
        ) is CommitResult.COMMITTED
        assert await _read_fixture_quantities(tab) == {
            "FULL": "2", "OTHER": "0"
        }
        assert await dispatch_submit(
            tab, build_submit_plan(plan)
        ) is SubmitDispatchResult.DISPATCHED
        assert await tab.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        ) == 1

        hidden_only = await _open_fixture_url(browser, url)
        hidden_snapshot = await read_quantity_snapshot(hidden_only)
        hidden_plan = select_quantity_snapshot(
            hidden_snapshot, "全票", 2,
            page_type="server_assigned_quantity",
        )
        assert await commit_quantity(
            hidden_only, hidden_snapshot, hidden_plan
        ) is CommitResult.COMMITTED
        await hidden_only.evaluate("document.querySelector('#CHK').remove()")
        assert await dispatch_submit(
            hidden_only, build_submit_plan(hidden_plan)
        ) is SubmitDispatchResult.REJECTED
        assert await hidden_only.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        ) == 0

        invalid_form = await _open_fixture_url(browser, url)
        invalid_snapshot = await read_quantity_snapshot(invalid_form)
        invalid_plan = select_quantity_snapshot(
            invalid_snapshot, "全票", 2,
            page_type="server_assigned_quantity",
        )
        assert await commit_quantity(
            invalid_form, invalid_snapshot, invalid_plan
        ) is CommitResult.COMMITTED
        await invalid_form.evaluate(
            "document.querySelector('form').setAttribute('method', 'get')"
        )
        assert await dispatch_submit(
            invalid_form, build_submit_plan(invalid_plan)
        ) is SubmitDispatchResult.REJECTED
        await invalid_form.evaluate('''
            (() => {
                const form = document.querySelector('form');
                const button = document.querySelector('#addcart');
                form.setAttribute('method', 'post');
                form.setAttribute(
                    'action', 'UTK0201_001/ADD_SHOPPING_CAR');
                button.setAttribute('formmethod', '');
            })()
        ''')
        assert await dispatch_submit(
            invalid_form, build_submit_plan(invalid_plan)
        ) is SubmitDispatchResult.REJECTED
        await invalid_form.evaluate('''
            (() => {
                const button = document.querySelector('#addcart');
                button.removeAttribute('formmethod');
                button.setAttribute('formaction', '');
            })()
        ''')
        assert await dispatch_submit(
            invalid_form, build_submit_plan(invalid_plan)
        ) is SubmitDispatchResult.REJECTED
        assert await invalid_form.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        ) == 0
        await invalid_form.evaluate('''
            (() => {
                const form = document.querySelector('form');
                const button = document.querySelector('#addcart');
                button.removeAttribute('formaction');
                form.setAttribute('method', 'post');
                form.setAttribute('action', 'OTHER_ACTION');
            })()
        ''')
        assert await dispatch_submit(
            invalid_form, build_submit_plan(invalid_plan)
        ) is SubmitDispatchResult.REJECTED

        product_drift = await _open_fixture_url(browser, url)
        product_snapshot = await read_quantity_snapshot(product_drift)
        product_plan = select_quantity_snapshot(
            product_snapshot, "全票", 2,
            page_type="server_assigned_quantity",
        )
        await product_drift.evaluate('''
            document.querySelector('form').insertAdjacentHTML(
                'afterbegin',
                '<input id="PRODUCT_ID" value="PRODUCT_FIXTURE" hidden>'
            )
        ''')
        assert await commit_quantity(
            product_drift, product_snapshot, product_plan
        ) is CommitResult.STALE

        nested_url = url.replace(
            "/BROTHERS/UTK0201_001",
            "/BROTHERS/nested/UTK0201_001",
        )
        nested_tab = await _open_fixture_url(browser, nested_url)
        nested_snapshot = await read_quantity_snapshot(nested_tab)
        nested_plan = select_quantity_snapshot(
            nested_snapshot, "全票", 2,
            page_type="server_assigned_quantity",
        )
        assert await commit_quantity(
            nested_tab, nested_snapshot, nested_plan
        ) is CommitResult.REJECTED
        assert await nested_tab.evaluate(
            "Number(document.body.dataset.quantityEvents || 0)"
        ) == 0

        wrong_atype = await _open_fixture_url(
            browser, url.replace("ATYPE=N", "ATYPE=Y")
        )
        wrong_snapshot = await read_quantity_snapshot(wrong_atype)
        wrong_plan = select_quantity_snapshot(
            wrong_snapshot, "全票", 2,
            page_type="server_assigned_quantity",
        )
        assert await commit_quantity(
            wrong_atype, wrong_snapshot, wrong_plan
        ) is CommitResult.STALE
        assert await wrong_atype.evaluate(
            "Number(document.body.dataset.quantityEvents || 0)"
        ) == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def _assert_dialog_fixture(browser):
    tab = await _open_fixture(browser, "UTK0205_dialog.html")
    await tab.evaluate('''
        (() => {
            const visibleWrapper = document.querySelector('.ui-dialog');
            const hidden = visibleWrapper.cloneNode(true);
            hidden.style.display = 'none';
            hidden.querySelector('#dialog-message').textContent = '舊警告';
            document.body.prepend(hidden);
            const hiddenMessage = document.createElement('div');
            hiddenMessage.id = 'dialog-message';
            hiddenMessage.style.display = 'none';
            hiddenMessage.textContent = '同一容器內的舊警告';
            visibleWrapper.prepend(hiddenMessage);
        })()
    ''')
    before_html = await tab.evaluate("document.body.innerHTML")
    snapshot = await read_dialog_snapshot(tab)
    repeated = await read_dialog_snapshot(tab)
    assert snapshot == repeated
    assert snapshot.visible_count == 1
    assert snapshot.text == "該位置已被選走"
    assert _classify_ctbc_submit_dialog(snapshot.text) == "seat_conflict"
    assert await tab.evaluate("document.body.innerHTML") == before_html
    assert await tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 0
    await tab.evaluate('''
        (() => {
            const account = document.createElement('input');
            account.id = 'LOGIN_ID';
            account.value = 'fixture-user';
            const password = document.createElement('input');
            password.id = 'LOGIN_PWD';
            password.value = 'fixture-secret';
            document.body.append(account, password);
        })()
    ''')
    assert await _ctbc_login_fingerprint(
        tab, expected_dialog=snapshot
    )
    assert await _ctbc_login_fingerprint(
        tab,
        expected_dialog=replace(
            snapshot, document_id="different-document"
        ),
    ) == ""
    assert await dismiss_dialog(tab, snapshot) is CommitResult.COMMITTED
    assert (await read_dialog_snapshot(tab)).visible_count == 0
    assert await tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 1

    stale_tab = await _open_fixture(browser, "UTK0205_dialog.html")
    stale_snapshot = await read_dialog_snapshot(stale_tab)
    await stale_tab.evaluate(
        "document.querySelector('#dialog-message').textContent = '另一個警告'"
    )
    assert await dismiss_dialog(
        stale_tab, stale_snapshot
    ) is CommitResult.STALE
    assert await stale_tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 0

    identity_tab = await _open_fixture(browser, "UTK0205_dialog.html")
    identity_snapshot = await read_dialog_snapshot(identity_tab)
    assert await dismiss_dialog(
        identity_tab,
        replace(identity_snapshot, document_id="different-document"),
    ) is CommitResult.STALE
    assert await identity_tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 0

    rejected_tab = await _open_fixture(browser, "UTK0205_dialog.html")
    rejected_snapshot = await read_dialog_snapshot(rejected_tab)
    await rejected_tab.evaluate(
        "document.querySelector('.ui-dialog-buttonset button').disabled = true"
    )
    assert await dismiss_dialog(
        rejected_tab, rejected_snapshot
    ) is CommitResult.REJECTED
    assert await rejected_tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 0

    ambiguous_tab = await _open_fixture(browser, "UTK0205_dialog.html")
    await ambiguous_tab.evaluate('''
        (() => {
            const duplicate = document.querySelector('.ui-dialog')
                .cloneNode(true);
            duplicate.querySelector('#dialog-message').textContent = '另一個警告';
            document.body.append(duplicate);
        })()
    ''')
    ambiguous = await read_dialog_snapshot(ambiguous_tab)
    assert ambiguous.visible_count == 2
    assert ambiguous.text == ""

    delayed_tab = await _open_fixture(browser, "UTK0205_dialog.html")
    await delayed_tab.evaluate('''
        document.querySelector('.ui-dialog-buttonset button').onclick =
            function() {
                document.body.dataset.dialogClicks = String(
                    Number(document.body.dataset.dialogClicks || 0) + 1);
                const wrapper = this.closest('.ui-dialog');
                setTimeout(() => { wrapper.style.display = 'none'; }, 30);
            };
    ''')
    assert await _dismiss_ctbc_dialog(
        delayed_tab, "該位置已被選走"
    )
    assert await delayed_tab.evaluate(
        "Number(document.body.dataset.dialogClicks || 0)"
    ) == 1


async def _assert_fast_marker_fixture(browser):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

    handler = partial(QuietHandler, directory=str(FIXTURE_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        source_url = f"{base_url}/UTK0201_date.html?PRODUCT_ID=PRODUCT_FIXTURE"
        destination_url = (
            f"{base_url}/UTK0205_?PERFORMANCE_ID=P16D0B8Z&"
            "GROUP_ID=GROUP_FRONT&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        area_url = (
            f"{base_url}/UTK0204_?PERFORMANCE_ID=P16D0B8Z&"
            "PRODUCT_ID=PRODUCT_FIXTURE"
        )
        onclick = f"location.href='{area_url}'"

        def classify_fixture_url(url):
            if "/UTK0201_date.html" in str(url):
                return PageKind.DATE
            if "/UTK0204_" in str(url):
                return PageKind.AREA
            if "/UTK0205_" in str(url):
                return PageKind.RESERVED_SEAT
            return PageKind.UNKNOWN

        profile = types.SimpleNamespace(
            tenant_path="",
            host="127.0.0.1",
            map_namespace="FIXTURE",
            fast_area_prefetch=True,
        )

        def parse_fixture_date_target(target_value, current_url):
            if str(target_value) != area_url or str(current_url) != source_url:
                return None
            return {
                "target_url": area_url,
                "performance_id": "P16D0B8Z",
                "product_id": "PRODUCT_FIXTURE",
                "tenant_path": profile.tenant_path,
                "host": profile.host,
                "map_namespace": profile.map_namespace,
                "fast_area_prefetch": profile.fast_area_prefetch,
            }

        async def open_fast_tab(*, control_id="buy_btn", duplicate_row=False):
            tab = await _open_fixture_url(browser, source_url)
            await tab.evaluate(f'''(() => {{
                const button = document.querySelector(
                    '#PerformanceListTable button');
                button.id = {json.dumps(control_id)};
                button.setAttribute('onclick', {json.dumps(onclick)});
                if ({json.dumps(duplicate_row)}) {{
                    const row = button.closest('tr').cloneNode(true);
                    row.querySelector('td').textContent = '2026/09/12（六）18:35';
                    document.querySelector('#PerformanceListTable tbody')
                        .append(row);
                }}
            }})()''')
            return tab

        async def fast_commit(tab, nonce):
            with (
                patch(
                    "platforms.utiki.dom.classify_utiki_page",
                    side_effect=classify_fixture_url,
                ),
                patch(
                    "platforms.utiki.selection.classify_utiki_page",
                    side_effect=classify_fixture_url,
                ),
                patch(
                    "platforms.utiki.selection._parse_date_target_url",
                    side_effect=parse_fixture_date_target,
                ),
            ):
                snapshot = await read_date_snapshot(tab)
                plan = select_date_snapshot(
                    snapshot, config(date_keyword='"9/10"')
                )
                assert plan is not None
                assert plan.action_kind == "normal"
                return await commit_date_action(
                    tab,
                    snapshot,
                    plan,
                    nonce,
                    fast_destination_url=destination_url,
                    fast_marker_key="ticketsHunter.ctbc.fast-navigation.v1",
                    fast_marker_ttl_ms=15000,
                )

        wrong_id_tab = await open_fast_tab(control_id="wrong_buy_btn")
        assert await fast_commit(
            wrong_id_tab, "b" * 32
        ) is CommitResult.REJECTED
        assert str(wrong_id_tab.target.url) == source_url
        assert await wrong_id_tab.evaluate(
            "Number(document.body.dataset.dateClicks || 0)"
        ) == 0

        duplicate_tab = await open_fast_tab(duplicate_row=True)
        assert await fast_commit(
            duplicate_tab, "c" * 32
        ) is CommitResult.REJECTED
        assert str(duplicate_tab.target.url) == source_url
        assert await duplicate_tab.evaluate(
            "Number(document.body.dataset.dateClicks || 0)"
        ) == 0

        tab = await open_fast_tab()
        nonce = "a" * 32
        try:
            await fast_commit(tab, nonce)
        except Exception:
            # A real navigation may destroy the source execution context
            # after the atomic marker and navigation already committed.
            pass

        for _ in range(100):
            if str(tab.target.url) == destination_url:
                marker_state = await _ctbc_read_fast_navigation_marker(tab)
                if marker_state.get("present"):
                    break
            await tab.sleep(0.02)
        else:
            raise AssertionError("fast marker fixture did not reach destination")

        marker = marker_state["marker"]
        assert marker["nonce"] == nonce
        assert marker["source"] == source_url
        assert marker["destination"] == destination_url
        assert not await _ctbc_delete_fast_navigation_marker(tab, "b" * 32)
        assert (await _ctbc_read_fast_navigation_marker(tab))["marker"] == marker
        assert await _ctbc_delete_fast_navigation_marker(tab, nonce)
        assert not (await _ctbc_read_fast_navigation_marker(tab))["present"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
