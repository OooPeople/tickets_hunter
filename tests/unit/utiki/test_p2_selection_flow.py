"""Offline date -> area -> quantity flow and exclusive claim behavior."""

import asyncio
import base64
import json
import threading
import time
from dataclasses import replace
from unittest.mock import patch

import pytest

from platforms.utiki.auth import TENANT_AUTH_PROFILES
from platforms.utiki.adapters.refresh import SqliteRefreshAdapter
from platforms.utiki.adapters.notification import CheckoutNotificationAdapter
from platforms.utiki.application.engine import SelectionProgress
from platforms.utiki.application.flow_settings import FlowSettings
from platforms.utiki.application.ports import (
    CaptchaResetObservation, CoreControl, CoreGate, InlineLoginAudit, InlineLoginCredentials,
    PageObservation, PurchaseAuthority, PurchaseInputObservation, RejectedLoginObservation,
    SubmitOutcomeObservation, VerifiedLoginAcceptance,
)
from platforms.utiki.application.refresh_contract import RefreshDocument
from platforms.utiki.bootstrap import create_engine
from platforms.utiki.domain.effects import (
    ClaimId, DispatchStage, EffectEvidence, EffectReceipt, EffectStatus,
    HeldEffect, HeldReason, RunningEffect,
)
from platforms.utiki.domain.identity import AuthEpoch, DocumentKey, TabKey
from platforms.utiki.domain.inputs import (
    BlockedInput, CaptchaImageKey, InputBlockReason, InputSource, ReadyInput,
    WaitingManualInput,
)
from platforms.utiki.domain.purchase import ManualSubmitEvidence, SubmitSource
from platforms.utiki.domain.snapshots import AreaSnapshot, DateSnapshot, DialogSnapshot, QuantitySnapshot, SeatSnapshot
from platforms.utiki.profiles import PageKind
from platforms.utiki.selection import (
    parse_area_snapshot, parse_date_snapshot, parse_quantity_snapshot, parse_seat_snapshot,
)
from refresh_coordinator import SharedRefreshCoordinator
from tests.ctbc_support import (
    CTBC_TEST_AREA_URL, CTBC_TEST_QUANTITY_URL, CTBC_TEST_SEAT_URL,
    CTBC_TEST_SERVER_QUANTITY_URL,
    make_ctbc_area_snapshot, make_ctbc_date_snapshot,
    make_ctbc_quantity_snapshot, make_ctbc_seat_snapshot, make_effective_policy,
)
from utiki_config import CurrentPolicyState, apply_current_policy_reload


class FakeCore:
    def __init__(self, tab: TabKey) -> None:
        self.tab = tab
        self.control = CoreControl(1, CoreGate.PURCHASE_ALLOWED)
        self.authority: PurchaseAuthority | None = PurchaseAuthority(AuthEpoch(1), 1)
        self.login_credentials = InlineLoginCredentials("fixture-account", "fixture-password", 1)
        self.clearance: tuple[str, str] | None = None
        self.verified_acceptance: VerifiedLoginAcceptance | None = None

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

    def current_health_clearance(self, tab: TabKey) -> tuple[str, str] | None:
        assert tab == self.tab
        return self.clearance

    def current_verified_login(
        self, tab: TabKey, rejected_attempt_id: str,
    ) -> VerifiedLoginAcceptance | None:
        assert tab == self.tab
        acceptance = self.verified_acceptance
        return acceptance if (
            acceptance is not None
            and acceptance.rejected_attempt_id == rejected_attempt_id
        ) else None


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
        self.quantity_readback_lag = False
        self._lagged_quantity: QuantitySnapshot | None = None
        self.input_ready = True
        self.input_release: asyncio.Event | None = None
        self.input_started = asyncio.Event()
        self.submit_release: asyncio.Event | None = None
        self.submit_started = asyncio.Event()
        self.submit_exception = False
        self.submit_no_effect = False
        self.submit_possible = False
        self.outcome_url = self.quantity.page_url
        self.outcome_document = self.quantity.document_id
        self.dialog_text = ""
        self.dismiss_started = asyncio.Event()
        self.dismiss_release: asyncio.Event | None = None
        self.dismiss_unknown = False
        self.dismiss_no_effect = False
        self.captcha_image_token = "fixture-image-1"
        self.captcha_image_data_url: str | None = None
        self.captcha_refreshed_token = "fixture-image-2"
        self.captcha_field_empty = False
        self.captcha_manual_revision = 0
        self.captcha_written_answer: str | None = None
        self.captcha_input_started = asyncio.Event()
        self.captcha_input_release: asyncio.Event | None = None
        self.captcha_input_unknown = False
        self.captcha_reset_started = asyncio.Event()
        self.captcha_reset_release: asyncio.Event | None = None
        self.captcha_reset_unknown = False
        self.captcha_reset_partial = False
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
        self.login_no_effect = False
        self.login_audit_match = True
        self.login_manual_ready = False
        self.login_filled_credentials: InlineLoginCredentials | None = None
        self.rejected_login_token = ""
        self.rejected_login_changed = False
        self.conflict_partial = False
        self.manual_click_token: str | None = None
        self.manual_arm_token = "fixture-arm"
        self.manual_selection_revision = 0

    async def observe_refresh_document(self, tab: TabKey) -> RefreshDocument:
        assert tab == self.tab
        page = self.seat if self.stage == "seat" else (
            self.quantity if self.stage == "quantity" else
            self.area if self.stage == "area" else self.date
        )
        return RefreshDocument(tab, page.page_url, page.document_id, "normal", 1.0)

    async def dispatch_reload(self, claim: RunningEffect, expected_url: str) -> EffectReceipt:
        page = self.seat if self.stage == "seat" else self.quantity
        assert expected_url == page.page_url
        self.commits.append("reload")
        if self.stage == "seat":
            self.seat = replace(self.seat, document_id="document-after-reload")
        else:
            self.quantity = replace(self.quantity, document_id="document-after-reload")
        self.outcome_document = "document-after-reload"
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE,
            DispatchStage.REQUEST_BOUNDARY,
        )

    async def observe_date(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "date"
        return PageObservation(tab, self.date)

    async def observe_area(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "area"
        return PageObservation(tab, self.area)

    async def observe_quantity(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "quantity"
        if self._lagged_quantity is not None:
            observed = self._lagged_quantity
            self._lagged_quantity = None
            return PageObservation(tab, observed)
        return PageObservation(tab, self.quantity, self.manual_selection_revision)

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
        assert plan.target_key == "FULL" and plan.ticket_number in (2, 4)
        self.commits.append("quantity")
        if self.quantity_readback_lag:
            self._lagged_quantity = self.quantity
        self.quantity = replace(
            self.quantity,
            performance_id=("OTHER" if self.changed_hidden_identity else self.quantity.performance_id),
            rows=tuple(replace(row, value=str(plan.ticket_number) if row.key == "FULL" else (
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
        page = self.seat if self.stage == "seat" else self.quantity
        return PurchaseInputObservation(
            tab, page.page_url, page.document_id,
            True, True, True, self.input_ready and not self.captcha_field_empty, self.login_required,
            not self.login_required or (
                self.login_account_present and self.login_password_present
            ), True, False,
            self.login_required, self.login_account_present,
            self.login_password_present,
        )

    async def arm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str,
        prior: ManualSubmitEvidence | None = None,
    ) -> str:
        assert tab == self.tab
        page = self.seat if self.stage == "seat" else self.quantity
        assert page_url == page.page_url
        assert document_id == page.document_id
        if prior is not None:
            assert prior.arm_token == self.manual_arm_token
            assert prior.click_token == self.manual_click_token
            self.manual_arm_token += "-next"
        self.manual_click_token = None
        return self.manual_arm_token

    async def disarm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str, arm_token: str,
    ) -> bool:
        assert tab == self.tab
        page = self.seat if self.stage == "seat" else self.quantity
        return bool(
            self.manual_click_token is None
            and page_url == page.page_url
            and document_id == page.document_id
            and arm_token == self.manual_arm_token
        )

    async def commit_inline_login(self, intent, credentials, claim: RunningEffect) -> EffectReceipt:
        assert self.stage == "quantity"
        assert intent.document_id == self.quantity.document_id
        assert credentials.account == "fixture-account"
        self.commits.append("login")
        self.login_started.set()
        if self.login_release is not None:
            await self.login_release.wait()
        if self.login_no_effect:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
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
            self.login_manual_ready and self.login_account_present
            and self.login_password_present and not self.login_audit_match,
        )

    async def arm_rejected_login(self, tab, page_url, document_id, token, claim):
        assert tab == self.tab and page_url == self.quantity.page_url
        assert document_id == self.quantity.document_id
        self.commits.append("login_arm")
        self.rejected_login_token = token
        return self._receipt(claim, navigation=False)

    async def observe_rejected_login(self, tab, page_url, document_id, token):
        assert tab == self.tab
        return RejectedLoginObservation(
            tab, page_url, document_id, token,
            token == self.rejected_login_token and self.rejected_login_changed
            and self.login_account_present and self.login_password_present,
        )

    async def clear_rejected_selection(self, attempt, claim):
        self.commits.append("conflict_clear")
        if self.conflict_partial:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        if self.stage == "quantity":
            self.quantity = replace(self.quantity, rows=tuple(
                replace(row, value="0") for row in self.quantity.rows
            ))
        return self._receipt(claim, navigation=False)

    async def dispatch_submit(
        self, plan, claim: RunningEffect, login_intent=None, credentials=None,
        captcha_observed=None, captcha_answer=None, manual_login_token=None,
    ) -> EffectReceipt:
        page = self.seat if self.stage == "seat" else self.quantity
        assert plan.document_id == page.document_id
        if login_intent is not None:
            assert login_intent.document_id == plan.document_id
            assert credentials == self.login_filled_credentials
        if manual_login_token is not None:
            assert manual_login_token == self.rejected_login_token
            assert self.rejected_login_changed
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
        if self.submit_possible:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE,
                DispatchStage.REQUEST_BOUNDARY,
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
            ManualSubmitEvidence(
                self.quantity.page_url, self.quantity.document_id,
                self.manual_arm_token, self.manual_click_token,
            ) if self.manual_click_token is not None else None,
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
            self.captcha_image_data_url or f"data:image/png;base64,{self.captcha_image_token}",
            True, self.captcha_field_empty, True, bool(self.dialog_text),
            self.input_ready and not self.captcha_field_empty,
            self.captcha_manual_revision,
        )

    async def commit_captcha_input(
        self, observed, attempt, answer: str, claim: RunningEffect,
    ) -> EffectReceipt:
        self.commits.append("captcha_input")
        self.captcha_input_started.set()
        if self.captcha_input_release is not None:
            await self.captcha_input_release.wait()
        if self.captcha_input_unknown:
            raise RuntimeError("input write outcome unknown")
        if (
            observed.image is None
            or observed.image.load_revision != self.captcha_image_token
            or observed.manual_revision != self.captcha_manual_revision
            or not self.captcha_field_empty
        ):
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        self.captcha_written_answer = answer
        self.captcha_field_empty = False
        self.input_ready = True
        return self._receipt(claim, navigation=False)

    async def audit_captcha_input(self, observed, answer: str) -> bool:
        return bool(
            observed.image is not None
            and observed.image.load_revision == self.captcha_image_token
            and observed.manual_revision == self.captcha_manual_revision
            and self.captcha_written_answer == answer
            and not self.captcha_field_empty
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
        if self.captcha_reset_partial:
            self.captcha_field_empty = True
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        self.captcha_field_empty = True
        self.captcha_image_token = self.captcha_refreshed_token
        return self._receipt(claim, navigation=True)


def _engine(
    *, auto_submit: bool = False, ocr_enabled: bool | None = None,
    force_submit: bool | None = None, provider=None, notification=None,
):
    tab = TabKey(1, "offline-flow")
    core = FakeCore(tab)
    browser = FakeFlowBrowser(tab)
    policy = apply_current_policy_reload(None, json.dumps({"utiki": {
        "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }}, ensure_ascii=False))
    settings = FlowSettings(
        True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
        ocr_enabled=auto_submit if ocr_enabled is None else ocr_enabled,
        force_submit=auto_submit if force_submit is None else force_submit,
    )
    engine = create_engine(
        make_effective_policy(policy, settings), browser, core, provider, notification,
    )
    return engine, engine.attach(tab), browser, core


class _BlockingCaptchaProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def classification(self, image: bytes) -> str:
        assert image
        self.started.set()
        assert self.release.wait(5)
        return "ABCD"


def _fake_png_token(value: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(value).decode("ascii")


class FakeServerAssignedBrowser(FakeFlowBrowser):
    def __init__(self, tab: TabKey) -> None:
        super().__init__(tab)
        area = make_ctbc_area_snapshot(include_back=False)
        area["document_id"] = "document-2"
        self.area = parse_area_snapshot(area)
        quantity = make_ctbc_quantity_snapshot(
            page_url=CTBC_TEST_SERVER_QUANTITY_URL,
            group_id="GROUP_FRONT", product_id="P16ANF0L",
        )
        quantity["document_id"] = "document-3"
        self.quantity = parse_quantity_snapshot(quantity)
        assert isinstance(self.area, AreaSnapshot)
        assert isinstance(self.quantity, QuantitySnapshot)
        self.outcome_url = self.quantity.page_url
        self.outcome_document = self.quantity.document_id
        self.native_unknown = False

    async def commit_server_assigned_area(
        self, snapshot, plan, ticket_number, claim: RunningEffect,
    ) -> EffectReceipt:
        assert snapshot == self.area and self.stage == "area"
        assert plan.route == "0205" and ticket_number == 2
        self.commits.append("server_area")
        if self.native_unknown:
            raise RuntimeError("native click outcome unknown")
        self.stage = "quantity"
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE,
            DispatchStage.REQUEST_BOUNDARY,
        )


def _server_engine(*, auto_submit: bool = True, provider=None):
    tab = TabKey(1, "offline-server-assigned")
    core = FakeCore(tab)
    browser = FakeServerAssignedBrowser(tab)
    policy = apply_current_policy_reload(None, json.dumps({"utiki": {
        "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
        "seat_selection_mode": "computer",
    }}, ensure_ascii=False))
    settings = FlowSettings(
        True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
        ocr_enabled=auto_submit, force_submit=auto_submit,
    )
    engine = create_engine(make_effective_policy(policy, settings), browser, core, provider)
    return engine, engine.attach(tab), browser, core


def test_server_assigned_source_to_quantity_submit_and_known_result() -> None:
    engine, handle, browser, _core = _server_engine()
    browser.login_required = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        plan = engine._sessions[handle.tab].ready_selection
        assert plan is not None and plan.page_type == "server_assigned_quantity"
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        submission = engine._sessions[handle.tab].submission
        assert submission is not None
        assert submission.attempt.key.page_mode is PageKind.SERVER_ASSIGNED_QUANTITY
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "server_area", "quantity", "login", "submit"]


def test_server_assigned_direct_destination_has_no_source_authority() -> None:
    engine, handle, browser, _core = _server_engine()
    browser.stage = "quantity"

    async def exercise() -> None:
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == []


@pytest.mark.parametrize("drift", ["atype", "product", "group"])
def test_server_assigned_destination_requires_exact_source_identity(drift: str) -> None:
    engine, handle, browser, _core = _server_engine()
    if drift == "atype":
        browser.quantity = replace(
            browser.quantity,
            page_url=browser.quantity.page_url.replace("ATYPE=N", "ATYPE=Y"),
        )
    elif drift == "product":
        browser.quantity = replace(browser.quantity, product_id="WRONG_PRODUCT")
    else:
        browser.quantity = replace(
            browser.quantity,
            page_url=browser.quantity.page_url.replace("GROUP_FRONT", "OTHER_GROUP"),
        )

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "server_area"]


def test_server_assigned_unsupported_tenant_and_uncertain_native_action_block() -> None:
    engine, handle, browser, _core = _server_engine()
    browser.date = replace(
        browser.date, page_url="https://tix.wdragons.com/UTK0201_?PRODUCT_ID=P16ANF0L",
    )
    assert asyncio.run(engine.advance_date(handle)) is SelectionProgress.BLOCKED
    assert browser.commits == []

    engine, handle, browser, _core = _server_engine()
    browser.native_unknown = True

    async def unknown() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits == ["date", "server_area"]

    asyncio.run(unknown())


def test_server_assigned_does_not_dispatch_general_admission_area_route() -> None:
    engine, handle, browser, _core = _server_engine()
    browser.area = replace(
        browser.area,
        areas=tuple(replace(area, href=area.href.replace("'0205'", "'0202'"))
                    for area in browser.area.areas),
    )

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_server_assigned_uncertain_native_action_only_continues_after_exact_destination() -> None:
    engine, handle, browser, _core = _server_engine()
    browser.native_unknown = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_area(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits == ["date", "server_area"]
        browser.stage = "quantity"
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        assert browser.commits == ["date", "server_area", "quantity"]

    asyncio.run(exercise())


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


def test_closed_run_releases_original_pre_dispatch_no_effect_only() -> None:
    engine, handle, browser, core = _engine()
    browser.date_release = asyncio.Event()
    browser.stale_date = True

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        browser.date_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert handle.tab not in engine._retired_runs
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        fresh = engine.attach(handle.tab)
        assert fresh.generation != handle.generation
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


def test_pause_during_date_commit_attributes_destination_after_resume() -> None:
    engine, handle, browser, core = _engine()
    browser.date_release = asyncio.Event()

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        core.control = CoreControl(2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        browser.date_release.set()
        assert await pending is SelectionProgress.DATE_DISPATCHED
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.update_control(handle)
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits.count("date") == 1

    asyncio.run(exercise())
    assert browser.commits == ["date", "area"]


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


def test_delayed_quantity_readback_finishes_original_mutation() -> None:
    engine, handle, browser, _core = _engine()
    browser.quantity_readback_lag = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_policy_two_to_four_replans_after_old_quantity_is_verified() -> None:
    engine, handle, browser, _core = _engine()
    browser.quantity = replace(browser.quantity, quantity_limit_raw="4")

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        flow = engine._policy.flow
        assert flow is not None
        updated = replace(
            engine._policy, flow=replace(flow, ticket_number=4),
            policy_revision=engine._policy.policy_revision + 1,
        )
        assert engine.update_policy(updated)
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        assert engine._sessions[handle.tab].ready_selection.ticket_number == 4

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "quantity"]


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
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

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


def test_late_login_audit_accepts_verified_manual_completion_once() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_audit_match = False

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        # A later audit sees the exact intended values on the same document.
        browser.login_audit_match = True
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login", "submit"]


def test_trusted_manual_login_correction_completes_original_write() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True
    browser.login_audit_match = False

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.login_manual_ready = True
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login", "submit"]


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


def test_stop_during_inline_login_retires_completed_local_write() -> None:
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
        assert handle.tab not in engine._retired_runs
        try:
            engine.attach(handle.tab)
        except ValueError as exc:
            assert "closing control" in str(exc)
        else:
            raise AssertionError("closing control must prevent reattach")
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        fresh = engine.attach(handle.tab)
        assert fresh.generation != handle.generation
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

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


def test_disabling_auto_submit_keeps_original_checkout_attributable() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        flow = engine._policy.flow
        assert flow is not None
        assert engine.update_policy(replace(
            engine._policy, flow=replace(flow, ocr_enabled=False),
            policy_revision=engine._policy.policy_revision + 1,
        ))
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_closed_run_can_attribute_late_original_checkout_without_restarting() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        assert not hasattr(engine._retired_runs[handle.tab], "ready_selection")
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_closed_run_possible_request_accepts_known_late_checkout() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()
    browser.submit_possible = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_submit_requires_manual_field_audit_and_manual_mode_does_not_dispatch() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.input_ready = False

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        browser.input_ready = True
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


@pytest.mark.parametrize("ocr_enabled,force_submit", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_four_captcha_policy_combinations_gate_submit(
    ocr_enabled: bool, force_submit: bool,
) -> None:
    engine, handle, browser, _core = _engine(
        ocr_enabled=ocr_enabled, force_submit=force_submit,
    )

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        result = await engine.advance_submit(handle)
        assert result is (
            SelectionProgress.SUBMIT_DISPATCHED
            if ocr_enabled and force_submit else SelectionProgress.AWAITING_EVIDENCE
        )
        current = engine._sessions[handle.tab]
        if ocr_enabled and force_submit:
            assert current.submission is not None
            assert current.submission.attempt.input_evidence.source is InputSource.MANUAL
            assert browser.commits.count("submit") == 1
        else:
            assert current.manual_preparation is not None
            assert current.manual_preparation.input_evidence.context.auto_submit is False
            assert "submit" not in browser.commits

    asyncio.run(exercise())


def test_provider_unavailable_then_policy_enable_uses_manual_input_same_run() -> None:
    engine, handle, browser, _core = _engine(ocr_enabled=False, force_submit=False)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        flow = engine._policy.flow
        assert flow is not None
        assert engine.update_policy(replace(
            engine._policy, flow=replace(flow, ocr_enabled=True, force_submit=True),
            policy_revision=engine._policy.policy_revision + 1,
        ))
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        browser.input_ready = False
        browser.captcha_field_empty = True
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        state = engine.input_state(handle)
        assert isinstance(state, BlockedInput)
        assert state.reason is InputBlockReason.PROVIDER_RESTART_REQUIRED
        browser.input_ready = True
        browser.captcha_field_empty = False
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert engine._sessions[handle.tab].submission is not None
        assert engine._sessions[handle.tab].submission.attempt.input_evidence.source is InputSource.MANUAL
        assert browser.commits.count("submit") == 1

    asyncio.run(exercise())


def test_unavailable_run_provider_reports_reason_and_keeps_manual_submit() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.input_ready = False
    browser.captcha_field_empty = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        state = engine.input_state(handle)
        assert isinstance(state, BlockedInput)
        assert state.reason is InputBlockReason.PROVIDER_UNAVAILABLE
        browser.input_ready = True
        browser.captcha_field_empty = False
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert browser.commits.count("submit") == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("intervention", ["new_image", "manual_edit", "policy", "stop"])
def test_late_ocr_never_overwrites_changed_source(intervention: str) -> None:
    provider = _BlockingCaptchaProvider()
    engine, handle, browser, core = _engine(auto_submit=True, provider=provider)
    browser.input_ready = False
    browser.captcha_field_empty = True
    browser.captcha_image_data_url = _fake_png_token(b"synthetic-captcha-one")

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        assert await asyncio.to_thread(provider.started.wait, 3)
        if intervention == "new_image":
            browser.captcha_image_token = "fixture-image-2"
            browser.captcha_image_data_url = _fake_png_token(b"synthetic-captcha-two")
        elif intervention == "manual_edit":
            browser.captcha_manual_revision += 1
            browser.captcha_field_empty = False
            browser.input_ready = True
        elif intervention == "policy":
            flow = engine._policy.flow
            assert flow is not None
            assert engine.update_policy(replace(
                engine._policy, flow=replace(flow, force_submit=False),
                policy_revision=engine._policy.policy_revision + 1,
            ))
        else:
            core.control = CoreControl(2, CoreGate.CLOSING)
            assert engine.update_control(handle) and engine.close(handle)
        provider.release.set()
        result = await pending
        assert browser.captcha_written_answer is None
        if intervention == "manual_edit":
            assert result is SelectionProgress.SUBMIT_DISPATCHED
            assert browser.commits.count("submit") == 1
            current = engine._sessions[handle.tab]
            assert current.submission is not None
            assert current.submission.attempt.input_evidence.source is InputSource.MANUAL
        else:
            assert result is SelectionProgress.BLOCKED
            assert "submit" not in browser.commits
        assert browser.commits.count("captcha_input") <= 1

    asyncio.run(exercise())


def test_ocr_readback_error_ends_recognizing_state_without_repeating_ocr() -> None:
    provider = _BlockingCaptchaProvider()
    engine, handle, browser, _core = _engine(auto_submit=True, provider=provider)
    browser.input_ready = False
    browser.captcha_field_empty = True
    browser.captcha_image_data_url = _fake_png_token(b"synthetic-captcha-one")

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        original = browser.observe_quantity
        reads = 0

        async def fail_second_read(tab):
            nonlocal reads
            reads += 1
            if reads == 2:
                raise RuntimeError("selection readback unavailable")
            return await original(tab)

        browser.observe_quantity = fail_second_read
        pending = asyncio.create_task(engine.advance_input(handle))
        assert await asyncio.to_thread(provider.started.wait, 3)
        provider.release.set()
        assert await pending is SelectionProgress.BLOCKED
        assert isinstance(engine.input_state(handle), WaitingManualInput)
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        assert browser.commits.count("captcha_input") == 0

    asyncio.run(exercise())


def test_same_url_new_image_invalidates_ready_ocr_until_new_manual_edit() -> None:
    provider = _BlockingCaptchaProvider()
    provider.release.set()
    engine, handle, browser, _core = _engine(
        ocr_enabled=True, force_submit=False, provider=provider,
    )
    browser.input_ready = False
    browser.captcha_field_empty = True
    browser.captcha_image_data_url = _fake_png_token(b"synthetic-captcha-one")

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_input(handle) is SelectionProgress.INPUT_READY
        current = engine._sessions[handle.tab]
        assert isinstance(current.input_state, ReadyInput)
        assert current.input_state.evidence.source is InputSource.RECOGNITION
        assert browser.captcha_written_answer == "ABCD"
        browser.captcha_image_token = "fixture-image-2"
        browser.captcha_image_data_url = _fake_png_token(b"synthetic-captcha-two")
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        assert browser.commits.count("captcha_input") == 1
        browser.captcha_manual_revision += 1
        assert await engine.advance_input(handle) is SelectionProgress.INPUT_READY
        assert isinstance(current.input_state, ReadyInput)
        assert current.input_state.evidence.source is InputSource.MANUAL
        assert browser.commits.count("captcha_input") == 1

    asyncio.run(exercise())


def test_restarted_run_on_unattributed_purchase_page_does_not_prepare_input() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.stage = "quantity"
    restarted = create_engine(engine._policy, browser, core)
    fresh = restarted.attach(handle.tab)

    async def exercise() -> None:
        assert await engine.advance_input(handle) is SelectionProgress.BLOCKED
        assert await restarted.advance_input(fresh) is SelectionProgress.BLOCKED
        assert await restarted.advance_submit(fresh) is SelectionProgress.BLOCKED
        assert browser.commits == []

    asyncio.run(exercise())

    engine, handle, browser, _core = _engine()

    async def disabled() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE

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
        assert current.submission is not None and current.submission.dispatched_at is not None
        current.submission.dispatched_at -= 11
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert current.submission.terminal is None

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
        assert current.submission is not None
        parent_id = current.submission.attempt.attempt_id.token
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert current.active is not None
        assert current.active.claim.proposal.parent_recovery_id == parent_id
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss"]


def test_only_attributed_known_rejections_can_dismiss() -> None:
    for text in (
        "限購已達上限", "系統忙碌，請稍候",
        "帳號或密碼錯誤", "該位置已被選走",
    ):
        engine, handle, browser, _core = _engine(auto_submit=True)

        async def exercise() -> None:
            await _ready_quantity(engine, handle)
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            browser.dialog_text = text
            expected = (
                SelectionProgress.QUOTA_BLOCKED if text == "限購已達上限"
                else SelectionProgress.SUBMIT_BLOCKED
            )
            assert await engine.advance_submit(handle) is expected
            assert await engine.advance_submit(handle) is expected
            expected_dismissal = (
                SelectionProgress.DIALOG_DISMISSED
                if text in {"帳號或密碼錯誤", "該位置已被選走"}
                else SelectionProgress.BLOCKED
            )
            assert await engine.advance_dialog_dismissal(handle) is expected_dismissal

        asyncio.run(exercise())
        assert browser.commits == [
            "date", "area", "quantity", "submit",
            *(["dismiss"] if text in {"帳號或密碼錯誤", "該位置已被選走"} else []),
        ]

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


def test_auth_rejection_waits_for_post_rejection_manual_edit() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.login_required = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "帳號或密碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        original = engine._sessions[handle.tab].submission
        assert original is not None
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
        assert browser.commits.count("submit") == 1
        browser.rejected_login_changed = True
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_RECOVERY_READY
        current = engine._sessions[handle.tab]
        assert current.prior_submissions == [original]
        assert original.terminal is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert current.submission is not None
        assert current.submission.attempt.attempt_id != original.attempt.attempt_id

    asyncio.run(exercise())
    assert browser.commits == [
        "date", "area", "quantity", "login", "submit", "dismiss", "login_arm", "submit",
    ]


def test_same_login_text_requires_host_accepted_auth_for_new_attempt() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.login_required = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "帳號或密碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        submission = engine._sessions[handle.tab].submission
        assert submission is not None
        browser.dialog_text = ""
        core.authority = PurchaseAuthority(AuthEpoch(2), 1)
        core.verified_acceptance = VerifiedLoginAcceptance(
            handle.tab, "wrong-tenant", browser.quantity.page_url,
            browser.quantity.document_id, submission.attempt.attempt_id.token,
            core.authority.auth_epoch,
        )
        assert await engine.advance_login(handle) is SelectionProgress.BLOCKED
        assert browser.commits.count("submit") == 1
        core.verified_acceptance = replace(
            core.verified_acceptance, tenant_key="ctbc_brothers",
        )
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_RECOVERY_READY
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert engine._sessions[handle.tab].prior_submissions == [submission]

    asyncio.run(exercise())
    assert browser.commits.count("login") == 1
    assert browser.commits.count("submit") == 2


@pytest.mark.parametrize("mode", ["quantity", "seat", "computer"])
def test_known_conflict_uses_original_attempt_and_shared_reload(tmp_path, mode: str) -> None:
    engine, handle, browser, core = (
        _seat_engine(auto_submit=True) if mode == "seat" else
        _server_engine(auto_submit=True) if mode == "computer" else
        _engine(auto_submit=True)
    )

    async def exercise() -> None:
        if mode == "seat":
            await _ready_seat_area(engine, handle)
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
            page = browser.seat
        else:
            await _ready_quantity(engine, handle)
            page = browser.quantity
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "該位置已被選走"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        current = engine._sessions[handle.tab]
        rejected = current.submission
        assert rejected is not None
        coordinator = SharedRefreshCoordinator(
            tmp_path / f"{mode}.sqlite3", clock=lambda: 100.0,
            random_uniform=lambda _a, _b: 0.1,
        )
        engine._refresh_coordinator = SqliteRefreshAdapter(
            coordinator, lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            },
        )
        core.clearance = (page.page_url, page.document_id)
        assert await engine.observe_health(handle) is not None
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_CLEARED
        assert rejected.terminal is SelectionProgress.SUBMIT_BLOCKED
        assert current.submission is rejected and rejected.conflict_cleared
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits.count("reload") == 1
        next_page = browser.seat if mode == "seat" else browser.quantity
        core.clearance = (next_page.page_url, next_page.document_id)
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_RECOVERED
        assert current.prior_submissions[-1] is rejected
        assert current.submission is None
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.BLOCKED
        assert browser.commits.count("reload") == 1
        if mode == "seat":
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        else:
            assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["quantity", "seat", "computer"])
def test_partial_conflict_clear_retains_rejection_without_reload(mode: str) -> None:
    engine, handle, browser, _core = (
        _seat_engine(auto_submit=True) if mode == "seat" else
        _server_engine(auto_submit=True) if mode == "computer" else
        _engine(auto_submit=True)
    )
    browser.conflict_partial = True

    async def exercise() -> None:
        if mode == "seat":
            await _ready_seat_area(engine, handle)
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        else:
            await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "該位置已被選走"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        original = engine._sessions[handle.tab].submission
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED
        assert engine._sessions[handle.tab].submission is original
        assert original is not None and not original.conflict_cleared
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits.count("conflict_clear") == 1
    assert "reload" not in browser.commits
    assert browser.commits.count("submit") == 1


def test_control_change_after_conflict_clear_revokes_reload_eligibility(tmp_path) -> None:
    engine, handle, browser, core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "該位置已被選走"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        coordinator = SharedRefreshCoordinator(
            tmp_path / "revoked.sqlite3", clock=lambda: 100.0,
            random_uniform=lambda _a, _b: 0.1,
        )
        engine._refresh_coordinator = SqliteRefreshAdapter(
            coordinator, lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            },
        )
        core.clearance = (browser.quantity.page_url, browser.quantity.document_id)
        assert await engine.observe_health(handle) is not None
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_CLEARED
        core.control = CoreControl(2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.update_control(handle)
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.MANUAL_ACTION_REQUIRED

    asyncio.run(exercise())
    assert browser.commits.count("conflict_clear") == 1
    assert "reload" not in browser.commits


def test_observed_manual_conflict_recovery_rearms_without_auto_claim(tmp_path) -> None:
    engine, handle, browser, core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.manual_click_token = "manual-first-click"
        browser.dialog_text = "該位置已被選走"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        current = engine._sessions[handle.tab]
        original = current.submission
        assert original is not None and original.attempt.source is SubmitSource.OBSERVED_MANUAL
        coordinator = SharedRefreshCoordinator(
            tmp_path / "manual.sqlite3", clock=lambda: 100.0,
            random_uniform=lambda _a, _b: 0.1,
        )
        engine._refresh_coordinator = SqliteRefreshAdapter(
            coordinator, lambda _force: {
                "enabled": True, "min_interval_seconds": 1.0,
                "initial_cooldown_seconds": 1.0,
                "max_cooldown_seconds": 4.0,
            },
        )
        core.clearance = (browser.quantity.page_url, browser.quantity.document_id)
        assert await engine.observe_health(handle) is not None
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_CLEARED
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.AWAITING_EVIDENCE
        core.clearance = (browser.quantity.page_url, browser.quantity.document_id)
        assert await engine.advance_conflict_recovery(handle) is SelectionProgress.CONFLICT_RECOVERED
        assert current.prior_submissions == [original]
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.manual_click_token = "manual-second-click"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert current.submission is not None
        assert current.submission.attempt.source is SubmitSource.OBSERVED_MANUAL
        assert current.submission.attempt.attempt_id != original.attempt.attempt_id

    asyncio.run(exercise())
    assert "submit" not in browser.commits
    assert browser.commits.count("reload") == 1


def test_checkout_notification_failure_does_not_reopen_submit() -> None:
    class FailingNotifier:
        calls = 0

        def start_checkout_notification(self) -> None:
            self.calls += 1
            raise RuntimeError("synthetic launcher failure")

    notifier = FailingNotifier()
    engine, handle, browser, _core = _engine(auto_submit=True, notification=notifier)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        submission = engine._sessions[handle.tab].submission
        assert submission is not None and submission.notification_attempted
        assert submission.notification_start_failed
        assert engine.context(handle) is None
        assert await engine.advance_quantity(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert notifier.calls == 1
    assert browser.commits.count("submit") == 1


def test_checkout_adapter_starts_existing_synchronous_launchers_once() -> None:
    adapter = CheckoutNotificationAdapter({"advanced": {"play_sound": {"order": True}}})
    engine, handle, browser, _core = _engine(auto_submit=True, notification=adapter)
    with (
        patch("nodriver_common.play_sound_while_ordering") as sound,
        patch("nodriver_common.send_discord_notification") as discord,
        patch("nodriver_common.send_telegram_notification") as telegram,
    ):
        async def exercise() -> None:
            await _ready_quantity(engine, handle)
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
            browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
            browser.outcome_document = "checkout-document"
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
            assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

        asyncio.run(exercise())
        sound.assert_called_once()
        discord.assert_called_once()
        telegram.assert_called_once()


def test_quota_remains_run_terminal_after_control_and_policy_update() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        browser.dialog_text = "限購已達上限"
        assert await engine.advance_submit(handle) is SelectionProgress.QUOTA_BLOCKED
        core.control = CoreControl(2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.update_control(handle)
        assert engine.context(handle) is None
        assert await engine.advance_submit(handle) is SelectionProgress.QUOTA_BLOCKED
        assert await engine.advance_quantity(handle) is SelectionProgress.BLOCKED

    asyncio.run(exercise())
    assert browser.commits.count("submit") == 1


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
                    CurrentPolicyState(
                        engine._policy.last_valid, engine._policy.policy_revision,
                        engine._policy.control_revision, engine._policy.block_reason,
                    ),
                    json.dumps({"utiki": {
                        "ticket_type_keyword": "其他票",
                        "seat_sorting": "row_seat",
                        "seat_selection_mode": "manual",
                    }}, ensure_ascii=False),
                )
                assert engine.update_policy(make_effective_policy(next_policy, engine._flow_settings))
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


def test_known_captcha_reset_allows_one_new_manual_input_attempt() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        original = engine._sessions[handle.tab].submission
        assert original is not None
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        current = engine._sessions[handle.tab]
        assert current.submission is original
        assert current.submission is not None
        assert current.submission.terminal is SelectionProgress.SUBMIT_BLOCKED
        assert current.submission.captcha_reset_verified and browser.captcha_field_empty
        assert current.active is not None
        assert current.active.claim.proposal.parent_recovery_id == original.attempt.attempt_id.token
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_field_empty = False
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert current.prior_submissions == [original]
        assert current.submission is not None
        assert current.submission.attempt.attempt_id != original.attempt.attempt_id
        assert current.submission.attempt.cycle == original.attempt.cycle
        assert browser.commits.count("submit") == 2
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert browser.commits.count("submit") == 2

    asyncio.run(exercise())
    assert browser.commits == [
        "date", "area", "quantity", "submit", "dismiss", "captcha_reset", "submit",
    ]


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
        assert not engine._sessions[handle.tab].submission.captcha_reset_verified

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_partial_captcha_clear_retains_reset_claim_without_second_click() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.captcha_reset_partial = True

    async def exercise() -> None:
        await _known_captcha_dialog_closed(engine, handle, browser)
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_image_token = "fixture-image-2"
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert browser.commits.count("captcha_reset") == 1
        assert browser.commits.count("submit") == 1

    asyncio.run(exercise())


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
                    CurrentPolicyState(
                        engine._policy.last_valid, engine._policy.policy_revision,
                        engine._policy.control_revision, engine._policy.block_reason,
                    ),
                    json.dumps({"utiki": {
                        "ticket_type_keyword": "其他票",
                        "seat_sorting": "row_seat",
                        "seat_selection_mode": "manual",
                    }}, ensure_ascii=False),
                )
                assert engine.update_policy(make_effective_policy(next_policy, engine._flow_settings))
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
        assert current.submission is not None and current.submission.captcha_reset is not None
        browser.captcha_image_token = "fixture-image-2"
        with patch(
            "platforms.utiki.application.engine.time.monotonic",
            return_value=current.submission.captcha_reset.deadline_monotonic + 1,
        ):
            assert await engine.advance_captcha_reset(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert not current.submission.captcha_reset_verified
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
        assert current.submission is not None and current.submission.captcha_reset is not None and current.active is not None
        current.submission.captcha_reset = replace(
            current.submission.captcha_reset, deadline_monotonic=time.monotonic() + 0.05,
        )
        current.active.plan = current.submission.captcha_reset
        browser.captcha_image_token = "fixture-image-2"
        browser.captcha_observe_started.clear()
        browser.captcha_observe_release = asyncio.Event()
        pending = asyncio.create_task(engine.advance_captcha_reset(handle))
        await browser.captcha_observe_started.wait()
        await asyncio.sleep(0.08)
        browser.captcha_observe_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert not current.submission.captcha_reset_verified
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit", "dismiss", "captcha_reset"]


def test_pre_dispatch_stale_submit_cannot_claim_later_checkout() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_no_effect = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        current = engine._sessions[handle.tab]
        assert current.submission is not None
        assert current.submission.terminal is SelectionProgress.SUBMIT_BLOCKED
        assert current.active is not None
        assert isinstance(current.active.claim, HeldEffect)
        assert current.active.claim.reason is HeldReason.NO_EFFECT_CONFIRMED
        assert current.active.receipt is not None
        assert current.active.receipt.evidence is EffectEvidence.NO_EFFECT
        assert current.prior_submissions == []
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
        assert current.submission is not None and current.submission.dispatched_at is not None
        current.submission.dispatched_at -= 11
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-document"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_possible_request_waits_without_resend_then_accepts_known_checkout() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)
    browser.submit_possible = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.submission is not None and current.submission.dispatched_at is not None
        current.submission.dispatched_at -= 11
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
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        try:
            engine.attach(handle.tab)
        except ValueError as exc:
            assert "retained outcome" in str(exc)
        else:
            raise AssertionError("closed submit claim must prevent reattach")

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_closed_submit_pre_dispatch_no_effect_releases_original_retention() -> None:
    engine, handle, browser, core = _engine(auto_submit=True)
    browser.submit_release = asyncio.Event()
    browser.submit_no_effect = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        pending = asyncio.create_task(engine.advance_submit(handle))
        await browser.submit_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        browser.submit_release.set()
        assert await pending is SelectionProgress.SUBMIT_BLOCKED
        assert handle.tab not in engine._retired_runs
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        fresh = engine.attach(handle.tab)
        assert fresh.generation != handle.generation
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED

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
        assert current.submission is not None and current.submission.dispatched_at is not None
        current.submission.dispatched_at -= 11
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
        self.seat_readback_lag = False
        self._lagged_seat: SeatSnapshot | None = None

    async def commit_area(self, snapshot, plan, ticket_number, claim: RunningEffect) -> EffectReceipt:
        assert snapshot == self.area and self.stage == "area"
        assert plan.route == "0205" and ticket_number == 2
        self.commits.append("area")
        self.stage = "seat"
        self.outcome_url = self.seat.page_url
        self.outcome_document = self.seat.document_id
        return self._receipt(claim, navigation=True)

    async def observe_seat(self, tab: TabKey) -> PageObservation:
        assert tab == self.tab and self.stage == "seat"
        if self._lagged_seat is not None:
            observed = self._lagged_seat
            self._lagged_seat = None
            return PageObservation(tab, observed, self.manual_selection_revision)
        return PageObservation(tab, self.seat, self.manual_selection_revision)

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
        if self.seat_readback_lag:
            self._lagged_seat = self.seat
        seat["page_url"] = self.seat.page_url
        seat["document_id"] = self.seat.document_id
        seat["product_id"] = self.seat.product_id
        self.seat = parse_seat_snapshot(seat)
        assert isinstance(self.seat, SeatSnapshot)
        return self._receipt(claim, navigation=False)

    async def clear_rejected_selection(self, attempt, claim):
        self.commits.append("conflict_clear")
        if self.conflict_partial:
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        seat = make_ctbc_seat_snapshot(selected=())
        seat["page_url"] = self.seat.page_url
        seat["document_id"] = self.seat.document_id
        seat["product_id"] = self.seat.product_id
        self.seat = parse_seat_snapshot(seat)
        assert isinstance(self.seat, SeatSnapshot)
        return self._receipt(claim, navigation=False)


def _seat_engine(
    *, preselected: bool = False, incomplete_selection: bool = False,
    auto_submit: bool = False,
):
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
        make_effective_policy(
            policy, FlowSettings(
                True, True, 2, date_keyword='"9/10"', area_keyword='"前排"',
                ocr_enabled=auto_submit, force_submit=auto_submit,
            ),
        ), browser, core,
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


def test_reserved_seat_known_captcha_rejection_gets_one_new_attempt() -> None:
    engine, handle, browser, _core = _seat_engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        original = engine._sessions[handle.tab].submission
        assert original is not None
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_field_empty = False
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        current = engine._sessions[handle.tab]
        assert current.prior_submissions == [original]
        assert current.submission is not None
        assert current.submission.attempt.attempt_id != original.attempt.attempt_id
        assert browser.commits.count("submit") == 2

    asyncio.run(exercise())


def test_delayed_seat_readback_finishes_original_selection() -> None:
    engine, handle, browser, _core = _seat_engine()
    browser.seat_readback_lag = True

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "seat"]


def test_trusted_manual_seat_replacement_ends_original_without_reselect() -> None:
    engine, handle, browser, _core = _seat_engine()
    browser.seat_readback_lag = True

    async def exercise() -> None:
        await _ready_seat_area(engine, handle)
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
        seat = make_ctbc_seat_snapshot(selected=(0,))
        seat["page_url"] = browser.seat.page_url
        seat["document_id"] = browser.seat.document_id
        seat["product_id"] = browser.seat.product_id
        browser.seat = parse_seat_snapshot(seat)
        assert isinstance(browser.seat, SeatSnapshot)
        browser.manual_selection_revision += 1
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert engine._sessions[handle.tab].active is None
        assert await engine.advance_seat(handle) is SelectionProgress.AWAITING_EVIDENCE

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


def test_reserved_seat_source_survives_policy_revalidation() -> None:
    for preselected in (False, True):
        engine, handle, browser, _core = _seat_engine(preselected=preselected)

        async def exercise() -> None:
            await _ready_seat_area(engine, handle)
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY
            previous = engine._policy
            assert previous.flow is not None
            assert engine.update_policy(replace(
                previous,
                flow=replace(previous.flow, refresh_interval=1),
                policy_revision=previous.policy_revision + 1,
            ))
            assert await engine.advance_seat(handle) is SelectionProgress.SEAT_READY

        asyncio.run(exercise())
        assert browser.commits == (
            ["date", "area"] if preselected else ["date", "area", "seat"]
        )


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


def test_observed_manual_submit_uses_prepared_source_without_dispatch_claim() -> None:
    engine, handle, browser, core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert engine._sessions[handle.tab].submission is None
        browser.manual_click_token = "trusted-click-1"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        submission = engine._sessions[handle.tab].submission
        attempt = submission.attempt if submission is not None else None
        assert attempt is not None and attempt.source is SubmitSource.OBSERVED_MANUAL
        assert attempt.stamp is None and attempt.manual_evidence is not None
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "manual-checkout"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_observed_manual_captcha_rejection_rearms_after_verified_new_image() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.manual_click_token = "trusted-click-1"
        browser.dialog_text = "驗證碼錯誤"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        current = engine._sessions[handle.tab]
        original = current.submission
        assert original is not None and original.attempt.stamp is None
        assert await engine.advance_dialog_dismissal(handle) is SelectionProgress.DIALOG_DISMISSED
        assert await engine.advance_captcha_reset(handle) is SelectionProgress.CAPTCHA_RESET_VERIFIED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_BLOCKED
        browser.captcha_field_empty = False
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert current.prior_submissions == [original]
        assert current.submission is None
        browser.manual_click_token = "trusted-click-2"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        replacement = current.submission
        assert replacement is not None
        assert replacement.attempt.source is SubmitSource.OBSERVED_MANUAL
        assert replacement.attempt.stamp is None
        assert replacement.attempt.attempt_id != original.attempt.attempt_id
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "manual-checkout"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED
        assert "submit" not in browser.commits

    asyncio.run(exercise())


def test_manual_submit_requires_matching_armed_click_and_prepared_input() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        browser.input_ready = False
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        browser.input_ready = True
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.manual_arm_token = "other-arm"
        browser.manual_click_token = "unmatched-click"
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert engine._sessions[handle.tab].submission is None

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_trusted_manual_quantity_replacement_ends_original_local_write() -> None:
    engine, handle, browser, _core = _engine()
    browser.quantity_readback_lag = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.quantity = replace(
            browser.quantity,
            rows=tuple(replace(row, value="3") if row.key == "FULL" else row
                       for row in browser.quantity.rows),
        )
        browser.manual_selection_revision += 1
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.active is None and current.ready_selection is None
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_paused_quantity_readback_finishes_original_then_resumes_without_rewrite() -> None:
    engine, handle, browser, core = _engine()
    browser.quantity_readback_lag = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        core.control = CoreControl(2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        assert await engine.advance_quantity(handle) is SelectionProgress.AWAITING_EVIDENCE
        current = engine._sessions[handle.tab]
        assert current.active is None and current.verified_quantity is not None
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.update_control(handle)
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_duplicate_and_conflicting_submit_receipts_keep_original_outcome_path() -> None:
    engine, handle, browser, _core = _engine(auto_submit=True)

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_DISPATCHED
        active = engine._sessions[handle.tab].active
        assert active is not None and active.receipt is not None
        original = active.receipt
        running = RunningEffect(active.claim.claim_id, active.claim.proposal)
        assert engine._record_receipt(handle, running, original)
        conflicting = EffectReceipt(
            running.claim_id, running.proposal.stamp, running.proposal.stamp.document,
            EffectStatus.STALE, EffectEvidence.NO_EFFECT,
            DispatchStage.BEFORE_DISPATCH,
        )
        assert not engine._record_receipt(handle, running, conflicting)
        assert active.receipt_conflict and active.receipt is original
        browser.outcome_url = "https://tix.ctbcsports.com/BROTHERS/UTK0206_"
        browser.outcome_document = "checkout-after-conflict"
        assert await engine.advance_submit(handle) is SelectionProgress.SUBMIT_CONFIRMED

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "submit"]


def test_closed_navigation_reconciles_its_original_destination_without_new_click() -> None:
    engine, handle, browser, core = _engine()
    browser.date_release = asyncio.Event()

    async def exercise() -> None:
        pending = asyncio.create_task(engine.advance_date(handle))
        await browser.date_started.wait()
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        browser.date_release.set()
        assert await pending is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.reconcile_closed(handle)
        assert handle.tab not in engine._retired_runs
        assert not await engine.reconcile_closed(handle)

    asyncio.run(exercise())
    assert browser.commits == ["date"]


def test_flow_assembly_rejects_missing_host_authority_capability() -> None:
    engine, _handle, browser, core = _engine()
    core.current_purchase_authority = None  # type: ignore[method-assign,assignment]
    with pytest.raises(ValueError, match="host capability missing: current_purchase_authority"):
        create_engine(engine._policy, browser, core)


def test_manual_preparation_disarms_before_replanning_new_policy() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        flow = engine._policy.flow
        assert flow is not None
        assert engine.update_policy(replace(
            engine._policy, flow=replace(flow, ticket_number=4),
            policy_revision=engine._policy.policy_revision + 1,
        ))
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        assert engine._sessions[handle.tab].manual_preparation is None
        assert await engine.advance_quantity(handle) is SelectionProgress.QUANTITY_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "quantity"]


def test_closed_manual_preparation_without_click_disarms_and_releases_tab() -> None:
    engine, handle, browser, core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        core.control = CoreControl(2, CoreGate.CLOSING)
        assert engine.update_control(handle) and engine.close(handle)
        assert await engine.advance_submit(handle) is SelectionProgress.BLOCKED
        assert handle.tab not in engine._retired_runs
        core.control = CoreControl(3, CoreGate.PURCHASE_ALLOWED)
        assert engine.attach(handle.tab).generation != handle.generation

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_click_seen_before_manual_preparation_does_not_create_attempt() -> None:
    engine, handle, browser, _core = _engine()

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        browser.manual_click_token = "earlier-click"
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_submit(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert engine._sessions[handle.tab].submission is None

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity"]


def test_login_pre_dispatch_no_effect_reobserves_before_new_write() -> None:
    engine, handle, browser, _core = _engine()
    browser.login_required = True
    browser.login_no_effect = True

    async def exercise() -> None:
        await _ready_quantity(engine, handle)
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_login(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.login_no_effect = False
        assert await engine.advance_login(handle) is SelectionProgress.LOGIN_READY

    asyncio.run(exercise())
    assert browser.commits == ["date", "area", "quantity", "login", "login"]


def test_date_pre_dispatch_no_effect_can_replan_after_fresh_source_read() -> None:
    engine, handle, browser, _core = _engine()
    browser.stale_date = True

    async def exercise() -> None:
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        assert await engine.advance_date(handle) is SelectionProgress.AWAITING_EVIDENCE
        browser.stale_date = False
        assert await engine.advance_date(handle) is SelectionProgress.DATE_DISPATCHED
        assert await engine.advance_area(handle) is SelectionProgress.AREA_DISPATCHED

    asyncio.run(exercise())
    assert browser.commits == ["date", "date", "area"]
