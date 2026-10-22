"""Batch 0 complete synthetic flow on a production-shaped, isolated origin."""

import asyncio
import base64
import builtins
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import zendriver as uc
from zendriver import cdp


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from platforms.utiki.adapters.browser import ZendriverSelectionBrowser
from platforms.utiki.adapters.session import BrowserSessionAuthority
from platforms.utiki.adapters.session import install_startup_cookie_set
from platforms.utiki.adapters.refresh import SqliteRefreshAdapter
from platforms.utiki.application.engine import SelectionProgress
from platforms.utiki.application.refresh_contract import RefreshProgress
from platforms.utiki.application.refresh_host import IsolatedRefreshHost
from platforms.utiki.application.flow_settings import apply_effective_policy_reload
from platforms.utiki.application.ports import (
    CoreControl, CoreGate, InlineLoginCredentials, PurchaseAuthority,
)
from platforms.utiki.bootstrap import create_engine, create_run_engine, create_refresh_run_engine
from platforms.utiki.domain.identity import AuthEpoch, TabKey
from platforms.utiki.profiles import PageKind, classify_utiki_page
from refresh_coordinator import SharedRefreshCoordinator, UTK_SPORTS_SCOPE
from nodriver_tixcraft import advance_utiki_route
from settings import get_default_config


pytestmark = pytest.mark.browser
FIXTURES = ROOT / "tests" / "fixtures" / "ctbc"
ORIGIN = "https://tix.ctbcsports.com"
DATE = ORIGIN + "/BROTHERS/UTK0201_?PRODUCT_ID=P16ANF0L"
AREA = ORIGIN + "/BROTHERS/UTK0204_?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L"
QUANTITY = (
    ORIGIN + "/BROTHERS/UTK0203_?PERFORMANCE_ID=P16D0B8Z"
    "&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)
SERVER_QUANTITY = (
    ORIGIN + "/BROTHERS/UTK0201_001?PERFORMANCE_ID=P16D0B8Z"
    "&GROUP_ID=GROUP_FRONT&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT&ATYPE=N"
)
SEAT = (
    ORIGIN + "/BROTHERS/UTK0205_?PERFORMANCE_ID=P16D0B8Z"
    "&GROUP_ID=GROUP_FRONT&PERFORMANCE_PRICE_AREA_ID=PRICE_FRONT"
)
CHECKOUT = ORIGIN + "/BROTHERS/UTK0206_"
SESSION_COOKIE_NAME = "CTBC_fixture|session"
SESSION_COOKIE_VALUE = "synthetic-bound-session"
SESSION_COOKIE = f"{SESSION_COOKIE_NAME}={SESSION_COOKIE_VALUE}"


class ObservedHost:
    """A host seam that grants authority only after reading this tab's origin/session."""

    def __init__(self, tab, tab_key: TabKey) -> None:
        self.tab = tab
        self.tab_key = tab_key
        self.revision = 1
        self.gate = CoreGate.PURCHASE_ALLOWED
        self.document = ""
        self.clearance = None
        self.page_kind = PageKind.UNKNOWN
        self.session = BrowserSessionAuthority(
            tab, tab_key, InlineLoginCredentials("fixture-user", "fixture-password", 1),
        )

    async def observe(self) -> None:
        url = await self.tab.evaluate("location.href")
        document = await self.tab.evaluate("String(performance.timeOrigin)")
        assert isinstance(url, str) and isinstance(document, str)
        if document != self.document:
            self.revision += 1
            self.document = document
        self.page_kind = classify_utiki_page(url)
        await self.session.observe(url)

    def pause(self) -> None:
        self.revision += 1
        self.gate = CoreGate.PAUSED

    def current_control(self, tab: TabKey) -> CoreControl:
        if tab != self.tab_key or self.page_kind is PageKind.UNKNOWN:
            raise ValueError("host tab has no verified route")
        return CoreControl(self.revision, self.gate)

    def current_health_clearance(self, tab: TabKey):
        assert tab == self.tab_key
        return self.clearance

    async def verify_health_without_mutation(self, document) -> bool:
        if document.tab != self.tab_key or document.classification != "normal":
            return False
        if self.document != document.document_id:
            return False
        challenge = await self.tab.evaluate(
            "!!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]')"
        )
        if challenge:
            return False
        self.clearance = (document.url, document.document_id)
        return True

    def current_purchase_authority(self, tab: TabKey) -> PurchaseAuthority | None:
        return self.session.current_purchase_authority(tab)

    def current_inline_login_credentials(
        self, tab: TabKey, tenant_key: str,
    ) -> InlineLoginCredentials | None:
        return self.session.current_inline_login_credentials(tab, tenant_key)

    def current_verified_login(self, tab: TabKey, rejected_attempt_id: str):
        return self.session.current_verified_login(tab, rejected_attempt_id)


class DelayedReadbackBrowser(ZendriverSelectionBrowser):
    """Replay one real precommit browser observation after each quantity write."""

    def __init__(self, tab, tab_key: TabKey) -> None:
        super().__init__(tab, tab_key)
        self.pending_readback = None
        self.quantity_commits: list[int] = []

    async def observe_quantity(self, tab: TabKey):
        if self.pending_readback is not None:
            observed = self.pending_readback
            self.pending_readback = None
            return observed
        return await super().observe_quantity(tab)

    async def commit_quantity_snapshot(self, snapshot, plan, claim):
        previous = await super().observe_quantity(self.bound_tab)
        receipt = await super().commit_quantity_snapshot(snapshot, plan, claim)
        self.quantity_commits.append(plan.ticket_number)
        self.pending_readback = previous
        return receipt


class NotificationRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def start_checkout_notification(self) -> None:
        self.calls += 1


def _policy(*, ticket_number: int = 2, previous=None,
            ocr_path: str = "synthetic-model", computer: bool = False,
            manual_submit: bool = False):
    profile = {
        "utiki": {"ticket_type_keyword": "全票", "seat_sorting": "row_seat",
                  "seat_selection_mode": "computer" if computer else "manual"},
        "date_auto_select": {"enable": True, "date_keyword": '"9/10"',
                             "mode": "from top to bottom"},
        "area_auto_select": {"enable": True, "area_keyword": '"前排"',
                             "mode": "from top to bottom"},
        "date_auto_fallback": False, "area_auto_fallback": False,
        "keyword_exclude": "", "ticket_number": ticket_number,
        "advanced": {"disable_adjacent_seat": False,
                     "auto_reload_page_interval": 5},
        "ocr_captcha": {"enable": not manual_submit,
                        "force_submit": not manual_submit, "beta": True,
                        "image_source": "canvas", "use_universal": True,
                        "path": ocr_path},
        "refresh_datetime": "",
    }
    result = apply_effective_policy_reload(previous, json.dumps(profile, ensure_ascii=False))
    assert result.allows_new_mutation
    return result


def _captcha_image(text: str) -> str:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (60, 20), "white")
    ImageDraw.Draw(image).text((3, 2), text, font=ImageFont.load_default(), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _pages(*, captcha_rejection: bool = False, auth_rejection: bool = False,
           conflict_rejection: bool = False,
           computer: bool = False, seat: bool = False,
           seat_tamper: bool = False) -> dict[str, str]:
    date = (FIXTURES / "UTK0201_flow.html").read_text(encoding="utf-8")
    date = date.replace(
        "UTK0204_flow.html?PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L", AREA,
    )
    area = (FIXTURES / (
        "UTK0204_seat_flow.html" if seat else "UTK0204_flow.html"
    )).read_text(encoding="utf-8")
    if seat:
        area = area.replace(
            "'UTK0205_seat.html?PERFORMANCE_ID=' + performanceId +\n"
            "          '&GROUP_ID=' + groupId + '&PERFORMANCE_PRICE_AREA_ID=' + priceAreaId",
            json.dumps(SEAT),
        )
    if computer:
        area = area.replace("Send('0202'", "Send('0205'")
        area = area.replace("'PRICE_FRONT','','45'", "'PRICE_FRONT','GROUP_FRONT','45'")
        area = area.replace(
            '<button id="byhend" class="active" type="button">手動選位</button>',
            '<button id="byhend" class="active" type="button">手動選位</button>'
            '<div id="ABdiv_ATYPE_CB"><input id="ATYPE_CB" type="checkbox" checked></div>',
        )
        area = area.replace(
            "window.mainMap = {front: true};",
            "window.mainMap = {front: true};"
            "document.querySelector('#byServer').addEventListener('click',()=>{"
            "document.querySelector('#byServer').classList.add('active');"
            "document.querySelector('#byhend').classList.remove('active');});",
        )
        area = area.replace(
            "function Send(_page, performanceId, priceAreaId, groupId) {",
            "function Send(_page, performanceId, priceAreaId, groupId) {"
            "if(_page!=='0205'||groupId!=='GROUP_FRONT'||"
            "!document.querySelector('#byServer').classList.contains('active')||"
            "document.querySelector('#ATYPE_CB').checked)return;"
            "sessionStorage.setItem('syntheticServerNativePrepared','1');",
        )
    area = area.replace(
        "'UTK0203_flow.html?PERFORMANCE_ID=' + performanceId +\n"
        "          '&GROUP_ID=' + groupId + '&PERFORMANCE_PRICE_AREA_ID=' + priceAreaId",
        json.dumps(SERVER_QUANTITY if computer else QUANTITY),
    )
    quantity = (FIXTURES / (
        "UTK0205_seat.html" if seat else
        "UTK0201_001" if computer else "UTK0203_flow.html"
    )).read_text(encoding="utf-8")
    if computer or seat:
        quantity = quantity.replace('value="PRODUCT_FIXTURE"', 'value="P16ANF0L"')
        if seat:
            if seat_tamper:
                quantity = quantity.replace(
                    'style="cursor: default; background-image: none"',
                    'style="cursor: pointer; background-image: url(\'seat-empty-fixture.png\')"'
                    ' onclick="toggleSeat(this, \'S3\')"',
                )
            quantity = quantity.replace(
                "seat-empty-fixture.png", "data:,seat-empty-fixture.png",
            ).replace(
                "icon_chair_select_fixture.png", "data:,icon_chair_select_fixture.png",
            )
        if computer:
            quantity = quantity.replace('    <img id="chk_pic" alt="captcha">\n', '')
            quantity = quantity.replace(
                '    <button id="chang_pic" type="button">更換驗證碼</button>\n', '',
            )
        quantity = quantity.replace(
            '<button id="addcart"',
            '<input id="LOGIN_ID" value=""><input id="LOGIN_PWD" type="password" value="">'
            '<button id="addcart"',
        )
    if captcha_rejection or auth_rejection or conflict_rejection:
        rejection_text = (
            "驗證碼錯誤" if captcha_rejection else
            "帳號或密碼錯誤" if auth_rejection else "該位置已被選走"
        )
        challenge = (
            '<img id="chk_pic" width="60" height="20" src='
            + json.dumps(_captcha_image("WXYZ")) + '>'
            '<button id="chang_pic" type="button">更換驗證碼</button>'
            '<div class="ui-dialog" style="display:none">'
            '<div id="dialog-message">' + rejection_text + '</div>'
            '<div class="ui-dialog-buttonset"><button type="button">確定</button></div>'
            '</div>'
        )
        callback = (
            "const n=Number(sessionStorage.getItem('syntheticSubmitCount')||'0')+1;"
            "sessionStorage.setItem('syntheticSubmitCount',String(n));"
            "if(n===1){document.querySelector('.ui-dialog').style.display='block';}"
            f"else{{setTimeout(() => location.href = {json.dumps(CHECKOUT)}, 20);}}"
        )
        setup = (
            (
                "document.querySelector('#chang_pic').addEventListener('click',()=>{"
                "document.querySelector('#chk_pic').src="
                + json.dumps(_captcha_image("ABCD")) + ";});"
            ) if captcha_rejection else ""
        ) + (
            "document.querySelector('.ui-dialog-buttonset button').addEventListener"
            "('click',()=>{document.querySelector('.ui-dialog').style.display='none';});"
        )
    else:
        challenge = (
            '<img id="chk_pic" width="60" height="20" src='
            + json.dumps(_captcha_image("WXYZ")) + '>'
        )
        callback = (
            "sessionStorage.setItem('syntheticSubmitCount', "
            "String(Number(sessionStorage.getItem('syntheticSubmitCount') || '0') + 1));"
            f"setTimeout(() => location.href = {json.dumps(CHECKOUT)}, 20);"
        )
        setup = ""
    quantity = quantity.replace(
        "</body>", challenge + "<script>" + setup +
        "document.querySelector('#addcart').addEventListener('click', () => {" +
        callback + "});</script></body>",
    )
    checkout = "<!doctype html><html><head><title>Synthetic checkout</title></head>"
    checkout += "<body><h1>Known checkout</h1></body></html>"
    favicon = '<link rel="icon" href="data:,">'
    return {
        DATE: date.replace("<head>", "<head>" + favicon, 1),
        AREA: area.replace("<head>", "<head>" + favicon, 1),
        SEAT if seat else SERVER_QUANTITY if computer else QUANTITY:
            quantity.replace("<head>", "<head>" + favicon, 1),
        CHECKOUT: checkout.replace("<head>", "<head>" + favicon, 1),
    }


async def _wait_page(tab, expected: str) -> None:
    for _ in range(120):
        if await tab.evaluate("location.href") == expected:
            return
        await asyncio.sleep(0.03)
    raise AssertionError(f"synthetic navigation did not reach {expected}")


async def _exercise(*, captcha_rejection: bool = False,
                    auth_rejection: bool = False,
                    auth_tamper: bool = False,
                    conflict_rejection: bool = False,
                    conflict_tamper: bool = False,
                    computer: bool = False,
                    seat: bool = False,
                    manual_submit: bool = False) -> None:
    browser = None
    profile = tempfile.TemporaryDirectory(prefix="utiki-batch0-origin-")
    pages = _pages(
        captcha_rejection=captcha_rejection, auth_rejection=auth_rejection,
        conflict_rejection=conflict_rejection, computer=computer, seat=seat,
        seat_tamper=conflict_tamper and seat,
    )
    quantity_url = SEAT if seat else SERVER_QUANTITY if computer else QUANTITY
    intercepted: list[str] = []
    rejected: list[str] = []
    try:
        browser = await uc.start(
            user_data_dir=profile.name, headless=True, sandbox=False,
            browser_args=[
                "--disable-background-networking", "--disable-default-apps",
                "--disable-sync", "--no-default-browser-check", "--no-first-run",
            ],
        )
        tab = browser.main_tab

        async def handle_request(event) -> None:
            url = event.request.url
            intercepted.append(url)
            html = pages.get(url)
            if html is None:
                rejected.append(url)
                await tab.send(cdp.fetch.fail_request(
                    request_id=event.request_id,
                    error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                ))
                return
            headers = [cdp.fetch.HeaderEntry(
                name="Content-Type", value="text/html; charset=utf-8",
            )]
            if url == DATE:
                headers.append(cdp.fetch.HeaderEntry(
                    name="Set-Cookie", value=SESSION_COOKIE + "; Path=/BROTHERS; SameSite=Lax; Secure; HttpOnly",
                ))
            await tab.send(cdp.fetch.fulfill_request(
                request_id=event.request_id, response_code=200,
                response_headers=headers,
                body=base64.b64encode(html.encode("utf-8")).decode("ascii"),
            ))

        tab.add_handler(cdp.fetch.RequestPaused, handle_request)
        await tab.send(cdp.fetch.enable([cdp.fetch.RequestPattern(
            url_pattern="*", request_stage=cdp.fetch.RequestStage.REQUEST,
        )]))
        await asyncio.wait_for(tab.get(DATE), timeout=10.0)
        await _wait_page(tab, DATE)
        await tab.evaluate(
            "(() => { window.__blockedRequestResult = 'pending';"
            "void fetch('https://fixture.invalid/blocked')"
            ".then(() => { window.__blockedRequestResult = 'unexpected'; })"
            ".catch(() => { window.__blockedRequestResult = 'blocked'; }); })()"
        )
        for _ in range(120):
            if await tab.evaluate("window.__blockedRequestResult") == "blocked":
                break
            await asyncio.sleep(0.03)
        assert await tab.evaluate("window.__blockedRequestResult") == "blocked"
        assert rejected == ["https://fixture.invalid/blocked"]
        rejected.clear()
        tab_key = TabKey(1, "batch0-isolated-tab")
        host = ObservedHost(tab, tab_key)
        await host.observe()
        assert host.page_kind is PageKind.DATE
        assert host.current_purchase_authority(tab_key) is None
        adapter = DelayedReadbackBrowser(tab, tab_key)
        notifier = NotificationRecorder()
        ocr_path = "assets/model/universal" if captcha_rejection else "synthetic-model"
        initial_policy = _policy(
            ocr_path=ocr_path, computer=computer, manual_submit=manual_submit,
        )
        coordinator = None
        harness = None
        if conflict_rejection:
            coordinator = SharedRefreshCoordinator(Path(profile.name) / "refresh.sqlite3")
            refresh_adapter = SqliteRefreshAdapter(
                coordinator, lambda _force: {
                    "enabled": True, "min_interval_seconds": 0.1,
                    "initial_cooldown_seconds": 0.1,
                    "max_cooldown_seconds": 1.0,
                },
            )
            engine = create_refresh_run_engine(
                initial_policy, adapter, host, refresh_adapter, notifier,
            )
            harness = IsolatedRefreshHost(engine, host)
        else:
            engine = (
                create_run_engine(initial_policy, adapter, host, notifier)
                if captcha_rejection else create_engine(
                    initial_policy, adapter, host, checkout_notification=notifier,
                )
            )
        if captcha_rejection and not manual_submit:
            assert engine._captcha_provider is not None
        handle = engine.attach(tab_key)

        if harness is not None:
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING

        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        await _wait_page(tab, AREA)
        await host.observe()
        assert engine.update_control(handle)
        if harness is not None:
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        assert host.page_kind is PageKind.AREA
        assert host.current_purchase_authority(tab_key) is None
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        await _wait_page(tab, quantity_url)
        await host.observe()
        assert engine.update_control(handle)
        if harness is not None:
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
        assert host.page_kind is (
            PageKind.RESERVED_SEAT if seat else
            PageKind.SERVER_ASSIGNED_QUANTITY if computer else PageKind.GENERAL_ADMISSION
        )
        assert host.current_purchase_authority(tab_key) is not None
        # A scoped browser session is an identity boundary, not accepted login.
        inputs = await adapter.observe_purchase_input(tab_key)
        assert inputs.login_required and not inputs.login_ready
        if seat:
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        else:
            assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
            updated_policy = _policy(
                ticket_number=4, previous=initial_policy, ocr_path=ocr_path,
                computer=computer, manual_submit=manual_submit,
            )
            assert updated_policy.policy_revision > initial_policy.policy_revision
            assert engine.update_policy(updated_policy)
            assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
            assert adapter.quantity_commits == [2, 4]
            assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        await tab.send(cdp.network.delete_cookies(
            name=SESSION_COOKIE_NAME, domain="tix.ctbcsports.com", path="/BROTHERS",
        ))
        await host.observe()
        assert host.current_purchase_authority(tab_key) is None
        assert await engine.advance_login(handle) is SelectionProgress.BLOCKED
        assert await tab.send(cdp.network.set_cookie(
            name=SESSION_COOKIE_NAME, value=SESSION_COOKIE_VALUE,
            domain="tix.ctbcsports.com", path="/BROTHERS", secure=True, http_only=True,
        )) is not False
        await host.observe()
        assert host.current_purchase_authority(tab_key) == PurchaseAuthority(AuthEpoch(3), 1)
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        if manual_submit:
            assert captcha_rejection and computer
            assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
            position = await tab.evaluate("""(() => {
                const r = document.querySelector('#addcart').getBoundingClientRect();
                return [r.left + r.width / 2, r.top + r.height / 2];
            })()""")
            x, y = position

            async def click_submit() -> None:
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mousePressed", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=1, click_count=1,
                ))
                await tab.send(cdp.input_.dispatch_mouse_event(
                    "mouseReleased", x, y, button=cdp.input_.MouseButton.LEFT,
                    buttons=0, click_count=1,
                ))

            await click_submit()
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
            for _ in range(120):
                reset_progress = await engine.advance_captcha_reset(handle)
                if reset_progress is SelectionProgress.CAPTCHA_RESET_VERIFIED:
                    break
                assert reset_progress is SelectionProgress.AWAITING_EVIDENCE
                await asyncio.sleep(0.03)
            else:
                raise AssertionError("new synthetic CAPTCHA image was not verified")
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            await tab.evaluate("document.querySelector('#CHK').focus()")
            await tab.send(cdp.input_.insert_text(text="ABCD"))
            assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
            await click_submit()
        else:
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        if auth_rejection:
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_login(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
            assert await tab.evaluate("document.querySelector('#LOGIN_PWD').value") == "fixture-password"
            await tab.evaluate("(() => { const p=document.querySelector('#LOGIN_PWD');"
                               "p.focus();p.setSelectionRange(0,p.value.length); })()")
            await tab.send(cdp.input_.insert_text(text="fixture-password"))
            assert await engine.advance_login(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
            await tab.send(cdp.input_.insert_text(text="-manual-change"))
            assert await engine.advance_login(handle) is SelectionProgress.LOGIN_RECOVERY_READY
            assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
            assert await tab.evaluate("document.querySelector('#LOGIN_PWD').value") == "fixture-password-manual-change"
            if auth_tamper:
                await tab.evaluate("document.querySelector('#LOGIN_PWD').value='script-change'")
                assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
                assert await tab.evaluate("sessionStorage.getItem('syntheticSubmitCount')") == "1"
                assert notifier.calls == 0
                return
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        if conflict_rejection:
            assert harness is not None
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            if conflict_tamper:
                if seat:
                    await tab.evaluate("document.querySelector('[data-seat-id=S2]').click()")
                    await tab.evaluate("document.querySelector('[data-seat-id=S3]').click()")
                else:
                    await tab.evaluate("(() => {const x=document.querySelector('input.numbox[key=FULL]');"
                                       "x.focus();x.setSelectionRange(0,x.value.length);})()")
                    await tab.send(cdp.input_.insert_text(text="3"))
                assert await engine.advance_conflict_recovery(handle) is SelectionProgress.AWAITING_EVIDENCE
                assert await engine.advance_conflict_recovery(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
                if seat:
                    assert await tab.evaluate("Object.keys(window.seats).sort().join(',')") == "S1,S3"
                else:
                    assert await tab.evaluate("document.querySelector('input.numbox[key=FULL]').value") == "3"
                assert await tab.evaluate("sessionStorage.getItem('syntheticSubmitCount')") == "1"
                assert intercepted.count(quantity_url) == 1
                return
            assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_CLEARED
            assert await engine.advance_conflict_recovery(handle) is SelectionProgress.AWAITING_EVIDENCE
            await host.observe()
            assert engine.update_control(handle)
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.COMPLETE
            assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_RECOVERED
            for _ in range(5):
                selected = (
                    await engine.advance_seat(handle) if seat
                    else await engine.advance_quantity(handle)
                )
                if selected is (
                    SelectionProgress.SEAT_READY if seat else SelectionProgress.QUANTITY_READY
                ):
                    break
            else:
                raise AssertionError("fresh source did not reselect after conflict")
            assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        if captcha_rejection and not manual_submit:
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
            for _ in range(120):
                reset_progress = await engine.advance_captcha_reset(handle)
                if reset_progress is SelectionProgress.CAPTCHA_RESET_VERIFIED:
                    break
                assert reset_progress is SelectionProgress.AWAITING_EVIDENCE
                await asyncio.sleep(0.03)
            else:
                raise AssertionError("new synthetic CAPTCHA image was not verified")
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            assert await tab.evaluate("document.querySelector('#CHK').value") == "ABCD"
        host.pause()
        assert engine.update_control(handle)
        await _wait_page(tab, CHECKOUT)
        await host.observe()
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert notifier.calls == 1
        assert await tab.evaluate("sessionStorage.getItem('syntheticSubmitCount')") == (
            "2" if captcha_rejection or auth_rejection or conflict_rejection else "1"
        )
        if computer:
            assert await tab.evaluate(
                "sessionStorage.getItem('syntheticServerNativePrepared')"
            ) == "1"
        assert intercepted.count(DATE) == 1
        assert intercepted.count(AREA) == 1
        assert intercepted.count(quantity_url) == (2 if conflict_rejection else 1)
        assert intercepted.count(CHECKOUT) == 1
        assert not rejected
    finally:
        if browser is not None:
            await browser.stop()
        profile.cleanup()


def test_batch0_complete_production_origin_flow() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise())


def test_batch1_rejection_ocr_and_new_submit_on_isolated_production_origin() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(captcha_rejection=True))


@pytest.mark.parametrize("captcha_rejection", [False, True])
def test_batch2_server_assignment_on_isolated_production_origin(
    captcha_rejection: bool,
) -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(captcha_rejection=captcha_rejection, computer=True))


def test_batch2_server_assignment_manual_rejection_on_isolated_origin() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(captcha_rejection=True, computer=True, manual_submit=True))


def test_batch4_auth_rejection_uses_trusted_browser_edit_on_isolated_origin() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(auth_rejection=True))


def test_batch4_untrusted_login_change_keeps_original_rejection() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(auth_rejection=True, auth_tamper=True))


@pytest.mark.parametrize("computer", [False, True])
def test_batch4_quantity_conflict_clears_and_reloads_on_isolated_origin(
    computer: bool,
) -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(conflict_rejection=True, computer=computer))


def test_batch4_reserved_conflict_clears_and_reloads_on_isolated_origin() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(conflict_rejection=True, seat=True))


def test_batch4_new_manual_quantity_is_not_cleared_after_rejection() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(conflict_rejection=True, conflict_tamper=True))


def test_batch4_foreign_seat_payload_is_not_cleared_after_rejection() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")
    asyncio.run(_exercise(conflict_rejection=True, conflict_tamper=True, seat=True))


def test_batch3_real_coordinator_reload_and_same_url_health_clearance() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("production-origin flow lacks independent network isolation")

    async def run() -> None:
        browser = None
        profile = tempfile.TemporaryDirectory(prefix="utiki-batch3-origin-")
        pages = _pages()
        intercepted = []
        rejected = []
        try:
            browser = await uc.start(
                user_data_dir=profile.name, headless=True, sandbox=False,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            tab = browser.main_tab

            async def handle_request(event) -> None:
                url = event.request.url
                intercepted.append(url)
                html = pages.get(url)
                if html is None:
                    rejected.append(url)
                    await tab.send(cdp.fetch.fail_request(
                        request_id=event.request_id,
                        error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                    ))
                    return
                headers = [cdp.fetch.HeaderEntry(
                    name="Content-Type", value="text/html; charset=utf-8",
                )]
                if url == DATE:
                    headers.append(cdp.fetch.HeaderEntry(
                        name="Set-Cookie", value=SESSION_COOKIE + "; Path=/BROTHERS; SameSite=Lax; Secure; HttpOnly",
                    ))
                await tab.send(cdp.fetch.fulfill_request(
                    request_id=event.request_id, response_code=200,
                    response_headers=headers,
                    body=base64.b64encode(html.encode("utf-8")).decode("ascii"),
                ))

            tab.add_handler(cdp.fetch.RequestPaused, handle_request)
            await tab.send(cdp.fetch.enable([cdp.fetch.RequestPattern(
                url_pattern="*", request_stage=cdp.fetch.RequestStage.REQUEST,
            )]))
            await asyncio.wait_for(tab.get(DATE), timeout=10.0)
            await _wait_page(tab, DATE)
            tab_key = TabKey(1, "batch3-isolated-tab")
            host = ObservedHost(tab, tab_key)
            await host.observe()
            assert host.page_kind is PageKind.DATE
            assert host.current_purchase_authority(tab_key) is None
            selection_browser = ZendriverSelectionBrowser(tab, tab_key)
            coordinator = SharedRefreshCoordinator(
                Path(profile.name) / "refresh.sqlite3",
                random_uniform=lambda _a, _b: 0.1,
            )
            shared = SqliteRefreshAdapter(coordinator, lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            })
            engine = create_refresh_run_engine(
                _policy(), selection_browser, host, shared,
            )
            handle = engine.attach(tab_key)
            harness = IsolatedRefreshHost(engine, host)
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
            assert engine.context(handle) is not None
            before = host.document
            progress = await harness.tick(handle, schedule_due=True)
            assert progress in {
                RefreshProgress.RELOAD_POSSIBLE,
                RefreshProgress.AWAITING_CLEARANCE,
            }
            assert intercepted.count(DATE) == 2
            assert host.current_health_clearance(tab_key) == (DATE, before)
            assert engine.context(handle) is None
            await host.observe()
            assert host.document != before
            assert engine.update_control(handle)
            for _ in range(5):
                progress = await harness.tick(handle, schedule_due=True)
                if progress is RefreshProgress.COMPLETE:
                    break
            assert progress is RefreshProgress.COMPLETE
            assert engine.context(handle) is not None
            assert await harness.tick(handle, schedule_due=True) is RefreshProgress.COMPLETE
            assert intercepted.count(DATE) == 2
            assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["sequence"] == 1
            pages[DATE] = (
                "<!doctype html><html><head><link rel='icon' href='data:,'>"
                "</head><body>HTTP 429 Too Many Requests"
                "</body></html>"
            )
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
            for _ in range(30):
                progress = await harness.tick(handle, schedule_due=True)
                if intercepted.count(DATE) == 3:
                    break
                await asyncio.sleep(0.1)
            assert intercepted.count(DATE) == 3
            assert progress in {
                RefreshProgress.RELOAD_POSSIBLE,
                RefreshProgress.AWAITING_CLEARANCE,
                RefreshProgress.BLOCKED,
            }
            observed_429 = await selection_browser.observe_refresh_document(tab_key)
            assert observed_429.classification == "blocked"
            assert await harness.tick(handle, schedule_due=False) is RefreshProgress.WAITING
            assert coordinator.snapshot_sync(UTK_SPORTS_SCOPE)["bucket"]["consecutive_429"] == 1
            assert intercepted.count(DATE) == 3
            assert rejected == []
        finally:
            if browser is not None:
                await browser.stop()
            profile.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("mode,scenario", [
    ("general", "normal"), ("seat", "normal"), ("computer", "normal"),
    ("general", "captcha"), ("seat", "conflict"), ("general", "auth"),
    ("general", "auth-manual"), ("general", "policy"),
    ("general", "late-policy"),
    ("general", "scheduled"), ("general", "challenge"),
    ("general", "startup-cookie"), ("general", "no-session"),
])
def test_batch5_formal_route_reaches_attributed_result(mode: str, scenario: str) -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("formal route lacks independent network isolation")

    async def run_flow() -> None:
        browser = None
        run = None
        sandbox = tempfile.TemporaryDirectory(prefix="utiki-batch5-origin-")
        is_seat, is_computer = mode == "seat", mode == "computer"
        pages = _pages(
            seat=is_seat, computer=is_computer,
            captcha_rejection=scenario == "captcha",
            auth_rejection=scenario in {"auth", "auth-manual"},
            conflict_rejection=scenario == "conflict",
        )
        if scenario == "challenge":
            pages[DATE] = pages[DATE].replace(
                "</body>", "<div id='cf-challenge-running'></div></body>",
            )
        purchase_url = SEAT if is_seat else SERVER_QUANTITY if is_computer else QUANTITY
        intercepted: list[str] = []
        rejected: list[str] = []
        profile = get_default_config()
        profile["homepage"] = DATE
        profile["date_auto_select"].update({
            "enable": True, "date_keyword": '"9/10"', "mode": "from top to bottom",
        })
        profile["area_auto_select"].update({
            "enable": True, "area_keyword": '"前排"', "mode": "from top to bottom",
        })
        profile["ticket_number"] = 2
        profile["keyword_exclude"] = ""
        profile["utiki"]["seat_selection_mode"] = "computer" if is_computer else "manual"
        profile["ocr_captcha"].update({
            "enable": True, "force_submit": True, "use_universal": True,
            "path": "assets/model/universal" if scenario == "captcha" else "synthetic-model",
        })
        profile["accounts"]["ctbc_account"] = "fixture-user"
        profile["accounts"]["ctbc_password"] = "fixture-password"
        if scenario == "startup-cookie":
            profile["accounts"]["ctbc_cookie"] = SESSION_COOKIE
        if scenario == "scheduled":
            profile["refresh_datetime"] = (
                datetime.now() + timedelta(seconds=2)
            ).strftime("%Y/%m/%d %H:%M:%S")
        shared = SqliteRefreshAdapter(
            SharedRefreshCoordinator(Path(sandbox.name) / "refresh.sqlite3"),
            lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            },
        )
        try:
            browser = await uc.start(
                user_data_dir=sandbox.name, headless=True, sandbox=False,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            tab = browser.main_tab

            async def handle_request(event) -> None:
                url = event.request.url
                intercepted.append(url)
                html = pages.get(url)
                if html is None:
                    rejected.append(url)
                    await tab.send(cdp.fetch.fail_request(
                        request_id=event.request_id,
                        error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                    ))
                    return
                headers = [cdp.fetch.HeaderEntry(
                    name="Content-Type", value="text/html; charset=utf-8",
                )]
                if url == DATE and scenario not in {"startup-cookie", "no-session"}:
                    headers.append(cdp.fetch.HeaderEntry(
                        name="Set-Cookie",
                        value=SESSION_COOKIE + "; Path=/BROTHERS; SameSite=Lax; Secure; HttpOnly",
                    ))
                await tab.send(cdp.fetch.fulfill_request(
                    request_id=event.request_id, response_code=200,
                    response_headers=headers,
                    body=base64.b64encode(html.encode("utf-8")).decode("ascii"),
                ))

            tab.add_handler(cdp.fetch.RequestPaused, handle_request)
            await tab.send(cdp.fetch.enable([cdp.fetch.RequestPattern(
                url_pattern="*", request_stage=cdp.fetch.RequestStage.REQUEST,
            )]))
            if scenario == "startup-cookie":
                assert await install_startup_cookie_set(
                    tab, DATE, profile["accounts"],
                )
            await asyncio.wait_for(tab.get(DATE), timeout=10.0)
            await _wait_page(tab, DATE)
            raw = json.dumps(profile, ensure_ascii=False)
            if scenario == "policy":
                run, paused = await advance_utiki_route(
                    tab, DATE, profile, raw, paused=True, refresh_adapter=shared,
                )
                assert paused.progress == "paused"
                original_engine = run.engine
                assert not run.update_policy('{"ctbc": {}}')
                run, blocked = await advance_utiki_route(tab, DATE, profile, raw, run)
                assert blocked.progress != SelectionProgress.DATE_DISPATCHED.value
                assert await tab.evaluate("location.href") == DATE
                assert run.update_policy(raw)
                assert run.engine is original_engine
            edited_login = False
            challenge_ticks = 0
            manual_ticks = 0
            invalidated_after_submit = False
            trail = []
            for _ in range(260):
                url = await tab.evaluate("location.href")
                run, result = await advance_utiki_route(
                    tab, url, profile, raw, run, refresh_adapter=shared,
                )
                trail.append((url, result.progress))
                if (
                    scenario == "late-policy" and not invalidated_after_submit
                    and result.progress == SelectionProgress.SUBMIT_DISPATCHED.value
                ):
                    original_engine = run.engine
                    assert not run.update_policy('{"ctbc": {}}')
                    assert run.engine is original_engine
                    invalidated_after_submit = True
                if result.progress == SelectionProgress.SUBMIT_CONFIRMED.value:
                    break
                if scenario == "auth-manual" and result.progress == SelectionProgress.MANUAL_ACTION_REQUIRED.value:
                    manual_ticks += 1
                    if manual_ticks == 3:
                        break
                if scenario == "no-session" and result.progress == "manual-action-required":
                    break
                if scenario == "challenge" and await tab.evaluate("!!document.querySelector('#cf-challenge-running')"):
                    challenge_ticks += 1
                    assert await tab.evaluate("location.href") == DATE
                    if challenge_ticks == 3:
                        await tab.evaluate("document.querySelector('#cf-challenge-running').remove()")
                if scenario == "auth" and result.progress == SelectionProgress.MANUAL_ACTION_REQUIRED.value and not edited_login:
                    await tab.evaluate(
                        "(() => { const p=document.querySelector('#LOGIN_PWD');"
                        "p.focus();p.setSelectionRange(0,p.value.length); })()"
                    )
                    await tab.send(cdp.input_.insert_text(text="fixture-password-manual-change"))
                    edited_login = True
                await asyncio.sleep(0.03)
            else:
                raise AssertionError(f"formal route did not finish: {result}; {trail[-25:]}; {intercepted}")
            if scenario == "auth-manual":
                assert result.manual_reason.startswith("accepted manual login cannot be verified")
                assert await tab.evaluate("location.href") == purchase_url
                assert await tab.evaluate(
                    "sessionStorage.getItem('syntheticSubmitCount')"
                ) == "1"
                assert run.engine.submission_state(run.handle) == (
                    SelectionProgress.SUBMIT_BLOCKED, "auth",
                )
                # A later manual navigation has no new submit claim. Keep the
                # original rejection and make checkout visible for inspection.
                await tab.get(CHECKOUT)
                await _wait_page(tab, CHECKOUT)
                run, late = await advance_utiki_route(
                    tab, CHECKOUT, profile, raw, run, refresh_adapter=shared,
                )
                assert late.progress == "checkout-needs-attribution"
                assert late.manual_reason.startswith("inspect checkout manually")
                assert run.engine.submission_state(run.handle) == (
                    SelectionProgress.SUBMIT_BLOCKED, "auth",
                )
                assert await tab.evaluate(
                    "sessionStorage.getItem('syntheticSubmitCount')"
                ) == "1"
                return
            if scenario == "no-session":
                assert result.manual_reason.startswith("scoped purchase session is unavailable")
                assert await tab.evaluate("location.href") == purchase_url
                assert await tab.evaluate(
                    "sessionStorage.getItem('syntheticSubmitCount')"
                ) is None
                return
            assert await tab.evaluate("location.href") == CHECKOUT
            assert intercepted.count(DATE) == (2 if scenario == "scheduled" else 1)
            assert intercepted.count(AREA) == 1
            assert intercepted.count(purchase_url) == (2 if scenario == "conflict" else 1)
            assert intercepted.count(CHECKOUT) == 1
            assert not rejected
            assert await tab.evaluate(
                "sessionStorage.getItem('syntheticSubmitCount')"
            ) == ("2" if scenario in {"captcha", "auth", "conflict"} else "1")
            assert run.engine.submission_state(run.handle)[0] is SelectionProgress.SUBMIT_CONFIRMED
            if scenario == "late-policy":
                assert invalidated_after_submit
                assert not run.policy.allows_new_mutation
        finally:
            if run is not None:
                await run.close()
            if browser is not None:
                await browser.stop()
            sandbox.cleanup()

    asyncio.run(run_flow())


def test_batch5_main_caller_uses_formal_owner_on_isolated_origin() -> None:
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.skip("requires the hard-egress browser launcher")
    interfaces = Path("/sys/class/net")
    if os.name != "posix" or not interfaces.exists() or {
        item.name for item in interfaces.iterdir()
    } != {"lo"}:
        pytest.fail("formal main lacks independent network isolation")

    async def run_main() -> None:
        from nodriver_tixcraft import main

        browser = None
        sandbox = tempfile.TemporaryDirectory(prefix="utiki-batch5-main-")
        profile = get_default_config()
        profile["homepage"] = DATE
        profile["advanced"]["headless"] = True
        profile["advanced"]["play_sound"]["order"] = False
        profile["date_auto_select"].update({
            "enable": True, "date_keyword": '"9/10"', "mode": "from top to bottom",
        })
        profile["area_auto_select"].update({
            "enable": True, "area_keyword": '"前排"', "mode": "from top to bottom",
        })
        profile["ticket_number"] = 1
        profile["ocr_captcha"].update({
            "enable": True, "force_submit": True, "use_universal": True,
            "path": "synthetic-model",
        })
        profile["accounts"]["ctbc_cookie"] = SESSION_COOKIE
        profile["accounts"]["ctbc_account"] = "fixture-user"
        profile["accounts"]["ctbc_password"] = "fixture-password"
        config_path = Path(sandbox.name) / "profile.json"
        config_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        args = SimpleNamespace(
            input=str(config_path), instance=None, mcp_debug=False,
            headless=None, homepage=None, ticket_number=2, browser=None,
            proxy_server=None, window_size=None, date_auto_select_mode=None,
            date_keyword=None, area_auto_select_mode=None, area_keyword=None,
        )
        pages = _pages()
        pages[QUANTITY] = pages[QUANTITY].replace(
            "document.querySelector('#addcart').addEventListener('click', () => {",
            "document.querySelector('#addcart').addEventListener('click', () => {"
            "sessionStorage.setItem('syntheticTicketCount', "
            "document.querySelector('input.numbox[key=\"FULL\"]').value);",
            1,
        )
        rejected = []
        checkout_checks = 0
        submit_count_at_quit = None
        selected_count = None
        original_print = builtins.print
        printed = []
        shared = SqliteRefreshAdapter(
            SharedRefreshCoordinator(Path(sandbox.name) / "refresh.sqlite3"),
            lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            },
        )
        try:
            browser = await uc.start(
                user_data_dir=sandbox.name, headless=True, sandbox=False,
                browser_args=[
                    "--disable-background-networking", "--disable-default-apps",
                    "--disable-sync", "--no-default-browser-check", "--no-first-run",
                ],
            )
            tab = browser.main_tab

            async def handle_request(event) -> None:
                url = event.request.url
                html = pages.get(url)
                if html is None:
                    rejected.append(url)
                    await tab.send(cdp.fetch.fail_request(
                        request_id=event.request_id,
                        error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                    ))
                    return
                await tab.send(cdp.fetch.fulfill_request(
                    request_id=event.request_id, response_code=200,
                    response_headers=[cdp.fetch.HeaderEntry(
                        name="Content-Type", value="text/html; charset=utf-8",
                    )],
                    body=base64.b64encode(html.encode("utf-8")).decode("ascii"),
                ))

            tab.add_handler(cdp.fetch.RequestPaused, handle_request)
            await tab.send(cdp.fetch.enable([cdp.fetch.RequestPattern(
                url_pattern="*", request_stage=cdp.fetch.RequestStage.REQUEST,
            )]))

            async def homepage(_driver, _config):
                await tab.get(DATE)
                await _wait_page(tab, DATE)
                return tab

            async def keep_tab(current_tab, _config):
                return current_tab

            async def quit_after_result(_config):
                nonlocal checkout_checks, submit_count_at_quit, selected_count
                current_url = await tab.evaluate("location.href")
                if current_url == CHECKOUT:
                    checkout_checks += 1
                    submit_count_at_quit = await tab.evaluate(
                        "sessionStorage.getItem('syntheticSubmitCount')"
                    )
                    selected_count = await tab.evaluate(
                        "sessionStorage.getItem('syntheticTicketCount')"
                    )
                return checkout_checks >= 3

            def capture_print(*parts, **_kwargs):
                printed.append(" ".join(str(part) for part in parts))

            with (
                patch("nodriver_tixcraft.uc.start", return_value=browser),
                patch("nodriver_tixcraft.get_extension_config", return_value=SimpleNamespace()),
                patch("nodriver_tixcraft.nodriver_overwrite_prefs"),
                patch("nodriver_tixcraft.nodriver_goto_homepage", side_effect=homepage),
                patch("nodriver_tixcraft.nodrver_block_urls", side_effect=keep_tab),
                patch("nodriver_tixcraft.check_and_handle_quit", side_effect=quit_after_result),
                patch("nodriver_tixcraft.check_and_handle_pause", return_value=False),
                patch("nodriver_tixcraft.write_last_url_to_file"),
                patch("platforms.utiki.production_run.SqliteRefreshAdapter", return_value=shared),
                patch("nodriver_tixcraft.util.get_instance_state_path",
                      side_effect=lambda name: str(Path(sandbox.name) / name)),
                patch("builtins.print", side_effect=capture_print),
            ):
                try:
                    await asyncio.wait_for(main(args), timeout=30.0)
                except TimeoutError as exc:
                    current_url = await tab.evaluate("location.href")
                    raise AssertionError(
                        f"main did not finish: url={current_url}, "
                        f"progress={printed[-25:]}, rejected={rejected}"
                    ) from exc

            assert submit_count_at_quit == "1"
            assert selected_count == "2"
            assert any("[uTiki] submit-confirmed" in line for line in printed)
            assert not rejected
        finally:
            builtins.print = original_print
            if browser is not None:
                try:
                    await browser.stop()
                except Exception:
                    pass
            sandbox.cleanup()

    asyncio.run(run_main())
