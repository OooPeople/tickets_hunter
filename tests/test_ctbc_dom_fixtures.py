import asyncio
import os
import tempfile
import threading
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import zendriver as uc

from tests.ctbc_support import config

from platforms.utiki.dom import CommitResult, commit_area, read_area_snapshot
from platforms.utiki.selection import (
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    quantity_audit_is_valid as _ctbc_general_quantity_audit_is_valid,
    select_area_snapshot,
)
from platforms.utiki.workflow import (
    _ctbc_commit_fast_navigation,
    _ctbc_delete_fast_navigation_marker,
    _ctbc_read_fast_navigation_marker,
    _dismiss_ctbc_dialog,
    _get_ctbc_visible_dialog_text,
    _state,
    _validate_ctbc_seat_selection,
    nodriver_ctbc_date_auto_select,
    nodriver_ctbc_general_admission_main,
    nodriver_ctbc_seat_auto_select,
    nodriver_ctbc_ticket_type_auto_select,
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
            _assert_dialog_fixture,
            _assert_fast_marker_fixture,
        ):
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


async def _assert_date_fixture(browser):
    tab = await _open_fixture(
        browser,
        "UTK0201_date.html",
        "?PRODUCT_ID=PRODUCT_FIXTURE",
    )
    cfg = config(date_keyword='"9/10"')

    assert await nodriver_ctbc_date_auto_select(tab, cfg)
    assert await tab.evaluate("document.body.dataset.selectedDate || ''") == "DATE_FRONT"
    assert await tab.evaluate("Number(document.body.dataset.dateClicks || 0)") == 1


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


async def _assert_seat_fixture(browser):
    tab = await _open_fixture(
        browser,
        "UTK0205_seat.html",
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
    )
    cfg = config()

    assert await nodriver_ctbc_ticket_type_auto_select(tab, cfg)
    assert await nodriver_ctbc_seat_auto_select(tab, cfg)
    assert await _validate_ctbc_seat_selection(tab, cfg)
    selected = await tab.evaluate(
        "Array.from(document.querySelectorAll('#TBL td[title]'))"
        ".filter(seat => seat.dataset.selected === '1')"
        ".map(seat => seat.title)"
    )
    assert selected == ["A區-1排-1號", "A區-1排-2號"]


async def _assert_quantity_fixture(browser):
    tab = await _open_fixture(
        browser,
        "UTK0203_quantity.html",
        (
            "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        ),
    )
    cfg = config()
    events = []

    def audit_spy(*args):
        events.append("audit")
        return _ctbc_general_quantity_audit_is_valid(*args)

    async def submit_effect(*_args):
        events.append("submit")
        return True

    submit_gate = AsyncMock(side_effect=submit_effect)
    attestation = AsyncMock(return_value=True)
    with patch(
        "platforms.utiki.workflow._ctbc_general_quantity_audit_is_valid",
        side_effect=audit_spy,
    ) as audit, patch(
        "platforms.utiki.workflow._ctbc_captcha_and_submit",
        submit_gate,
    ), patch(
        "platforms.utiki.workflow._ctbc_attest_fast_destination",
        attestation,
    ), patch(
        "platforms.utiki.workflow._ctbc_purchase_state_key",
        return_value=("fixture-purchase",),
    ):
        assert await nodriver_ctbc_general_admission_main(tab, cfg, ocr=None)

    audit.assert_called_once()
    attestation.assert_awaited_once_with(tab, "general_admission", cfg)
    submit_gate.assert_awaited_once_with(tab, cfg, None)
    assert events == ["audit", "submit"]
    assert await _read_fixture_quantities(tab) == {"FULL": "2", "OTHER": "0"}
    assert await tab.evaluate("Number(document.body.dataset.addcartClicks || 0)") == 0

    race_tab = await _open_fixture(
        browser,
        "UTK0203_quantity.html",
        (
            "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT&RACE=1"
        ),
    )
    await race_tab.evaluate("document.body.dataset.reboundOther = '1'")
    race_cfg = config()
    race_gate = AsyncMock(return_value=True)
    race_attestation = AsyncMock(return_value=True)
    with patch(
        "platforms.utiki.workflow._ctbc_general_quantity_audit_is_valid",
        wraps=_ctbc_general_quantity_audit_is_valid,
    ) as race_audit, patch(
        "platforms.utiki.workflow._ctbc_captcha_and_submit",
        race_gate,
    ), patch(
        "platforms.utiki.workflow._ctbc_attest_fast_destination",
        race_attestation,
    ), patch(
        "platforms.utiki.workflow._ctbc_purchase_state_key",
        return_value=("fixture-race-purchase",),
    ):
        assert not await nodriver_ctbc_general_admission_main(
            race_tab, race_cfg, ocr=None
        )

    race_audit.assert_called_once()
    race_attestation.assert_awaited_once_with(
        race_tab, "general_admission", race_cfg
    )
    race_gate.assert_not_awaited()
    assert await _read_fixture_quantities(race_tab) == {"FULL": "2", "OTHER": "1"}
    assert await race_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 0


async def _read_fixture_quantities(tab):
    return await tab.evaluate(
        "Object.fromEntries(Array.from("
        "document.querySelectorAll('input.numbox[key]')"
        ").map(input => [input.getAttribute('key'), input.value]))"
    )


async def _assert_dialog_fixture(browser):
    tab = await _open_fixture(browser, "UTK0205_dialog.html")
    dialog_text = await _get_ctbc_visible_dialog_text(tab)
    assert dialog_text == "該位置已被選走"
    assert _classify_ctbc_submit_dialog(dialog_text) == "seat_conflict"
    assert await _dismiss_ctbc_dialog(tab, dialog_text)
    assert await _get_ctbc_visible_dialog_text(tab) == ""
    assert await tab.evaluate(
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
            f"{base_url}/UTK0205_seat.html?PERFORMANCE_ID=P16D0B8Z&"
            "GROUP_ID=GROUP_FRONT&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
        )
        tab = await browser.get(source_url)
        for _ in range(50):
            if await tab.evaluate("document.readyState === 'complete'"):
                break
            await tab.sleep(0.02)

        nonce = "a" * 32
        pending = {
            "source_url": source_url,
            "destination_url": destination_url,
            "nonce": nonce,
        }
        try:
            await _ctbc_commit_fast_navigation(
                tab, pending, "selectFixtureDate('DATE_FRONT')"
            )
        except Exception:
            # A real navigation may destroy the source execution context before
            # DevTools returns even though the marker and navigation committed.
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
