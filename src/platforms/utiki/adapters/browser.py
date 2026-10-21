"""Production-script browser operations for one bound uTiki tab."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib import import_module
import json
from typing import Any


from ..application.ports import (
    CaptchaResetObservation, InlineLoginAudit, InlineLoginCredentials, InlineLoginIntent,
    PageObservation, PurchaseInputObservation, QuantityObservation, SubmitOutcomeObservation,
)
from ..domain.effects import (
    DispatchStage, EffectEvidence, EffectKind, EffectReceipt, EffectStatus,
    RunningEffect,
)
from ..domain.identity import DocumentKey, OperationStamp, TabKey
from ..domain.inputs import CaptchaImageKey
from ..domain.plans import AreaActionPlan, DateActionPlan, QuantityPlan, SeatActionPlan, SubmitPlan
from ..domain.snapshots import AreaSnapshot, DateSnapshot, DialogSnapshot, QuantitySnapshot, SeatSnapshot
from .script_resources import assemble_script


_FAST_MARKER_KEY = "ticketsHunter.ctbc.fast-navigation.v1"
# The existing production DOM module is the untyped I/O boundary until stage 5.
dom: Any = import_module("platforms.utiki.dom")
native_input: Any = import_module("nodriver_common")


def _receipt(claim: RunningEffect, result: object, *, navigation: bool) -> EffectReceipt:
    if result is dom.CommitResult.REJECTED and navigation:
        status, evidence, stage = (
            EffectStatus.REJECTED, EffectEvidence.NO_EFFECT, DispatchStage.BEFORE_DISPATCH,
        )
    elif result is dom.CommitResult.STALE and navigation:
        status, evidence, stage = (
            EffectStatus.STALE, EffectEvidence.NO_EFFECT, DispatchStage.BEFORE_DISPATCH,
        )
    elif result is dom.CommitResult.COMMITTED and not navigation:
        status, evidence, stage = (
            EffectStatus.APPLIED, EffectEvidence.LOCAL_MUTATION, DispatchStage.LOCAL_MUTATION,
        )
    elif result is dom.CommitResult.COMMITTED:
        status, evidence, stage = (
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_DISPATCHED, DispatchStage.REQUEST_BOUNDARY,
        )
    elif navigation:
        status, evidence, stage = (
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE, DispatchStage.REQUEST_BOUNDARY,
        )
    else:
        status, evidence, stage = (
            EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION, DispatchStage.LOCAL_MUTATION,
        )
    return EffectReceipt(
        claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
        status, evidence, stage,
    )


class ZendriverSelectionBrowser:
    """A capability bound to one tab; session grants remain with the engine."""

    def __init__(self, tab: object, tab_key: TabKey) -> None:
        if not isinstance(tab_key, TabKey):
            raise ValueError("browser adapter requires a tab key")
        self._tab: Any = tab
        self._tab_key = tab_key

    def _check_claim(self, claim: RunningEffect, document_id: str) -> None:
        if (
            not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.SELECT
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != document_id
        ):
            raise ValueError("selection mutation requires a matching running grant")

    async def observe_date(self, tab: TabKey) -> PageObservation:
        if tab != self._tab_key:
            raise ValueError("date observation requested for another tab")
        snapshot = await dom.read_date_snapshot(self._tab)
        if not isinstance(snapshot, DateSnapshot):
            raise ValueError("date reader returned an invalid snapshot")
        return PageObservation(self._tab_key, snapshot)

    async def observe_area(self, tab: TabKey) -> PageObservation:
        if tab != self._tab_key:
            raise ValueError("area observation requested for another tab")
        snapshot = await dom.read_area_snapshot(self._tab)
        if not isinstance(snapshot, AreaSnapshot):
            raise ValueError("area reader returned an invalid snapshot")
        return PageObservation(self._tab_key, snapshot)

    async def observe_quantity(self, tab: TabKey) -> PageObservation:
        if tab != self._tab_key:
            raise ValueError("quantity observation requested for another tab")
        snapshot = await dom.read_quantity_snapshot(self._tab)
        if not isinstance(snapshot, QuantitySnapshot):
            raise ValueError("quantity reader returned an invalid snapshot")
        return PageObservation(self._tab_key, snapshot)

    async def observe_seat(self, tab: TabKey) -> PageObservation:
        if tab != self._tab_key:
            raise ValueError("seat observation requested for another tab")
        snapshot = await dom.read_seat_snapshot(self._tab)
        if not isinstance(snapshot, SeatSnapshot):
            raise ValueError("seat reader returned an invalid snapshot")
        return PageObservation(self._tab_key, snapshot)

    async def observe_page(self, expected: OperationStamp) -> QuantityObservation:
        observation = await self.observe_quantity(expected.tab)
        snapshot = observation.snapshot
        if not isinstance(snapshot, QuantitySnapshot):
            raise ValueError("quantity reader returned an invalid snapshot")
        source = replace(expected, document=DocumentKey(snapshot.document_id))
        return QuantityObservation(source, snapshot)

    async def commit_date(
        self, snapshot: DateSnapshot, plan: DateActionPlan, claim: RunningEffect
    ) -> EffectReceipt:
        self._check_claim(claim, snapshot.document_id)
        result = await dom.commit_date_action(
            self._tab, snapshot, plan, claim.claim_id.token,
        )
        return _receipt(claim, result, navigation=True)

    async def commit_area(
        self, snapshot: AreaSnapshot, plan: AreaActionPlan,
        ticket_number: int, claim: RunningEffect,
    ) -> EffectReceipt:
        self._check_claim(claim, snapshot.document_id)
        result = await dom.commit_area(
            self._tab, snapshot, plan, ticket_number,
            claim.claim_id.token, _FAST_MARKER_KEY,
        )
        return _receipt(claim, result, navigation=True)

    async def commit_quantity_snapshot(
        self, snapshot: QuantitySnapshot, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        self._check_claim(claim, snapshot.document_id)
        result = await dom.commit_quantity(self._tab, snapshot, plan)
        return _receipt(claim, result, navigation=False)

    async def commit_seat_snapshot(
        self, snapshot: SeatSnapshot, plan: SeatActionPlan, claim: RunningEffect,
    ) -> EffectReceipt:
        self._check_claim(claim, snapshot.document_id)
        result = await dom.commit_seat(self._tab, snapshot, plan)
        return _receipt(claim, result, navigation=False)

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        observation = await self.observe_quantity(claim.proposal.stamp.tab)
        snapshot = observation.snapshot
        if not isinstance(snapshot, QuantitySnapshot):
            raise ValueError("quantity reader returned an invalid snapshot")
        return await self.commit_quantity_snapshot(snapshot, plan, claim)

    async def observe_purchase_input(self, tab: TabKey) -> PurchaseInputObservation:
        if tab != self._tab_key:
            raise ValueError("input observation requested for another tab")
        raw = await self._tab.evaluate(assemble_script("purchase_input_snapshot"))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except ValueError as exc:
            raise ValueError("input reader returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("input reader returned an invalid snapshot")
        return PurchaseInputObservation(self._tab_key, **value)

    async def _login_guard(
        self, mode: str, intent: InlineLoginIntent, credentials: InlineLoginCredentials,
    ) -> dict[str, object]:
        script = assemble_script("inline_login_guarded", {
            "__UTIKI_LOGIN_GUARD_ARG_0__": json.dumps(mode),
            "__UTIKI_LOGIN_GUARD_ARG_1__": json.dumps(intent.page_url),
            "__UTIKI_LOGIN_GUARD_ARG_2__": json.dumps(intent.document_id),
            "__UTIKI_LOGIN_GUARD_ARG_3__": json.dumps(intent.expected_origin),
            "__UTIKI_LOGIN_GUARD_ARG_4__": json.dumps(credentials.account),
            "__UTIKI_LOGIN_GUARD_ARG_5__": json.dumps(credentials.password),
            "__UTIKI_LOGIN_GUARD_ARG_6__": json.dumps(intent.write_account),
            "__UTIKI_LOGIN_GUARD_ARG_7__": json.dumps(intent.write_password),
        }, native_setter_fragment=native_input.CONST_NATIVE_INPUT_SETTER_JS)
        raw = await self._tab.evaluate(script)
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except ValueError as exc:
            raise ValueError("inline login returned invalid JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"page_url", "document_id", "status"}
            or not isinstance(value["page_url"], str)
            or not isinstance(value["document_id"], str)
            or value["status"] not in {"applied", "rejected", "unknown"}
        ):
            raise ValueError("inline login returned an invalid result")
        return value

    async def commit_inline_login(
        self, intent: InlineLoginIntent, credentials: InlineLoginCredentials,
        claim: RunningEffect,
    ) -> EffectReceipt:
        if (
            not isinstance(intent, InlineLoginIntent)
            or not isinstance(credentials, InlineLoginCredentials)
            or not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.INPUT_WRITE
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != intent.document_id
            or credentials.revision != intent.credential_revision
        ):
            raise ValueError("inline login requires a matching running grant")
        value = await self._login_guard("write", intent, credentials)
        status_name = value["status"]
        if (
            value["page_url"] != intent.page_url
            or value["document_id"] != intent.document_id
        ):
            status_name = "unknown" if status_name != "rejected" else "rejected"
        if status_name == "applied":
            status, evidence, stage = (
                EffectStatus.APPLIED, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        elif status_name == "rejected":
            status, evidence, stage = (
                EffectStatus.REJECTED, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        else:
            status, evidence, stage = (
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            status, evidence, stage,
        )

    async def audit_inline_login(
        self, tab: TabKey, intent: InlineLoginIntent,
        credentials: InlineLoginCredentials,
    ) -> InlineLoginAudit:
        if tab != self._tab_key or credentials.revision != intent.credential_revision:
            raise ValueError("inline login audit requires matching tab and revision")
        value = await self._login_guard("audit", intent, credentials)
        return InlineLoginAudit(
            tab, str(value["page_url"]), str(value["document_id"]),
            value["status"] == "applied"
            and value["page_url"] == intent.page_url
            and value["document_id"] == intent.document_id,
        )

    async def dispatch_submit(
        self, plan: SubmitPlan, claim: RunningEffect,
        login_intent: InlineLoginIntent | None = None,
        credentials: InlineLoginCredentials | None = None,
    ) -> EffectReceipt:
        if (
            not isinstance(plan, SubmitPlan) or not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.SUBMIT
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != plan.document_id
        ):
            raise ValueError("submit requires a matching running grant")
        if (login_intent is None) != (credentials is None):
            raise ValueError("submit login guard requires both intent and credentials")
        expected_login = None
        if login_intent is not None and credentials is not None:
            if (
                login_intent.page_url != plan.page_url
                or login_intent.document_id != plan.document_id
                or login_intent.credential_revision != credentials.revision
            ):
                raise ValueError("submit login guard differs from the source page")
            expected_login = {
                "origin": login_intent.expected_origin,
                "account": credentials.account,
                "password": credentials.password,
            }
        result = await dom.dispatch_submit(
            self._tab, plan, expected_login=expected_login,
        )
        if result is dom.SubmitDispatchResult.DISPATCHED:
            status, evidence, stage = (
                EffectStatus.UNKNOWN, EffectEvidence.REQUEST_DISPATCHED,
                DispatchStage.REQUEST_BOUNDARY,
            )
        elif result in (dom.SubmitDispatchResult.REJECTED, dom.SubmitDispatchResult.STALE):
            status, evidence, stage = (
                EffectStatus.REJECTED if result is dom.SubmitDispatchResult.REJECTED
                else EffectStatus.STALE,
                EffectEvidence.NO_EFFECT, DispatchStage.BEFORE_DISPATCH,
            )
        else:
            raise ValueError("submit dispatch outcome is unknown")
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            status, evidence, stage,
        )

    async def observe_submit_outcome(self, tab: TabKey) -> SubmitOutcomeObservation:
        if tab != self._tab_key:
            raise ValueError("submit observation requested for another tab")
        dialog = await dom.read_dialog_snapshot(self._tab)
        if not isinstance(dialog, DialogSnapshot):
            raise ValueError("dialog reader returned an invalid snapshot")
        return SubmitOutcomeObservation(tab, dialog.page_url, dialog.document_id, dialog)

    async def dismiss_submit_dialog(
        self, dialog: DialogSnapshot, claim: RunningEffect,
    ) -> EffectReceipt:
        if (
            not isinstance(dialog, DialogSnapshot)
            or not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.DISMISS_DIALOG
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != dialog.document_id
        ):
            raise ValueError("dialog dismissal requires a matching running grant")
        result = await dom.dismiss_dialog(self._tab, dialog)
        return _receipt(claim, result, navigation=False)

    async def observe_captcha_reset(self, tab: TabKey) -> CaptchaResetObservation:
        if tab != self._tab_key:
            raise ValueError("CAPTCHA reset observation requested for another tab")
        raw = await self._tab.evaluate(assemble_script("captcha_reset_observe"))
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict):
            raise ValueError("CAPTCHA reset reader returned an invalid snapshot")
        image_data_url = value.get("image_data_url")
        image_source = value.get("image_source")
        if not isinstance(image_data_url, str) or not isinstance(image_source, str):
            raise ValueError("CAPTCHA reset reader returned invalid image data")
        image = (
            CaptchaImageKey(
                image_source, "img#chk_pic",
                hashlib.sha256(image_data_url.encode("ascii")).hexdigest(), None,
            ) if image_data_url and image_source else None
        )
        return CaptchaResetObservation(
            tab, value["page_url"], value["document_id"], image,
            image_data_url, value["field_usable"], value["field_empty"],
            value["refresh_usable"], value["dialog_visible"],
        )

    async def commit_captcha_reset(
        self, observed: CaptchaResetObservation, claim: RunningEffect,
    ) -> EffectReceipt:
        if (
            not isinstance(observed, CaptchaResetObservation)
            or not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.CAPTCHA_RESET
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != observed.document_id
            or observed.image is None
            or not observed.field_usable or not observed.refresh_usable
            or observed.dialog_visible
        ):
            raise ValueError("CAPTCHA reset requires a matching running grant")
        raw = await self._tab.evaluate(assemble_script("captcha_reset_commit", {
            "__UTIKI_CAPTCHA_RESET_ARG_0__": json.dumps({
                "pageUrl": observed.page_url,
                "documentId": observed.document_id,
                "imageSource": observed.image.source,
                "imageDataUrl": observed.image_data_url,
            }),
        }))
        try:
            result = dom.CommitResult(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("CAPTCHA reset outcome is unknown") from exc
        if result is dom.CommitResult.COMMITTED:
            status, evidence, stage = (
                EffectStatus.UNKNOWN, EffectEvidence.REQUEST_DISPATCHED,
                DispatchStage.REQUEST_BOUNDARY,
            )
        elif result is dom.CommitResult.REJECTED:
            status, evidence, stage = (
                EffectStatus.REJECTED, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        else:
            # STALE can occur after the field was cleared but before refresh.
            status, evidence, stage = (
                EffectStatus.UNKNOWN, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            status, evidence, stage,
        )
