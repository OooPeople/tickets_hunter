"""New owner runs production scripts across synthetic local pages only."""

import asyncio
import base64
import io
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest
import zendriver as uc
from zendriver import cdp

from platforms.utiki.adapters.browser import ZendriverSelectionBrowser
from platforms.utiki import dom
from platforms.utiki.auth import TenantAuthProfile
from platforms.utiki.application.engine import SelectionProgress
from platforms.utiki.application.flow_settings import FlowSettings
from tests.ctbc_support import config, make_effective_policy, selector_settings
from platforms.utiki.application.ports import (
    CoreControl, CoreGate, InlineLoginCredentials, InlineLoginIntent,
    PurchaseAuthority,
)
from platforms.utiki.bootstrap import build_run_captcha_provider, create_engine
from platforms.utiki.domain.effects import (
    ClaimId, EffectKind, EffectProposal, EffectStatus, RunningEffect,
)
from platforms.utiki.domain.identity import (
    AuthEpoch, DocumentKey, OperationId, OperationStamp, TabKey,
)
from platforms.utiki.domain.purchase import SubmitSource
from platforms.utiki.profiles import PageKind, TENANT_PROFILES
from platforms.utiki.selection import select_date_snapshot
from utiki_config import UtikiBehavior


pytestmark = pytest.mark.browser
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ctbc"


class LocalCore:
    def current_control(self, _tab: TabKey) -> CoreControl:
        return CoreControl(1, CoreGate.PURCHASE_ALLOWED)

    def current_purchase_authority(self, _tab: TabKey) -> PurchaseAuthority:
        return PurchaseAuthority(AuthEpoch(1), 1)

    def current_inline_login_credentials(
        self, _tab: TabKey, tenant_key: str,
    ) -> InlineLoginCredentials | None:
        if tenant_key != "ctbc_brothers":
            return None
        return InlineLoginCredentials("fixture-user", "fixture-password", 1)


async def _wait_url(tab: object, name: str) -> None:
    for _ in range(100):
        try:
            if name in str(tab.target.url) and await tab.evaluate(
                "document.readyState === 'complete'"
            ):
                return
        except Exception:
            pass
        await asyncio.sleep(0.03)
    raise AssertionError(f"synthetic navigation did not reach {name}")


async def _wait_captcha_image(tab: object) -> None:
    for _ in range(100):
        if await tab.evaluate(
            "document.querySelector('#chk_pic')?.complete === true && "
            "document.querySelector('#chk_pic')?.naturalWidth > 0"
        ):
            return
        await asyncio.sleep(0.03)
    raise AssertionError("synthetic CAPTCHA image did not load")


def _synthetic_captcha_image() -> str:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (60, 20), "white")
    ImageDraw.Draw(image).text((3, 2), "ABCD", font=ImageFont.load_default(), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


async def _exercise(
    *, tamper_login_before_submit: bool = False,
    tamper_captcha_before_submit: bool = False,
    known_rejection_dialog: bool = False,
    captcha_reset: bool = False,
    unsafe_refresh_control: str | None = None,
    manual_submit: bool = False,
    ocr_after_reset: bool = False,
) -> None:
    browser = None
    with tempfile.TemporaryDirectory(prefix="utiki-engine-flow-") as profile:
        try:
            browser = await uc.start(
                user_data_dir=profile, headless=True,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            date_url = (FIXTURES / "UTK0201_flow.html").resolve().as_uri()
            tab = await browser.get(date_url + "?PRODUCT_ID=P16ANF0L")
            await _wait_url(tab, "UTK0201_flow.html")
            tab_key = TabKey(1, "synthetic-flow")
            policy = UtikiBehavior("全票", "row_seat", "manual")
            selection_browser = ZendriverSelectionBrowser(tab, tab_key)
            effective = make_effective_policy(policy, FlowSettings(
                    True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
                    ocr_enabled=not manual_submit, force_submit=not manual_submit,
                ))
            engine = create_engine(
                effective, selection_browser, LocalCore(),
                build_run_captcha_provider(effective.flow) if ocr_after_reset else None,
            )
            if ocr_after_reset:
                assert engine._captcha_provider is not None
            handle = engine.attach(tab_key)
            quantity_path = urlsplit(
                (FIXTURES / "UTK0203_flow.html").resolve().as_uri()
            ).path
            general_routes = tuple(
                (origin, quantity_path) for origin in ("file://", "null")
            )
            with (
                patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.DATE),
                patch("platforms.utiki.selection.classify_utiki_page", return_value=PageKind.DATE),
            ):
                assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
            await _wait_url(tab, "UTK0204_flow.html")
            assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
            await _wait_url(tab, "UTK0203_flow.html")
            with (
                patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.selection.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.domain.plans.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.dom.CONST_GENERAL_ADMISSION_ROUTES", general_routes),
            ):
                assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
                assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
            assert await tab.evaluate("document.querySelector('input[key=FULL]').value") == "2"
            assert await tab.evaluate("document.querySelector('input[key=OTHER]').value") == "0"
            assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
            if known_rejection_dialog:
                image_after = (
                    _synthetic_captcha_image() if ocr_after_reset else None
                )
                await tab.evaluate("""(() => {
                    document.body.dataset.dialogClicks = '0';
                    document.body.insertAdjacentHTML('beforeend',
                        '<div class="ui-dialog" style="display:none">' +
                        '<div id="dialog-message">驗證碼錯誤</div>' +
                        '<div class="ui-dialog-buttonset"><button type="button">確定</button></div>' +
                        '</div>');
                    const wrapper = document.querySelector('.ui-dialog');
                    document.querySelector('#addcart').addEventListener('click',
                        () => { wrapper.style.display = 'block'; });
                    wrapper.querySelector('button').addEventListener('click', () => {
                        document.body.dataset.dialogClicks = String(
                            Number(document.body.dataset.dialogClicks) + 1);
                        wrapper.style.display = 'none';
                    });
                })()""")
            if captcha_reset:
                assert known_rejection_dialog
                script = """(() => {
                    const makeImage = color => {
                        const canvas = document.createElement('canvas');
                        canvas.width = 24;
                        canvas.height = 12;
                        canvas.getContext('2d').fillStyle = color;
                        canvas.getContext('2d').fillRect(0, 0, 24, 12);
                        return canvas.toDataURL('image/png');
                    };
                    const image = document.createElement('img');
                    image.id = 'chk_pic';
                    image.width = 24;
                    image.height = 12;
                    image.src = makeImage('red');
                    const refresh = document.createElement('button');
                    refresh.id = 'chang_pic';
                    refresh.type = 'button';
                    refresh.textContent = '更換驗證碼';
                    document.body.dataset.captchaRefreshClicks = '0';
                    refresh.addEventListener('click', () => {
                        document.body.dataset.captchaRefreshClicks = String(
                            Number(document.body.dataset.captchaRefreshClicks) + 1);
                        image.src = __NEXT_IMAGE__;
                    });
                    document.body.append(image, refresh);
                })()""".replace("__NEXT_IMAGE__", json.dumps(image_after) if image_after
                               else "makeImage('blue')")
                await tab.evaluate(script)
                await _wait_captcha_image(tab)
                if unsafe_refresh_control == "submit":
                    await tab.evaluate("document.querySelector('#chang_pic').type = 'submit'")
                elif unsafe_refresh_control == "link":
                    await tab.evaluate(
                        "document.querySelector('#chang_pic').outerHTML = "
                        "'<a id=\"chang_pic\" href=\"#unsafe\">更換驗證碼</a>'"
                    )
            else:
                await tab.evaluate("""(() => {
                    const canvas = document.createElement('canvas');
                    canvas.width = 24;
                    canvas.height = 12;
                    canvas.getContext('2d').fillStyle = 'red';
                    canvas.getContext('2d').fillRect(0, 0, 24, 12);
                    const image = document.createElement('img');
                    image.id = 'chk_pic';
                    image.src = canvas.toDataURL('image/png');
                    document.body.append(image);
                })()""")
                await _wait_captcha_image(tab)
            input_observation = await engine._purchase_browser().observe_purchase_input(handle.tab)
            assert input_observation.manual_value_ready, input_observation
            assert (
                input_observation.captcha_unique and input_observation.captcha_enabled
                and input_observation.login_required and not input_observation.login_ready
                and input_observation.button_ready
                and not input_observation.dialog_visible
            ), input_observation
            with (
                patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.domain.plans.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.domain.purchase.classify_utiki_page", return_value=PageKind.GENERAL_ADMISSION),
                patch("platforms.utiki.domain.purchase.get_utiki_tenant_profile", return_value=TENANT_PROFILES["tix.ctbcsports.com"]),
                patch("platforms.utiki.application.engine.get_utiki_tenant_profile", return_value=TENANT_PROFILES["tix.ctbcsports.com"]),
                patch("platforms.utiki.application.engine.get_tenant_auth_profile", return_value=TenantAuthProfile(
                    "ctbc_brothers", account_key="ctbc_account", password_key="ctbc_password",
                    inline_login_origin="file://",
                )),
                patch("platforms.utiki.dom.CONST_GENERAL_ADMISSION_ROUTES", general_routes),
            ):
                await tab.evaluate("document.querySelector('#CHK').value = ''")
                assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
                assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
                await tab.evaluate("document.querySelector('#CHK').value = 'ABCD'")
                assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
                assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
                assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
                assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
                assert await tab.evaluate(
                    "document.querySelector('#LOGIN_ID').value === 'fixture-user' && "
                    "document.querySelector('#LOGIN_PWD').value === 'fixture-password'"
                )
                input_observation = await engine._purchase_browser().observe_purchase_input(handle.tab)
                assert input_observation.login_ready
                if manual_submit:
                    assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
                    position = await tab.evaluate("""(() => {
                        const r = document.querySelector('#addcart').getBoundingClientRect();
                        return [r.left + r.width / 2, r.top + r.height / 2];
                    })()""")
                    x, y = position
                    await tab.send(cdp.input_.dispatch_mouse_event(
                        "mousePressed", x, y, button=cdp.input_.MouseButton.LEFT,
                        buttons=1, click_count=1,
                    ))
                    await tab.send(cdp.input_.dispatch_mouse_event(
                        "mouseReleased", x, y, button=cdp.input_.MouseButton.LEFT,
                        buttons=0, click_count=1,
                    ))
                    observed_manual = await engine.advance_submit(handle)
                    assert observed_manual is (
                        SelectionProgress.SUBMIT_BLOCKED if known_rejection_dialog
                        else SelectionProgress.AWAITING_EVIDENCE
                    )
                    submission = engine._sessions[handle.tab].submission
                    assert submission is not None
                    assert submission.attempt.source is SubmitSource.OBSERVED_MANUAL
                    assert submission.attempt.stamp is None
                    assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
                    if known_rejection_dialog and captcha_reset:
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
                        for _ in range(100):
                            reset_progress = await engine.advance_captcha_reset(handle)
                            if reset_progress is SelectionProgress.CAPTCHA_RESET_VERIFIED:
                                break
                            assert reset_progress is SelectionProgress.AWAITING_EVIDENCE
                            await asyncio.sleep(0.03)
                        else:
                            raise AssertionError("manual rejection did not receive a new image")
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                        await tab.evaluate("document.querySelector('#CHK').value = 'WXYZ'")
                        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
                        assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
                        await tab.send(cdp.input_.dispatch_mouse_event(
                            "mousePressed", x, y, button=cdp.input_.MouseButton.LEFT,
                            buttons=1, click_count=1,
                        ))
                        await tab.send(cdp.input_.dispatch_mouse_event(
                            "mouseReleased", x, y, button=cdp.input_.MouseButton.LEFT,
                            buttons=0, click_count=1,
                        ))
                        assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 2
                        await tab.get((FIXTURES / "UTK0206_flow.html").resolve().as_uri())
                        await _wait_url(tab, "UTK0206_flow.html")
                        with patch(
                            "platforms.utiki.application.engine.classify_utiki_page",
                            return_value=PageKind.CHECKOUT,
                        ):
                            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
                        assert len(engine._sessions[handle.tab].prior_submissions) == 1
                        return
                    await tab.get((FIXTURES / "UTK0206_flow.html").resolve().as_uri())
                    await _wait_url(tab, "UTK0206_flow.html")
                    with patch(
                        "platforms.utiki.application.engine.classify_utiki_page",
                        return_value=PageKind.CHECKOUT,
                    ):
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
                    return
                if tamper_login_before_submit:
                    dispatch = selection_browser.dispatch_submit

                    async def tampered_dispatch(
                        plan, claim, login_intent=None, credentials=None,
                        captcha_observed=None, captcha_answer=None,
                        manual_login_token=None,
                    ):
                        await tab.evaluate(
                            "document.querySelector('#LOGIN_PWD').value = 'changed'"
                        )
                        return await dispatch(
                            plan, claim, login_intent, credentials,
                            captcha_observed, captcha_answer, manual_login_token,
                        )

                    selection_browser.dispatch_submit = tampered_dispatch
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
                    return
                if tamper_captcha_before_submit:
                    dispatch = selection_browser.dispatch_submit

                    async def changed_challenge(
                        plan, claim, login_intent=None, credentials=None,
                        captcha_observed=None, captcha_answer=None,
                        manual_login_token=None,
                    ):
                        await tab.evaluate("""(() => {
                            const image = document.querySelector('#chk_pic');
                            const canvas = document.createElement('canvas');
                            canvas.width = 24;
                            canvas.height = 12;
                            canvas.getContext('2d').fillStyle = 'blue';
                            canvas.getContext('2d').fillRect(0, 0, 24, 12);
                            image.src = canvas.toDataURL('image/png');
                        })()""")
                        await _wait_captcha_image(tab)
                        return await dispatch(
                            plan, claim, login_intent, credentials,
                            captcha_observed, captcha_answer, manual_login_token,
                        )

                    selection_browser.dispatch_submit = changed_challenge
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    assert len(engine._sessions[handle.tab].prior_submissions) == 1
                    await tab.evaluate("document.querySelector('#CHK').value = ''")
                    await tab.evaluate("document.querySelector('#CHK').focus()")
                    await tab.send(cdp.input_.insert_text(text="WXYZ"))
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
                    assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
                    return
                assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
                if known_rejection_dialog:
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
                    assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    if captcha_reset:
                        if unsafe_refresh_control:
                            assert await engine.advance_captcha_reset(handle) is SelectionProgress.AWAITING_EVIDENCE
                            assert await tab.evaluate("Number(document.body.dataset.captchaRefreshClicks)") == 0
                            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                            assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
                            return
                        previous = await selection_browser.observe_captcha_reset(handle.tab)
                        assert previous.image is not None
                        for _ in range(100):
                            progress = await engine.advance_captcha_reset(handle)
                            if progress is SelectionProgress.CAPTCHA_RESET_VERIFIED:
                                break
                            assert progress is SelectionProgress.AWAITING_EVIDENCE
                            await asyncio.sleep(0.03)
                        else:
                            raise AssertionError("new CAPTCHA image was not verified")
                        updated = await selection_browser.observe_captcha_reset(handle.tab)
                        assert updated.image is not None
                        assert updated.image.load_revision != previous.image.load_revision
                        assert updated.field_empty
                        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
                        expected_first = (
                            SelectionProgress.SUBMIT_DISPATCHED if ocr_after_reset
                            else SelectionProgress.SUBMIT_BLOCKED
                        )
                        first = await engine.advance_submit(handle)
                        assert first is expected_first, (
                            first, engine._sessions[handle.tab].input_state,
                            engine._sessions[handle.tab].active,
                        )
                        if not ocr_after_reset:
                            await tab.evaluate("document.querySelector('#CHK').value = 'WXYZ'")
                            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                        assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 2
                else:
                    assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == (
                2 if captcha_reset else 1
            )
            if known_rejection_dialog:
                assert await tab.evaluate("Number(document.body.dataset.dialogClicks)") == 1
            if captcha_reset:
                assert await tab.evaluate("Number(document.body.dataset.captchaRefreshClicks)") == 1
        finally:
            if browser is not None:
                await browser.stop()


def test_new_owner_production_scripts_on_local_flow() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise())


def test_date_script_rejects_control_changed_after_snapshot() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")

    async def exercise() -> None:
        browser = None
        with tempfile.TemporaryDirectory(prefix="utiki-date-preflight-") as profile:
            try:
                browser = await uc.start(
                    user_data_dir=profile, headless=True,
                    browser_args=["--disable-background-networking", "--no-first-run"],
                )
                tab = await browser.get(
                    (FIXTURES / "UTK0201_flow.html").resolve().as_uri()
                    + "?PRODUCT_ID=P16ANF0L"
                )
                await _wait_url(tab, "UTK0201_flow.html")
                with (
                    patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.DATE),
                    patch("platforms.utiki.selection.classify_utiki_page", return_value=PageKind.DATE),
                ):
                    snapshot = await dom.read_date_snapshot(tab)
                    plan = select_date_snapshot(snapshot, selector_settings())
                    assert plan is not None
                    await tab.evaluate(
                        "window.__dateClicks = 0; "
                        "document.querySelector('#buy_btn').addEventListener('click', "
                        "() => window.__dateClicks++); "
                        "document.querySelector('#buy_btn').setAttribute('onclick', 'changedDateAction()')"
                    )
                    result = await dom.commit_date_action(
                        tab, snapshot, plan, "a" * 32,
                    )
                assert result is dom.CommitResult.STALE
                assert await tab.evaluate("window.__dateClicks") == 0
                assert "UTK0201_flow.html" in str(tab.target.url)
                assert await tab.evaluate(
                    "document.querySelector('#buy_btn').getAttribute('onclick')"
                ) == "changedDateAction()"
            finally:
                if browser is not None:
                    await browser.stop()

    asyncio.run(exercise())


def test_observed_manual_submit_reaches_known_local_checkout_without_auto_click() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(manual_submit=True))


def test_manual_submit_marker_requires_a_trusted_button_event() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")

    async def exercise() -> None:
        browser = None
        with tempfile.TemporaryDirectory(prefix="utiki-manual-submit-") as profile:
            try:
                browser = await uc.start(user_data_dir=profile, headless=True,
                                         browser_args=["--disable-background-networking",
                                                       "--no-first-run"])
                tab = await browser.get((FIXTURES / "UTK0203_flow.html").resolve().as_uri())
                await _wait_url(tab, "UTK0203_flow.html")
                tab_key = TabKey(8, "manual-submit-fixture")
                adapter = ZendriverSelectionBrowser(tab, tab_key)
                prepared = await adapter.observe_purchase_input(tab_key)
                token = await adapter.arm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                )
                assert token
                assert await adapter.disarm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                    token,
                )
                rearmed = await adapter.arm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                )
                assert rearmed != token
                token = await adapter.arm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                )
                assert token and token != rearmed
                baseline = await adapter._manual_selection_revision()
                await tab.evaluate("""(() => {
                    const input = document.querySelector('#table_tickettype input.numbox');
                    input.value = '3';
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                })()""")
                assert await adapter._manual_selection_revision() == baseline
                await tab.evaluate(
                    "document.querySelector('#table_tickettype input.numbox').focus()"
                )
                await tab.send(cdp.input_.insert_text("4"))
                changed = await adapter._manual_selection_revision()
                assert changed[:2] == baseline[:2] and changed[2] > baseline[2]
                await tab.evaluate("document.querySelector('#addcart').click()")
                assert (await adapter.observe_submit_outcome(tab_key)).manual_submit is None
                position = await tab.evaluate("""(() => {
                    const r = document.querySelector('#addcart').getBoundingClientRect();
                    return [r.left + r.width / 2, r.top + r.height / 2];
                })()""")
                x, y = position
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mousePressed", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mouseReleased", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=0, click_count=1,
                ))
                observed = await adapter.observe_submit_outcome(tab_key)
                assert observed.manual_submit is not None
                assert observed.manual_submit.arm_token == token
                assert observed.manual_submit.document_id == prepared.document_id
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mousePressed", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mouseReleased", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=0, click_count=1,
                ))
                assert (await adapter.observe_submit_outcome(tab_key)).manual_submit == observed.manual_submit
                assert not await adapter.arm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                )
                assert not await adapter.disarm_manual_submit(
                    tab_key, prepared.page_url, prepared.document_id,
                    token,
                )
            finally:
                if browser is not None:
                    await browser.stop()

    asyncio.run(exercise())


def test_submit_guard_rejects_login_changed_after_readback() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(tamper_login_before_submit=True))


def test_submit_guard_rejects_captcha_changed_after_readback() -> None:
    asyncio.run(_exercise(tamper_captcha_before_submit=True))


def test_known_rejection_dialog_dismisses_once_on_local_flow() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(known_rejection_dialog=True))


def test_known_captcha_reset_allows_one_new_manual_input_submit() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(known_rejection_dialog=True, captcha_reset=True))


def test_known_captcha_reset_uses_real_ocr_and_production_input_guard() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(
        known_rejection_dialog=True, captcha_reset=True, ocr_after_reset=True,
    ))


def test_observed_manual_rejection_rearms_on_new_image_with_production_scripts() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(
        manual_submit=True, known_rejection_dialog=True, captcha_reset=True,
    ))


@pytest.mark.parametrize("unsafe_control", ["submit", "link"])
def test_captcha_reset_rejects_submit_or_navigation_control(unsafe_control: str) -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(
        known_rejection_dialog=True, captcha_reset=True,
        unsafe_refresh_control=unsafe_control,
    ))


async def _exercise_inline_login_guards() -> None:
    browser = None
    with tempfile.TemporaryDirectory(prefix="utiki-login-guards-") as profile:
        try:
            browser = await uc.start(
                user_data_dir=profile, headless=True,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            tab = await browser.get((FIXTURES / "UTK0203_flow.html").resolve().as_uri())
            await _wait_url(tab, "UTK0203_flow.html")
            tab_key = TabKey(3, "synthetic-login-guards")
            adapter = ZendriverSelectionBrowser(tab, tab_key)
            inputs = await adapter.observe_purchase_input(tab_key)
            assert inputs.login_required and inputs.login_fields_usable
            assert not inputs.login_ready
            credentials = InlineLoginCredentials("fixture-user", "fixture-password", 1)
            intent = InlineLoginIntent(
                inputs.page_url, inputs.document_id, "file://", 1, True, True,
            )
            stamp = OperationStamp(
                tab_key, 1, OperationId("login-guard"), DocumentKey(inputs.document_id),
                1, 1,
            )
            claim = RunningEffect(
                ClaimId("login-guard"), EffectProposal(stamp, EffectKind.INPUT_WRITE)
            )
            wrong_url = replace(intent, page_url=intent.page_url + "#other")
            assert (await adapter.commit_inline_login(wrong_url, credentials, claim)).status is EffectStatus.REJECTED
            wrong_document = replace(intent, document_id="other-document")
            wrong_document_claim = replace(
                claim, proposal=EffectProposal(
                    replace(stamp, document=DocumentKey("other-document")),
                    EffectKind.INPUT_WRITE,
                ),
            )
            assert (
                await adapter.commit_inline_login(wrong_document, credentials, wrong_document_claim)
            ).status is EffectStatus.REJECTED
            await tab.evaluate("document.querySelector('#LOGIN_ID').disabled = true")
            assert (await adapter.commit_inline_login(intent, credentials, claim)).status is EffectStatus.REJECTED
            await tab.evaluate("document.querySelector('#LOGIN_ID').disabled = false")
            await tab.evaluate("document.body.appendChild(document.querySelector('#LOGIN_ID').cloneNode())")
            assert (await adapter.commit_inline_login(intent, credentials, claim)).status is EffectStatus.REJECTED
            await tab.evaluate("document.querySelectorAll('#LOGIN_ID')[1].remove()")
            await tab.evaluate("document.querySelector('#LOGIN_ID').value = 'other-account'")
            password_only = replace(intent, write_account=False)
            assert (
                await adapter.commit_inline_login(password_only, credentials, claim)
            ).status is EffectStatus.REJECTED
            await tab.evaluate("document.querySelector('#LOGIN_ID').value = ''")
            await tab.evaluate("document.querySelector('#LOGIN_PWD').value = 'other-password'")
            account_only = replace(intent, write_password=False)
            assert (
                await adapter.commit_inline_login(account_only, credentials, claim)
            ).status is EffectStatus.REJECTED
            await tab.evaluate("document.querySelector('#LOGIN_PWD').value = ''")
            assert (await adapter.commit_inline_login(intent, credentials, claim)).status is EffectStatus.APPLIED
            assert (await adapter.audit_inline_login(tab_key, intent, credentials)).matched
            await tab.evaluate("document.querySelector('#LOGIN_PWD').value = 'changed'")
            audit = await adapter.audit_inline_login(tab_key, intent, credentials)
            assert not audit.matched and not audit.manual_ready
            await tab.evaluate("document.querySelector('#LOGIN_ID').focus()")
            await tab.send(cdp.input_.insert_text(text="-manual"))
            audit = await adapter.audit_inline_login(tab_key, intent, credentials)
            assert not audit.matched and not audit.manual_ready
            await tab.evaluate("document.querySelector('#LOGIN_PWD').focus()")
            await tab.send(cdp.input_.insert_text(text="-manual"))
            audit = await adapter.audit_inline_login(tab_key, intent, credentials)
            assert not audit.matched and audit.manual_ready
        finally:
            if browser is not None:
                await browser.stop()


def test_inline_login_guarded_production_script_on_local_page() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise_inline_login_guards())


async def _exercise_reserved_seat(*, preselect_one: bool = False) -> None:
    browser = None
    with tempfile.TemporaryDirectory(prefix="utiki-engine-seat-") as profile:
        try:
            browser = await uc.start(
                user_data_dir=profile, headless=True,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            date_url = (FIXTURES / "UTK0201_flow.html").resolve().as_uri()
            tab = await browser.get(date_url + "?PRODUCT_ID=PRODUCT_FIXTURE")
            await _wait_url(tab, "UTK0201_flow.html")
            await tab.evaluate("document.body.dataset.flow = 'seat'")
            tab_key = TabKey(2, "synthetic-seat-flow")
            policy = UtikiBehavior("全票", "row_seat", "manual")
            engine = create_engine(
                make_effective_policy(policy, FlowSettings(
                    True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
                    ocr_enabled=True, force_submit=True,
                )), ZendriverSelectionBrowser(tab, tab_key), LocalCore(),
            )
            handle = engine.attach(tab_key)
            seat_path = urlsplit(
                (FIXTURES / "UTK0205_seat.html").resolve().as_uri()
            ).path
            seat_routes = tuple((origin, seat_path) for origin in ("file://", "null"))
            with (
                patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.DATE),
                patch("platforms.utiki.selection.classify_utiki_page", return_value=PageKind.DATE),
            ):
                assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
            await _wait_url(tab, "UTK0204_seat_flow.html")
            assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
            await _wait_url(tab, "UTK0205_seat.html")
            await tab.evaluate("""(() => {
                const canvas = document.createElement('canvas');
                canvas.width = 24;
                canvas.height = 12;
                canvas.getContext('2d').fillStyle = 'red';
                canvas.getContext('2d').fillRect(0, 0, 24, 12);
                const image = document.createElement('img');
                image.id = 'chk_pic';
                image.src = canvas.toDataURL('image/png');
                document.body.append(image);
            })()""")
            await _wait_captcha_image(tab)
            if preselect_one:
                await tab.evaluate('document.querySelector("[data-seat-id=S1]").click()')
            with (
                patch("platforms.utiki.dom.classify_utiki_page", return_value=PageKind.RESERVED_SEAT),
                patch("platforms.utiki.selection.classify_utiki_page", return_value=PageKind.RESERVED_SEAT),
                patch("platforms.utiki.application.engine.classify_utiki_page", return_value=PageKind.RESERVED_SEAT),
                patch("platforms.utiki.domain.plans.classify_utiki_page", return_value=PageKind.RESERVED_SEAT),
                patch("platforms.utiki.domain.purchase.classify_utiki_page", return_value=PageKind.RESERVED_SEAT),
                patch("platforms.utiki.domain.purchase.get_utiki_tenant_profile", return_value=TENANT_PROFILES["tix.ctbcsports.com"]),
                patch("platforms.utiki.application.engine.get_utiki_tenant_profile", return_value=TENANT_PROFILES["tix.ctbcsports.com"]),
                patch("platforms.utiki.dom.CONST_RESERVED_SEAT_ROUTES", seat_routes),
            ):
                if preselect_one:
                    assert await engine.advance_seat(handle) is SelectionProgress.SEAT_CLEARED
                assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
                assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
                assert await tab.evaluate("Number(document.body.dataset.seatClicks)") == (
                    4 if preselect_one else 2
                )
                assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
                assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
                assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
                assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
        finally:
            if browser is not None:
                await browser.stop()


def test_new_owner_reserved_seat_production_scripts_on_local_flow() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise_reserved_seat())


def test_new_owner_clears_incomplete_seat_before_new_selection() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise_reserved_seat(preselect_one=True))
