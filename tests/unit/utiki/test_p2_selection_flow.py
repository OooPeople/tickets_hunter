"""Offline date -> area -> quantity flow and exclusive claim behavior."""

import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import patch

from platforms.utiki.auth import TENANT_AUTH_PROFILES
from platforms.utiki.application.engine import SelectionProgress
from platforms.utiki.application.flow_settings import FlowSettings
from platforms.utiki.application.ports import (
    CaptchaResetObservation, CoreControl, CoreGate, InlineLoginAudit, InlineLoginCredentials,
    PageObservation, PurchaseAuthority, PurchaseInputObservation,
    SubmitOutcomeObservation,
)
from platforms.utiki.bootstrap import create_engine
from platforms.utiki.domain.effects import (
    ClaimId, DispatchStage, EffectEvidence, EffectReceipt, EffectStatus,
    RunningEffect,
)
from platforms.utiki.domain.identity import AuthEpoch, DocumentKey, TabKey
from platforms.utiki.domain.inputs import CaptchaImageKey
from platforms.utiki.domain.snapshots import AreaSnapshot, DateSnapshot, DialogSnapshot, QuantitySnapshot, SeatSnapshot
from platforms.utiki.selection import (
    parse_area_snapshot, parse_date_snapshot, parse_quantity_snapshot, parse_seat_snapshot,
)
from tests.ctbc_support import (
    CTBC_TEST_AREA_URL, CTBC_TEST_QUANTITY_URL, CTBC_TEST_SEAT_URL,
    make_ctbc_area_snapshot, make_ctbc_date_snapshot,
    make_ctbc_quantity_snapshot, make_ctbc_seat_snapshot,
)
from utiki_config import apply_current_policy_reload


class FakeCore:
    def __init__(self, tab: TabKey) -> None:
        self.tab = tab
        self.control = CoreControl(1, CoreGate.PURCHASE_ALLOWED)
        self.authority: PurchaseAuthority | None = PurchaseAuthority(AuthEpoch(1), 1)
        self.login_credentials = InlineLoginCredentials("fixture-account", "fixture-password", 1)

    def current_control(self, tab: TabKey) -> CoreControl:
        assert tab == self.tab
        return self.control

    def current_purchase_authority(self, tab: TabKey) -> PurchaseAuthority | None:
        assert tab == self.tab
        return self.authority

    def current_inline_login_credentials(
        self, tab: TabKey, tenant_key: str,
    ) -> InlineLoginCredentials | None:
        assert tab == self.tab and tenant_key == "ctbc_brothers"
        return self.login_credentials


class FakeFlowBrowser:
    def __init__(self, tab: TabKey) -> None:
        self.tab = tab
        date = make_ctbc_date_snapshot()
        area = make_ctbc_area_snapshot(include_back=False)
        area["page_url"] = CTBC_TEST_AREA_URL
        area["document_id"] = "document-2"
        area["areas"][0]["href"] = (
            "javascript:Send('0202','P16D0B8Z','PRICE_FRONT','','45')"
        )
        quantity = make_ctbc_quantity_snapshot()
        quantity["page_url"] = CTBC_TEST_QUANTITY_URL
        quantity["document_id"] = "document-3"
        self.date = parse_date_snapshot(date)
        self.area = parse_area_snapshot(area)
        self.quantity = parse_quantity_snapshot(quantity)
        assert isinstance(self.date, DateSnapshot)
        assert isinstance(self.area, AreaSnapshot)
        assert isinstance(self.quantity, QuantitySnapshot)
        self.stage = "date"
        self.commits: list[str] = []
        self.date_started = asyncio.Event()
        self.date_release: asyncio.Event | None = None
        self.wrong_receipt = False
        self.audit_mismatch = False
        self.stale_date = False
        self.wrong_receipt_document = False
        self.changed_hidden_identity = False
        self.input_ready = True
        self.input_release: asyncio.Event | None = None
        self.input_started = asyncio.Event()
        self.submit_release: asyncio.Event | None = None
        self.submit_started = asyncio.Event()
        self.submit_exception = False
        self.submit_no_effect = False
        self.outcome_url = self.quantity.page_url
        self.outcome_document = self.quantity.document_id
        self.dialog_text = ""
        self.dismiss_started = asyncio.Event()
        self.dismiss_release: asyncio.Event | None = None
        self.dismiss_unknown = False
        self.dismiss_no_effect = False
        self.captcha_image_token = "fixture-image-1"
        self.captcha_refreshed_token = "fixture-image-2"
        self.captcha_field_empty = False
        self.captcha_reset_started = asyncio.Event()
        self.captcha_reset_release: asyncio.Event | None = None
        self.captcha_reset_unknown = False
        self.captcha_observe_started = asyncio.Event()
        self.captcha_observe_release: asyncio.Event | None = None
        self.outcome_exception = False
        self.outcome_started = asyncio.Event()
        self.outcome_release: asyncio.Event | None = None
        self.login_required = False
        self.login_account_present = False
        self.login_password_present = False
        self.login_started = asyncio.Event()
        self.login_release: asyncio.Event | None = None
        self.login_partial = False
        self.login_audit_match = True
        self.login_filled_credentials: InlineLoginCredentials | None = None

    async def observe_date(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "date"
        return PageObservation(tab, self.date)

    async def observe_area(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "area"
        return PageObservation(tab, self.area)

    async def observe_quantity(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "quantity"
        return PageObservation(tab, self.quantity)

    @staticmethod
    def _receipt(claim: RunningEffect, *, navigation: bool) -> EffectReceipt:
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN if navigation else EffectStatus.APPLIED,
            EffectEvidence.REQUEST_DISPATCHED if navigation else EffectEvidence.LOCAL_MUTATION,
            DispatchStage.REQUEST_BOUNDARY if navigation else DispatchStage.LOCAL_MUTATION,
        )

    async def commit_date(self, snapshot, plan, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.date and self.stage == "date"
        self.commits.append("date")
        self.date_started.set()
        if self.date_release is not None:
            await self.date_release.wait()
        if self.stale_date:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        self.stage = "area"
        receipt = self._receipt(claim, navigation=True)
        if self.wrong_receipt_document:
            return replace(receipt, observed_document=DocumentKey("other-document"))
        return replace(receipt, claim_id=ClaimId("wrong")) if self.wrong_receipt else receipt

    async def commit_area(self, snapshot, plan, ticket_number, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.area and self.stage == "area"
        assert plan.route == "0202" and ticket_number == 2
        self.commits.append("area")
        self.stage = "quantity"
        return self._receipt(claim, navigation=True)

    async def commit_quantity_snapshot(self, snapshot, plan, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.quantity and self.stage == "quantity"
        assert plan.target_key == "FULL" and plan.ticket_number == 2
        self.commits.append("quantity")
        self.quantity = replace(
            self.quantity,
            performance_id=("OTHER" if self.changed_hidden_identity else self.quantity.performance_id),
            rows=tuple(replace(row, value="2" if row.key == "FULL" else (
                "1" if self.audit_mismatch else "0"
            ))
                       for row in self.quantity.rows),
        )
        return self._receipt(claim, navigation=False)

    async def observe_purchase_input(self, tab: TabKey) -> PurchaseInputObservation:
        assert tab == self.tab
        self.input_started.set()
        if self.input_release is not None:
            await self.input_release.wait()
        return PurchaseInputObservation(
            tab, self.quantity.page_url, self.quantity.document_id,
            True, True, True, self.input_ready and not self.captcha_field_empty, self.login_required,
            not self.login_required or (
                self.login_account_present and self.login_password_present
            ), True, False,
            self.login_required, self.login_account_present,
            self.login_password_present,
        )

    async def commit_inline_login(self, intent, credentials, claim: RunningEffect) -> EffectReceipt:
        assert self.stage == "quantity"
        assert intent.document_id == self.quantity.document_id
        assert credentials.account == "fixture-account"
        self.commits.append("login")
        self.login_started.set()
        if self.login_release is not None:
            await self.login_release.wait()
        if intent.write_account:
            self.login_account_present = True
        if not self.login_partial and intent.write_password:
            self.login_password_present = True
        self.login_filled_credentials = credentials
        if self.login_partial:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        return self._receipt(claim, navigation=False)

    async def audit_inline_login(self, tab, intent, credentials) -> InlineLoginAudit:
        assert tab == self.tab and credentials.revision == intent.credential_revision
        return InlineLoginAudit(
            tab, intent.page_url, intent.document_id,
            self.login_audit_match and self.login_account_present
            and self.login_password_present
            and credentials == self.login_filled_credentials,
        )

    async def dispatch_submit(
        self, plan, claim: RunningEffect, login_intent=None, credentials=None,
    ) -> EffectReceipt:
        assert plan.document_id == self.quantity.document_id
        if login_intent is not None:
            assert login_intent.document_id == plan.document_id
            assert credentials == self.login_filled_credentials
        self.commits.append("submit")
        self.submit_started.set()
        if self.submit_release is not None:
            await self.submit_release.wait()
        if self.submit_exception:
            raise RuntimeError("transport lost after click")
        if self.submit_no_effect:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        return self._receipt(claim, navigation=True)

    async def observe_submit_outcome(self, tab: TabKey) -> SubmitOutcomeObservation:
        assert tab == self.tab
        self.outcome_started.set()
        if self.outcome_release is not None:
            await self.outcome_release.wait()
        if self.outcome_exception:
            raise RuntimeError("outcome reader unavailable")
        dialog = DialogSnapshot(
            self.outcome_url, self.outcome_document,
            1 if self.dialog_text else 0, self.dialog_text,
        )
        return SubmitOutcomeObservation(
            tab, self.outcome_url, self.outcome_document, dialog,
        )

    async def dismiss_submit_dialog(
        self, dialog: DialogSnapshot, claim: RunningEffect,
    ) -> EffectReceipt:
        assert dialog.page_url == self.outcome_url
        assert dialog.document_id == self.outcome_document
        assert dialog.text == self.dialog_text
        self.commits.append("dismiss")
        self.dismiss_started.set()
        if self.dismiss_release is not None:
            await self.dismiss_release.wait()
        if self.dismiss_unknown:
            raise RuntimeError("dismissal transport lost after click")
        if self.dismiss_no_effect:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        self.dialog_text = ""
        return self._receipt(claim, navigation=False)

    async def observe_captcha_reset(self, tab: TabKey) -> CaptchaResetObservation:
        assert tab == self.tab
        self.captcha_observe_started.set()
        if self.captcha_observe_release is not None:
            await self.captcha_observe_release.wait()
        return CaptchaResetObservation(
            tab, self.outcome_url, self.outcome_document,
            CaptchaImageKey("fixture-image", "img#chk_pic", self.captcha_image_token, None),
            f"data:image/png;base64,{self.captcha_image_token}",
            True, self.captcha_field_empty, True, bool(self.dialog_text),
        )

    async def commit_captcha_reset(
        self, observed: CaptchaResetObservation, claim: RunningEffect,
    ) -> EffectReceipt:
        assert observed.image is not None
        assert observed.image.load_revision == self.captcha_image_token
        self.commits.append("captcha_reset")
        self.captcha_reset_started.set()
        if self.captcha_reset_release is not None:
            await self.captcha_reset_release.wait()
        if self.captcha_reset_unknown:
            raise RuntimeError("captcha refresh outcome unknown")
        self.captcha_field_empty = True
        self.captcha_image_token = self.captcha_refreshed_token
        return self._receipt(claim, navigation=True)


def _engine(*, auto_submit: bool = False):
    tab = TabKey(1, "offline-flow")
    core = FakeCore(tab)
    browser = FakeFlowBrowser(tab)
    policy = apply_current_policy_reload(None, json.dumps({"utiki": {
        "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }}, ensure_ascii=False))
    settings = FlowSettings(
        True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
        auto_submit=auto_submit,
    )
    engine = create_engine(policy, browser, core, settings)
    return engine, engine.attach(tab), browser, core


def test_offline_flow_reaches_audited_quantity_without_duplicate_mutations() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_duplicate_tick_while_date_commit_pending_does_not_click_twice() -> None:
    engine, handle, browser, _core = _engine()
    browser.date_release = asyncio.Event()

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        await asyncio.sleep(0.05)
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits == ["date"]
        browser.date_release.set()
        assert await pending is SelectionProgress.DATE_DISPATCHED

    asyncio.run(exercise())


def test_stop_before_late_receipt_retains_original_claim_and_blocks_progress() -> None:
    engine, handle, browser, core = _engine()
    browser.date_release = asyncio.Event()

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.date_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_unmatched_receipt_or_wrong_destination_does_not_advance() -> None:
    engine, handle, browser, _core = _engine()
    browser.wrong_receipt = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits == ["date"]

    asyncio.run(exercise())

    engine, handle, browser, _core = _engine()
    browser.area = replace(browser.area, document_id=browser.date.document_id)

    async def wrong_document() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits == ["date"]

    asyncio.run(wrong_document())


def test_pause_during_date_commit_holds_claim_after_resume() -> None:
    engine, handle, browser, core = _engine()
    browser.date_release = asyncio.Event()

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        core.control = CoreControl(2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        browser.date_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.update_control(handle)
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_quantity_readback_mismatch_blocks_ready_and_retry() -> None:
    engine, handle, browser, _core = _engine()
    browser.audit_mismatch = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_stale_date_receipt_does_not_retry_or_advance() -> None:
    engine, handle, browser, _core = _engine()
    browser.stale_date = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_ready_cache_requires_fresh_same_document_and_values() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        browser.quantity = replace(browser.quantity, document_id="document-4")
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_receipt_observed_document_must_match_claim_source() -> None:
    engine, handle, browser, _core = _engine()
    browser.wrong_receipt_document = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_quantity_hidden_identity_change_blocks_ready() -> None:
    engine, handle, browser, _core = _engine()
    browser.changed_hidden_identity = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


async def _ready_quantity(engine, handle) -> None:
    assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
    assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
    assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY


def test_inline_login_fills_missing_fields_before_single_submit() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login", "submit"]


def test_inline_login_runs_even_when_automatic_submit_is_disabled() -> None:
    engine, handle, browser, _core = _engine()
    browser.login_required = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login"]


def test_inline_login_reentry_and_credential_change_do_not_write_twice() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_login(handle))
        await browser.login_started.wait()
        await asyncio.sleep(0.05)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        core.authority = PurchaseAuthority(AuthEpoch(1), 2)
        core.login_credentials = InlineLoginCredentials("new-account", "new-password", 2)
        browser.login_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login"]


def test_autofilled_login_revision_change_blocks_submit() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.login_required = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        core.authority = PurchaseAuthority(AuthEpoch(1), 2)
        core.login_credentials = InlineLoginCredentials("new-account", "new-password", 2)
        assert await engine.advance_login(handle) is SelectionProgress.BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login"]


def test_partial_inline_login_write_keeps_original_claim() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_partial = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login"]


def test_inline_login_audit_failure_or_unsupported_tenant_does_not_retry() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_audit_match = False

    async def audit_failure() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(audit_failure())
    assert browser.commits == ["date", "area", "quantity", "login"]

    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True

    async def unsupported() -> None:
        await _ready_quantity(engine, handle)
        with patch(
            "platforms.utiki.application.engine.get_tenant_auth_profile",
            return_value=TENANT_AUTH_PROFILES["wei_chuan_dragons"],
        ):
            assert await engine.advance_login(handle) is SelectionProgress.BLOCKED

    asyncio.run(unsupported())
    assert browser.commits == ["date", "area", "quantity"]


def test_stop_during_inline_login_retains_original_claim() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_login(handle))
        await browser.login_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.login_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        try:
            engine.attach(handle.tab)
        except ValueError as exc:
            assert "retained outcome" in str(exc)
        else:
            raise AssertionError("closed login claim must prevent reattach")

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login"]


def test_manual_input_submit_claim_waits_for_checkout_without_resending() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_submit_requires_manual_field_audit_and_enabled_policy() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.input_ready = False

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        browser.input_ready = True
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]

    engine, handle, browser, _core = _engine()

    async def disabled() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

    asyncio.run(disabled())
    assert browser.commits == ["date", "area", "quantity"]


def test_submit_exception_and_duplicate_tick_keep_one_original_claim() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()
    browser.submit_exception = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        await asyncio.sleep(0.05)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_uncertain_submit_dispatch_cannot_claim_unrelated_late_dialog_or_checkout() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_exception = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.dialog_text = ""
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "unrelated-checkout"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.submit_dispatched_at is not None
        current.submit_dispatched_at -= 11
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert current.submit_terminal is None

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_negative_dialog_prevents_submit_retry() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_known_submit_rejection_dismisses_once_under_parent_claim() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        current = engine._sessions[handle.tab]
        assert current.submit_attempt is not None
        parent_id = current.submit_attempt.attempt_id.token
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert current.active is not None
        assert current.active.claim.proposal.parent_recovery_id == parent_id
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss"]


def test_unknown_or_unattributed_submit_outcome_never_dismisses() -> None:
    for text in (
        "限購已達上限", "系統忙碌，請稍候",
        "帳號或密碼錯誤", "該位置已被選走",
    ):
        engine, handle, browser, _core = _engine(auto_submit=True)

        async def exercise() -> None:
            await _ready_quantity(engine, handle)
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            browser.dialog_text = text
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.BLOCKED

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "quantity", "submit"]

    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_exception = True

    async def unattributed() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.BLOCKED

    asyncio.run(unattributed())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_dialog_change_or_failed_dismissal_does_not_click_twice() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.dialog_text = "登入失敗"
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.dialog_text = "驗證碼錯誤"
        browser.dismiss_no_effect = True
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss"]


def test_dialog_dismissal_reentry_and_stop_retains_one_claim() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.dismiss_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        pending = asyncio.create_task(engine.advance_dialog_dismissal(handle))
        await browser.dismiss_started.wait()
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.AWAITING_EVIDENCE
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.dismiss_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.BLOCKED
        try:
            engine.attach(handle.tab)
        except ValueError as exc:
            assert "retained outcome" in str(exc)
        else:
            raise AssertionError("closed dismissal claim must prevent reattach")

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss"]


def test_dialog_dismissal_refuses_old_policy_or_control_revision() -> None:
    for change in ("policy", "control"):
        engine, handle, browser, core = _engine(auto_submit=True)

        async def exercise() -> None:
            await _ready_quantity(engine, handle)
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            browser.dialog_text = "驗證碼錯誤"
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            if change == "policy":
                next_policy = apply_current_policy_reload(
                    engine._policy,
                    json.dumps({"utiki": {
                        "ticket_type_keyword": "其他票",
                        "seat_sorting": "row_seat",
                        "seat_selection_mode": "manual",
                    }}, ensure_ascii=False),
                )
                assert engine.update_policy(next_policy)
            else:
                core.control = CoreControl(2, CoreGate.PAUSED)
                assert engine.update_control(handle)
                core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
                assert engine.update_control(handle)
            assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.BLOCKED
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "quantity", "submit"]


def test_dialog_dismissal_refuses_changed_trusted_authority() -> None:
    for authority in (
        PurchaseAuthority(AuthEpoch(2), 1),
        PurchaseAuthority(AuthEpoch(1), 2),
    ):
        engine, handle, browser, core = _engine(auto_submit=True)

        async def exercise() -> None:
            await _ready_quantity(engine, handle)
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            browser.dialog_text = "驗證碼錯誤"
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
            core.authority = authority
            assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.BLOCKED

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "quantity", "submit"]


def test_authority_changed_during_dismissal_observation_blocks_click() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_started.clear()
        browser.outcome_release = asyncio.Event()
        pending = asyncio.create_task(engine.advance_dialog_dismissal(handle))
        await browser.outcome_started.wait()
        core.authority = PurchaseAuthority(AuthEpoch(2), 1)
        browser.outcome_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


async def _known_captcha_dialog_closed(engine, handle, browser) -> None:
    await _ready_quantity(engine, handle)
    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
    browser.dialog_text = "驗證碼錯誤"
    assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
    assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED


def test_known_captcha_reset_proves_new_image_but_keeps_submit_blocked() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        original = engine._sessions[handle.tab].submit_attempt
        assert original is not None
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        current = engine._sessions[handle.tab]
        assert current.submit_attempt is original
        assert current.submit_terminal is SelectionProgress.SUBMIT_BLOCKED
        assert current.captcha_reset_verified and browser.captcha_field_empty
        assert current.active is not None
        assert current.active.claim.proposal.parent_recovery_id == original.attempt_id.token
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_field_empty = False  # synthetic manual input remains blocked in this slice
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_same_captcha_image_keeps_original_blocker_and_does_not_refresh_twice() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.captcha_refreshed_token = browser.captcha_image_token

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_image_token = "fixture-image-2"
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_unknown_captcha_reset_keeps_claim_even_if_image_later_changes() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.captcha_reset_unknown = True

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_image_token = "fixture-image-2"
        browser.captcha_field_empty = True
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert not engine._sessions[handle.tab].captcha_reset_verified

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_stop_during_captcha_reset_retains_original_blocker() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.captcha_reset_release = asyncio.Event()

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        pending = asyncio.create_task(engine.advance_captcha_reset(handle))
        await browser.captcha_reset_started.wait()
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.captcha_reset_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_captcha_reset_refuses_changed_policy_or_trusted_authority() -> None:
    for change in ("policy", "authority"):
        engine, handle, browser, core = _engine(auto_submit=True)

        async def exercise() -> None:
            await _known_captcha_dialog_closed(engine, handle, browser)
            if change == "policy":
                next_policy = apply_current_policy_reload(
                    engine._policy,
                    json.dumps({"utiki": {
                        "ticket_type_keyword": "其他票",
                        "seat_sorting": "row_seat",
                        "seat_selection_mode": "manual",
                    }}, ensure_ascii=False),
                )
                assert engine.update_policy(next_policy)
            else:
                core.authority = PurchaseAuthority(AuthEpoch(2), 1)
            assert await engine.advance_captcha_reset(handle) is SelectionProgress.BLOCKED
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "quantity", "submit", "dismiss"]


def test_captcha_reset_timeout_does_not_accept_late_image() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.captcha_refreshed_token = browser.captcha_image_token

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.captcha_reset is not None
        browser.captcha_image_token = "fixture-image-2"
        with patch(
            "platforms.utiki.application.engine.time.monotonic",
            return_value=current.captcha_reset.deadline_monotonic + 1,
        ):
            assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert not current.captcha_reset_verified
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_captcha_readback_crossing_deadline_keeps_submit_blocked() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.captcha_refreshed_token = browser.captcha_image_token

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.captcha_reset is not None and current.active is not None
        current.captcha_reset = replace(
            current.captcha_reset, deadline_monotonic=time.monotonic() + 0.05,
        )
        current.active.plan = current.captcha_reset
        browser.captcha_image_token = "fixture-image-2"
        browser.captcha_observe_started.clear()
        browser.captcha_observe_release = asyncio.Event()
        pending = asyncio.create_task(engine.advance_captcha_reset(handle))
        await browser.captcha_observe_started.wait()
        await asyncio.sleep(0.08)
        browser.captcha_observe_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert not current.captcha_reset_verified
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_pre_dispatch_stale_submit_cannot_claim_later_checkout() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_no_effect = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_submit_requires_trusted_auth_epoch() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    core.authority = None

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_submit_wrong_tenant_or_document_is_not_confirmation() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.outcome_url = "https://tix.wdragons.com/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = browser.quantity.document_id
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_submit_timeout_holds_claim_but_late_checkout_still_belongs_to_attempt() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        current = engine._sessions[handle.tab]
        assert current.submit_dispatched_at is not None
        current.submit_dispatched_at -= 11
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_stop_during_submit_retains_original_claim_and_blocks_reentry() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        try:
            engine.attach(handle.tab)
        except ValueError as exc:
            assert "retained outcome" in str(exc)
        else:
            raise AssertionError("closed submit claim must prevent reattach")

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_checkout_seen_while_dispatch_running_cannot_override_no_effect() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()
    browser.submit_no_effect = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_authority_changed_during_input_read_blocks_submit() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.input_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.input_started.wait()
        core.authority = PurchaseAuthority(AuthEpoch(2), 2)
        browser.input_release.set()
        assert await pending is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_outcome_reader_error_respects_timeout_without_retry() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.outcome_exception = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        current = engine._sessions[handle.tab]
        assert current.submit_dispatched_at is not None
        current.submit_dispatched_at -= 11
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_exception = False
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


class FakeSeatFlowBrowser(FakeFlowBrowser):
    def __init__(
        self, tab: TabKey, *, preselected: bool = False,
        incomplete_selection: bool = False,
    ) -> None:
        super().__init__(tab)
        area = make_ctbc_area_snapshot(include_back=False)
        area["page_url"] = CTBC_TEST_AREA_URL
        area["document_id"] = "document-2"
        self.area = parse_area_snapshot(area)
        selected = (0, 1) if preselected else ((0,) if incomplete_selection else ())
        seat = make_ctbc_seat_snapshot(selected=selected)
        seat["page_url"] = CTBC_TEST_SEAT_URL
        seat["document_id"] = "document-3"
        seat["product_id"] = "P16ANF0L"
        self.seat = parse_seat_snapshot(seat)
        assert isinstance(self.area, AreaSnapshot)
        assert isinstance(self.seat, SeatSnapshot)
        self.seat_started = asyncio.Event()
        self.seat_release: asyncio.Event | None = None
        self.seat_partial = False
        self.seat_stale = False
        self.seat_clear_partial = False

    async def commit_area(self, snapshot, plan, ticket_number, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.area and self.stage == "area"
        assert plan.route == "0205" and ticket_number == 2
        self.commits.append("area")
        self.stage = "seat"
        return self._receipt(claim, navigation=True)

    async def observe_seat(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "seat"
        return PageObservation(tab, self.seat)

    async def commit_seat_snapshot(self, snapshot, plan, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.seat and self.stage == "seat"
        assert plan.action in {"select", "clear"}
        self.commits.append("seat_clear" if plan.action == "clear" else "seat")
        self.seat_started.set()
        if self.seat_release is not None:
            await self.seat_release.wait()
        if self.seat_stale:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT, DispatchStage.BEFORE_DISPATCH,
            )
        selected = (
            (0,) if self.seat_clear_partial else ()
        ) if plan.action == "clear" else (
            (0,) if self.seat_partial else (0, 1)
        )
        seat = make_ctbc_seat_snapshot(selected=selected)
        seat["page_url"] = self.seat.page_url
        seat["document_id"] = self.seat.document_id
        seat["product_id"] = self.seat.product_id
        self.seat = parse_seat_snapshot(seat)
        assert isinstance(self.seat, SeatSnapshot)
        return self._receipt(claim, navigation=False)


def _seat_engine(*, preselected: bool = False, incomplete_selection: bool = False):
    tab = TabKey(1, "offline-seat-flow")
    core = FakeCore(tab)
    browser = FakeSeatFlowBrowser(
        tab, preselected=preselected, incomplete_selection=incomplete_selection,
    )
    policy = apply_current_policy_reload(None, json.dumps({"utiki": {
        "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }}, ensure_ascii=False))
    engine = create_engine(
        policy, browser, core,
        FlowSettings(True, True, 2, date_keyword='"9/10"', area_keyword='"前排"'),
    )
    return engine, engine.attach(tab), browser, core


async def _ready_seat_area(engine, handle) -> None:
    assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
    assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED


def test_reserved_seat_clean_flow_reads_back_exact_selection() -> None:
    engine, handle, browser, _core = _seat_engine()

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "seat"]


def test_reserved_seat_preselected_ready_does_not_click_again() -> None:
    engine, handle, browser, _core = _seat_engine(preselected=True)

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area"]


def test_reserved_seat_partial_or_stale_commit_never_retries() -> None:
    for condition in ("seat_partial", "seat_stale"):
        engine, handle, browser, _core = _seat_engine()
        setattr(browser, condition, True)

        async def exercise() -> None:
            await _ready_seat_area(engine, handle)
            assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "seat"]


def test_reserved_seat_duplicate_tick_and_wrong_product_block_second_click() -> None:
    engine, handle, browser, _core = _seat_engine()
    browser.seat_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        pending = asyncio.create_task(engine.advance_seat(handle))
        await browser.seat_started.wait()
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.seat_release.set()
        assert await pending is SelectionProgress.SEAT_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "seat"]

    engine, handle, browser, _core = _seat_engine()
    browser.seat = replace(browser.seat, product_id="OTHER_PRODUCT")

    async def wrong_product() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(wrong_product())
    assert browser.commits == ["date", "area"]


def test_incomplete_seat_selection_clears_then_selects_under_new_claim() -> None:
    engine, handle, browser, _core = _seat_engine(incomplete_selection=True)

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_CLEARED
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "seat_clear", "seat"]


def test_stale_or_partial_seat_clear_keeps_original_claim_without_retry() -> None:
    for condition in ("seat_stale", "seat_clear_partial"):
        engine, handle, browser, _core = _seat_engine(incomplete_selection=True)
        setattr(browser, condition, True)

        async def exercise() -> None:
            await _ready_seat_area(engine, handle)
            assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
            assert await engine.advance_seat(handle) is (
                SelectionProgress.AWAITING_EVIDENCE if condition == "seat_stale"
                else SelectionProgress.BLOCKED
            )

        asyncio.run(exercise())
        assert browser.commits == ["date", "area", "seat_clear"]


def test_stop_during_seat_selection_keeps_original_effect() -> None:
    engine, handle, browser, core = _seat_engine()
    browser.seat_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        pending = asyncio.create_task(engine.advance_seat(handle))
        await browser.seat_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        browser.seat_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_seat(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "seat"]
