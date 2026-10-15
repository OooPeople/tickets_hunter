import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import zendriver as uc

from tests.ctbc_support import config

from platforms.ctbc import (
    _classify_ctbc_submit_dialog,
    _ctbc_general_quantity_audit_is_valid,
    _dismiss_ctbc_dialog,
    _get_ctbc_visible_dialog_text,
    _state,
    _validate_ctbc_seat_selection,
    nodriver_ctbc_area_auto_select,
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

    assert await nodriver_ctbc_area_auto_select(tab, cfg)
    assert await tab.evaluate("document.body.dataset.selectedArea || ''") == "PRICE_FRONT"
    assert await tab.evaluate("Number(document.body.dataset.areaClicks || 0)") == 1


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
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE",
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
        "platforms.ctbc._ctbc_general_quantity_audit_is_valid",
        side_effect=audit_spy,
    ) as audit, patch(
        "platforms.ctbc._ctbc_captcha_and_submit",
        submit_gate,
    ), patch(
        "platforms.ctbc._ctbc_attest_fast_destination",
        attestation,
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
        "?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=PRODUCT_FIXTURE&RACE=1",
    )
    await race_tab.evaluate("document.body.dataset.reboundOther = '1'")
    race_cfg = config()
    race_gate = AsyncMock(return_value=True)
    race_attestation = AsyncMock(return_value=True)
    with patch(
        "platforms.ctbc._ctbc_general_quantity_audit_is_valid",
        wraps=_ctbc_general_quantity_audit_is_valid,
    ) as race_audit, patch(
        "platforms.ctbc._ctbc_captcha_and_submit",
        race_gate,
    ), patch(
        "platforms.ctbc._ctbc_attest_fast_destination",
        race_attestation,
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
