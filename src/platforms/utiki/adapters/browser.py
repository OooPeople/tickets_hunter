"""Production-script browser operations for one bound uTiki tab."""

from __future__ import annotations

import asyncio
import hashlib
from importlib import import_module
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit


from ..application.ports import (
    CaptchaResetObservation, InlineLoginAudit, InlineLoginCredentials, InlineLoginIntent,
    PageObservation, PurchaseInputObservation, RejectedLoginObservation,
    SubmitOutcomeObservation,
)
from ..application.refresh_contract import RefreshDocument
from ..domain.effects import (
    DispatchStage, EffectEvidence, EffectKind, EffectReceipt, EffectStatus,
    RunningEffect,
)
from ..domain.identity import TabKey
from ..domain.inputs import CaptchaAttempt, CaptchaImageKey
from ..domain.plans import AreaActionPlan, DateActionPlan, QuantityPlan, SeatActionPlan, SubmitPlan
from ..domain.purchase import ManualSubmitEvidence, SubmitAttempt
from ..domain.snapshots import AreaSnapshot, DateSnapshot, DialogSnapshot, QuantitySnapshot, SeatSnapshot
from ..profiles import PageKind, classify_utiki_page, get_utiki_tenant_profile
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

    @property
    def bound_tab(self) -> TabKey:
        return self._tab_key

    def _check_claim(self, claim: RunningEffect, document_id: str) -> None:
        if (
            not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.SELECT
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != document_id
        ):
            raise ValueError("selection mutation requires a matching running grant")

    async def observe_refresh_document(self, tab: TabKey) -> RefreshDocument:
        if tab != self._tab_key:
            raise ValueError("refresh observation requested for another tab")
        url = await self._tab.evaluate("location.href")
        if not isinstance(url, str):
            raise ValueError("refresh URL is unavailable")
        selector = {
            PageKind.DATE: "#PerformanceListTable",
            PageKind.AREA: ".seats, #mapdata",
            PageKind.RESERVED_SEAT: "#TBL, .ticket",
            PageKind.GENERAL_ADMISSION: "#table_tickettype, .ticket",
            PageKind.SERVER_ASSIGNED_QUANTITY: "#table_tickettype",
        }.get(classify_utiki_page(url))
        raw = await self._tab.evaluate(assemble_script("document_health", {
            "__UTIKI_DOCUMENT_HEALTH_ARG_0__": (
                f"!!document.querySelector({json.dumps(selector)})"
                if selector else "false"
            ),
            "__UTIKI_DOCUMENT_HEALTH_ARG_1__": str(bool(selector)).lower(),
        }))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(value, dict) or value.get("url") != url:
                raise ValueError("refresh document changed during observation")
            document_id = value["document_id"]
            started_at = float(value["document_started_at"])
            if (
                not isinstance(document_id, str)
                or not math.isfinite(float(document_id))
                or float(document_id) <= 0
                or not math.isfinite(started_at) or started_at < 0
            ):
                raise ValueError("refresh document identity is invalid")
            body = str(value.get("body_text") or "")
            has_code = bool(re.search(
                r"(^|\s|[:：])(?:HTTP\s*)?429(?:\s|[-:：]|$)", body, re.I,
            ))
            has_message = bool(re.search(
                r"請求頻率過高|Too Many Requests", body, re.I,
            ))
            normal_root = value.get("has_normal_root") is True
            if value.get("ready") is not True:
                classification = "unknown"
            elif (
                has_code and has_message and not normal_root
                and get_utiki_tenant_profile(url) is not None
                and classify_utiki_page(url) is not PageKind.UNKNOWN
            ):
                classification = "blocked"
            elif normal_root:
                classification = "normal"
            elif not selector:
                classification = "unverified"
            else:
                classification = "unknown"
            return RefreshDocument(
                tab, url, document_id, classification, started_at,
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("refresh document evidence is invalid") from exc

    async def dispatch_reload(self, claim: RunningEffect, expected_url: str) -> EffectReceipt:
        if (
            not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.RELOAD
            or claim.proposal.stamp.tab != self._tab_key
        ):
            raise ValueError("reload requires this tab's running claim")
        observed = await self.observe_refresh_document(self._tab_key)
        if (
            observed.document_id != claim.proposal.stamp.document.token
            or observed.url != expected_url
        ):
            return EffectReceipt(
                claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
                DispatchStage.BEFORE_DISPATCH,
            )
        try:
            await asyncio.wait_for(self._tab.reload(), timeout=120.0)
        except Exception:
            pass  # The browser may have sent the request before reporting failure.
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE,
            DispatchStage.REQUEST_BOUNDARY,
        )

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
        before = await self._manual_selection_revision()
        snapshot = await dom.read_quantity_snapshot(self._tab)
        if not isinstance(snapshot, QuantitySnapshot):
            raise ValueError("quantity reader returned an invalid snapshot")
        after = await self._manual_selection_revision()
        if before != after:
            before = after
            snapshot = await dom.read_quantity_snapshot(self._tab)
            after = await self._manual_selection_revision()
            if not isinstance(snapshot, QuantitySnapshot) or before != after:
                raise ValueError("quantity changed during observation")
        if after[:2] != (snapshot.page_url, snapshot.document_id):
            raise ValueError("quantity manual source changed during observation")
        return PageObservation(self._tab_key, snapshot, after[2])

    async def observe_seat(self, tab: TabKey) -> PageObservation:
        if tab != self._tab_key:
            raise ValueError("seat observation requested for another tab")
        before = await self._manual_selection_revision()
        snapshot = await dom.read_seat_snapshot(self._tab)
        if not isinstance(snapshot, SeatSnapshot):
            raise ValueError("seat reader returned an invalid snapshot")
        after = await self._manual_selection_revision()
        if before != after:
            before = after
            snapshot = await dom.read_seat_snapshot(self._tab)
            after = await self._manual_selection_revision()
            if not isinstance(snapshot, SeatSnapshot) or before != after:
                raise ValueError("seat changed during observation")
        if after[:2] != (snapshot.page_url, snapshot.document_id):
            raise ValueError("seat manual source changed during observation")
        return PageObservation(self._tab_key, snapshot, after[2])

    async def _manual_selection_revision(self) -> tuple[str, str, int]:
        raw = await self._tab.evaluate(assemble_script("manual_selection_observe"))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except ValueError as exc:
            raise ValueError("manual selection reader returned invalid JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"page_url", "document_id", "revision"}
            or type(value["page_url"]) is not str
            or type(value["document_id"]) is not str
            or type(value["revision"]) is not int
            or value["revision"] < 0
        ):
            raise ValueError("manual selection reader returned invalid evidence")
        return value["page_url"], value["document_id"], value["revision"]

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

    async def commit_server_assigned_area(
        self, snapshot: AreaSnapshot, plan: AreaActionPlan,
        ticket_number: int, claim: RunningEffect,
    ) -> EffectReceipt:
        self._check_claim(claim, snapshot.document_id)
        result = await dom.commit_server_assigned_area(
            self._tab, snapshot, plan, ticket_number,
            claim.claim_id.token, _FAST_MARKER_KEY,
        )
        # The native script can prepare mode/checkbox controls before a
        # refusal, and a row click is not proof of an accepted request.
        evidence, stage = (
            (EffectEvidence.REQUEST_POSSIBLE, DispatchStage.REQUEST_BOUNDARY)
            if result is dom.CommitResult.COMMITTED else
            (EffectEvidence.LOCAL_MUTATION, DispatchStage.LOCAL_MUTATION)
        )
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.UNKNOWN, evidence, stage,
        )

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

    async def arm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str,
        prior: ManualSubmitEvidence | None = None,
    ) -> str:
        if tab != self._tab_key or not page_url or not document_id:
            raise ValueError("manual arm requires original tab and document")
        armed = await self._tab.evaluate(assemble_script("manual_submit_arm", {
            "__UTIKI_MANUAL_ARM_ARG_0__": json.dumps(page_url),
            "__UTIKI_MANUAL_ARM_ARG_1__": json.dumps(document_id),
            "__UTIKI_MANUAL_ARM_ARG_2__": json.dumps({
                "page_url": prior.page_url,
                "document_id": prior.document_id,
                "arm_token": prior.arm_token,
                "click_token": prior.click_token,
            } if prior is not None else None),
        }))
        try:
            arm_value = json.loads(armed) if isinstance(armed, str) else None
        except ValueError as exc:
            raise ValueError("manual submit arm returned invalid JSON") from exc
        if (
            not isinstance(arm_value, dict) or set(arm_value) != {"arm_token"}
            or not isinstance(arm_value["arm_token"], str)
        ):
            raise ValueError("manual submit arm returned invalid evidence")
        return arm_value["arm_token"]

    async def disarm_manual_submit(
        self, tab: TabKey, page_url: str, document_id: str, arm_token: str,
    ) -> bool:
        if tab != self._tab_key or not all(
            type(value) is str and value for value in (page_url, document_id, arm_token)
        ):
            raise ValueError("manual disarm requires original tab and source")
        raw = await self._tab.evaluate(assemble_script("manual_submit_disarm", {
            "__UTIKI_MANUAL_DISARM_ARG_0__": json.dumps(page_url),
            "__UTIKI_MANUAL_DISARM_ARG_1__": json.dumps(document_id),
            "__UTIKI_MANUAL_DISARM_ARG_2__": json.dumps(arm_token),
        }))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
        except ValueError as exc:
            raise ValueError("manual disarm returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"disarmed"} or type(value["disarmed"]) is not bool:
            raise ValueError("manual disarm returned invalid result")
        return value["disarmed"]

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
            or set(value) != {"page_url", "document_id", "status", "manual_ready"}
            or not isinstance(value["page_url"], str)
            or not isinstance(value["document_id"], str)
            or value["status"] not in {"applied", "rejected", "unknown"}
            or type(value["manual_ready"]) is not bool
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
            value.get("manual_ready") is True
            and value["page_url"] == intent.page_url
            and value["document_id"] == intent.document_id,
        )

    async def _rejected_login_script(
        self, mode: str, page_url: str, document_id: str, token: str,
    ) -> RejectedLoginObservation:
        if not page_url or not document_id or not token:
            raise ValueError("rejected login requires source identity")
        raw = await self._tab.evaluate(assemble_script("rejected_login_observe", {
            "__UTIKI_REJECTED_LOGIN_ARG_0__": json.dumps({
                "mode": mode, "pageUrl": page_url,
                "documentId": document_id, "origin": (
                    f"{urlsplit(page_url).scheme}://{urlsplit(page_url).netloc}"
                ), "token": token,
            }),
        }))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(value, dict) or set(value) != {
                "page_url", "document_id", "token", "changed_ready",
            }:
                raise ValueError("rejected login observation is invalid")
            return RejectedLoginObservation(
                self._tab_key, value["page_url"], value["document_id"],
                value["token"], value["changed_ready"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("rejected login observation is invalid") from exc

    async def arm_rejected_login(
        self, tab: TabKey, page_url: str, document_id: str, token: str,
        claim: RunningEffect,
    ) -> EffectReceipt:
        if (
            tab != self._tab_key or claim.proposal.kind is not EffectKind.INPUT_WRITE
            or claim.proposal.stamp.tab != tab
            or claim.proposal.stamp.document.token != document_id
            or not claim.proposal.parent_recovery_id
        ):
            raise ValueError("rejected login arm requires its own recovery claim")
        observed = await self._rejected_login_script("arm", page_url, document_id, token)
        valid = (
            observed.page_url == page_url and observed.document_id == document_id
            and observed.token == token and observed.changed_ready
        )
        return EffectReceipt(
            claim.claim_id, claim.proposal.stamp, claim.proposal.stamp.document,
            EffectStatus.APPLIED if valid else EffectStatus.REJECTED,
            EffectEvidence.LOCAL_MUTATION if valid else EffectEvidence.NO_EFFECT,
            DispatchStage.LOCAL_MUTATION if valid else DispatchStage.BEFORE_DISPATCH,
        )

    async def observe_rejected_login(
        self, tab: TabKey, page_url: str, document_id: str, token: str,
    ) -> RejectedLoginObservation:
        if tab != self._tab_key:
            raise ValueError("rejected login observation requested for another tab")
        return await self._rejected_login_script("observe", page_url, document_id, token)

    async def clear_rejected_selection(
        self, attempt: SubmitAttempt, claim: RunningEffect,
    ) -> EffectReceipt:
        plan = attempt.plan
        if (
            claim.proposal.kind is not EffectKind.SELECT
            or claim.proposal.parent_recovery_id != attempt.attempt_id.token
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != plan.document_id
        ):
            raise ValueError("conflict clear requires its own original attempt claim")
        result = await dom.clear_conflict_selection(
            self._tab, plan.page_kind, plan.page_url, plan.document_id,
            attempt.key.product_id, plan.selection_plan,
        )
        if result is dom.CommitResult.COMMITTED:
            status, evidence, stage = (
                EffectStatus.APPLIED, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        elif result is dom.CommitResult.REJECTED:
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

    async def dispatch_submit(
        self, plan: SubmitPlan, claim: RunningEffect,
        login_intent: InlineLoginIntent | None = None,
        credentials: InlineLoginCredentials | None = None,
        captcha_observed: CaptchaResetObservation | None = None,
        captcha_answer: str | None = None,
        manual_login_token: str | None = None,
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
        if manual_login_token is not None and (
            not manual_login_token or login_intent is not None
        ):
            raise ValueError("manual login guard requires its own source token")
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
        elif manual_login_token is not None:
            parsed = urlsplit(plan.page_url)
            expected_login = {
                "origin": f"{parsed.scheme}://{parsed.netloc}",
                "manualToken": manual_login_token,
                "documentId": plan.document_id,
            }
        result = await dom.dispatch_submit(
            self._tab, plan, expected_login=expected_login,
            expected_captcha=(
                {
                    "imageSource": captcha_observed.image.source,
                    "imageDataUrl": captcha_observed.image_data_url,
                    "manualRevision": captcha_observed.manual_revision,
                    "answer": captcha_answer,
                }
                if captcha_observed is not None and captcha_observed.image is not None
                and captcha_observed.tab == self._tab_key
                and captcha_observed.page_url == plan.page_url
                and captcha_observed.document_id == plan.document_id
                else None
            ),
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
        raw = await self._tab.evaluate(assemble_script("manual_submit_read"))
        try:
            value = json.loads(raw) if isinstance(raw, str) else None
            manual = ManualSubmitEvidence(**value) if isinstance(value, dict) else None
        except (TypeError, ValueError) as exc:
            raise ValueError("manual submit reader returned invalid evidence") from exc
        return SubmitOutcomeObservation(
            tab, dialog.page_url, dialog.document_id, dialog, manual,
        )

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
            value["manual_value_ready"], value["manual_revision"],
        )

    async def _captcha_input_guard(
        self, mode: str, observed: CaptchaResetObservation, answer: str,
    ) -> str:
        if (
            observed.tab != self._tab_key or observed.image is None
            or not observed.field_usable or observed.dialog_visible
            or mode not in {"write", "audit"}
            or not isinstance(answer, str) or len(answer) != 4
        ):
            raise ValueError("CAPTCHA input guard requires current image and field")
        result = await self._tab.evaluate(assemble_script("captcha_input_guard", {
            "__UTIKI_CAPTCHA_INPUT_ARG_0__": json.dumps({
                "mode": mode, "pageUrl": observed.page_url,
                "documentId": observed.document_id,
                "imageSource": observed.image.source,
                "imageDataUrl": observed.image_data_url,
                "manualRevision": observed.manual_revision,
                "answer": answer,
            }),
        }, native_setter_fragment=native_input.CONST_NATIVE_INPUT_SETTER_JS))
        if not isinstance(result, str):
            raise ValueError("CAPTCHA input guard returned an invalid result")
        return result

    async def commit_captcha_input(
        self, observed: CaptchaResetObservation, attempt: CaptchaAttempt,
        answer: str, claim: RunningEffect,
    ) -> EffectReceipt:
        if (
            not isinstance(attempt, CaptchaAttempt)
            or not isinstance(claim, RunningEffect)
            or claim.proposal.kind is not EffectKind.INPUT_WRITE
            or claim.proposal.stamp.tab != self._tab_key
            or claim.proposal.stamp.document.token != observed.document_id
            or attempt.image != observed.image or not observed.field_empty
            or attempt.context.document.token != observed.document_id
        ):
            raise ValueError("CAPTCHA input write requires a matching running grant")
        result = await self._captcha_input_guard("write", observed, answer)
        if result not in {"committed", "rejected", "stale", "partial"}:
            raise RuntimeError("CAPTCHA input write returned an unknown result")
        if result == "committed":
            status, evidence, stage = (
                EffectStatus.APPLIED, EffectEvidence.LOCAL_MUTATION,
                DispatchStage.LOCAL_MUTATION,
            )
        elif result in {"rejected", "stale"}:
            status, evidence, stage = (
                EffectStatus.STALE, EffectEvidence.NO_EFFECT,
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

    async def audit_captcha_input(
        self, observed: CaptchaResetObservation, answer: str,
    ) -> bool:
        return await self._captcha_input_guard("audit", observed, answer) == "audited"

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
