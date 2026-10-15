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

from platforms.utiki.dom import (
    CommitResult,
    SubmitDispatchResult,
    commit_area,
    commit_quantity,
    dispatch_quantity_submit,
    read_area_snapshot,
    read_quantity_snapshot,
)
from platforms.utiki.selection import (
    classify_submit_dialog as _classify_ctbc_submit_dialog,
    select_area_snapshot,
    select_quantity_snapshot,
)
from platforms.utiki.workflow import (
    _ctbc_commit_fast_navigation,
    _ctbc_captcha_and_submit,
    _ctbc_delete_fast_navigation_marker,
    _ctbc_purchase_state_key,
    _ctbc_read_fast_navigation_marker,
    _ctbc_tab_identity,
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
    query = (
        "?PERFORMANCE_ID=P16D0B8Z&GROUP_ID=&"
        "PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
    )
    cfg = config()
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
    submit_gate = AsyncMock(return_value=True)
    with patch(
        "platforms.utiki.workflow._ctbc_captcha_and_submit",
        submit_gate,
    ), patch(
        "platforms.utiki.workflow._ctbc_attest_fast_destination",
        AsyncMock(return_value=True),
    ), patch(
        "platforms.utiki.workflow._ctbc_purchase_state_key",
        return_value=("fixture-purchase",),
    ):
        assert await nodriver_ctbc_general_admission_main(
            flow_tab, cfg, ocr=None
        )
    submit_gate.assert_awaited_once()
    assert submit_gate.await_args.args == (flow_tab, cfg, None)
    assert submit_gate.await_args.kwargs["quantity_plan"].target_key == "FULL"
    assert await _read_fixture_quantities(flow_tab) == {
        "FULL": "2", "OTHER": "0"
    }

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
    assert await dispatch_quantity_submit(
        dispatch_tab, dispatch_plan
    ) is SubmitDispatchResult.DISPATCHED
    assert await dispatch_tab.evaluate(
        "Number(document.body.dataset.addcartClicks || 0)"
    ) == 1
    assert await dispatch_quantity_submit(
        dispatch_tab, dispatch_plan
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
    assert await dispatch_quantity_submit(
        marker_tab, marker_plan
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
    assert await dispatch_quantity_submit(
        dialog_tab, dialog_plan
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
            result = await dispatch_quantity_submit(*args)
            dispatch_results.append(result)
            return result

        with patch(
            "platforms.utiki.workflow._ctbc_captcha",
            AsyncMock(side_effect=mutate_during_ocr),
        ) as captcha_solver, patch(
            "platforms.utiki.workflow.dispatch_quantity_submit",
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
                wait_tab, wait_cfg, ocr=None, quantity_plan=wait_plan
            )
        captcha_solver.assert_awaited_once()
        assert dispatch_results == [SubmitDispatchResult.STALE], mutation
        observe.assert_not_awaited()
        assert reload_page.await_count == 1, mutation
        assert await wait_tab.evaluate(
            "Number(document.body.dataset.addcartClicks || 0)"
        ) == 0
        assert (f"fixture-wait-{mutation}",) not in _state["submitted"]

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
        if reload_calls == 1:
            _state["reload_request"][identity] = {"reason": reason}
            return False
        _state["reload_request"].pop(identity, None)
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
