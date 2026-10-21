"""One uTiki planning owner for the current core control interface."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, cast
from urllib.parse import urlsplit

from utiki_config import CurrentPolicyState

from ..auth import get_tenant_auth_profile
from ..domain.effects import (
    ActiveEffect, AwaitingEvidenceEffect, ClaimId, EffectKind, EffectProposal,
    EffectEvidence, EffectReceipt, EffectStatus, HeldEffect, HeldReason, RunningEffect,
    receipt_matches,
)
from ..domain.identity import (
    DocumentKey, OperationId, OperationStamp, PurchaseKey, TabKey, TenantKey,
)
from ..domain.inputs import (
    CaptchaResetAttempt, InputContext, InputPolicy, InputReadyEvidence,
    InputSource, PurchaseCycleId, ResetId, ResetReason,
)
from ..domain.plans import AreaActionPlan, DateActionPlan, QuantityPlan, SeatActionPlan, SubmitPlan, build_submit_plan
from ..domain.purchase import SubmitAttempt, SubmitAttemptId
from ..domain.snapshots import (
    AreaSnapshot, DateSnapshot, DialogSnapshot, PresentField, QuantitySnapshot, SeatSnapshot,
    parse_nonnegative_count,
)
from ..profiles import PageKind, classify_utiki_page, get_utiki_tenant_profile, same_origin, unique_query
from ..selection import (
    classify_submit_dialog, quantity_audit_is_valid, select_area_snapshot, select_date_snapshot,
    select_quantity_snapshot, select_seat_snapshot,
)
from .flow_settings import FlowSettings
from .ports import (
    CaptchaResetObservation, CoreControl, CoreControlPort, CoreGate, InlineLoginAudit,
    InlineLoginCredentials, InlineLoginCredentialPort, InlineLoginIntent,
    PageObservation, PurchaseAuthority, PurchaseAuthorityPort, PurchaseBrowserPort,
    PurchaseInputObservation,
    QuantityBrowserPort, QuantityObservation, SelectionBrowserPort,
    SubmitOutcomeObservation,
)


@dataclass(frozen=True, slots=True)
class SessionHandle:
    tab: TabKey
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or type(self.generation) is not int or self.generation <= 0:
            raise ValueError("session handle requires tab and generation")


@dataclass(frozen=True, slots=True)
class PlanningContext:
    session_generation: int
    policy_revision: int
    control_revision: int


class SelectionProgress(str, Enum):
    BLOCKED = "blocked"
    AWAITING_EVIDENCE = "awaiting-evidence"
    DATE_DISPATCHED = "date-dispatched"
    AREA_DISPATCHED = "area-dispatched"
    QUANTITY_READY = "quantity-ready"
    SEAT_CLEARED = "seat-cleared"
    SEAT_READY = "seat-ready"
    LOGIN_READY = "login-ready"
    SUBMIT_DISPATCHED = "submit-dispatched"
    SUBMIT_CONFIRMED = "submit-confirmed"
    SUBMIT_BLOCKED = "submit-blocked"
    DIALOG_DISMISSED = "dialog-dismissed"
    CAPTCHA_RESET_VERIFIED = "captcha-reset-verified"


@dataclass(slots=True)
class _ActiveSelection:
    stage: str
    claim: ActiveEffect
    source_url: str
    plan: DateActionPlan | AreaActionPlan | QuantityPlan | SeatActionPlan | SubmitPlan | InlineLoginIntent | DialogSnapshot | CaptchaResetAttempt


@dataclass(slots=True)
class _Session:
    handle: SessionHandle
    control: CoreControl
    control_revision: int
    closing: bool = False
    active: _ActiveSelection | None = None
    ready_selection: QuantityPlan | SeatActionPlan | None = None
    ready_context: PlanningContext | None = None
    login_verified: InlineLoginIntent | None = None
    submit_attempt: SubmitAttempt | None = None
    submit_dispatched_at: float | None = None
    submit_request_boundary: bool = False
    submit_terminal: SelectionProgress | None = None
    rejection_kind: str | None = None
    rejection_dialog: DialogSnapshot | None = None
    dialog_dismissed: bool = False
    captcha_reset: CaptchaResetAttempt | None = None
    captcha_reset_verified: bool = False


class UtikiEngine:
    """Own attached tabs; only return read-only plans while all gates agree."""

    def __init__(
        self,
        policy: CurrentPolicyState,
        browser: QuantityBrowserPort,
        core: CoreControlPort,
        flow_settings: FlowSettings | None = None,
    ) -> None:
        if not isinstance(policy, CurrentPolicyState):
            raise ValueError("engine requires validated current policy state")
        self._policy = policy
        self._browser = browser
        self._core = core
        if flow_settings is not None and not isinstance(flow_settings, FlowSettings):
            raise ValueError("flow settings must be validated")
        self._flow_settings = flow_settings
        self._sessions: dict[TabKey, _Session] = {}
        self._retired_effects: dict[TabKey, _ActiveSelection] = {}
        self._next_generation = 0
        self._policy_conflict = False

    def _host_control(self, tab: TabKey) -> CoreControl:
        value = self._core.current_control(tab)
        if not isinstance(value, CoreControl):
            raise ValueError("core control source returned an invalid gate")
        return value

    def attach(self, tab: TabKey) -> SessionHandle:
        if not isinstance(tab, TabKey):
            raise ValueError("attach requires a typed tab")
        if tab in self._retired_effects:
            raise ValueError("retained outcome requires manual reconciliation")
        control = self._host_control(tab)
        if control.gate is CoreGate.CLOSING:
            raise ValueError("closing control cannot attach a session")
        existing = self._sessions.get(tab)
        if existing is not None:
            if existing.closing or existing.control != control:
                raise ValueError("reattach cannot replace current control")
            return existing.handle
        self._next_generation += 1
        handle = SessionHandle(tab, self._next_generation)
        self._sessions[tab] = _Session(handle, control, 1)
        return handle

    def _current(self, handle: SessionHandle) -> _Session | None:
        if not isinstance(handle, SessionHandle):
            return None
        current = self._sessions.get(handle.tab)
        return current if current is not None and current.handle == handle else None

    def update_control(self, handle: SessionHandle) -> bool:
        current = self._current(handle)
        if current is None:
            return False
        control = self._host_control(handle.tab)
        if control.revision < current.control.revision or (
            control.revision == current.control.revision and control != current.control
        ):
            if not current.closing and current.control.gate is not CoreGate.PAUSED:
                current.control = CoreControl(current.control.revision, CoreGate.PAUSED)
                current.control_revision += 1
            return False
        if control == current.control:
            return True
        current.control = control
        current.control_revision += 1
        current.closing = current.closing or control.gate is CoreGate.CLOSING
        return True

    def update_policy(self, policy: CurrentPolicyState) -> bool:
        if not isinstance(policy, CurrentPolicyState):
            self._policy_conflict = True
            return False
        if (
            policy.policy_revision < self._policy.policy_revision
            or policy.control_revision < self._policy.control_revision
            or (
                policy.last_valid != self._policy.last_valid
                and policy.policy_revision <= self._policy.policy_revision
            )
            or (
                policy != self._policy
                and policy.policy_revision == self._policy.policy_revision
                and policy.control_revision == self._policy.control_revision
            )
            or (
                self._policy_conflict
                and policy.policy_revision == self._policy.policy_revision
                and policy.control_revision == self._policy.control_revision
            )
        ):
            self._policy_conflict = True
            return False
        if policy != self._policy or self._policy_conflict:
            self._policy = policy
            self._policy_conflict = False
            for current in self._sessions.values():
                current.control_revision += 1
        return True

    def close(self, handle: SessionHandle) -> bool:
        current = self._current(handle)
        if current is None:
            return False
        if not current.closing:
            current.closing = True
            current.control_revision += 1
        if current.active is not None:
            current.active.claim = HeldEffect(
                current.active.claim.claim_id,
                current.active.claim.proposal,
                HeldReason.CONTROL_REVOKED,
            )
        control = self._host_control(handle.tab)
        if (
            control.gate is not CoreGate.CLOSING
            or control.revision < current.control.revision
            or (
                control.revision == current.control.revision
                and control != current.control
            )
        ):
            return False
        if current.active is not None:
            self._retired_effects[handle.tab] = current.active
        self._sessions.pop(handle.tab, None)
        return True

    def context(self, handle: SessionHandle) -> PlanningContext | None:
        current = self._current(handle)
        if (
            current is None or current.closing or self._policy_conflict
            or not self._policy.allows_new_mutation
            or current.control.gate is not CoreGate.PURCHASE_ALLOWED
            or self._host_control(handle.tab) != current.control
        ):
            return None
        return PlanningContext(
            handle.generation, self._policy.policy_revision, current.control_revision
        )

    async def plan_quantity(
        self, handle: SessionHandle, stamp: OperationStamp, ticket_number: int
    ) -> QuantityPlan | None:
        """Observe once; discard a plan if its source or current authority changes."""
        before = self.context(handle)
        if (
            before is None or not isinstance(stamp, OperationStamp)
            or stamp.tab != handle.tab
            or (
                stamp.session_generation, stamp.policy_revision, stamp.control_revision
            ) != (
                before.session_generation, before.policy_revision, before.control_revision
            )
        ):
            return None
        observation = await self._browser.observe_page(stamp)
        if (
            self.context(handle) != before
            or not isinstance(observation, QuantityObservation)
            or observation.source != stamp
            or observation.snapshot.document_id != stamp.document.token
        ):
            return None
        policy = self._policy.last_valid
        if policy is None:
            return None
        return select_quantity_snapshot(
            observation.snapshot, policy.ticket_type_keyword, ticket_number
        )

    def _flow_context(self, handle: SessionHandle) -> PlanningContext | None:
        context = self.context(handle)
        if (
            context is None or self._flow_settings is None
            or self._policy.last_valid is None
            or self._policy.last_valid.seat_selection_mode != "manual"
        ):
            return None
        return context

    def _flow_browser(self) -> SelectionBrowserPort:
        return cast(SelectionBrowserPort, self._browser)

    def _observation_valid(
        self, handle: SessionHandle, before: PlanningContext,
        observation: object, snapshot_type: type,
    ) -> bool:
        return bool(
            self._flow_context(handle) == before
            and isinstance(observation, PageObservation)
            and observation.tab == handle.tab
            and isinstance(observation.snapshot, snapshot_type)
            and observation.snapshot.ok
            and observation.snapshot.page_url
            and observation.snapshot.document_id
        )

    def _claim_selection(
        self, handle: SessionHandle, before: PlanningContext,
        stage: str, snapshot: DateSnapshot | AreaSnapshot | QuantitySnapshot | SeatSnapshot,
        plan: DateActionPlan | AreaActionPlan | QuantityPlan | SeatActionPlan,
        replacing: _ActiveSelection | None = None,
    ) -> RunningEffect | None:
        current = self._current(handle)
        if (
            current is None or self._flow_context(handle) != before
            or current.active is not replacing
        ):
            return None
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            DocumentKey(snapshot.document_id), before.policy_revision,
            before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(stamp, EffectKind.SELECT)
        )
        current.active = _ActiveSelection(stage, claim, snapshot.page_url, plan)
        return claim

    def _active_for_claim(self, handle: SessionHandle, claim: ActiveEffect) -> _ActiveSelection | None:
        current = self._current(handle)
        active = current.active if current is not None else self._retired_effects.get(handle.tab)
        return active if active is not None and active.claim.claim_id == claim.claim_id else None

    def _hold_claim(self, handle: SessionHandle, claim: ActiveEffect, reason: HeldReason) -> None:
        active = self._active_for_claim(handle, claim)
        if active is not None:
            active.claim = HeldEffect(claim.claim_id, claim.proposal, reason)

    async def _dispatch_selection(
        self, handle: SessionHandle, claim: RunningEffect,
        operation: Awaitable[EffectReceipt],
    ) -> bool:
        try:
            receipt = await operation
        except asyncio.CancelledError:
            self._hold_claim(handle, claim, HeldReason.UNCERTAIN_DISPATCH)
            raise
        except Exception:
            self._hold_claim(handle, claim, HeldReason.UNCERTAIN_DISPATCH)
            return False
        active = self._active_for_claim(handle, claim)
        if (
            active is None or not isinstance(receipt, EffectReceipt)
            or not receipt_matches(claim, receipt)
            or receipt.observed_document != claim.proposal.stamp.document
        ):
            self._hold_claim(handle, claim, HeldReason.UNMATCHED_COMPLETION)
            return False
        if not isinstance(active.claim, RunningEffect):
            return False
        valid_evidence = (
            receipt.status is EffectStatus.UNKNOWN
            and receipt.evidence in (
                EffectEvidence.REQUEST_DISPATCHED, EffectEvidence.REQUEST_POSSIBLE,
            )
            if active.stage in {"date", "area", "submit", "captcha_reset"} else (
                receipt.status is EffectStatus.APPLIED
                and receipt.evidence is EffectEvidence.LOCAL_MUTATION
            )
        )
        if (
            not valid_evidence
            or self._flow_context(handle) != PlanningContext(
                claim.proposal.stamp.session_generation,
                claim.proposal.stamp.policy_revision,
                claim.proposal.stamp.control_revision,
            )
        ):
            active.claim = HeldEffect(claim.claim_id, claim.proposal, HeldReason.UNCERTAIN_DISPATCH)
            current = self._current(handle)
            if (
                active.stage == "submit" and current is not None
                and receipt.evidence is EffectEvidence.NO_EFFECT
            ):
                current.submit_terminal = SelectionProgress.SUBMIT_BLOCKED
            return False
        active.claim = AwaitingEvidenceEffect(claim.claim_id, claim.proposal)
        return True

    @staticmethod
    def _destination_changed(active: _ActiveSelection, page_url: str, document_id: str) -> bool:
        try:
            source = urlsplit(active.source_url)
            destination = urlsplit(page_url)
            return bool(
                document_id != active.claim.proposal.stamp.document.token
                and same_origin(source, destination)
                and source.path.rpartition("/")[0] == destination.path.rpartition("/")[0]
            )
        except ValueError:
            return False

    @staticmethod
    def _prior_matches_context(prior: _ActiveSelection, context: PlanningContext) -> bool:
        stamp = prior.claim.proposal.stamp
        return (
            isinstance(prior.claim, AwaitingEvidenceEffect)
            and (
                stamp.session_generation, stamp.policy_revision, stamp.control_revision
            ) == (
                context.session_generation, context.policy_revision,
                context.control_revision,
            )
        )

    def _date_destination_matches(self, active: _ActiveSelection, snapshot: AreaSnapshot) -> bool:
        if not isinstance(active.plan, DateActionPlan) or not self._destination_changed(
            active, snapshot.page_url, snapshot.document_id
        ):
            return False
        source = unique_query(active.source_url)
        destination = unique_query(snapshot.page_url)
        return bool(
            source is not None and destination is not None
            and source.get("PRODUCT_ID")
            and destination.get("PRODUCT_ID") == source["PRODUCT_ID"]
            and destination.get("PERFORMANCE_ID")
            and (
                not active.plan.performance_id
                or destination["PERFORMANCE_ID"] == active.plan.performance_id
            )
        )

    def _area_destination_matches(self, active: _ActiveSelection, snapshot: QuantitySnapshot) -> bool:
        if not isinstance(active.plan, AreaActionPlan) or not self._destination_changed(
            active, snapshot.page_url, snapshot.document_id
        ):
            return False
        query = unique_query(snapshot.page_url)
        plan = active.plan
        return bool(
            query is not None
            and query.get("PERFORMANCE_ID") == plan.performance_id == snapshot.performance_id
            and query.get("GROUP_ID") == plan.group_id == snapshot.group_id
            and query.get("PERFORMANCE_PRICE_AREA_ID") == plan.price_area_id == snapshot.price_area_id
        )

    def _seat_destination_matches(self, active: _ActiveSelection, snapshot: SeatSnapshot) -> bool:
        if (
            not isinstance(active.plan, AreaActionPlan) or active.plan.route != "0205"
            or not self._destination_changed(active, snapshot.page_url, snapshot.document_id)
            or classify_utiki_page(snapshot.page_url) is not PageKind.RESERVED_SEAT
        ):
            return False
        source = unique_query(active.source_url)
        destination = unique_query(snapshot.page_url)
        plan = active.plan
        return bool(
            source is not None and set(source) == {"PERFORMANCE_ID", "PRODUCT_ID"}
            and destination is not None
            and set(destination) == {
                "PERFORMANCE_ID", "GROUP_ID", "PERFORMANCE_PRICE_AREA_ID",
            }
            and source["PERFORMANCE_ID"] == plan.performance_id
            and source["PRODUCT_ID"]
            and destination["PERFORMANCE_ID"] == plan.performance_id == snapshot.performance_id
            and destination["GROUP_ID"] == plan.group_id == snapshot.group_id
            and destination["PERFORMANCE_PRICE_AREA_ID"] == plan.price_area_id == snapshot.price_area_id
            and snapshot.product_hidden_present
            and snapshot.product_id == source["PRODUCT_ID"]
        )

    @staticmethod
    def _seat_after_clear_matches(active: _ActiveSelection, snapshot: SeatSnapshot) -> bool:
        plan = active.plan
        return bool(
            active.stage == "seat_clear" and isinstance(plan, SeatActionPlan)
            and plan.action == "clear" and snapshot.ok
            and snapshot.page_url == plan.page_url
            and snapshot.document_id == plan.document_id
            and snapshot.performance_id == plan.performance_id
            and snapshot.group_id == plan.group_id
            and snapshot.price_area_id == plan.price_area_id
            and snapshot.product_hidden_present
            and snapshot.product_id == plan.product_id
        )

    async def advance_date(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if before is None or current is None or settings is None or not settings.date_enabled:
            return SelectionProgress.BLOCKED
        if current.active is not None:
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_date(handle.tab)
        if not self._observation_valid(handle, before, observation, DateSnapshot):
            return SelectionProgress.BLOCKED
        snapshot = cast(DateSnapshot, observation.snapshot)
        plan = select_date_snapshot(snapshot, settings.selector_values())
        if plan is None:
            return SelectionProgress.BLOCKED
        claim = self._claim_selection(handle, before, "date", snapshot, plan)
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim, self._flow_browser().commit_date(snapshot, plan, claim)
        )
        return SelectionProgress.DATE_DISPATCHED if dispatched else SelectionProgress.AWAITING_EVIDENCE

    async def advance_area(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if before is None or current is None or settings is None or not settings.area_enabled:
            return SelectionProgress.BLOCKED
        prior = current.active
        if prior is None or prior.stage != "date" or not self._prior_matches_context(prior, before):
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_area(handle.tab)
        if not self._observation_valid(handle, before, observation, AreaSnapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        snapshot = cast(AreaSnapshot, observation.snapshot)
        if not self._date_destination_matches(prior, snapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        query = unique_query(snapshot.page_url)
        if query is None:
            return SelectionProgress.AWAITING_EVIDENCE
        plan = select_area_snapshot(
            snapshot, settings.selector_values(), query["PERFORMANCE_ID"],
            require_direct_destination=False,
        )
        if plan is None or plan.route not in {"0202", "0205"}:
            return SelectionProgress.BLOCKED
        claim = self._claim_selection(handle, before, "area", snapshot, plan, prior)
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_area(snapshot, plan, settings.ticket_number, claim),
        )
        return SelectionProgress.AREA_DISPATCHED if dispatched else SelectionProgress.AWAITING_EVIDENCE

    @staticmethod
    def _quantity_is_ready(snapshot: QuantitySnapshot, plan: QuantityPlan) -> bool:
        target_rows = [row for row in snapshot.rows if row.key == plan.target_key]
        limit = parse_nonnegative_count(snapshot.quantity_limit_raw) if (
            snapshot.quantity_limit_present
        ) else None
        return bool(
            snapshot.ok and snapshot.page_url == plan.page_url
            and snapshot.document_id == plan.document_id
            and snapshot.performance_id == plan.performance_id
            and snapshot.group_id == plan.group_id
            and snapshot.price_area_id == plan.price_area_id
            and snapshot.product_id == plan.product_id
            and snapshot.performance_hidden_present == plan.performance_hidden_present
            and snapshot.product_hidden_present == plan.product_hidden_present
            and snapshot.group_hidden_present == plan.group_hidden_present
            and snapshot.price_area_hidden_present == plan.price_area_hidden_present
            and snapshot.quantity_limit_present == plan.quantity_limit_present
            and (
                isinstance(limit, PresentField) and limit.value == plan.quantity_limit
                if snapshot.quantity_limit_present else (
                    not snapshot.quantity_limit_raw and plan.quantity_limit == 0
                )
            )
            and len(target_rows) == 1
            and target_rows[0].name == plan.target_name
            and target_rows[0].remaining_present == plan.target_remaining_present
            and quantity_audit_is_valid(
                [{"key": row.key, "value": row.value} for row in snapshot.rows],
                plan.target_key, plan.ticket_number,
            )
        )

    async def advance_quantity(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if before is None or current is None or settings is None:
            return SelectionProgress.BLOCKED
        if isinstance(current.ready_selection, QuantityPlan):
            readback = await self._flow_browser().observe_quantity(handle.tab)
            current = self._current(handle)
            if (
                current is not None
                and isinstance(current.ready_selection, QuantityPlan)
                and current.ready_context == before
                and self._observation_valid(handle, before, readback, QuantitySnapshot)
                and self._quantity_is_ready(
                    cast(QuantitySnapshot, readback.snapshot), current.ready_selection
                )
            ):
                return SelectionProgress.QUANTITY_READY
            if current is not None:
                current.ready_selection = None
                current.ready_context = None
                current.login_verified = None
            return SelectionProgress.AWAITING_EVIDENCE
        prior = current.active
        if (
            prior is None or prior.stage != "area"
            or not isinstance(prior.plan, AreaActionPlan) or prior.plan.route != "0202"
            or not self._prior_matches_context(prior, before)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_quantity(handle.tab)
        if not self._observation_valid(handle, before, observation, QuantitySnapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        snapshot = cast(QuantitySnapshot, observation.snapshot)
        if not self._area_destination_matches(prior, snapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        policy = self._policy.last_valid
        if policy is None:
            return SelectionProgress.BLOCKED
        plan = select_quantity_snapshot(
            snapshot, policy.ticket_type_keyword, settings.ticket_number
        )
        if plan is None:
            return SelectionProgress.BLOCKED
        claim = self._claim_selection(handle, before, "quantity", snapshot, plan, prior)
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_quantity_snapshot(snapshot, plan, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        readback = await self._flow_browser().observe_quantity(handle.tab)
        current = self._current(handle)
        if (
            not self._observation_valid(handle, before, readback, QuantitySnapshot)
            or current is None or current.active is None
            or current.active.claim.claim_id != claim.claim_id
            or not self._quantity_is_ready(cast(QuantitySnapshot, readback.snapshot), plan)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.ready_selection = plan
        current.ready_context = before
        current.login_verified = None
        current.active = None
        return SelectionProgress.QUANTITY_READY

    def _seat_ready_plan(
        self, snapshot: SeatSnapshot, expected: SeatActionPlan | None,
    ) -> SeatActionPlan | None:
        settings = self._flow_settings
        policy = self._policy.last_valid
        if settings is None or policy is None:
            return None
        plan, _reason = select_seat_snapshot(
            snapshot, policy.ticket_type_keyword, settings.ticket_number,
            settings.allow_non_adjacent, policy.seat_sorting,
        )
        if plan is None or plan.action != "ready":
            return None
        if expected is None:
            return plan
        fingerprints = (
            expected.target_seat_fingerprints if expected.action == "select"
            else expected.selected_seat_fingerprints
        )
        if (
            plan.page_url != expected.page_url or plan.document_id != expected.document_id
            or plan.performance_id != expected.performance_id
            or plan.group_id != expected.group_id
            or plan.price_area_id != expected.price_area_id
            or plan.product_id != expected.product_id
            or plan.ticket_number != expected.ticket_number
            or plan.ticket_type_id != expected.ticket_type_id
            or plan.ticket_type_name != expected.ticket_type_name
            or plan.require_physical_adjacency != expected.require_physical_adjacency
            or plan.selected_seat_fingerprints != fingerprints
            or (expected.action == "ready" and plan != expected)
        ):
            return None
        return plan

    async def advance_seat(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if before is None or current is None or settings is None:
            return SelectionProgress.BLOCKED
        if isinstance(current.ready_selection, SeatActionPlan):
            readback = await self._flow_browser().observe_seat(handle.tab)
            current = self._current(handle)
            if (
                current is not None and isinstance(current.ready_selection, SeatActionPlan)
                and current.ready_context == before
                and self._observation_valid(handle, before, readback, SeatSnapshot)
                and self._seat_ready_plan(
                    cast(SeatSnapshot, readback.snapshot), current.ready_selection,
                ) is not None
            ):
                return SelectionProgress.SEAT_READY
            if current is not None:
                current.ready_selection = None
                current.ready_context = None
                current.login_verified = None
            return SelectionProgress.AWAITING_EVIDENCE
        prior = current.active
        if prior is None or not self._prior_matches_context(prior, before):
            return SelectionProgress.AWAITING_EVIDENCE
        from_area = (
            prior.stage == "area" and isinstance(prior.plan, AreaActionPlan)
            and prior.plan.route == "0205"
        )
        from_clear = prior.stage == "seat_clear" and isinstance(prior.plan, SeatActionPlan)
        if not from_area and not from_clear:
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_seat(handle.tab)
        if not self._observation_valid(handle, before, observation, SeatSnapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        snapshot = cast(SeatSnapshot, observation.snapshot)
        if not (
            self._seat_destination_matches(prior, snapshot) if from_area
            else self._seat_after_clear_matches(prior, snapshot)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        policy = self._policy.last_valid
        if policy is None:
            return SelectionProgress.BLOCKED
        plan, _reason = select_seat_snapshot(
            snapshot, policy.ticket_type_keyword, settings.ticket_number,
            settings.allow_non_adjacent, policy.seat_sorting,
        )
        if plan is None:
            return SelectionProgress.BLOCKED
        if plan.action == "ready":
            current = self._current(handle)
            if current is None or current.active is not prior or self._flow_context(handle) != before:
                return SelectionProgress.BLOCKED
            current.ready_selection = plan
            current.ready_context = before
            current.login_verified = None
            current.active = None
            return SelectionProgress.SEAT_READY
        if plan.action == "clear" and from_area:
            claim = self._claim_selection(handle, before, "seat_clear", snapshot, plan, prior)
            if claim is None:
                return SelectionProgress.BLOCKED
            dispatched = await self._dispatch_selection(
                handle, claim,
                self._flow_browser().commit_seat_snapshot(snapshot, plan, claim),
            )
            if not dispatched:
                return SelectionProgress.AWAITING_EVIDENCE
            readback = await self._flow_browser().observe_seat(handle.tab)
            current = self._current(handle)
            if (
                not self._observation_valid(handle, before, readback, SeatSnapshot)
                or current is None or current.active is None
                or current.active.claim.claim_id != claim.claim_id
                or not self._seat_after_clear_matches(
                    current.active, cast(SeatSnapshot, readback.snapshot),
                )
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            next_plan, _reason = select_seat_snapshot(
                cast(SeatSnapshot, readback.snapshot), policy.ticket_type_keyword,
                settings.ticket_number, settings.allow_non_adjacent,
                policy.seat_sorting,
            )
            return (
                SelectionProgress.SEAT_CLEARED
                if next_plan is not None and next_plan.action == "select"
                else SelectionProgress.AWAITING_EVIDENCE
            )
        if plan.action != "select":
            return SelectionProgress.BLOCKED
        claim = self._claim_selection(handle, before, "seat", snapshot, plan, prior)
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_seat_snapshot(snapshot, plan, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        readback = await self._flow_browser().observe_seat(handle.tab)
        current = self._current(handle)
        ready_plan = (
            self._seat_ready_plan(cast(SeatSnapshot, readback.snapshot), plan)
            if self._observation_valid(handle, before, readback, SeatSnapshot)
            else None
        )
        if (
            current is None or current.active is None
            or current.active.claim.claim_id != claim.claim_id
            or ready_plan is None
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.ready_selection = ready_plan
        current.ready_context = before
        current.login_verified = None
        current.active = None
        return SelectionProgress.SEAT_READY

    def _purchase_authority(self, tab: TabKey) -> PurchaseAuthority | None:
        port = cast(PurchaseAuthorityPort, self._core)
        method = getattr(port, "current_purchase_authority", None)
        if method is None:
            return None
        value = method(tab)
        return value if isinstance(value, PurchaseAuthority) else None

    def _purchase_browser(self) -> PurchaseBrowserPort:
        return cast(PurchaseBrowserPort, self._browser)

    def _inline_login_credentials(
        self, tab: TabKey, tenant_key: str,
    ) -> InlineLoginCredentials | None:
        port = cast(InlineLoginCredentialPort, self._core)
        method = getattr(port, "current_inline_login_credentials", None)
        if method is None:
            return None
        value = method(tab, tenant_key)
        return value if isinstance(value, InlineLoginCredentials) else None

    async def _autofilled_login_current(
        self, handle: SessionHandle, before: PlanningContext,
        current: _Session, plan: QuantityPlan | SeatActionPlan,
        authority: PurchaseAuthority | None,
    ) -> bool:
        intent = current.login_verified
        if intent is None:
            return True
        profile = get_utiki_tenant_profile(plan.page_url)
        if (
            authority is None or profile is None
            or intent.page_url != plan.page_url
            or intent.document_id != plan.document_id
            or intent.credential_revision != authority.credential_revision
        ):
            return False
        credentials = self._inline_login_credentials(handle.tab, profile.key)
        if credentials is None or credentials.revision != intent.credential_revision:
            return False
        try:
            audit = await self._purchase_browser().audit_inline_login(
                handle.tab, intent, credentials,
            )
        except Exception:
            return False
        return bool(
            isinstance(audit, InlineLoginAudit) and audit.matched
            and audit.tab == handle.tab and audit.page_url == intent.page_url
            and audit.document_id == intent.document_id
            and self._current(handle) is current
            and current.login_verified == intent
            and current.ready_selection == plan and current.ready_context == before
            and current.active is None and self._flow_context(handle) == before
            and self._purchase_authority(handle.tab) == authority
            and self._inline_login_credentials(handle.tab, profile.key) == credentials
        )

    async def advance_login(self, handle: SessionHandle) -> SelectionProgress:
        """Fill missing inline credentials under one tab claim, without exporting values."""
        before = self._flow_context(handle)
        current = self._current(handle)
        if before is None or current is None:
            return SelectionProgress.BLOCKED
        if current.active is not None:
            return SelectionProgress.AWAITING_EVIDENCE
        plan = current.ready_selection
        if plan is None or current.ready_context != before:
            return SelectionProgress.BLOCKED
        selection = (
            await self._purchase_browser().observe_seat(handle.tab)
            if isinstance(plan, SeatActionPlan)
            else await self._purchase_browser().observe_quantity(handle.tab)
        )
        selection_ready = (
            self._observation_valid(handle, before, selection, SeatSnapshot)
            and self._seat_ready_plan(cast(SeatSnapshot, selection.snapshot), plan) is not None
            if isinstance(plan, SeatActionPlan) else (
                self._observation_valid(handle, before, selection, QuantitySnapshot)
                and self._quantity_is_ready(cast(QuantitySnapshot, selection.snapshot), plan)
            )
        )
        if not selection_ready:
            return SelectionProgress.BLOCKED
        inputs = await self._purchase_browser().observe_purchase_input(handle.tab)
        if (
            not isinstance(inputs, PurchaseInputObservation)
            or inputs.tab != handle.tab or inputs.page_url != plan.page_url
            or inputs.document_id != plan.document_id or inputs.dialog_visible
            or self._flow_context(handle) != before
            or self._current(handle) is not current or current.active is not None
            or current.ready_selection != plan or current.ready_context != before
        ):
            return SelectionProgress.BLOCKED
        if not inputs.login_required or inputs.login_ready:
            authority = self._purchase_authority(handle.tab)
            return (
                SelectionProgress.LOGIN_READY
                if await self._autofilled_login_current(
                    handle, before, current, plan, authority,
                ) else SelectionProgress.BLOCKED
            )
        if not inputs.login_fields_usable:
            return SelectionProgress.BLOCKED
        profile = get_utiki_tenant_profile(plan.page_url)
        auth_profile = get_tenant_auth_profile(plan.page_url)
        authority = self._purchase_authority(handle.tab)
        if (
            profile is None or auth_profile is None
            or profile.key != auth_profile.tenant_key
            or not auth_profile.supports_inline_login
            or authority is None
        ):
            return SelectionProgress.BLOCKED
        credentials = self._inline_login_credentials(handle.tab, profile.key)
        if credentials is None or credentials.revision != authority.credential_revision:
            return SelectionProgress.BLOCKED
        intent = InlineLoginIntent(
            plan.page_url, plan.document_id, auth_profile.inline_login_origin,
            credentials.revision, not inputs.login_account_present,
            not inputs.login_password_present,
        )
        current = self._current(handle)
        if (
            current is None or current.active is not None
            or current.ready_selection != plan or current.ready_context != before
            or self._flow_context(handle) != before
            or self._purchase_authority(handle.tab) != authority
            or self._inline_login_credentials(handle.tab, profile.key) != credentials
        ):
            return SelectionProgress.BLOCKED
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            DocumentKey(plan.document_id), before.policy_revision,
            before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(stamp, EffectKind.INPUT_WRITE)
        )
        current.active = _ActiveSelection("login", claim, plan.page_url, intent)
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._purchase_browser().commit_inline_login(intent, credentials, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        try:
            audit = await self._purchase_browser().audit_inline_login(
                handle.tab, intent, credentials,
            )
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        current = self._current(handle)
        if (
            not isinstance(audit, InlineLoginAudit)
            or audit.tab != handle.tab or audit.page_url != intent.page_url
            or audit.document_id != intent.document_id or not audit.matched
            or current is None or current.active is None
            or current.active.claim.claim_id != claim.claim_id
            or current.ready_selection != plan or current.ready_context != before
            or self._flow_context(handle) != before
            or self._purchase_authority(handle.tab) != authority
            or self._inline_login_credentials(handle.tab, profile.key) != credentials
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.active = None
        current.login_verified = intent
        return SelectionProgress.LOGIN_READY

    def _submit_pending(
        self, handle: SessionHandle, current: _Session, active: _ActiveSelection,
    ) -> SelectionProgress:
        if (
            current.submit_dispatched_at is not None
            and time.monotonic() - current.submit_dispatched_at >= 10
        ):
            # A still-running browser call can later report NO_EFFECT. Keep its
            # original running claim until that receipt arrives.
            if isinstance(active.claim, AwaitingEvidenceEffect):
                self._hold_claim(handle, active.claim, HeldReason.UNCERTAIN_DISPATCH)
            return SelectionProgress.SUBMIT_BLOCKED
        return SelectionProgress.AWAITING_EVIDENCE

    async def _reconcile_submit(
        self, handle: SessionHandle, before: PlanningContext
    ) -> SelectionProgress:
        current = self._current(handle)
        if current is None or current.active is None or current.active.stage != "submit":
            return SelectionProgress.BLOCKED
        if current.submit_terminal is not None:
            return current.submit_terminal
        active = current.active
        stamp = active.claim.proposal.stamp
        if (
            stamp.session_generation, stamp.policy_revision, stamp.control_revision
        ) != (
            before.session_generation, before.policy_revision, before.control_revision
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        if isinstance(active.claim, RunningEffect):
            return self._submit_pending(handle, current, active)
        if isinstance(active.claim, HeldEffect) and active.claim.reason is not HeldReason.UNCERTAIN_DISPATCH:
            return SelectionProgress.SUBMIT_BLOCKED
        if not current.submit_request_boundary:
            return self._submit_pending(handle, current, active)
        try:
            observation = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return self._submit_pending(handle, current, active)
        if (
            self._flow_context(handle) != before
            or not isinstance(observation, SubmitOutcomeObservation)
            or observation.tab != handle.tab
            or self._current(handle) is not current
            or current.active is not active
        ):
            return self._submit_pending(handle, current, active)
        source_profile = get_utiki_tenant_profile(active.source_url)
        destination_profile = get_utiki_tenant_profile(observation.page_url)
        try:
            same_site = (
                source_profile is not None and destination_profile is not None
                and source_profile.key == destination_profile.key
                and same_origin(urlsplit(active.source_url), urlsplit(observation.page_url))
            )
        except ValueError:
            same_site = False
        if not same_site:
            return self._submit_pending(handle, current, active)
        dialog = observation.dialog
        if dialog.visible_count:
            if (
                observation.page_url != active.source_url
                or observation.document_id != active.claim.proposal.stamp.document.token
            ):
                return self._submit_pending(handle, current, active)
            outcome = classify_submit_dialog(dialog.text) if dialog.visible_count == 1 else None
            if outcome in {"captcha", "auth", "seat_conflict"}:
                current.rejection_kind = outcome
                current.rejection_dialog = dialog
            current.submit_terminal = (
                SelectionProgress.SUBMIT_CONFIRMED if outcome == "success"
                else SelectionProgress.SUBMIT_BLOCKED
            )
            return current.submit_terminal
        if (
            observation.document_id != active.claim.proposal.stamp.document.token
            and classify_utiki_page(observation.page_url) is PageKind.CHECKOUT
        ):
            current.submit_terminal = SelectionProgress.SUBMIT_CONFIRMED
            return current.submit_terminal
        return self._submit_pending(handle, current, active)

    def _recovery_source_current(
        self, handle: SessionHandle, before: PlanningContext,
        current: _Session, active: _ActiveSelection,
    ) -> bool:
        attempt = current.submit_attempt
        dialog = current.rejection_dialog
        if attempt is None or dialog is None:
            return False
        stamp = attempt.stamp
        authority = self._purchase_authority(handle.tab)
        if (
            (stamp.session_generation, stamp.policy_revision, stamp.control_revision)
            != (before.session_generation, before.policy_revision, before.control_revision)
            or stamp.tab != handle.tab
            or stamp.document.token != dialog.document_id
            or attempt.plan.page_url != dialog.page_url
            or authority is None
            or authority.auth_epoch != attempt.key.auth_epoch
            or authority.credential_revision
            != attempt.input_evidence.context.credential_revision
            or active.source_url != attempt.plan.page_url
        ):
            return False
        proposal = active.claim.proposal
        if active.stage == "submit":
            return proposal.kind is EffectKind.SUBMIT and proposal.stamp == stamp
        return (
            active.stage in {"dialog_dismiss", "captcha_reset"}
            and proposal.kind is (
                EffectKind.DISMISS_DIALOG if active.stage == "dialog_dismiss"
                else EffectKind.CAPTCHA_RESET
            )
            and proposal.parent_recovery_id == attempt.attempt_id.token
            and proposal.stamp.tab == stamp.tab
            and proposal.stamp.document == stamp.document
            and (
                proposal.stamp.session_generation,
                proposal.stamp.policy_revision,
                proposal.stamp.control_revision,
            ) == (
                stamp.session_generation, stamp.policy_revision,
                stamp.control_revision,
            )
            and (
                active.stage != "captcha_reset" or (
                    current.captcha_reset is not None
                    and active.plan == current.captcha_reset
                    and current.captcha_reset.context == attempt.input_evidence.context
                )
            )
        )

    async def _observe_dialog_closed(
        self, handle: SessionHandle, before: PlanningContext,
        current: _Session, active: _ActiveSelection,
    ) -> SelectionProgress:
        expected = current.rejection_dialog
        if (
            expected is None or not isinstance(active.claim, AwaitingEvidenceEffect)
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        try:
            observation = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observation, SubmitOutcomeObservation)
            or observation.tab != handle.tab
            or observation.page_url != expected.page_url
            or observation.document_id != expected.document_id
            or observation.dialog.visible_count != 0
            or self._flow_context(handle) != before
            or self._current(handle) is not current
            or current.active is not active
            or current.rejection_dialog != expected
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.dialog_dismissed = True
        return SelectionProgress.DIALOG_DISMISSED

    async def advance_dialog_dismissal(self, handle: SessionHandle) -> SelectionProgress:
        """Close one known rejection while retaining its original submit blocker."""
        before = self._flow_context(handle)
        current = self._current(handle)
        if (
            before is None or current is None
            or current.submit_terminal is not SelectionProgress.SUBMIT_BLOCKED
            or current.rejection_kind != "captcha"
            or current.rejection_dialog is None
            or current.submit_attempt is None
            or not current.submit_request_boundary
            or current.active is None
        ):
            return SelectionProgress.BLOCKED
        active = current.active
        if not self._recovery_source_current(handle, before, current, active):
            return SelectionProgress.BLOCKED
        if active.stage == "dialog_dismiss":
            if current.dialog_dismissed:
                return SelectionProgress.DIALOG_DISMISSED
            return await self._observe_dialog_closed(handle, before, current, active)
        if active.stage != "submit" or isinstance(active.claim, RunningEffect):
            return SelectionProgress.AWAITING_EVIDENCE
        expected = current.rejection_dialog
        try:
            observation = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observation, SubmitOutcomeObservation)
            or observation.tab != handle.tab
            or observation.page_url != expected.page_url
            or observation.document_id != expected.document_id
            or observation.dialog != expected
            or expected.visible_count != 1
            or self._flow_context(handle) != before
            or self._current(handle) is not current
            or current.active is not active
            or current.rejection_dialog != expected
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            DocumentKey(expected.document_id), before.policy_revision,
            before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(
                stamp, EffectKind.DISMISS_DIALOG,
                parent_recovery_id=current.submit_attempt.attempt_id.token,
            )
        )
        current.active = _ActiveSelection(
            "dialog_dismiss", claim, expected.page_url, expected,
        )
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._purchase_browser().dismiss_submit_dialog(expected, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        active = current.active
        if active is None or active.claim.claim_id != claim.claim_id:
            return SelectionProgress.AWAITING_EVIDENCE
        return await self._observe_dialog_closed(handle, before, current, active)

    async def _observe_captcha_refreshed(
        self, handle: SessionHandle, before: PlanningContext,
        current: _Session, active: _ActiveSelection,
    ) -> SelectionProgress:
        reset = current.captcha_reset
        if (
            reset is None or not isinstance(active.claim, AwaitingEvidenceEffect)
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        if current.captcha_reset_verified:
            return SelectionProgress.CAPTCHA_RESET_VERIFIED
        if time.monotonic() >= reset.deadline_monotonic:
            self._hold_claim(handle, active.claim, HeldReason.UNCERTAIN_DISPATCH)
            return SelectionProgress.SUBMIT_BLOCKED
        try:
            observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if time.monotonic() >= reset.deadline_monotonic:
            self._hold_claim(handle, active.claim, HeldReason.UNCERTAIN_DISPATCH)
            return SelectionProgress.SUBMIT_BLOCKED
        if (
            not isinstance(observed, CaptchaResetObservation)
            or observed.tab != handle.tab
            or observed.page_url != active.source_url
            or observed.document_id != reset.context.document.token
            or observed.image is None
            or observed.image.load_revision == reset.before_image.load_revision
            or not observed.field_usable or not observed.field_empty
            or not observed.refresh_usable or observed.dialog_visible
            or self._flow_context(handle) != before
            or self._current(handle) is not current
            or current.active is not active
            or current.ready_context != before
            or current.ready_selection is None
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.captcha_reset_verified = True
        return SelectionProgress.CAPTCHA_RESET_VERIFIED

    async def advance_captcha_reset(self, handle: SessionHandle) -> SelectionProgress:
        """Refresh one rejected challenge, then wait for a distinct ready image."""
        before = self._flow_context(handle)
        current = self._current(handle)
        if (
            before is None or current is None
            or current.submit_terminal is not SelectionProgress.SUBMIT_BLOCKED
            or current.rejection_kind != "captcha"
            or not current.dialog_dismissed
            or current.submit_attempt is None
            or not current.submit_request_boundary
            or current.active is None
        ):
            return SelectionProgress.BLOCKED
        active = current.active
        if not self._recovery_source_current(handle, before, current, active):
            return SelectionProgress.BLOCKED
        if active.stage == "captcha_reset":
            return await self._observe_captcha_refreshed(handle, before, current, active)
        if active.stage != "dialog_dismiss" or not isinstance(
            active.claim, AwaitingEvidenceEffect
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        try:
            observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observed, CaptchaResetObservation)
            or observed.tab != handle.tab
            or observed.page_url != active.source_url
            or observed.document_id != current.submit_attempt.stamp.document.token
            or observed.image is None
            or not observed.field_usable or not observed.refresh_usable
            or observed.dialog_visible
            or self._flow_context(handle) != before
            or self._current(handle) is not current
            or current.active is not active
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        reset = CaptchaResetAttempt(
            ResetId(uuid.uuid4().hex), current.submit_attempt.input_evidence.context,
            observed.image, ResetReason.KNOWN_REJECTION,
            time.monotonic() + 30,
        )
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            DocumentKey(observed.document_id), before.policy_revision,
            before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(
                stamp, EffectKind.CAPTCHA_RESET,
                parent_recovery_id=current.submit_attempt.attempt_id.token,
            )
        )
        current.captcha_reset = reset
        current.active = _ActiveSelection("captcha_reset", claim, observed.page_url, reset)
        dispatched = await self._dispatch_selection(
            handle, claim, self._purchase_browser().commit_captcha_reset(observed, claim),
        )
        if not dispatched:
            return SelectionProgress.SUBMIT_BLOCKED
        active = current.active
        if active is None or active.claim.claim_id != claim.claim_id:
            return SelectionProgress.SUBMIT_BLOCKED
        return await self._observe_captcha_refreshed(handle, before, current, active)

    async def advance_submit(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if before is None or current is None or settings is None or not settings.auto_submit:
            return SelectionProgress.BLOCKED
        if current.active is not None:
            return (
                await self._reconcile_submit(handle, before)
                if current.active.stage == "submit" else (
                    current.submit_terminal or SelectionProgress.AWAITING_EVIDENCE
                )
            )
        if current.submit_terminal is not None:
            return current.submit_terminal
        plan = current.ready_selection
        if plan is None or current.ready_context != before:
            return SelectionProgress.BLOCKED
        authority = self._purchase_authority(handle.tab)
        profile = get_utiki_tenant_profile(plan.page_url)
        if authority is None or profile is None:
            return SelectionProgress.BLOCKED
        selection = (
            await self._purchase_browser().observe_seat(handle.tab)
            if isinstance(plan, SeatActionPlan)
            else await self._purchase_browser().observe_quantity(handle.tab)
        )
        inputs = await self._purchase_browser().observe_purchase_input(handle.tab)
        selection_ready = (
            self._observation_valid(handle, before, selection, SeatSnapshot)
            and self._seat_ready_plan(cast(SeatSnapshot, selection.snapshot), plan) is not None
            if isinstance(plan, SeatActionPlan) else (
                self._observation_valid(handle, before, selection, QuantitySnapshot)
                and self._quantity_is_ready(cast(QuantitySnapshot, selection.snapshot), plan)
            )
        )
        if (
            not selection_ready
            or not isinstance(inputs, PurchaseInputObservation)
            or inputs.tab != handle.tab or inputs.page_url != plan.page_url
            or inputs.document_id != plan.document_id
            or not (
                inputs.captcha_present and inputs.captcha_unique
                and inputs.captcha_enabled and inputs.manual_value_ready
                and inputs.login_ready and inputs.button_ready
            )
            or inputs.dialog_visible
        ):
            return SelectionProgress.BLOCKED
        if not await self._autofilled_login_current(
            handle, before, current, plan, authority,
        ):
            return SelectionProgress.BLOCKED
        login_intent = current.login_verified
        login_credentials = (
            self._inline_login_credentials(handle.tab, profile.key)
            if login_intent is not None else None
        )
        if login_intent is not None and (
            login_credentials is None
            or login_credentials.revision != login_intent.credential_revision
        ):
            return SelectionProgress.BLOCKED
        submit_plan = build_submit_plan(plan)
        if submit_plan is None:
            return SelectionProgress.BLOCKED
        context = InputContext(
            handle.tab, DocumentKey(plan.document_id), PurchaseCycleId(uuid.uuid4().hex),
            1, authority.credential_revision, 1, InputPolicy.MANUAL_ONLY, True,
        )
        evidence = InputReadyEvidence(context, InputSource.MANUAL, None, None, True)
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            context.document, before.policy_revision, before.control_revision,
        )
        try:
            key = PurchaseKey(
                handle.tab, TenantKey(profile.key), authority.auth_epoch,
                plan.performance_id, plan.group_id, plan.price_area_id,
                PageKind.RESERVED_SEAT if isinstance(plan, SeatActionPlan)
                else PageKind.GENERAL_ADMISSION,
                plan.product_id,
            )
            attempt = SubmitAttempt(
                SubmitAttemptId(uuid.uuid4().hex), context.cycle, key, stamp,
                submit_plan, evidence,
            )
        except ValueError:
            return SelectionProgress.BLOCKED
        current = self._current(handle)
        if (
            current is None or self._flow_context(handle) != before
            or current.active is not None or current.ready_selection != plan
            or current.ready_context != before
            or current.login_verified != login_intent
            or self._purchase_authority(handle.tab) != authority
            or (
                login_intent is not None
                and self._inline_login_credentials(handle.tab, profile.key)
                != login_credentials
            )
        ):
            return SelectionProgress.BLOCKED
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(stamp, EffectKind.SUBMIT)
        )
        current.active = _ActiveSelection("submit", claim, plan.page_url, submit_plan)
        current.submit_attempt = attempt
        current.submit_dispatched_at = time.monotonic()
        dispatched = await self._dispatch_selection(
            handle, claim, self._purchase_browser().dispatch_submit(
                submit_plan, claim, login_intent, login_credentials,
            )
        )
        current = self._current(handle)
        if (
            dispatched and current is not None and current.active is not None
            and current.active.claim.claim_id == claim.claim_id
        ):
            current.submit_request_boundary = True
        return (
            SelectionProgress.SUBMIT_DISPATCHED if dispatched
            else SelectionProgress.SUBMIT_BLOCKED
        )
