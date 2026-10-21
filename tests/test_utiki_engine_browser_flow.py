"""New owner runs production scripts across synthetic local pages only."""

import asyncio
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest
import zendriver as uc

from platforms.utiki.adapters.browser import ZendriverSelectionBrowser
from platforms.utiki.auth import TenantAuthProfile
from platforms.utiki.application.engine import SelectionProgress
from platforms.utiki.application.flow_settings import FlowSettings
from platforms.utiki.application.ports import (
    CoreControl, CoreGate, InlineLoginCredentials, InlineLoginIntent,
    PurchaseAuthority,
)
from platforms.utiki.bootstrap import create_engine
from platforms.utiki.domain.effects import (
    ClaimId, EffectKind, EffectProposal, EffectStatus, RunningEffect,
)
from platforms.utiki.domain.identity import (
    AuthEpoch, DocumentKey, OperationId, OperationStamp, TabKey,
)
from platforms.utiki.profiles import PageKind, TENANT_PROFILES
from utiki_config import apply_current_policy_reload


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


async def _exercise(
    *, tamper_login_before_submit: bool = False,
    known_rejection_dialog: bool = False,
    captcha_reset: bool = False,
    unsafe_refresh_control: str | None = None,
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
            policy = apply_current_policy_reload(None, json.dumps({"utiki": {
                "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                "seat_selection_mode": "manual",
            }}, ensure_ascii=False))
            selection_browser = ZendriverSelectionBrowser(tab, tab_key)
            engine = create_engine(
                policy, selection_browser, LocalCore(),
                FlowSettings(
                    True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
                    auto_submit=True,
                ),
            )
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
                await tab.evaluate("""(() => {
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
                        image.src = makeImage('blue');
                    });
                    document.body.append(image, refresh);
                })()""")
                await _wait_captcha_image(tab)
                if unsafe_refresh_control == "submit":
                    await tab.evaluate("document.querySelector('#chang_pic').type = 'submit'")
                elif unsafe_refresh_control == "link":
                    await tab.evaluate(
                        "document.querySelector('#chang_pic').outerHTML = "
                        "'<a id=\"chang_pic\" href=\"#unsafe\">更換驗證碼</a>'"
                    )
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
                if tamper_login_before_submit:
                    dispatch = selection_browser.dispatch_submit

                    async def tampered_dispatch(plan, claim, login_intent=None, credentials=None):
                        await tab.evaluate(
                            "document.querySelector('#LOGIN_PWD').value = 'changed'"
                        )
                        return await dispatch(plan, claim, login_intent, credentials)

                    selection_browser.dispatch_submit = tampered_dispatch
                    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                    assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 0
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
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                        await tab.evaluate("document.querySelector('#CHK').value = 'WXYZ'")
                        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
                else:
                    assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await tab.evaluate("Number(document.body.dataset.addcartClicks)") == 1
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


def test_submit_guard_rejects_login_changed_after_readback() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(tamper_login_before_submit=True))


def test_known_rejection_dialog_dismisses_once_on_local_flow() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(known_rejection_dialog=True))


def test_known_captcha_reset_verifies_new_local_image_without_resubmit() -> None:
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.skip("set TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1")
    asyncio.run(_exercise(known_rejection_dialog=True, captcha_reset=True))


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
            assert not (await adapter.audit_inline_login(tab_key, intent, credentials)).matched
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
            policy = apply_current_policy_reload(None, json.dumps({"utiki": {
                "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                "seat_selection_mode": "manual",
            }}, ensure_ascii=False))
            engine = create_engine(
                policy, ZendriverSelectionBrowser(tab, tab_key), LocalCore(),
                FlowSettings(
                    True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
                    auto_submit=True,
                ),
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
