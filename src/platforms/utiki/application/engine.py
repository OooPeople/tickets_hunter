"""One uTiki planning owner for the current core control interface."""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Awaitable, cast
from urllib.parse import urlsplit

from utiki_config import UtikiBehavior

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
    BlockedInput, CaptchaAttempt, CaptchaImageKey, CaptchaResetAttempt,
    ConsumedInput,
    InputAttemptId, InputBlockReason, InputContext, InputPolicy,
    InputReadyEvidence, InputSource, InputState, PurchaseCycleId,
    ReadyInput, RecognizingInput, ResetId, ResetReason, WaitingManualInput,
    WritingInput,
)
from ..domain.plans import AreaActionPlan, DateActionPlan, QuantityPlan, SeatActionPlan, SubmitPlan, build_submit_plan
from ..domain.purchase import ManualSubmitEvidence, SubmitAttempt, SubmitAttemptId, SubmitSource
from ..domain.snapshots import (
    AreaSnapshot, DateSnapshot, DialogSnapshot, PresentField, QuantitySnapshot, SeatSnapshot,
    parse_nonnegative_count,
)
from ..profiles import PageKind, classify_utiki_page, get_utiki_tenant_profile, same_origin, unique_query
from ..selection import (
    classify_submit_dialog, quantity_audit_is_valid, select_area_snapshot, select_date_snapshot,
    select_quantity_snapshot, select_seat_snapshot,
)
from .flow_settings import EffectivePolicy, FlowSettings
from .ports import (
    CaptchaProviderPort, CaptchaResetObservation, CheckoutNotificationPort, CoreControl, CoreControlPort, CoreGate, InlineLoginAudit,
    InlineLoginCredentials, InlineLoginCredentialPort, InlineLoginIntent,
    PageObservation, PurchaseAuthority, PurchaseAuthorityPort, PurchaseBrowserPort,
    PurchaseInputObservation, RejectedLoginObservation,
    SelectionBrowserPort,
    SubmitOutcomeObservation, VerifiedLoginAcceptance,
)
from .refresh_contract import (
    RefreshBrowserPort, RefreshCoordinatorPort, RefreshDocument, RefreshIntent,
    RefreshPhase, RefreshProgress, RefreshReason,
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
    QUOTA_BLOCKED = "quota-blocked"
    DIALOG_DISMISSED = "dialog-dismissed"
    CAPTCHA_RESET_VERIFIED = "captcha-reset-verified"
    INPUT_READY = "input-ready"
    LOGIN_RECOVERY_READY = "login-recovery-ready"
    CONFLICT_CLEARED = "conflict-cleared"
    CONFLICT_RECOVERED = "conflict-recovered"
    MANUAL_ACTION_REQUIRED = "manual-action-required"


@dataclass(slots=True)
class _ActiveSelection:
    stage: str
    claim: ActiveEffect
    source_url: str
    plan: DateActionPlan | AreaActionPlan | QuantityPlan | SeatActionPlan | SubmitPlan | InlineLoginIntent | DialogSnapshot | CaptchaResetAttempt | CaptchaAttempt
    login_credentials: InlineLoginCredentials | None = field(default=None, repr=False)
    login_authority: PurchaseAuthority | None = None
    receipt: EffectReceipt | None = None
    receipt_conflict: bool = False
    manual_revision_at_claim: int = 0
    seat_behavior_at_claim: UtikiBehavior | None = None


@dataclass(frozen=True, slots=True)
class _ManualLoginEvidence:
    intent: InlineLoginIntent
    credentials: InlineLoginCredentials = field(repr=False)
    authority: PurchaseAuthority


@dataclass(frozen=True, slots=True)
class _RejectedManualLoginEvidence:
    attempt_id: SubmitAttemptId
    page_url: str
    document_id: str
    token: str


@dataclass(frozen=True, slots=True)
class _AcceptedManualLoginEvidence:
    rejection: VerifiedLoginAcceptance


@dataclass(slots=True)
class _ManualPreparation:
    plan: SubmitPlan
    input_evidence: InputReadyEvidence
    key: PurchaseKey
    arm_token: str
    context: PlanningContext
    behavior_at_arm: UtikiBehavior | None
    flow_at_arm: FlowSettings | None


@dataclass(frozen=True, slots=True)
class _Readiness:
    plan: QuantityPlan | SeatActionPlan
    context: PlanningContext


@dataclass(slots=True)
class _Submission:
    attempt: SubmitAttempt
    original: _ActiveSelection | None
    dispatched_at: float | None
    behavior_at_attempt: UtikiBehavior | None = None
    flow_at_attempt: FlowSettings | None = None
    terminal: SelectionProgress | None = None
    rejection_kind: str | None = None
    rejection_dialog: DialogSnapshot | None = None
    dialog_dismissed: bool = False
    captcha_reset: CaptchaResetAttempt | None = None
    captcha_reset_verified: bool = False
    verified_image: CaptchaImageKey | None = None
    login_observation_token: str = ""
    conflict_clear: _ActiveSelection | None = None
    conflict_cleared: bool = False
    notification_attempted: bool = False
    notification_start_failed: bool = False

    @property
    def request_boundary(self) -> bool:
        if self.attempt.source is SubmitSource.OBSERVED_MANUAL:
            return self.attempt.manual_evidence is not None
        receipt = self.original.receipt if self.original is not None else None
        return bool(receipt is not None and receipt.evidence in {
            EffectEvidence.REQUEST_DISPATCHED, EffectEvidence.REQUEST_POSSIBLE,
        })


@dataclass(slots=True)
class _Session:
    handle: SessionHandle
    control: CoreControl
    control_revision: int
    refresh_instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    closing: bool = False
    active: _ActiveSelection | None = None
    # A completed navigation remains source evidence after the next operation
    # is claimed. It never grants a second mutation by itself.
    selection_source: _ActiveSelection | None = None
    verified_quantity: QuantityPlan | None = None
    readiness: _Readiness | None = None
    login_verified: InlineLoginIntent | _ManualLoginEvidence | _RejectedManualLoginEvidence | _AcceptedManualLoginEvidence | None = None
    manual_preparation: _ManualPreparation | None = None
    submission: _Submission | None = None
    prior_submissions: list[_Submission] = field(default_factory=list)
    cycle: PurchaseCycleId = field(default_factory=lambda: PurchaseCycleId(uuid.uuid4().hex))
    input_revision: int = 1
    input_state: InputState | None = None
    input_image: CaptchaImageKey | None = None
    ocr_attempt: CaptchaAttempt | None = None
    input_observed: CaptchaResetObservation | None = None
    input_answer: str | None = field(default=None, repr=False)
    required_manual_revision: int | None = None
    refresh: RefreshIntent | None = None
    health: RefreshDocument | None = None
    reported_429: str = ""
    scheduled_armed: bool = True

    @property
    def ready_selection(self) -> QuantityPlan | SeatActionPlan | None:
        return self.readiness.plan if self.readiness is not None else None

    @property
    def ready_context(self) -> PlanningContext | None:
        return self.readiness.context if self.readiness is not None else None


@dataclass(slots=True)
class _RetiredOutcome:
    """Only the original outstanding claim and submit facts survive Run close."""

    handle: SessionHandle
    active: _ActiveSelection | None
    submission: _Submission | None
    manual_preparation: _ManualPreparation | None
    refresh: RefreshIntent | None = None


class UtikiEngine:
    """Own attached tabs; only return read-only plans while all gates agree."""

    def __init__(
        self,
        policy: EffectivePolicy,
        browser: SelectionBrowserPort,
        core: CoreControlPort,
        captcha_provider: CaptchaProviderPort | None = None,
        refresh_coordinator: RefreshCoordinatorPort | None = None,
        checkout_notification: CheckoutNotificationPort | None = None,
    ) -> None:
        if not isinstance(policy, EffectivePolicy):
            raise ValueError("engine requires validated effective policy state")
        self._policy = policy
        self._browser = browser
        self._core = core
        self._captcha_provider = captcha_provider
        self._refresh_coordinator = refresh_coordinator
        self._checkout_notification = checkout_notification
        self._refresh_busy = False
        if refresh_coordinator is not None:
            for name in ("observe_refresh_document", "dispatch_reload"):
                if not callable(getattr(browser, name, None)):
                    raise ValueError(f"uTiki refresh browser capability missing: {name}")
            if not callable(getattr(core, "current_health_clearance", None)):
                raise ValueError("uTiki host lacks document health clearance")
        if captcha_provider is not None and not callable(
            getattr(captcha_provider, "classification", None)
        ):
            raise ValueError("uTiki CAPTCHA provider lacks classification")
        self._provider_settings = (
            self._ocr_settings(policy.flow) if policy.flow is not None else None
        )
        self._ocr_enabled_at_start = bool(
            policy.flow is not None and policy.flow.ocr_enabled
        )
        self._validate_browser_capabilities(policy)
        self._sessions: dict[TabKey, _Session] = {}
        self._retired_runs: dict[TabKey, _RetiredOutcome] = {}
        self._next_generation = 0
        self._policy_conflict = False

    @property
    def _flow_settings(self) -> FlowSettings | None:
        return self._policy.flow

    @staticmethod
    def _ocr_settings(flow: FlowSettings) -> tuple[bool, str, str]:
        custom = flow.ocr_use_universal and flow.ocr_path not in (
            "", "assets/model/universal",
        )
        return (
            False if custom else flow.ocr_beta,
            "custom" if custom else "official",
            flow.ocr_path if custom else "",
        )

    def _validate_browser_capabilities(
        self, policy: EffectivePolicy, *, require_purchase: bool = False,
    ) -> None:
        flow = policy.flow
        if flow is None or not require_purchase and not (flow.date_enabled or flow.area_enabled):
            return
        required: tuple[str, ...] = (
            "observe_date", "observe_area", "observe_quantity",
            "commit_date", "commit_area", "commit_quantity_snapshot",
            "observe_purchase_input", "commit_inline_login", "audit_inline_login",
            "arm_manual_submit", "disarm_manual_submit",
            "dispatch_submit", "observe_submit_outcome", "dismiss_submit_dialog",
            "observe_captcha_reset", "commit_captcha_reset",
        )
        if (
            policy.last_valid is not None
            and policy.last_valid.seat_selection_mode == "computer"
        ):
            required += ("commit_server_assigned_area",)
        if self._captcha_provider is not None:
            required += ("commit_captcha_input", "audit_captcha_input")
        missing = [name for name in required if not callable(getattr(self._browser, name, None))]
        if missing:
            raise ValueError(f"uTiki browser capability missing: {missing[0]}")
        host_required = (
            "current_control", "current_purchase_authority",
            "current_inline_login_credentials",
        )
        host_missing = [
            name for name in host_required
            if not callable(getattr(self._core, name, None))
        ]
        if host_missing:
            raise ValueError(f"uTiki host capability missing: {host_missing[0]}")

    def ensure_purchase_capabilities(self, policy: EffectivePolicy) -> None:
        """Require every capability reachable from the formal purchase owner."""
        self._validate_browser_capabilities(policy, require_purchase=True)

    def _host_control(self, tab: TabKey) -> CoreControl:
        value = self._core.current_control(tab)
        if not isinstance(value, CoreControl):
            raise ValueError("core control source returned an invalid gate")
        return value

    def attach(self, tab: TabKey) -> SessionHandle:
        if not isinstance(tab, TabKey):
            raise ValueError("attach requires a typed tab")
        bound_tab = getattr(self._browser, "bound_tab", tab)
        if bound_tab != tab:
            raise ValueError("browser adapter is bound to another tab")
        if self._sessions and tab not in self._sessions:
            raise ValueError("a uTiki run has one active tab")
        if tab in self._retired_runs:
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

    def input_state(self, handle: SessionHandle) -> InputState | None:
        current = self._current(handle)
        return current.input_state if current is not None else None

    def submission_state(
        self, handle: SessionHandle,
    ) -> tuple[SelectionProgress | None, str | None]:
        """Expose only the attributed outcome needed by the formal host."""
        current = self._current(handle)
        retired = self._retired_runs.get(handle.tab) if current is None else None
        submission = (
            current.submission if current is not None else
            retired.submission if retired is not None and retired.handle == handle else None
        )
        return (
            (submission.terminal, submission.rejection_kind)
            if submission is not None else (None, None)
        )

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

    def update_policy(self, policy: EffectivePolicy) -> bool:
        if not isinstance(policy, EffectivePolicy):
            self._policy_conflict = True
            return False
        self._validate_browser_capabilities(policy)
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
        if current.active is not None and current.active.stage == "login":
            current.active.login_credentials = None
            current.active.login_authority = None
        if (
            current.active is not None or current.submission is not None
            or current.manual_preparation is not None or current.refresh is not None
        ):
            self._retired_runs[handle.tab] = _RetiredOutcome(
                current.handle, current.active, current.submission,
                current.manual_preparation, current.refresh,
            )
        self._sessions.pop(handle.tab, None)
        return True

    def retired_outcome_pending(self, handle: SessionHandle) -> bool:
        """A retired original still owns evidence or a coordinator token."""
        retired = self._retired_runs.get(handle.tab)
        return retired is not None and retired.handle == handle

    def _finish_retired_active(
        self, handle: SessionHandle, retired: _RetiredOutcome,
        *, finish_submission: bool = False,
    ) -> None:
        if self._retired_runs.get(handle.tab) is not retired or retired.handle != handle:
            return
        retired.active = None
        if finish_submission:
            retired.submission = None
        if (
            retired.submission is None and retired.manual_preparation is None
            and retired.refresh is None
        ):
            self._retired_runs.pop(handle.tab, None)

    def context(self, handle: SessionHandle) -> PlanningContext | None:
        current = self._current(handle)
        if (
            current is None or current.closing or self._policy_conflict
            or current.submission is not None and current.submission.terminal in {
                SelectionProgress.SUBMIT_CONFIRMED, SelectionProgress.QUOTA_BLOCKED,
            }
            or not self._policy.allows_new_mutation
            or current.control.gate is not CoreGate.PURCHASE_ALLOWED
            or self._host_control(handle.tab) != current.control
        ):
            return None
        if self._refresh_coordinator is not None:
            if current.refresh is not None and current.refresh.phase not in {
                RefreshPhase.COMPLETE, RefreshPhase.ABANDONED,
            }:
                return None
            observed = current.health
            clearance = getattr(self._core, "current_health_clearance")(handle.tab)
            if (
                observed is None or observed.classification != "normal"
                or clearance != (observed.url, observed.document_id)
            ):
                return None
        return PlanningContext(
            handle.generation, self._policy.policy_revision, current.control_revision
        )

    def _flow_context(self, handle: SessionHandle) -> PlanningContext | None:
        context = self.context(handle)
        if (
            context is None or self._flow_settings is None
            or self._policy.last_valid is None
        ):
            return None
        return context

    def _computer_mode(self) -> bool:
        behavior = self._policy.last_valid
        return bool(behavior is not None and behavior.seat_selection_mode == "computer")

    @staticmethod
    def _server_assignment_supported(page_url: str) -> bool:
        profile = get_utiki_tenant_profile(page_url)
        return bool(profile is not None and profile.supports_server_assignment)

    def _refresh_browser(self) -> RefreshBrowserPort:
        return cast(RefreshBrowserPort, self._browser)

    async def observe_health(self, handle: SessionHandle) -> RefreshDocument | None:
        """Read document health before schedule gating; report only a proven 429."""
        coordinator = self._refresh_coordinator
        current = self._current(handle)
        if coordinator is None or current is None:
            return None
        try:
            observed = await self._refresh_browser().observe_refresh_document(handle.tab)
        except Exception:
            return None
        if self._current(handle) is not current or observed.tab != handle.tab:
            return None
        prior = current.health
        if prior is None or (prior.url, prior.document_id) != (
            observed.url, observed.document_id
        ):
            current.readiness = None
            current.login_verified = None
        current.health = observed
        if observed.classification == "blocked" and current.reported_429 != observed.document_id:
            try:
                settings = coordinator.settings()
                if settings.enabled:
                    refresh = current.refresh
                    probe = (
                        refresh.permit_id if refresh is not None and refresh.is_probe
                        and refresh.phase in {
                            RefreshPhase.RELOAD_POSSIBLE,
                            RefreshPhase.AWAITING_CLEARANCE,
                        } else None
                    )
                    report = await coordinator.report_429(
                        current.refresh_instance_id, observed.started_at,
                        settings.initial_cooldown_seconds,
                        settings.max_cooldown_seconds, probe,
                    )
                    if report.accepted and self._current(handle) is current:
                        current.reported_429 = observed.document_id
            except Exception:
                pass  # A database failure cannot become a local refresh permit.
        return observed

    def _refresh_purchase_blocked(self, current: _Session) -> bool:
        if current.active is not None or current.manual_preparation is not None:
            return True
        if current.submission is not None:
            submission = current.submission
            return not (
                self._known_conflict_refresh_ready(current)
                and current.refresh is not None
                and current.refresh.reason is RefreshReason.KNOWN_REJECTION
                and current.refresh.source_url == submission.attempt.plan.page_url
                and current.refresh.source_document == submission.attempt.plan.document_id
            )
        readiness = current.readiness
        return bool(
            readiness is not None
            and readiness.context.session_generation == current.handle.generation
            and readiness.context.policy_revision == self._policy.policy_revision
            and readiness.context.control_revision == current.control_revision
        )

    def _refresh_preflight(
        self, handle: SessionHandle, current: _Session, intent: RefreshIntent,
    ) -> bool:
        health = current.health
        control = self._host_control(handle.tab)
        clearance = getattr(self._core, "current_health_clearance")(handle.tab)
        return bool(
            self._current(handle) is current and current.refresh is intent
            and not current.closing and not self._policy_conflict
            and self._policy.allows_new_mutation
            and self._policy.policy_revision == intent.policy_revision
            and current.control_revision == intent.control_revision
            and control == current.control
            and control.gate not in {CoreGate.PAUSED, CoreGate.CLOSING}
            and health is not None
            and (health.url, health.document_id)
            == (intent.source_url, intent.source_document)
            and (
                intent.reason is RefreshReason.RATE_LIMIT_RECOVERY
                and health.classification == "blocked"
                or health.classification == "normal"
                and clearance == (health.url, health.document_id)
            )
            and not self._refresh_purchase_blocked(current)
        )

    async def _refresh_cleanup(
        self, current: _Session | None, intent: RefreshIntent,
    ) -> RefreshProgress:
        coordinator = self._refresh_coordinator
        assert coordinator is not None
        intent.phase = RefreshPhase.CLEANUP_PENDING
        if intent.cleanup_attempts >= 3:
            intent.phase = RefreshPhase.RECONCILIATION_REQUIRED
            return RefreshProgress.MANUAL_RECONCILIATION
        if time.monotonic() < intent.next_retry_at:
            return RefreshProgress.CLEANUP_PENDING
        try:
            cleaned = (
                await coordinator.complete_probe(
                    intent.instance_id, intent.permit_id, False,
                ) if intent.cleanup_probe else
                await coordinator.release(
                    intent.instance_id, intent.reservation_id, intent.token,
                ) if intent.reservation_id else
                await coordinator.cancel(intent.instance_id, intent.token)
            )
            if intent.cleanup_probe:
                cleaned = bool(getattr(cleaned, "accepted", False))
        except Exception:
            cleaned = False
        if cleaned and (current is None or current.refresh is intent):
            intent.phase = (
                RefreshPhase.RECONCILIATION_REQUIRED
                if intent.probe_timeout else RefreshPhase.ABANDONED
            )
            return (
                RefreshProgress.MANUAL_RECONCILIATION
                if intent.probe_timeout else RefreshProgress.BLOCKED
            )
        intent.cleanup_attempts += 1
        intent.next_retry_at = time.monotonic() + min(0.5 * 2 ** intent.cleanup_attempts, 2.0)
        return RefreshProgress.CLEANUP_PENDING

    async def _refresh_abandon_permit(self, intent: RefreshIntent) -> RefreshProgress:
        coordinator = self._refresh_coordinator
        assert coordinator is not None
        if intent.is_probe and intent.permit_id:
            try:
                completion = await coordinator.complete_probe(
                    intent.instance_id, intent.permit_id, False,
                )
            except Exception:
                completion = None
            if completion is None or not completion.accepted:
                intent.phase = RefreshPhase.CLEANUP_PENDING
                intent.cleanup_probe = True
                return RefreshProgress.CLEANUP_PENDING
        intent.phase = RefreshPhase.ABANDONED
        return RefreshProgress.BLOCKED

    def refresh_intent(self, handle: SessionHandle) -> RefreshIntent | None:
        """Expose the current Run's factual refresh state to its isolated host."""
        current = self._current(handle)
        return current.refresh if current is not None else None

    def note_schedule_gate(self, handle: SessionHandle, due: bool) -> None:
        current = self._current(handle)
        if current is not None and not due:
            current.scheduled_armed = True

    async def maintain_paused_refresh(self, handle: SessionHandle) -> RefreshProgress:
        """Maintain only this Run's existing refresh resources while paused."""
        if self._refresh_busy:
            return RefreshProgress.WAITING
        self._refresh_busy = True
        try:
            current = self._current(handle)
            intent = current.refresh if current is not None else None
            if current is None or intent is None:
                return RefreshProgress.BLOCKED
            if intent.phase in {
                RefreshPhase.QUEUED, RefreshPhase.RESERVE_UNKNOWN,
                RefreshPhase.RESERVED, RefreshPhase.COMMIT_UNKNOWN,
                RefreshPhase.CLEANUP_PENDING,
            }:
                return await self._refresh_cleanup(current, intent)
            if intent.phase is RefreshPhase.PERMITTED:
                return await self._refresh_abandon_permit(intent)
            if intent.phase in {
                RefreshPhase.RELOAD_POSSIBLE, RefreshPhase.AWAITING_CLEARANCE,
            }:
                await self._refresh_probe_renew(intent)
                if intent.probe_timeout:
                    intent.cleanup_probe = True
                    return await self._refresh_cleanup(current, intent)
                if intent.probe_lost:
                    intent.phase = RefreshPhase.OWNERSHIP_LOST
                    return RefreshProgress.MANUAL_RECONCILIATION
                try:
                    observed = await self._refresh_browser().observe_refresh_document(
                        handle.tab,
                    )
                except Exception:
                    return RefreshProgress.RELOAD_POSSIBLE
                if self._current(handle) is not current or current.refresh is not intent:
                    return RefreshProgress.RELOAD_POSSIBLE
                if (observed.url, observed.document_id) != (
                    intent.source_url, intent.source_document,
                ):
                    intent.new_document = observed.document_id
                    intent.phase = RefreshPhase.AWAITING_CLEARANCE
                return (
                    RefreshProgress.AWAITING_CLEARANCE
                    if intent.phase is RefreshPhase.AWAITING_CLEARANCE
                    else RefreshProgress.RELOAD_POSSIBLE
                )
            if intent.phase in {
                RefreshPhase.OWNERSHIP_LOST, RefreshPhase.RECONCILIATION_REQUIRED,
            }:
                return RefreshProgress.MANUAL_RECONCILIATION
            return RefreshProgress.BLOCKED
        finally:
            self._refresh_busy = False

    async def reconcile_closed_refresh(self, handle: SessionHandle) -> RefreshProgress:
        """Finish only the retired Run's original token after stop or close."""
        if self._refresh_busy:
            return RefreshProgress.RELOAD_POSSIBLE
        self._refresh_busy = True
        try:
            return await self._reconcile_closed_refresh_owned(handle)
        finally:
            self._refresh_busy = False

    async def _reconcile_closed_refresh_owned(self, handle: SessionHandle) -> RefreshProgress:
        retired = self._retired_runs.get(handle.tab)
        if retired is None or retired.handle != handle or retired.refresh is None:
            return RefreshProgress.BLOCKED
        intent = retired.refresh
        if intent.phase in {
            RefreshPhase.QUEUED, RefreshPhase.RESERVE_UNKNOWN,
            RefreshPhase.RESERVED, RefreshPhase.COMMIT_UNKNOWN,
            RefreshPhase.CLEANUP_PENDING,
        }:
            progress = await self._refresh_cleanup(None, intent)
        elif intent.phase is RefreshPhase.PERMITTED:
            progress = await self._refresh_abandon_permit(intent)
        elif intent.phase in {
            RefreshPhase.RELOAD_POSSIBLE, RefreshPhase.AWAITING_CLEARANCE,
        }:
            await self._refresh_probe_renew(intent)
            if intent.probe_timeout:
                intent.cleanup_probe = True
                return await self._refresh_cleanup(None, intent)
            if intent.probe_lost:
                intent.phase = RefreshPhase.OWNERSHIP_LOST
                return RefreshProgress.MANUAL_RECONCILIATION
            try:
                observed = await self._refresh_browser().observe_refresh_document(handle.tab)
            except Exception:
                return RefreshProgress.RELOAD_POSSIBLE
            if observed.document_id == intent.source_document and (
                observed.url == intent.source_url
            ):
                return RefreshProgress.RELOAD_POSSIBLE
            intent.new_document = observed.document_id
            intent.phase = RefreshPhase.AWAITING_CLEARANCE
            if observed.classification != "normal":
                return RefreshProgress.AWAITING_CLEARANCE
            if intent.is_probe and intent.permit_id:
                coordinator = self._refresh_coordinator
                assert coordinator is not None
                try:
                    completed = await coordinator.complete_probe(
                        intent.instance_id, intent.permit_id, True,
                    )
                except Exception:
                    completed = None
                if completed is None or not completed.accepted:
                    return await self._probe_completion_unknown(intent)
            intent.phase = RefreshPhase.COMPLETE
            progress = RefreshProgress.COMPLETE
        elif intent.phase in {
            RefreshPhase.OWNERSHIP_LOST, RefreshPhase.RECONCILIATION_REQUIRED,
        }:
            return RefreshProgress.MANUAL_RECONCILIATION
        else:
            progress = RefreshProgress.COMPLETE
        if intent.phase in {
            RefreshPhase.ABANDONED, RefreshPhase.COMPLETE, RefreshPhase.BLOCKED_429,
        }:
            retired.refresh = None
            if (retired.active is None and retired.submission is None
                and retired.manual_preparation is None):
                self._retired_runs.pop(handle.tab, None)
        return progress

    async def _refresh_probe_renew(self, intent: RefreshIntent) -> None:
        if not intent.is_probe or not intent.permit_id:
            return
        if intent.probe_started_at and time.monotonic() - intent.probe_started_at >= 120:
            intent.probe_timeout = True
            return
        if time.monotonic() < intent.next_probe_renew_at:
            return
        coordinator = self._refresh_coordinator
        assert coordinator is not None
        try:
            renewed = await coordinator.renew(intent.permit_id)
        except Exception:
            renewed = None
        if renewed is False:
            intent.probe_lost = True
            intent.probe_unknown = False
        elif renewed is None:
            intent.probe_unknown = True
        else:
            intent.probe_unknown = False
        # None is unknown ownership, not a lost or renewed lease.
        intent.next_probe_renew_at = time.monotonic() + (1.0 if renewed is None else 5.0)

    async def _probe_completion_unknown(self, intent: RefreshIntent) -> RefreshProgress:
        """A failed completion can be a DB error; only an exact renew proves loss."""
        coordinator = self._refresh_coordinator
        assert coordinator is not None
        try:
            renewed = await coordinator.renew(intent.permit_id)
        except Exception:
            renewed = None
        if renewed is False:
            intent.probe_lost = True
            intent.phase = RefreshPhase.OWNERSHIP_LOST
            return RefreshProgress.MANUAL_RECONCILIATION
        intent.probe_unknown = renewed is None
        intent.next_probe_renew_at = time.monotonic() + (1.0 if renewed is None else 5.0)
        return RefreshProgress.AWAITING_CLEARANCE

    async def advance_refresh(
        self, handle: SessionHandle, reason: RefreshReason,
        *, schedule_due: bool = True, apply_local_interval: bool = True,
    ) -> RefreshProgress:
        """Advance one original refresh request without replaying a browser dispatch."""
        if self._refresh_busy:
            return RefreshProgress.WAITING
        self._refresh_busy = True
        try:
            return await self._advance_refresh_locked(
                handle, reason, schedule_due, apply_local_interval,
            )
        finally:
            self._refresh_busy = False

    async def _advance_refresh_locked(
        self, handle: SessionHandle, reason: RefreshReason,
        schedule_due: bool, apply_local_interval: bool,
    ) -> RefreshProgress:
        coordinator = self._refresh_coordinator
        current = self._current(handle)
        if coordinator is None or current is None or not isinstance(reason, RefreshReason):
            return RefreshProgress.BLOCKED
        intent = current.refresh
        if intent is not None and intent.phase is RefreshPhase.CLEANUP_PENDING:
            return await self._refresh_cleanup(current, intent)
        if intent is not None and intent.phase is RefreshPhase.RELOAD_POSSIBLE:
            return await self._refresh_reconcile_document(handle, current, intent)
        if intent is not None and intent.phase is RefreshPhase.AWAITING_CLEARANCE:
            return await self._refresh_clearance(handle, current, intent)
        if intent is not None and intent.phase in {
            RefreshPhase.OWNERSHIP_LOST, RefreshPhase.RECONCILIATION_REQUIRED,
        }:
            return RefreshProgress.MANUAL_RECONCILIATION
        if intent is not None and intent.phase is RefreshPhase.COMPLETE:
            if intent.reason is RefreshReason.KNOWN_REJECTION and reason is RefreshReason.KNOWN_REJECTION:
                return RefreshProgress.COMPLETE
            if (
                intent.reason is RefreshReason.SCHEDULED
                and reason is RefreshReason.SCHEDULED
                and not current.scheduled_armed
            ):
                return RefreshProgress.COMPLETE
            if intent.reason is RefreshReason.RATE_LIMIT_RECOVERY and (
                current.health is not None and current.health.classification != "blocked"
            ) and reason is RefreshReason.RATE_LIMIT_RECOVERY:
                return RefreshProgress.COMPLETE
            current.refresh = None
            intent = None
        if intent is not None and intent.phase in {
            RefreshPhase.ABANDONED, RefreshPhase.BLOCKED_429,
        }:
            # A later host tick may begin a new request, never replay this permit.
            if intent.reason is RefreshReason.SCHEDULED:
                current.scheduled_armed = True
            current.refresh = None
            intent = None
        if intent is None:
            if (
                reason is RefreshReason.KNOWN_REJECTION
                and not self._known_conflict_refresh_ready(current)
            ) or (
                reason is RefreshReason.SCHEDULED and not schedule_due
            ) or (
                reason is RefreshReason.SCHEDULED and not current.scheduled_armed
            ):
                return RefreshProgress.BLOCKED
            health = await self.observe_health(handle)
            if health is None or self._current(handle) is not current:
                return RefreshProgress.BLOCKED
            if health.classification != (
                "blocked" if reason is RefreshReason.RATE_LIMIT_RECOVERY else "normal"
            ):
                return RefreshProgress.BLOCKED
            if (
                self._refresh_purchase_blocked(current)
                and reason is not RefreshReason.KNOWN_REJECTION
            ) or current.closing:
                return RefreshProgress.BLOCKED
            clearance = getattr(self._core, "current_health_clearance")(handle.tab)
            if health.classification == "normal" and clearance != (
                health.url, health.document_id
            ):
                return RefreshProgress.BLOCKED
            intent = RefreshIntent(
                uuid.uuid4().hex, current.refresh_instance_id, "",
                health.url, health.document_id, handle.generation,
                self._policy.policy_revision, current.control_revision,
                reason, apply_local_interval, time.monotonic(),
            )
            current.refresh = intent
            if reason is RefreshReason.SCHEDULED:
                current.scheduled_armed = False
        if intent.phase in {RefreshPhase.QUEUED, RefreshPhase.RESERVE_UNKNOWN}:
            try:
                settings = coordinator.settings(force=True)
            except Exception:
                return RefreshProgress.SETTINGS_UNKNOWN
            if not self._refresh_preflight(handle, current, intent):
                if intent.request_id:
                    return await self._refresh_cleanup(current, intent)
                intent.phase = RefreshPhase.ABANDONED
                return RefreshProgress.BLOCKED
            if not settings.enabled:
                if intent.request_id:
                    return await self._refresh_cleanup(current, intent)
                interval = (
                    0.0 if intent.reason is RefreshReason.SCHEDULED
                    else max(1.0, float(self._policy.flow.refresh_interval))
                    if intent.apply_local_interval and self._policy.flow is not None
                    else 0.0
                )
                if time.monotonic() < intent.eligible_at + interval:
                    return RefreshProgress.WAITING
                intent.phase = RefreshPhase.PERMITTED
            else:
                try:
                    reservation = await coordinator.reserve(
                        intent.instance_id, intent.token, settings.min_interval_seconds,
                    )
                except Exception:
                    intent.phase = RefreshPhase.RESERVE_UNKNOWN
                    return RefreshProgress.RESERVE_UNKNOWN
                intent.request_id = reservation.request_id
                if reservation.error or reservation.status == "error":
                    intent.phase = RefreshPhase.RESERVE_UNKNOWN
                    return RefreshProgress.RESERVE_UNKNOWN
                if reservation.reserved and reservation.reservation_id:
                    intent.reservation_id = reservation.reservation_id
                    intent.is_probe = reservation.is_recovery_probe
                    intent.phase = RefreshPhase.RESERVED
                elif reservation.status in {"queued", "pacing", "cooldown", "reservation_active"}:
                    return RefreshProgress.WAITING
                else:
                    intent.phase = RefreshPhase.RESERVE_UNKNOWN
                    return RefreshProgress.RESERVE_UNKNOWN
        if intent.phase in {RefreshPhase.RESERVED, RefreshPhase.COMMIT_UNKNOWN}:
            await self.observe_health(handle)
            try:
                settings = coordinator.settings(force=True)
            except Exception:
                return RefreshProgress.SETTINGS_UNKNOWN
            if not settings.enabled or not self._refresh_preflight(handle, current, intent):
                return await self._refresh_cleanup(current, intent)
            try:
                decision = await coordinator.commit(
                    intent.instance_id, intent.reservation_id, intent.token,
                )
            except Exception:
                intent.phase = RefreshPhase.COMMIT_UNKNOWN
                return RefreshProgress.COMMIT_UNKNOWN
            if decision.error or decision.status == "error":
                intent.phase = RefreshPhase.COMMIT_UNKNOWN
                return RefreshProgress.COMMIT_UNKNOWN
            if decision.status == "already_committed":
                # A lost response cannot grant a delayed replay, even with a permit ID.
                intent.permit_id = decision.permit_id
                intent.is_probe = decision.is_recovery_probe
                return await self._refresh_abandon_permit(intent)
            if not decision.granted or decision.status != "granted":
                return await self._refresh_cleanup(current, intent)
            intent.permit_id = decision.permit_id
            intent.is_probe = decision.is_recovery_probe
            intent.phase = RefreshPhase.PERMITTED
        if intent.phase is RefreshPhase.PERMITTED:
            await self.observe_health(handle)
            try:
                settings = coordinator.settings(force=True)
            except Exception:
                return RefreshProgress.SETTINGS_UNKNOWN
            if not self._refresh_preflight(handle, current, intent) or (
                intent.permit_id and not settings.enabled
            ) or (not intent.permit_id and settings.enabled):
                return await self._refresh_abandon_permit(intent)
            stamp = OperationStamp(
                handle.tab, handle.generation, OperationId(uuid.uuid4().hex),
                DocumentKey(intent.source_document), intent.policy_revision,
                intent.control_revision,
            )
            claim = RunningEffect(
                ClaimId(uuid.uuid4().hex), EffectProposal(stamp, EffectKind.RELOAD),
            )
            intent.claim = claim
            intent.phase = RefreshPhase.RELOAD_POSSIBLE
            intent.submitted_at = time.monotonic()
            if intent.is_probe:
                intent.probe_started_at = intent.submitted_at
            try:
                receipt = await self._refresh_browser().dispatch_reload(
                    claim, intent.source_url,
                )
            except Exception:
                receipt = None
            owner_is_current = self._current(handle) is current and current.refresh is intent
            retired = self._retired_runs.get(handle.tab)
            owner_is_retired = bool(
                retired is not None and retired.handle == handle
                and retired.refresh is intent
            )
            if owner_is_current or owner_is_retired:
                if isinstance(receipt, EffectReceipt) and receipt_matches(claim, receipt):
                    intent.receipt = receipt
                    if receipt.evidence is EffectEvidence.NO_EFFECT:
                        return await self._refresh_abandon_permit(intent)
            if owner_is_current:
                return await self._refresh_reconcile_document(handle, current, intent)
            return RefreshProgress.RELOAD_POSSIBLE
        return RefreshProgress.BLOCKED

    def _known_conflict_refresh_ready(self, current: _Session) -> bool:
        submission = current.submission
        clear = submission.conflict_clear if submission is not None else None
        stamp = clear.claim.proposal.stamp if clear is not None else None
        authority = self._purchase_authority(current.handle.tab)
        return bool(
            submission is not None
            and submission.terminal is SelectionProgress.SUBMIT_BLOCKED
            and submission.rejection_kind == "seat_conflict"
            and submission.conflict_cleared
            and stamp is not None
            and stamp.policy_revision == self._policy.policy_revision
            and stamp.control_revision == current.control_revision
            and authority is not None
            and authority.auth_epoch == submission.attempt.key.auth_epoch
            and current.active is None and current.manual_preparation is None
            and current.readiness is None
        )

    async def _refresh_reconcile_document(
        self, handle: SessionHandle, current: _Session, intent: RefreshIntent,
    ) -> RefreshProgress:
        await self._refresh_probe_renew(intent)
        if intent.probe_timeout:
            intent.cleanup_probe = True
            return await self._refresh_cleanup(current, intent)
        health = await self.observe_health(handle)
        if health is None or self._current(handle) is not current:
            return RefreshProgress.RELOAD_POSSIBLE
        if intent.probe_lost:
            if (health.url, health.document_id) != (
                intent.source_url, intent.source_document,
            ):
                intent.new_document = health.document_id
            intent.phase = RefreshPhase.OWNERSHIP_LOST
            return RefreshProgress.MANUAL_RECONCILIATION
        if (health.url, health.document_id) == (
            intent.source_url, intent.source_document,
        ):
            return RefreshProgress.RELOAD_POSSIBLE
        intent.new_document = health.document_id
        intent.phase = RefreshPhase.AWAITING_CLEARANCE
        return await self._refresh_clearance(handle, current, intent)

    async def _refresh_clearance(
        self, handle: SessionHandle, current: _Session, intent: RefreshIntent,
    ) -> RefreshProgress:
        await self._refresh_probe_renew(intent)
        if intent.probe_timeout:
            intent.cleanup_probe = True
            return await self._refresh_cleanup(current, intent)
        if intent.probe_lost:
            intent.phase = RefreshPhase.OWNERSHIP_LOST
            return RefreshProgress.MANUAL_RECONCILIATION
        health = await self.observe_health(handle)
        if health is None or self._current(handle) is not current:
            return RefreshProgress.AWAITING_CLEARANCE
        if (
            health.document_id == intent.new_document
            and health.classification == "blocked"
            and current.reported_429 == health.document_id
        ):
            intent.phase = RefreshPhase.BLOCKED_429
            return RefreshProgress.BLOCKED
        if health.document_id != intent.new_document or health.classification != "normal":
            return RefreshProgress.AWAITING_CLEARANCE
        if getattr(self._core, "current_health_clearance")(handle.tab) != (
            health.url, health.document_id,
        ):
            return RefreshProgress.AWAITING_CLEARANCE
        if intent.is_probe and intent.permit_id:
            coordinator = self._refresh_coordinator
            assert coordinator is not None
            try:
                completed = await coordinator.complete_probe(
                    intent.instance_id, intent.permit_id, True,
                )
            except Exception:
                completed = None
            if completed is None or not completed.accepted:
                return await self._probe_completion_unknown(intent)
        intent.phase = RefreshPhase.COMPLETE
        return RefreshProgress.COMPLETE

    def _flow_browser(self) -> SelectionBrowserPort:
        return self._browser

    def _observation_valid(
        self, handle: SessionHandle, before: PlanningContext,
        observation: object, snapshot_type: type,
    ) -> bool:
        current = self._current(handle)
        return bool(
            self._flow_context(handle) == before
            and isinstance(observation, PageObservation)
            and observation.tab == handle.tab
            and isinstance(observation.snapshot, snapshot_type)
            and observation.snapshot.ok
            and observation.snapshot.page_url
            and observation.snapshot.document_id
            and (
                self._refresh_coordinator is None
                or current is not None and current.health is not None
                and (observation.snapshot.page_url, observation.snapshot.document_id)
                == (current.health.url, current.health.document_id)
            )
        )

    @staticmethod
    def _original_page_valid(
        handle: SessionHandle, active: _ActiveSelection,
        observation: object, snapshot_type: type,
    ) -> bool:
        return bool(
            isinstance(observation, PageObservation)
            and observation.tab == handle.tab
            and isinstance(observation.snapshot, snapshot_type)
            and observation.snapshot.ok
            and observation.snapshot.page_url == active.source_url
            and observation.snapshot.document_id
            == active.claim.proposal.stamp.document.token
        )

    def _claim_selection(
        self, handle: SessionHandle, before: PlanningContext,
        stage: str, snapshot: DateSnapshot | AreaSnapshot | QuantitySnapshot | SeatSnapshot,
        plan: DateActionPlan | AreaActionPlan | QuantityPlan | SeatActionPlan,
        replacing: _ActiveSelection | None = None,
        manual_revision: int = 0,
    ) -> RunningEffect | None:
        current = self._current(handle)
        if (
            current is None or self._flow_context(handle) != before
            or not (
                current.active is replacing
                or current.active is None and current.selection_source is replacing
            )
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
        if replacing is not None and replacing.stage in {
            "date", "area", "server_area", "seat_clear",
        }:
            current.selection_source = replacing
        current.active = _ActiveSelection(
            stage, claim, snapshot.page_url, plan,
            manual_revision_at_claim=manual_revision,
            seat_behavior_at_claim=(
                self._policy.last_valid if stage in {"seat", "seat_clear"} else None
            ),
        )
        return claim

    def _active_for_claim(self, handle: SessionHandle, claim: ActiveEffect) -> _ActiveSelection | None:
        current = self._current(handle)
        retired = self._retired_runs.get(handle.tab)
        active = current.active if current is not None else (
            retired.active if retired is not None and retired.handle == handle else None
        )
        return active if active is not None and active.claim.claim_id == claim.claim_id else None

    def _hold_claim(self, handle: SessionHandle, claim: ActiveEffect, reason: HeldReason) -> None:
        active = self._active_for_claim(handle, claim)
        if active is not None:
            active.claim = HeldEffect(claim.claim_id, claim.proposal, reason)

    def _record_receipt(
        self, handle: SessionHandle, claim: RunningEffect, receipt: EffectReceipt,
    ) -> bool:
        """Apply one factual receipt without consulting current dispatch authority."""
        active = self._active_for_claim(handle, claim)
        if active is None:
            return False
        if (
            not isinstance(receipt, EffectReceipt)
            or not receipt_matches(claim, receipt)
            or receipt.observed_document != claim.proposal.stamp.document
        ):
            if active.receipt is None:
                self._hold_claim(handle, claim, HeldReason.UNMATCHED_COMPLETION)
            return False
        prior = active.receipt
        if prior is not None:
            if prior == receipt:
                return prior.evidence not in {EffectEvidence.NO_EFFECT}
            # Once the request boundary was evidenced, a late pre-dispatch
            # refusal cannot erase it or permit another submit.
            active.receipt_conflict = True
            return False
        active.receipt = receipt
        current = self._current(handle)
        retired = self._retired_runs.get(handle.tab)
        is_retired = retired is not None and retired.handle == handle and retired.active is active
        if receipt.evidence is EffectEvidence.NO_EFFECT:
            active.claim = HeldEffect(
                claim.claim_id, claim.proposal, HeldReason.NO_EFFECT_CONFIRMED,
            )
            if is_retired and retired is not None and (
                active.stage == "submit" or retired.submission is None
            ):
                self._finish_retired_active(
                    handle, retired, finish_submission=active.stage == "submit",
                )
            elif current is not None and current.active is active:
                if active.stage == "submit" and current.submission is not None:
                    current.submission.terminal = SelectionProgress.SUBMIT_BLOCKED
            return False
        navigation = active.stage in {
            "date", "area", "server_area", "submit", "captcha_reset",
        }
        valid = (
            receipt.status is EffectStatus.UNKNOWN
            and receipt.evidence in {
                EffectEvidence.REQUEST_DISPATCHED, EffectEvidence.REQUEST_POSSIBLE,
            }
            if navigation else receipt.evidence is EffectEvidence.LOCAL_MUTATION
        )
        if not valid:
            self._hold_claim(handle, claim, HeldReason.UNCERTAIN_DISPATCH)
            return False
        if is_retired and not navigation and retired is not None and (
            retired.submission is None
            or active.stage in {"quantity", "seat", "seat_clear", "login"}
        ) and receipt.status is EffectStatus.APPLIED:
            self._finish_retired_active(handle, retired)
            return False
        active.claim = AwaitingEvidenceEffect(claim.claim_id, claim.proposal)
        return not is_retired

    async def reconcile_closed(self, handle: SessionHandle) -> bool:
        """Read only the original closed Run effect; never authorize a new action."""
        retired = self._retired_runs.get(handle.tab)
        if retired is None or retired.handle != handle or retired.active is None:
            return False
        active = retired.active
        if active.receipt is None:
            return False
        try:
            if active.stage == "date" and active.receipt.evidence in {
                EffectEvidence.REQUEST_DISPATCHED, EffectEvidence.REQUEST_POSSIBLE,
            }:
                observed = await self._flow_browser().observe_area(handle.tab)
                resolved = isinstance(observed, PageObservation) and (
                    observed.tab == handle.tab
                    and isinstance(observed.snapshot, AreaSnapshot)
                    and self._date_destination_matches(active, observed.snapshot)
                )
            elif active.stage in {"area", "server_area"} and active.receipt.evidence in {
                EffectEvidence.REQUEST_DISPATCHED, EffectEvidence.REQUEST_POSSIBLE,
            }:
                plan = active.plan
                if not isinstance(plan, AreaActionPlan):
                    return False
                if active.stage == "server_area":
                    observed = await self._flow_browser().observe_quantity(handle.tab)
                    resolved = isinstance(observed, PageObservation) and (
                        observed.tab == handle.tab
                        and isinstance(observed.snapshot, QuantitySnapshot)
                        and self._server_area_destination_matches(active, observed.snapshot)
                    )
                else:
                    observed = (
                        await self._flow_browser().observe_seat(handle.tab)
                        if plan.route == "0205"
                        else await self._flow_browser().observe_quantity(handle.tab)
                    )
                    resolved = isinstance(observed, PageObservation) and (
                        observed.tab == handle.tab and (
                            isinstance(observed.snapshot, SeatSnapshot)
                            and self._seat_destination_matches(active, observed.snapshot)
                            if plan.route == "0205" else
                            isinstance(observed.snapshot, QuantitySnapshot)
                            and self._area_destination_matches(active, observed.snapshot)
                        )
                    )
            elif active.stage == "quantity" and isinstance(active.plan, QuantityPlan):
                observed = await self._flow_browser().observe_quantity(handle.tab)
                resolved = self._original_page_valid(
                    handle, active, observed, QuantitySnapshot,
                ) and self._quantity_is_ready(
                    cast(QuantitySnapshot, observed.snapshot), active.plan,
                )
            elif active.stage == "seat" and isinstance(active.plan, SeatActionPlan):
                observed = await self._flow_browser().observe_seat(handle.tab)
                resolved = self._original_page_valid(
                    handle, active, observed, SeatSnapshot,
                ) and self._seat_ready_plan(
                    cast(SeatSnapshot, observed.snapshot), active.plan,
                    original_behavior=active.seat_behavior_at_claim,
                ) is not None
            else:
                return False
        except Exception:
            return False
        if resolved and self._retired_runs.get(handle.tab) is retired:
            self._finish_retired_active(handle, retired)
            return True
        return False

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
        return self._record_receipt(handle, claim, receipt)

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
            and stamp.session_generation == context.session_generation
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

    def _server_area_destination_matches(
        self, active: _ActiveSelection, snapshot: QuantitySnapshot,
    ) -> bool:
        plan = active.plan
        if (
            active.stage != "server_area"
            or not isinstance(plan, AreaActionPlan)
            or not self._destination_changed(
                active, snapshot.page_url, snapshot.document_id,
            )
            or not self._server_assignment_supported(active.source_url)
            or classify_utiki_page(snapshot.page_url)
            is not PageKind.SERVER_ASSIGNED_QUANTITY
        ):
            return False
        source = unique_query(active.source_url)
        destination = unique_query(snapshot.page_url)
        return bool(
            source is not None
            and set(source) == {"PERFORMANCE_ID", "PRODUCT_ID"}
            and source["PRODUCT_ID"]
            and source["PERFORMANCE_ID"] == plan.performance_id
            and destination is not None
            and set(destination) == {
                "PERFORMANCE_ID", "GROUP_ID",
                "PERFORMANCE_PRICE_AREA_ID", "ATYPE",
            }
            and destination["ATYPE"] == "N"
            and destination["PERFORMANCE_ID"]
            == plan.performance_id == snapshot.performance_id
            and destination["GROUP_ID"] == plan.group_id == snapshot.group_id
            and destination["PERFORMANCE_PRICE_AREA_ID"]
            == plan.price_area_id == snapshot.price_area_id
            and snapshot.product_hidden_present
            and snapshot.product_id == source["PRODUCT_ID"]
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
        if current.active is not None and current.active.stage == "date" and (
            current.active.receipt is not None
            and current.active.receipt.evidence is EffectEvidence.NO_EFFECT
        ):
            prior = current.active
            observed = await self._flow_browser().observe_date(handle.tab)
            if (
                self._observation_valid(handle, before, observed, DateSnapshot)
                and observed.snapshot.page_url == prior.source_url
                and observed.snapshot.document_id == prior.claim.proposal.stamp.document.token
                and self._current(handle) is current and current.active is prior
            ):
                current.active = None
            return SelectionProgress.AWAITING_EVIDENCE
        if current.active is not None:
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_date(handle.tab)
        if not self._observation_valid(handle, before, observation, DateSnapshot):
            return SelectionProgress.BLOCKED
        snapshot = cast(DateSnapshot, observation.snapshot)
        if self._computer_mode() and not self._server_assignment_supported(
            snapshot.page_url
        ):
            return SelectionProgress.BLOCKED
        plan = select_date_snapshot(snapshot, settings)
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
        if current.active is not None and current.active.stage in {
            "area", "server_area",
        } and (
            current.active.receipt is not None
            and current.active.receipt.evidence is EffectEvidence.NO_EFFECT
        ):
            prior_area = current.active
            observed = await self._flow_browser().observe_area(handle.tab)
            if (
                self._observation_valid(handle, before, observed, AreaSnapshot)
                and observed.snapshot.page_url == prior_area.source_url
                and observed.snapshot.document_id == prior_area.claim.proposal.stamp.document.token
                and self._current(handle) is current and current.active is prior_area
            ):
                current.active = None
            return SelectionProgress.AWAITING_EVIDENCE
        prior = current.active
        if prior is None:
            prior = current.selection_source
        if prior is None or prior.stage != "date" or not self._prior_matches_context(prior, before):
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_area(handle.tab)
        if not self._observation_valid(handle, before, observation, AreaSnapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        snapshot = cast(AreaSnapshot, observation.snapshot)
        if not self._date_destination_matches(prior, snapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        # The destination closes the original date navigation independently of
        # whether this new document has a selectable area. Keep its source for
        # a later area claim; a no-area refresh must not inherit an active date.
        if current.active is prior:
            current.active = None
            current.selection_source = prior
        computer = self._computer_mode()
        if computer and not self._server_assignment_supported(snapshot.page_url):
            return SelectionProgress.BLOCKED
        query = unique_query(snapshot.page_url)
        if query is None:
            return SelectionProgress.AWAITING_EVIDENCE
        plan = select_area_snapshot(
            snapshot, settings, query["PERFORMANCE_ID"],
            require_direct_destination=False,
        )
        if (
            plan is None or plan.route not in {"0202", "0205"}
            or (computer and (plan.route != "0205" or not plan.group_id))
        ):
            return SelectionProgress.BLOCKED
        if not computer and plan.route == "0205" and any(
            not callable(getattr(self._browser, name, None))
            for name in ("observe_seat", "commit_seat_snapshot")
        ):
            raise ValueError("reserved-seat browser capability is unavailable")
        stage = "server_area" if computer else "area"
        claim = self._claim_selection(handle, before, stage, snapshot, plan, prior)
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_server_assigned_area(
                snapshot, plan, settings.ticket_number, claim,
            ) if computer else self._flow_browser().commit_area(
                snapshot, plan, settings.ticket_number, claim,
            ),
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
        if current is not None and before is None and current.active is not None and (
            current.active.stage == "quantity"
            and isinstance(current.active.claim, AwaitingEvidenceEffect)
            and isinstance(current.active.plan, QuantityPlan)
        ):
            pending = current.active
            assert isinstance(pending.plan, QuantityPlan)
            observed = await self._flow_browser().observe_quantity(handle.tab)
            if (
                self._current(handle) is current and current.active is pending
                and self._original_page_valid(handle, pending, observed, QuantitySnapshot)
                and self._quantity_is_ready(cast(QuantitySnapshot, observed.snapshot), pending.plan)
            ):
                current.active = None
                current.verified_quantity = pending.plan
            return SelectionProgress.AWAITING_EVIDENCE
        if before is None or current is None or settings is None:
            return SelectionProgress.BLOCKED
        if isinstance(current.ready_selection, QuantityPlan):
            if current.ready_context != before:
                current.readiness = None
                current.login_verified = None
            else:
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
                    if (
                        isinstance(readback, PageObservation)
                        and isinstance(readback.snapshot, QuantitySnapshot)
                        and isinstance(current.ready_selection, QuantityPlan)
                        and readback.snapshot.document_id != current.ready_selection.document_id
                    ):
                        current.selection_source = None
                        current.verified_quantity = None
                    current.readiness = None
                    current.login_verified = None
                return SelectionProgress.AWAITING_EVIDENCE
        prior = current.active
        if prior is not None and prior.stage == "quantity" and (
            prior.receipt is not None and prior.receipt.evidence is EffectEvidence.NO_EFFECT
        ):
            observed = await self._flow_browser().observe_quantity(handle.tab)
            if (
                self._observation_valid(handle, before, observed, QuantitySnapshot)
                and observed.snapshot.page_url == prior.source_url
                and observed.snapshot.document_id == prior.claim.proposal.stamp.document.token
                and self._current(handle) is current and current.active is prior
            ):
                current.active = None
            return SelectionProgress.AWAITING_EVIDENCE
        if prior is not None and prior.stage == "quantity":
            if not isinstance(prior.claim, AwaitingEvidenceEffect) or not isinstance(prior.plan, QuantityPlan):
                return SelectionProgress.AWAITING_EVIDENCE
            readback = await self._flow_browser().observe_quantity(handle.tab)
            current = self._current(handle)
            if (
                current is None or current.active is not prior
                or not self._observation_valid(handle, before, readback, QuantitySnapshot)
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            if not self._quantity_is_ready(cast(QuantitySnapshot, readback.snapshot), prior.plan):
                if (
                    readback.manual_revision > prior.manual_revision_at_claim
                    and readback.snapshot.page_url == prior.source_url
                    and readback.snapshot.document_id == prior.plan.document_id
                ):
                    confirmed = await self._flow_browser().observe_quantity(handle.tab)
                    if (
                        self._current(handle) is current and current.active is prior
                        and self._observation_valid(handle, before, confirmed, QuantitySnapshot)
                        and confirmed.manual_revision == readback.manual_revision
                        and confirmed.snapshot == readback.snapshot
                    ):
                        current.active = None
                        current.selection_source = None
                        current.verified_quantity = None
                        current.readiness = None
                        current.login_verified = None
                return SelectionProgress.AWAITING_EVIDENCE
            current.active = None
            current.verified_quantity = prior.plan
            if prior.claim.proposal.stamp.policy_revision != before.policy_revision or (
                prior.claim.proposal.stamp.control_revision != before.control_revision
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            current.readiness = _Readiness(prior.plan, before)
            current.login_verified = None
            return SelectionProgress.QUANTITY_READY
        if prior is None:
            prior = current.selection_source
        computer = self._computer_mode()
        if (
            prior is None
            or prior.stage != ("server_area" if computer else "area")
            or not isinstance(prior.plan, AreaActionPlan)
            or (not computer and prior.plan.route != "0202")
            or not (
                self._prior_matches_context(prior, before)
                or computer and prior.stage == "server_area"
                and isinstance(prior.claim, HeldEffect)
                and prior.claim.reason is HeldReason.UNCERTAIN_DISPATCH
                and prior.claim.proposal.stamp.session_generation
                == before.session_generation
            )
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        observation = await self._flow_browser().observe_quantity(handle.tab)
        if not self._observation_valid(handle, before, observation, QuantitySnapshot):
            return SelectionProgress.AWAITING_EVIDENCE
        snapshot = cast(QuantitySnapshot, observation.snapshot)
        if not (
            self._server_area_destination_matches(prior, snapshot)
            if computer else self._area_destination_matches(prior, snapshot)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        policy = self._policy.last_valid
        if policy is None:
            return SelectionProgress.BLOCKED
        verified = current.verified_quantity
        planning_snapshot = snapshot
        if (
            verified is not None and verified.document_id == snapshot.document_id
            and verified.page_url == snapshot.page_url
            and self._quantity_is_ready(snapshot, verified)
        ):
            planning_snapshot = replace(
                snapshot, rows=tuple(replace(row, value="0") for row in snapshot.rows),
            )
        plan = select_quantity_snapshot(
            planning_snapshot, policy.ticket_type_keyword, settings.ticket_number,
            page_type=(
                "server_assigned_quantity" if computer else "general_admission"
            ),
        )
        if plan is None:
            return SelectionProgress.BLOCKED
        if (
            verified is not None and current.active is None
            and current.verified_quantity is verified
            and self._quantity_is_ready(snapshot, verified)
            and replace(plan, prior_commit=None) == replace(verified, prior_commit=None)
            and self._flow_context(handle) == before
        ):
            current.readiness = _Readiness(plan, before)
            current.login_verified = None
            return SelectionProgress.QUANTITY_READY
        if (
            verified is not None and verified.document_id == plan.document_id
            and verified.page_url == plan.page_url
            and (verified.target_key, verified.ticket_number)
            != (plan.target_key, plan.ticket_number)
        ):
            plan = replace(
                plan, prior_commit=(verified.target_key, verified.ticket_number),
            )
        claim = self._claim_selection(
            handle, before, "quantity", snapshot, plan, prior,
            observation.manual_revision,
        )
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_quantity_snapshot(snapshot, plan, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        return await self.advance_quantity(handle)

    def _seat_ready_plan(
        self, snapshot: SeatSnapshot, expected: SeatActionPlan | None,
        *, original_behavior: UtikiBehavior | None = None,
    ) -> SeatActionPlan | None:
        settings = self._flow_settings
        policy = original_behavior or self._policy.last_valid
        if settings is None or policy is None:
            return None
        plan, _reason = select_seat_snapshot(
            snapshot, policy.ticket_type_keyword,
            expected.ticket_number if original_behavior is not None and expected is not None
            else settings.ticket_number,
            not expected.require_physical_adjacency
            if original_behavior is not None and expected is not None
            else settings.allow_non_adjacent,
            policy.seat_sorting,
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
            or plan.expected_payload_keys != expected.expected_payload_keys
            or plan.selected_seat_fingerprints != fingerprints
            or (expected.action == "ready" and plan != expected)
        ):
            return None
        return plan

    async def advance_seat(self, handle: SessionHandle) -> SelectionProgress:
        before = self._flow_context(handle)
        current = self._current(handle)
        settings = self._flow_settings
        if current is not None and before is None and current.active is not None and (
            current.active.stage == "seat"
            and isinstance(current.active.claim, AwaitingEvidenceEffect)
            and isinstance(current.active.plan, SeatActionPlan)
        ):
            pending = current.active
            assert isinstance(pending.plan, SeatActionPlan)
            observed = await self._flow_browser().observe_seat(handle.tab)
            if (
                self._current(handle) is current and current.active is pending
                and self._original_page_valid(handle, pending, observed, SeatSnapshot)
                and self._seat_ready_plan(
                    cast(SeatSnapshot, observed.snapshot), pending.plan,
                    original_behavior=pending.seat_behavior_at_claim,
                )
                is not None
            ):
                current.active = None
            return SelectionProgress.AWAITING_EVIDENCE
        if before is None or current is None or settings is None:
            return SelectionProgress.BLOCKED
        if isinstance(current.ready_selection, SeatActionPlan):
            if current.ready_context != before:
                current.readiness = None
                current.login_verified = None
            else:
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
                    current.readiness = None
                    current.login_verified = None
                return SelectionProgress.AWAITING_EVIDENCE
        prior = current.active
        if prior is not None and prior.stage in {"seat", "seat_clear"} and (
            prior.receipt is not None and prior.receipt.evidence is EffectEvidence.NO_EFFECT
        ):
            observed = await self._flow_browser().observe_seat(handle.tab)
            if (
                self._observation_valid(handle, before, observed, SeatSnapshot)
                and observed.snapshot.page_url == prior.source_url
                and observed.snapshot.document_id == prior.claim.proposal.stamp.document.token
                and self._current(handle) is current and current.active is prior
            ):
                current.active = None
            return SelectionProgress.AWAITING_EVIDENCE
        if prior is not None and prior.stage == "seat":
            if not isinstance(prior.claim, AwaitingEvidenceEffect) or not isinstance(prior.plan, SeatActionPlan):
                return SelectionProgress.AWAITING_EVIDENCE
            readback = await self._flow_browser().observe_seat(handle.tab)
            current = self._current(handle)
            ready_plan = (
                self._seat_ready_plan(
                    cast(SeatSnapshot, readback.snapshot), prior.plan,
                    original_behavior=prior.seat_behavior_at_claim,
                )
                if self._observation_valid(handle, before, readback, SeatSnapshot) else None
            )
            if current is None or current.active is not prior:
                return SelectionProgress.AWAITING_EVIDENCE
            if ready_plan is None:
                if (
                    self._observation_valid(handle, before, readback, SeatSnapshot)
                    and readback.manual_revision > prior.manual_revision_at_claim
                    and readback.snapshot.page_url == prior.source_url
                    and readback.snapshot.document_id == prior.plan.document_id
                ):
                    confirmed = await self._flow_browser().observe_seat(handle.tab)
                    if (
                        self._current(handle) is current and current.active is prior
                        and self._observation_valid(handle, before, confirmed, SeatSnapshot)
                        and confirmed.manual_revision == readback.manual_revision
                        and confirmed.snapshot == readback.snapshot
                    ):
                        current.active = None
                        current.selection_source = None
                        current.readiness = None
                        current.login_verified = None
                return SelectionProgress.AWAITING_EVIDENCE
            current.active = None
            if prior.claim.proposal.stamp.policy_revision != before.policy_revision or (
                prior.claim.proposal.stamp.control_revision != before.control_revision
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            current.readiness = _Readiness(ready_plan, before)
            current.login_verified = None
            return SelectionProgress.SEAT_READY
        if prior is None:
            prior = current.selection_source
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
            if (
                current is None or self._flow_context(handle) != before
                or not (
                    current.active is prior
                    or current.active is None and current.selection_source is prior
                )
            ):
                return SelectionProgress.BLOCKED
            current.readiness = _Readiness(plan, before)
            current.login_verified = None
            current.selection_source = prior
            current.active = None
            return SelectionProgress.SEAT_READY
        if plan.action == "clear" and from_area:
            claim = self._claim_selection(
                handle, before, "seat_clear", snapshot, plan, prior,
                observation.manual_revision,
            )
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
        claim = self._claim_selection(
            handle, before, "seat", snapshot, plan, prior,
            observation.manual_revision,
        )
        if claim is None:
            return SelectionProgress.BLOCKED
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._flow_browser().commit_seat_snapshot(snapshot, plan, claim),
        )
        if not dispatched:
            return SelectionProgress.AWAITING_EVIDENCE
        return await self.advance_seat(handle)

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
        source = current.login_verified
        if source is None:
            return True
        if isinstance(source, _RejectedManualLoginEvidence):
            if source.page_url != plan.page_url or source.document_id != plan.document_id:
                return False
            try:
                observed = await self._purchase_browser().observe_rejected_login(
                    handle.tab, source.page_url, source.document_id, source.token,
                )
            except Exception:
                return False
            return bool(
                isinstance(observed, RejectedLoginObservation)
                and observed.tab == handle.tab
                and observed.page_url == source.page_url
                and observed.document_id == source.document_id
                and observed.token == source.token and observed.changed_ready
                and self._current(handle) is current
                and current.login_verified == source
                and current.ready_selection == plan and current.ready_context == before
                and current.active is None and self._flow_context(handle) == before
                and self._purchase_authority(handle.tab) == authority
            )
        if isinstance(source, _AcceptedManualLoginEvidence):
            acceptance = source.rejection
            profile = get_utiki_tenant_profile(plan.page_url)
            method = getattr(self._core, "current_verified_login", None)
            try:
                fresh_acceptance = (
                    method(handle.tab, acceptance.rejected_attempt_id)
                    if callable(method) else None
                )
            except Exception:
                return False
            return bool(
                profile is not None
                and acceptance.tab == handle.tab
                and acceptance.tenant_key == profile.key
                and acceptance.page_url == plan.page_url
                and acceptance.document_id == plan.document_id
                and authority is not None and authority.auth_epoch == acceptance.auth_epoch
                and fresh_acceptance == acceptance
                and self._current(handle) is current
                and current.login_verified == source
                and current.ready_selection == plan and current.ready_context == before
                and current.active is None and self._flow_context(handle) == before
            )
        if isinstance(source, _ManualLoginEvidence):
            intent = source.intent
            manual_credentials = source.credentials
            manual_authority = source.authority
        else:
            intent = source
            manual_credentials = None
            manual_authority = None
        profile = get_utiki_tenant_profile(plan.page_url)
        if (
            authority is None or profile is None
            or intent.page_url != plan.page_url
            or intent.document_id != plan.document_id
            or intent.credential_revision != authority.credential_revision
            or manual_authority is not None and manual_authority != authority
        ):
            return False
        credentials = self._inline_login_credentials(handle.tab, profile.key)
        if credentials is None or credentials.revision != intent.credential_revision:
            return False
        if manual_credentials is not None and credentials != manual_credentials:
            return False
        try:
            audit = await self._purchase_browser().audit_inline_login(
                handle.tab, intent, credentials,
            )
        except Exception:
            return False
        return bool(
            isinstance(audit, InlineLoginAudit)
            and (audit.manual_ready if manual_credentials is not None else audit.matched)
            and audit.tab == handle.tab and audit.page_url == intent.page_url
            and audit.document_id == intent.document_id
            and self._current(handle) is current
            and current.login_verified == source
            and current.ready_selection == plan and current.ready_context == before
            and current.active is None and self._flow_context(handle) == before
            and self._purchase_authority(handle.tab) == authority
            and self._inline_login_credentials(handle.tab, profile.key) == credentials
        )

    async def advance_login(self, handle: SessionHandle) -> SelectionProgress:
        """Fill missing inline credentials under one tab claim, without exporting values."""
        current = self._current(handle)
        if current is None:
            return SelectionProgress.BLOCKED
        if (
            current.submission is not None
            and current.submission.terminal is SelectionProgress.SUBMIT_BLOCKED
            and current.submission.rejection_kind == "auth"
        ):
            return await self.advance_login_recovery(handle)
        if current.active is not None and current.active.stage == "login":
            active = current.active
            if active.receipt is not None and active.receipt.evidence is EffectEvidence.NO_EFFECT:
                try:
                    observed = await self._purchase_browser().observe_purchase_input(handle.tab)
                except Exception:
                    return SelectionProgress.AWAITING_EVIDENCE
                if (
                    isinstance(observed, PurchaseInputObservation)
                    and observed.tab == handle.tab
                    and observed.page_url == active.source_url
                    and observed.document_id == active.claim.proposal.stamp.document.token
                    and self._current(handle) is current and current.active is active
                ):
                    current.active = None
                return SelectionProgress.AWAITING_EVIDENCE
            if (
                not (
                    isinstance(active.claim, AwaitingEvidenceEffect)
                    or isinstance(active.claim, HeldEffect)
                    and active.claim.reason is HeldReason.UNCERTAIN_DISPATCH
                )
                or not isinstance(active.plan, InlineLoginIntent)
                or active.login_credentials is None
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            try:
                audit = await self._purchase_browser().audit_inline_login(
                    handle.tab, active.plan, active.login_credentials,
                )
            except Exception:
                return SelectionProgress.AWAITING_EVIDENCE
            if (
                self._current(handle) is not current or current.active is not active
                or not isinstance(audit, InlineLoginAudit)
                or not (audit.matched or audit.manual_ready)
                or audit.tab != handle.tab or audit.page_url != active.plan.page_url
                or audit.document_id != active.plan.document_id
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            authority = self._purchase_authority(handle.tab)
            if (
                authority is None or authority != active.login_authority
                or authority.credential_revision != active.login_credentials.revision
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            current.active = None
            current.login_verified = (
                _ManualLoginEvidence(active.plan, active.login_credentials, authority)
                if audit.manual_ready else active.plan
            )
            profile = get_utiki_tenant_profile(active.source_url)
            return (
                SelectionProgress.LOGIN_READY
                if (
                    self._flow_context(handle) == current.ready_context
                    and profile is not None
                    and current.ready_selection is not None
                    and self._inline_login_credentials(handle.tab, profile.key)
                    == active.login_credentials
                )
                else SelectionProgress.AWAITING_EVIDENCE
            )
        before = self._flow_context(handle)
        if before is None:
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
        if isinstance(current.login_verified, _AcceptedManualLoginEvidence):
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
        current.active = _ActiveSelection(
            "login", claim, plan.page_url, intent, credentials, authority,
        )
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

    async def advance_login_recovery(self, handle: SessionHandle) -> SelectionProgress:
        """Observe a trusted post-rejection edit before preparing a new attempt."""
        before = self._flow_context(handle)
        current = self._current(handle)
        submission = current.submission if current is not None else None
        if (
            current is None or submission is None
            or submission.terminal is not SelectionProgress.SUBMIT_BLOCKED
            or submission.rejection_kind != "auth"
            or submission.rejection_dialog is None
            or not submission.request_boundary
        ):
            return SelectionProgress.BLOCKED
        accepted = (
            await self._verified_login_after_rejection(handle, current, submission)
            if before is not None else None
        )
        if accepted is not None:
            current.prior_submissions.append(submission)
            current.submission = None
            current.active = None
            current.manual_preparation = None
            current.login_verified = _AcceptedManualLoginEvidence(accepted)
            current.input_revision += 1
            current.input_state = None
            current.input_image = None
            current.input_observed = None
            current.input_answer = None
            current.ocr_attempt = None
            current.required_manual_revision = None
            return SelectionProgress.LOGIN_RECOVERY_READY
        if (
            current.active is not None and current.active.stage == "submit"
            and (before is None or not self._recovery_dispatch_ready(handle, before, submission))
        ):
            return SelectionProgress.BLOCKED
        if not submission.dialog_dismissed:
            dismissed = await self.advance_dialog_dismissal(handle)
            if dismissed is not SelectionProgress.DIALOG_DISMISSED:
                return dismissed
        active = current.active
        if active is not None and active.stage == "login_arm":
            if (
                not isinstance(active.claim, AwaitingEvidenceEffect)
                or not self._recovery_source_current(handle, before, current, active)
                or not submission.login_observation_token
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            try:
                observed = await self._purchase_browser().observe_rejected_login(
                    handle.tab, submission.attempt.plan.page_url,
                    submission.attempt.plan.document_id,
                    submission.login_observation_token,
                )
            except Exception:
                return SelectionProgress.AWAITING_EVIDENCE
            if (
                not isinstance(observed, RejectedLoginObservation)
                or observed.tab != handle.tab
                or observed.page_url != submission.attempt.plan.page_url
                or observed.document_id != submission.attempt.plan.document_id
                or observed.token != submission.login_observation_token
                or not observed.changed_ready
                or self._current(handle) is not current
                or current.active is not active or current.submission is not submission
                or not self._recovery_source_current(handle, before, current, active)
            ):
                return SelectionProgress.MANUAL_ACTION_REQUIRED
            current.prior_submissions.append(submission)
            current.submission = None
            current.active = None
            current.manual_preparation = None
            current.login_verified = _RejectedManualLoginEvidence(
                submission.attempt.attempt_id, observed.page_url,
                observed.document_id, observed.token,
            )
            current.input_revision += 1
            current.input_state = None
            current.input_image = None
            current.input_observed = None
            current.input_answer = None
            current.ocr_attempt = None
            current.required_manual_revision = None
            return SelectionProgress.LOGIN_RECOVERY_READY
        if before is None:
            return SelectionProgress.BLOCKED
        if (
            active is None or active.stage != "dialog_dismiss"
            or not isinstance(active.claim, AwaitingEvidenceEffect)
            or not self._recovery_source_current(handle, before, current, active)
            or not self._recovery_dispatch_ready(handle, before, submission)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        token = uuid.uuid4().hex
        plan = submission.attempt.plan
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            DocumentKey(plan.document_id), before.policy_revision,
            before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(
                stamp, EffectKind.INPUT_WRITE,
                parent_recovery_id=submission.attempt.attempt_id.token,
            ),
        )
        submission.login_observation_token = token
        current.active = _ActiveSelection("login_arm", claim, plan.page_url,
                                          submission.rejection_dialog)
        dispatched = await self._dispatch_selection(
            handle, claim, self._purchase_browser().arm_rejected_login(
                handle.tab, plan.page_url, plan.document_id, token, claim,
            ),
        )
        return (
            SelectionProgress.AWAITING_EVIDENCE if dispatched
            else SelectionProgress.BLOCKED
        )

    async def _verified_login_after_rejection(
        self, handle: SessionHandle, current: _Session, submission: _Submission,
    ) -> VerifiedLoginAcceptance | None:
        method = getattr(self._core, "current_verified_login", None)
        if not callable(method):
            return None
        attempt = submission.attempt
        profile = get_utiki_tenant_profile(attempt.plan.page_url)
        authority = self._purchase_authority(handle.tab)
        if profile is None or authority is None:
            return None
        try:
            acceptance = method(handle.tab, attempt.attempt_id.token)
            observed = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return None
        if (
            not isinstance(acceptance, VerifiedLoginAcceptance)
            or acceptance.tab != handle.tab
            or acceptance.tenant_key != profile.key
            or acceptance.page_url != attempt.plan.page_url
            or acceptance.document_id != attempt.plan.document_id
            or acceptance.rejected_attempt_id != attempt.attempt_id.token
            or acceptance.auth_epoch != authority.auth_epoch
            or acceptance.auth_epoch == attempt.key.auth_epoch
            or not isinstance(observed, SubmitOutcomeObservation)
            or observed.tab != handle.tab
            or observed.page_url != attempt.plan.page_url
            or observed.document_id != attempt.plan.document_id
            or observed.dialog.visible_count != 0
            or self._current(handle) is not current
            or current.submission is not submission
            or self._purchase_authority(handle.tab) != authority
            or method(handle.tab, attempt.attempt_id.token) != acceptance
        ):
            return None
        return acceptance

    def _submit_pending(
        self, handle: SessionHandle,
        current: _Session | _RetiredOutcome, active: _ActiveSelection,
    ) -> SelectionProgress:
        submission = current.submission
        if (
            submission is not None and submission.dispatched_at is not None
            and time.monotonic() - submission.dispatched_at >= 10
        ):
            # A still-running browser call can later report NO_EFFECT. Keep its
            # original running claim until that receipt arrives.
            if isinstance(active.claim, AwaitingEvidenceEffect):
                self._hold_claim(handle, active.claim, HeldReason.UNCERTAIN_DISPATCH)
            return SelectionProgress.SUBMIT_BLOCKED
        return SelectionProgress.AWAITING_EVIDENCE

    def _record_submit_terminal(
        self, submission: _Submission, progress: SelectionProgress,
    ) -> SelectionProgress:
        submission.terminal = progress
        if (
            progress is SelectionProgress.SUBMIT_CONFIRMED
            and not submission.notification_attempted
            and self._checkout_notification is not None
        ):
            # The wrapper starts background work and has no delivery receipt.
            submission.notification_attempted = True
            try:
                self._checkout_notification.start_checkout_notification()
            except Exception:
                submission.notification_start_failed = True
        return progress

    @staticmethod
    def _manual_marker_matches(
        preparation: _ManualPreparation, marker: ManualSubmitEvidence | None,
    ) -> bool:
        return bool(
            preparation.arm_token and marker is not None
            and marker.page_url == preparation.plan.page_url
            and marker.document_id == preparation.plan.document_id
            and marker.arm_token == preparation.arm_token
        )

    async def _reconcile_manual_submit(
        self, handle: SessionHandle, current: _Session | _RetiredOutcome,
    ) -> SelectionProgress:
        preparation = current.manual_preparation
        submission = current.submission
        attempt = submission.attempt if submission is not None else None
        if preparation is None or (
            attempt is not None and attempt.source is not SubmitSource.OBSERVED_MANUAL
        ):
            return SelectionProgress.BLOCKED
        if submission is not None and submission.terminal is not None:
            return submission.terminal
        if not preparation.arm_token:
            return SelectionProgress.AWAITING_EVIDENCE
        try:
            observation = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observation, SubmitOutcomeObservation)
            or observation.tab != handle.tab
            or self._current(handle) is not current
            and self._retired_runs.get(handle.tab) is not current
            or not self._manual_marker_matches(preparation, observation.manual_submit)
        ):
            if (
                current.manual_preparation is preparation
                and (
                    isinstance(current, _RetiredOutcome)
                    or self._flow_context(handle) != preparation.context
                )
                and isinstance(observation, SubmitOutcomeObservation)
                and observation.tab == handle.tab
                and observation.manual_submit is None
            ):
                try:
                    disarmed = await self._purchase_browser().disarm_manual_submit(
                        handle.tab, preparation.plan.page_url,
                        preparation.plan.document_id, preparation.arm_token,
                    )
                except Exception:
                    disarmed = False
                if disarmed and (
                    self._current(handle) is current
                    or self._retired_runs.get(handle.tab) is current
                ) and current.manual_preparation is preparation:
                    current.manual_preparation = None
                    if (
                        isinstance(current, _RetiredOutcome)
                        and current.active is None and current.submission is None
                        and current.refresh is None
                    ):
                        self._retired_runs.pop(handle.tab, None)
                    return SelectionProgress.BLOCKED
            return SelectionProgress.AWAITING_EVIDENCE
        marker = observation.manual_submit
        assert marker is not None
        if attempt is None:
            try:
                attempt = SubmitAttempt(
                    SubmitAttemptId(uuid.uuid4().hex),
                    preparation.input_evidence.context.cycle, preparation.key,
                    None, preparation.plan, preparation.input_evidence,
                    SubmitSource.OBSERVED_MANUAL, marker,
                )
            except ValueError:
                return SelectionProgress.SUBMIT_BLOCKED
            submission = _Submission(
                attempt, None, None,
                preparation.behavior_at_arm, preparation.flow_at_arm,
            )
            current.submission = submission
        elif attempt.manual_evidence != marker:
            return SelectionProgress.SUBMIT_BLOCKED
        assert submission is not None
        source_profile = get_utiki_tenant_profile(preparation.plan.page_url)
        destination_profile = get_utiki_tenant_profile(observation.page_url)
        try:
            same_site = bool(
                source_profile is not None and destination_profile is not None
                and source_profile.key == destination_profile.key
                and same_origin(
                    urlsplit(preparation.plan.page_url), urlsplit(observation.page_url),
                )
            )
        except ValueError:
            same_site = False
        if not same_site:
            return SelectionProgress.AWAITING_EVIDENCE
        dialog = observation.dialog
        if dialog.visible_count:
            if (
                observation.page_url != preparation.plan.page_url
                or observation.document_id != preparation.plan.document_id
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            outcome = classify_submit_dialog(dialog.text) if dialog.visible_count == 1 else None
            if outcome not in {"success", "quota", "captcha", "auth", "seat_conflict"}:
                # An unclassified dialog blocks new work but does not close the
                # original request. Later attributable checkout remains readable.
                return SelectionProgress.SUBMIT_BLOCKED
            if outcome in {"captcha", "auth", "seat_conflict"}:
                submission.rejection_kind = outcome
                submission.rejection_dialog = dialog
            terminal = (
                SelectionProgress.SUBMIT_CONFIRMED if outcome == "success"
                else SelectionProgress.QUOTA_BLOCKED if outcome == "quota"
                else SelectionProgress.SUBMIT_BLOCKED
            )
            return self._record_submit_terminal(submission, terminal)
        if (
            observation.document_id != preparation.plan.document_id
            and classify_utiki_page(observation.page_url) is PageKind.CHECKOUT
        ):
            return self._record_submit_terminal(
                submission, SelectionProgress.SUBMIT_CONFIRMED,
            )
        return SelectionProgress.AWAITING_EVIDENCE

    async def _reconcile_submit(
        self, handle: SessionHandle, before: PlanningContext | None
    ) -> SelectionProgress:
        current: _Session | _RetiredOutcome | None = self._current(handle)
        if current is None:
            retired = self._retired_runs.get(handle.tab)
            current = retired if retired is not None and retired.handle == handle else None
        if current is None:
            return SelectionProgress.BLOCKED
        submission = current.submission
        if current.manual_preparation is not None and (
            submission is None
            or submission.attempt.source is SubmitSource.OBSERVED_MANUAL
        ):
            return await self._reconcile_manual_submit(handle, current)
        if current.active is None or current.active.stage != "submit":
            return SelectionProgress.BLOCKED
        if submission is not None and submission.terminal is not None:
            return submission.terminal
        active = current.active
        if submission is None or submission.attempt.stamp != active.claim.proposal.stamp:
            return SelectionProgress.SUBMIT_BLOCKED
        if isinstance(active.claim, RunningEffect):
            return self._submit_pending(handle, current, active)
        if isinstance(active.claim, HeldEffect) and active.claim.reason not in (
            HeldReason.UNCERTAIN_DISPATCH, HeldReason.CONTROL_REVOKED,
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        if not submission.request_boundary:
            return self._submit_pending(handle, current, active)
        try:
            observation = await self._purchase_browser().observe_submit_outcome(handle.tab)
        except Exception:
            return self._submit_pending(handle, current, active)
        if (
            not isinstance(observation, SubmitOutcomeObservation)
            or observation.tab != handle.tab
            or (
                self._current(handle) is not current
                and self._retired_runs.get(handle.tab) is not current
            )
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
            if outcome not in {"success", "quota", "captcha", "auth", "seat_conflict"}:
                return SelectionProgress.SUBMIT_BLOCKED
            if outcome in {"captcha", "auth", "seat_conflict"}:
                submission.rejection_kind = outcome
                submission.rejection_dialog = dialog
            terminal = (
                SelectionProgress.SUBMIT_CONFIRMED if outcome == "success"
                else SelectionProgress.QUOTA_BLOCKED if outcome == "quota"
                else SelectionProgress.SUBMIT_BLOCKED
            )
            return self._record_submit_terminal(submission, terminal)
        if (
            observation.document_id != active.claim.proposal.stamp.document.token
            and classify_utiki_page(observation.page_url) is PageKind.CHECKOUT
        ):
            return self._record_submit_terminal(
                submission, SelectionProgress.SUBMIT_CONFIRMED,
            )
        return self._submit_pending(handle, current, active)

    def _recovery_source_current(
        self, handle: SessionHandle, before: PlanningContext | None,
        current: _Session, active: _ActiveSelection,
    ) -> bool:
        submission = current.submission
        if submission is None or submission.rejection_dialog is None:
            return False
        attempt = submission.attempt
        dialog = submission.rejection_dialog
        stamp = attempt.stamp
        recovery_kinds = {
            "dialog_dismiss": EffectKind.DISMISS_DIALOG,
            "captcha_reset": EffectKind.CAPTCHA_RESET,
            "login_arm": EffectKind.INPUT_WRITE,
            "conflict_clear": EffectKind.SELECT,
        }
        if stamp is None:
            preparation = current.manual_preparation
            marker = attempt.manual_evidence
            proposal = active.claim.proposal
            return bool(
                attempt.source is SubmitSource.OBSERVED_MANUAL
                and preparation is not None and marker is not None
                and self._manual_marker_matches(preparation, marker)
                and attempt.plan.page_url == dialog.page_url
                and attempt.plan.document_id == dialog.document_id
                and active.source_url == attempt.plan.page_url
                and active.stage in recovery_kinds
                and proposal.parent_recovery_id == attempt.attempt_id.token
                and proposal.kind is recovery_kinds[active.stage]
                and proposal.stamp.tab == handle.tab
                and proposal.stamp.document.token == dialog.document_id
                and proposal.stamp.session_generation == handle.generation
            )
        if (
            stamp.session_generation != handle.generation
            or stamp.tab != handle.tab
            or stamp.document.token != dialog.document_id
            or attempt.plan.page_url != dialog.page_url
            or active.source_url != attempt.plan.page_url
        ):
            return False
        proposal = active.claim.proposal
        if active.stage == "submit":
            return proposal.kind is EffectKind.SUBMIT and proposal.stamp == stamp
        return (
            active.stage in recovery_kinds
            and proposal.kind is recovery_kinds[active.stage]
            and proposal.parent_recovery_id == attempt.attempt_id.token
            and proposal.stamp.tab == stamp.tab
            and proposal.stamp.document == stamp.document
            and proposal.stamp.session_generation == handle.generation
            and (
                active.stage != "captcha_reset" or (
                    submission.captcha_reset is not None
                    and active.plan == submission.captcha_reset
                    and submission.captcha_reset.context == attempt.input_evidence.context
                )
            )
        )

    def _recovery_dispatch_ready(
        self, handle: SessionHandle, before: PlanningContext,
        submission: _Submission,
    ) -> bool:
        """A new recovery effect needs current authority, separate from old facts."""
        authority = self._purchase_authority(handle.tab)
        return bool(
            self._flow_context(handle) == before
            and authority is not None
            and authority.auth_epoch == submission.attempt.key.auth_epoch
            and authority.credential_revision
            == submission.attempt.input_evidence.context.credential_revision
            and self._policy.last_valid == submission.behavior_at_attempt
            and self._selection_flow_signature(self._flow_settings)
            == self._selection_flow_signature(submission.flow_at_attempt)
        )

    @staticmethod
    def _selection_flow_signature(settings: FlowSettings | None) -> tuple[object, ...] | None:
        if settings is None:
            return None
        return (
            settings.date_enabled, settings.area_enabled, settings.ticket_number,
            settings.date_keyword, settings.date_mode, settings.date_fallback,
            settings.area_keyword, settings.area_mode, settings.area_fallback,
            settings.keyword_exclude, settings.allow_non_adjacent,
        )

    def recovery_policy_changed(self, handle: SessionHandle) -> bool:
        current = self._current(handle)
        submission = current.submission if current is not None else None
        return bool(
            submission is not None
            and submission.terminal is SelectionProgress.SUBMIT_BLOCKED
            and (
                self._policy.last_valid != submission.behavior_at_attempt
                or self._selection_flow_signature(self._flow_settings)
                != self._selection_flow_signature(submission.flow_at_attempt)
            )
        )

    async def reconcile_existing_recovery(
        self, handle: SessionHandle,
    ) -> SelectionProgress | None:
        """Read back an already issued recovery claim; never create a new one."""
        current = self._current(handle)
        active = current.active if current is not None else None
        if active is None or not isinstance(active.claim, AwaitingEvidenceEffect):
            return None
        if active.stage == "dialog_dismiss":
            return await self.advance_dialog_dismissal(handle)
        if active.stage == "captcha_reset":
            return await self.advance_captcha_reset(handle)
        if active.stage == "login_arm":
            return await self.advance_login_recovery(handle)
        if active.stage == "conflict_clear":
            return await self.advance_conflict_recovery(handle)
        return None

    async def _observe_dialog_closed(
        self, handle: SessionHandle, before: PlanningContext | None,
        current: _Session, active: _ActiveSelection,
    ) -> SelectionProgress:
        submission = current.submission
        expected = submission.rejection_dialog if submission is not None else None
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
            or self._current(handle) is not current
            or current.active is not active
            or submission is None or submission.rejection_dialog != expected
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        submission.dialog_dismissed = True
        return SelectionProgress.DIALOG_DISMISSED

    async def advance_dialog_dismissal(self, handle: SessionHandle) -> SelectionProgress:
        """Close one known rejection while retaining its original submit blocker."""
        before = self._flow_context(handle)
        current = self._current(handle)
        submission = current.submission if current is not None else None
        if (
            current is None or submission is None
            or submission.terminal is not SelectionProgress.SUBMIT_BLOCKED
            or submission.rejection_kind not in {"captcha", "auth", "seat_conflict"}
            or submission.rejection_dialog is None
            or not submission.request_boundary
            or current.active is None
            and submission.attempt.source is not SubmitSource.OBSERVED_MANUAL
        ):
            return SelectionProgress.BLOCKED
        active = current.active
        if active is not None and not self._recovery_source_current(handle, before, current, active):
            return SelectionProgress.BLOCKED
        if active is not None and active.stage == "dialog_dismiss":
            if submission.dialog_dismissed:
                return SelectionProgress.DIALOG_DISMISSED
            return await self._observe_dialog_closed(handle, before, current, active)
        if before is None:
            return SelectionProgress.BLOCKED
        if active is not None and (active.stage != "submit" or isinstance(active.claim, RunningEffect)):
            return SelectionProgress.AWAITING_EVIDENCE
        if not self._recovery_dispatch_ready(handle, before, submission):
            return SelectionProgress.BLOCKED
        expected = submission.rejection_dialog
        assert expected is not None
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
            or submission.rejection_dialog != expected
            or active is not None
            and not self._recovery_source_current(handle, before, current, active)
            or not self._recovery_dispatch_ready(handle, before, submission)
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
                parent_recovery_id=submission.attempt.attempt_id.token,
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
        self, handle: SessionHandle, before: PlanningContext | None,
        current: _Session, active: _ActiveSelection,
    ) -> SelectionProgress:
        submission = current.submission
        reset = submission.captcha_reset if submission is not None else None
        if (
            reset is None or not isinstance(active.claim, AwaitingEvidenceEffect)
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        if submission is not None and submission.captcha_reset_verified:
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
            or self._current(handle) is not current
            or current.active is not active
            or not self._recovery_source_current(handle, before, current, active)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        assert submission is not None
        submission.captcha_reset_verified = True
        submission.verified_image = observed.image
        return SelectionProgress.CAPTCHA_RESET_VERIFIED

    async def advance_captcha_reset(self, handle: SessionHandle) -> SelectionProgress:
        """Refresh one rejected challenge, then wait for a distinct ready image."""
        before = self._flow_context(handle)
        current = self._current(handle)
        submission = current.submission if current is not None else None
        if (
            current is None or submission is None
            or submission.terminal is not SelectionProgress.SUBMIT_BLOCKED
            or submission.rejection_kind != "captcha"
            or submission.rejection_dialog is None
            or not submission.dialog_dismissed
            or not submission.request_boundary
            or current.active is None
        ):
            return SelectionProgress.BLOCKED
        active = current.active
        if not self._recovery_source_current(handle, before, current, active):
            return SelectionProgress.BLOCKED
        if active.stage == "captcha_reset":
            return await self._observe_captcha_refreshed(handle, before, current, active)
        if before is None:
            return SelectionProgress.BLOCKED
        if active.stage != "dialog_dismiss" or not isinstance(
            active.claim, AwaitingEvidenceEffect
        ):
            return SelectionProgress.SUBMIT_BLOCKED
        if not self._recovery_dispatch_ready(handle, before, submission):
            return SelectionProgress.BLOCKED
        try:
            observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observed, CaptchaResetObservation)
            or observed.tab != handle.tab
            or observed.page_url != active.source_url
            or observed.document_id != submission.rejection_dialog.document_id
            or observed.image is None
            or not observed.field_usable or not observed.refresh_usable
            or observed.dialog_visible
            or self._flow_context(handle) != before
            or self._current(handle) is not current
            or current.active is not active
            or not self._recovery_source_current(handle, before, current, active)
            or not self._recovery_dispatch_ready(handle, before, submission)
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        reset = CaptchaResetAttempt(
            ResetId(uuid.uuid4().hex), submission.attempt.input_evidence.context,
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
                parent_recovery_id=submission.attempt.attempt_id.token,
            )
        )
        submission.captcha_reset = reset
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

    @staticmethod
    def _conflict_selection_empty(
        attempt: SubmitAttempt, snapshot: QuantitySnapshot | SeatSnapshot,
    ) -> bool:
        def zero(value: str) -> bool:
            parsed = parse_nonnegative_count(value)
            return isinstance(parsed, PresentField) and parsed.value == 0

        plan = attempt.plan
        if (
            not snapshot.ok or snapshot.page_url != plan.page_url
            or snapshot.document_id != plan.document_id
            or snapshot.performance_id != attempt.key.performance_id
            or snapshot.group_id != attempt.key.group_id
            or snapshot.price_area_id != attempt.key.price_area_id
            or snapshot.product_id != attempt.key.product_id
        ):
            return False
        if isinstance(snapshot, QuantitySnapshot):
            return bool(snapshot.rows) and all(zero(row.value) for row in snapshot.rows)
        return bool(
            snapshot.product_hidden_present
            and snapshot.visual_selected_count == 0
            and not any(seat.selected for seat in snapshot.seats)
            and not any(seat.a for seat in snapshot.submitted_seats)
            and snapshot.counters
            and all(zero(counter.raw) for counter in snapshot.counters)
        )

    async def advance_conflict_recovery(
        self, handle: SessionHandle,
    ) -> SelectionProgress:
        """Clear one known rejected selection, then use the shared reload seam."""
        current = self._current(handle)
        before = self._flow_context(handle)
        if (
            before is None and current is not None and current.refresh is not None
            and current.refresh.reason is RefreshReason.KNOWN_REJECTION
        ):
            before = PlanningContext(
                handle.generation, current.refresh.policy_revision,
                current.refresh.control_revision,
            )
        submission = current.submission if current is not None else None
        if (
            current is None or submission is None
            or submission.terminal is not SelectionProgress.SUBMIT_BLOCKED
            or submission.rejection_kind != "seat_conflict"
            or submission.rejection_dialog is None
            or not submission.request_boundary
        ):
            return SelectionProgress.BLOCKED
        attempt = submission.attempt
        if not submission.dialog_dismissed:
            dismissed = await self.advance_dialog_dismissal(handle)
            if dismissed is not SelectionProgress.DIALOG_DISMISSED:
                return dismissed
        if not submission.conflict_cleared:
            active = current.active
            if active is None or active.stage == "dialog_dismiss":
                if (
                    before is None
                    or active is None or not isinstance(active.claim, AwaitingEvidenceEffect)
                    or not self._recovery_source_current(handle, before, current, active)
                    or not self._recovery_dispatch_ready(handle, before, submission)
                ):
                    return SelectionProgress.AWAITING_EVIDENCE
                stamp = OperationStamp(
                    handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
                    DocumentKey(attempt.plan.document_id), before.policy_revision,
                    before.control_revision,
                )
                claim = RunningEffect(
                    ClaimId(uuid.uuid4().hex), EffectProposal(
                        stamp, EffectKind.SELECT,
                        parent_recovery_id=attempt.attempt_id.token,
                    ),
                )
                active = _ActiveSelection(
                    "conflict_clear", claim, attempt.plan.page_url,
                    submission.rejection_dialog,
                )
                submission.conflict_clear = active
                current.active = active
                dispatched = await self._dispatch_selection(
                    handle, claim,
                    self._purchase_browser().clear_rejected_selection(attempt, claim),
                )
                if not dispatched:
                    return SelectionProgress.AWAITING_EVIDENCE
            if (
                current.active is not active or submission.conflict_clear is not active
                or not isinstance(active.claim, AwaitingEvidenceEffect)
                or active.receipt is None
                or active.receipt.status is not EffectStatus.APPLIED
                or active.receipt.evidence is not EffectEvidence.LOCAL_MUTATION
                or not self._recovery_source_current(handle, before, current, active)
            ):
                return (
                    SelectionProgress.MANUAL_ACTION_REQUIRED
                    if active.receipt is not None else SelectionProgress.AWAITING_EVIDENCE
                )
            try:
                observed = (
                    await self._flow_browser().observe_seat(handle.tab)
                    if attempt.key.page_mode is PageKind.RESERVED_SEAT
                    else await self._flow_browser().observe_quantity(handle.tab)
                )
            except Exception:
                return SelectionProgress.AWAITING_EVIDENCE
            if (
                not isinstance(observed, PageObservation)
                or observed.tab != handle.tab
                or not isinstance(observed.snapshot, (QuantitySnapshot, SeatSnapshot))
                or not self._conflict_selection_empty(attempt, observed.snapshot)
                or self._current(handle) is not current
                or current.active is not active or current.submission is not submission
                or not self._recovery_source_current(handle, before, current, active)
            ):
                return SelectionProgress.AWAITING_EVIDENCE
            submission.conflict_cleared = True
            current.active = None
            current.readiness = None
            current.verified_quantity = None
            current.login_verified = None
            current.manual_preparation = None
            return SelectionProgress.CONFLICT_CLEARED
        if before is None:
            return SelectionProgress.BLOCKED
        if self._refresh_coordinator is None:
            return SelectionProgress.MANUAL_ACTION_REQUIRED
        progress = await self.advance_refresh(
            handle, RefreshReason.KNOWN_REJECTION,
            schedule_due=True, apply_local_interval=True,
        )
        intent = current.refresh
        if (
            progress is not RefreshProgress.COMPLETE or intent is None
            or intent.reason is not RefreshReason.KNOWN_REJECTION
            or intent.source_url != attempt.plan.page_url
            or intent.source_document != attempt.plan.document_id
            or not intent.new_document
        ):
            return (
                SelectionProgress.MANUAL_ACTION_REQUIRED
                if progress is RefreshProgress.BLOCKED
                else SelectionProgress.AWAITING_EVIDENCE
            )
        try:
            observed = (
                await self._flow_browser().observe_seat(handle.tab)
                if attempt.key.page_mode is PageKind.RESERVED_SEAT
                else await self._flow_browser().observe_quantity(handle.tab)
            )
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        source = current.selection_source
        snapshot = observed.snapshot if isinstance(observed, PageObservation) else None
        after = self._flow_context(handle)
        snapshot_product = (
            snapshot.product_id if isinstance(snapshot, (QuantitySnapshot, SeatSnapshot))
            else None
        )
        source_matches = bool(
            source is not None and snapshot is not None
            and (
                self._seat_destination_matches(source, snapshot)
                if isinstance(snapshot, SeatSnapshot)
                else self._server_area_destination_matches(source, snapshot)
                if attempt.key.page_mode is PageKind.SERVER_ASSIGNED_QUANTITY
                and isinstance(snapshot, QuantitySnapshot)
                else self._area_destination_matches(source, snapshot)
                if isinstance(snapshot, QuantitySnapshot) else False
            )
        )
        if (
            not isinstance(observed, PageObservation)
            or observed.tab != handle.tab or snapshot is None or not snapshot.ok
            or snapshot.document_id != intent.new_document
            or snapshot.document_id == attempt.plan.document_id
            or not source_matches
            or classify_utiki_page(snapshot.page_url) is not attempt.key.page_mode
            or snapshot_product != attempt.key.product_id
            or self._current(handle) is not current
            or current.submission is not submission
            or after is None or after.policy_revision != intent.policy_revision
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        current.prior_submissions.append(submission)
        current.submission = None
        current.input_revision += 1
        current.input_state = None
        current.input_image = None
        current.input_observed = None
        current.input_answer = None
        current.ocr_attempt = None
        current.required_manual_revision = None
        return SelectionProgress.CONFLICT_RECOVERED

    @staticmethod
    def _captcha_answer(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        answer = value.strip().upper()
        return answer if len(answer) == 4 and answer.isascii() and answer.isalnum() else None

    @staticmethod
    def _captcha_bytes(value: str) -> bytes | None:
        prefix = "data:image/png;base64,"
        if not value.startswith(prefix) or len(value) > 2_000_000:
            return None
        try:
            return base64.b64decode(value[len(prefix):], validate=True)
        except (ValueError, binascii.Error):
            return None

    async def _audit_captcha_answer(
        self, observed: CaptchaResetObservation, answer: str,
    ) -> bool:
        try:
            return await self._purchase_browser().audit_captcha_input(observed, answer)
        except Exception:
            return False

    async def _reconcile_original_captcha_write(
        self, handle: SessionHandle, current: _Session,
    ) -> SelectionProgress | None:
        """Close an existing local write before asking for today's new grant."""
        active = current.active
        if active is None or active.stage != "captcha_input":
            return None
        if not isinstance(active.plan, CaptchaAttempt) or isinstance(active.claim, RunningEffect):
            return SelectionProgress.AWAITING_EVIDENCE
        prior = current.input_observed
        try:
            observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if (
            not isinstance(observed, CaptchaResetObservation)
            or observed.tab != handle.tab or observed.page_url != active.source_url
            or observed.document_id != active.claim.proposal.stamp.document.token
            or prior is None or prior.image != active.plan.image
            or self._current(handle) is not current or current.active is not active
        ):
            return SelectionProgress.AWAITING_EVIDENCE
        answer = current.input_answer
        if (
            isinstance(active.claim, AwaitingEvidenceEffect)
            and answer is not None and observed.image == active.plan.image
            and observed.manual_revision == prior.manual_revision
            and await self._audit_captcha_answer(prior, answer)
            and self._current(handle) is current and current.active is active
        ):
            current.active = None
            before = self._flow_context(handle)
            authority = self._purchase_authority(handle.tab)
            original = active.claim.proposal.stamp
            if (
                before is not None and current.ready_context == before
                and (before.session_generation, before.policy_revision,
                     before.control_revision) == (
                         original.session_generation, original.policy_revision,
                         original.control_revision)
                and authority is not None
                and authority.credential_revision == active.plan.context.credential_revision
            ):
                current.input_state = ReadyInput(InputReadyEvidence(
                    active.plan.context, InputSource.RECOGNITION, active.plan.image,
                    active.plan.attempt_id, True, observed.manual_revision,
                ))
                return SelectionProgress.INPUT_READY
            # The original write happened; a changed gate/policy must grant a
            # separate input. Do not silently reuse this answer as a new one.
            current.input_state = None
            current.input_answer = None
            current.required_manual_revision = observed.manual_revision
            return SelectionProgress.BLOCKED
        if (
            observed.manual_revision > prior.manual_revision
            and active.receipt is not None
            and receipt_matches(active.claim, active.receipt)
            and active.receipt.evidence in {
                EffectEvidence.LOCAL_MUTATION, EffectEvidence.NO_EFFECT,
            }
            and (
                isinstance(active.claim, AwaitingEvidenceEffect)
                or isinstance(active.claim, HeldEffect)
                and active.claim.reason is HeldReason.NO_EFFECT_CONFIRMED
            )
        ):
            current.active = None
            current.input_state = None
            current.input_answer = None
            current.required_manual_revision = None
            return None
        if (
            active.receipt is not None
            and active.receipt.evidence is EffectEvidence.NO_EFFECT
            and observed.field_empty
        ):
            current.active = None
            current.input_state = None
            current.input_answer = None
            return SelectionProgress.BLOCKED
        return SelectionProgress.AWAITING_EVIDENCE

    async def advance_input(self, handle: SessionHandle) -> SelectionProgress:
        """Prepare one field value from manual evidence or the Run-owned OCR provider."""
        current = self._current(handle)
        before = self._flow_context(handle)
        settings = self._flow_settings
        if current is None or before is None or settings is None:
            if current is None:
                return SelectionProgress.BLOCKED
            original = await self._reconcile_original_captcha_write(handle, current)
            return original if original is not None else SelectionProgress.BLOCKED
        original = await self._reconcile_original_captcha_write(handle, current)
        if original is not None:
            return original
        plan = current.ready_selection
        authority = self._purchase_authority(handle.tab)
        if plan is None or current.ready_context != before or authority is None:
            return SelectionProgress.BLOCKED
        if current.submission is not None:
            return SelectionProgress.BLOCKED
        if current.active is not None and current.active.stage != "captcha_input":
            return SelectionProgress.AWAITING_EVIDENCE
        try:
            selection = (
                await self._purchase_browser().observe_seat(handle.tab)
                if isinstance(plan, SeatActionPlan)
                else await self._purchase_browser().observe_quantity(handle.tab)
            )
            inputs = await self._purchase_browser().observe_purchase_input(handle.tab)
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        selection_ready = (
            self._observation_valid(handle, before, selection, SeatSnapshot)
            and self._seat_ready_plan(cast(SeatSnapshot, selection.snapshot), plan) is not None
            if isinstance(plan, SeatActionPlan) else (
                self._observation_valid(handle, before, selection, QuantitySnapshot)
                and self._quantity_is_ready(cast(QuantitySnapshot, selection.snapshot), plan)
            )
        )
        if (
            not selection_ready or not isinstance(inputs, PurchaseInputObservation)
            or inputs.tab != handle.tab or inputs.page_url != plan.page_url
            or inputs.document_id != plan.document_id
            or not inputs.captcha_unique or not inputs.captcha_enabled
            or inputs.dialog_visible or self._current(handle) is not current
            or self._flow_context(handle) != before
        ):
            return SelectionProgress.BLOCKED
        context = InputContext(
            handle.tab, DocumentKey(plan.document_id), current.cycle,
            selection.manual_revision, authority.credential_revision,
            current.input_revision,
            InputPolicy.RECOGNITION_ALLOWED if settings.ocr_enabled
            else InputPolicy.MANUAL_ONLY, settings.auto_submit,
        )
        observed: CaptchaResetObservation | None = None
        if current.input_image is not None or settings.ocr_enabled:
            try:
                observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
            except Exception:
                if current.input_image is not None:
                    return SelectionProgress.AWAITING_EVIDENCE
            if observed is not None and (
                not isinstance(observed, CaptchaResetObservation)
                or observed.tab != handle.tab or observed.page_url != plan.page_url
                or observed.document_id != plan.document_id
            ):
                return SelectionProgress.BLOCKED
        if self._current(handle) is not current or self._flow_context(handle) != before:
            return SelectionProgress.BLOCKED
        prior_image = current.input_image
        if prior_image is None and isinstance(current.input_state, ReadyInput):
            prior_image = current.input_state.evidence.image
        if observed is not None and prior_image is not None and observed.image != prior_image:
            if observed.image is None:
                return SelectionProgress.BLOCKED
            current.input_image = observed.image
            current.input_revision += 1
            current.input_state = None
            current.input_observed = observed
            current.input_answer = None
            current.ocr_attempt = None
            current.required_manual_revision = (
                observed.manual_revision if not observed.field_empty else None
            )
            return SelectionProgress.BLOCKED
        if isinstance(current.input_state, RecognizingInput):
            return SelectionProgress.AWAITING_EVIDENCE
        if isinstance(current.input_state, ReadyInput):
            evidence = current.input_state.evidence
            if evidence.context == context and inputs.manual_value_ready:
                if evidence.source is InputSource.MANUAL:
                    return SelectionProgress.INPUT_READY
                if (
                    observed is not None and current.input_observed is not None
                    and current.input_answer is not None
                    and observed.image == current.input_observed.image
                    and observed.manual_revision == current.input_observed.manual_revision
                    and await self._audit_captcha_answer(
                        current.input_observed, current.input_answer,
                    )
                    and self._current(handle) is current
                    and self._flow_context(handle) == before
                ):
                    return SelectionProgress.INPUT_READY
            current.input_state = WaitingManualInput(context)
            current.input_answer = None
        if inputs.manual_value_ready:
            if (
                current.required_manual_revision is not None
                and (observed is None or observed.manual_revision
                     <= current.required_manual_revision)
            ):
                current.input_state = WaitingManualInput(context)
                return SelectionProgress.BLOCKED
            evidence = InputReadyEvidence(
                context, InputSource.MANUAL,
                observed.image if observed is not None else None, None, True,
                observed.manual_revision if observed is not None else 0,
            )
            current.input_state = ReadyInput(evidence)
            current.input_observed = observed
            current.input_answer = None
            current.required_manual_revision = None
            return SelectionProgress.INPUT_READY
        provider = self._captcha_provider if (
            settings.ocr_enabled and self._provider_settings == self._ocr_settings(settings)
        ) else None
        if provider is None:
            current.input_state = (
                BlockedInput(context, (
                    InputBlockReason.PROVIDER_RESTART_REQUIRED
                    if not self._ocr_enabled_at_start
                    or self._provider_settings != self._ocr_settings(settings)
                    else InputBlockReason.PROVIDER_UNAVAILABLE
                ))
                if settings.ocr_enabled else WaitingManualInput(context)
            )
            return SelectionProgress.BLOCKED
        if (
            observed is None or observed.image is None or not observed.field_usable
            or not observed.field_empty or observed.dialog_visible
        ):
            current.input_state = BlockedInput(context, InputBlockReason.MISSING_FIELD)
            return SelectionProgress.BLOCKED
        if current.ocr_attempt is not None and current.ocr_attempt.image == observed.image:
            current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        image_bytes = self._captcha_bytes(observed.image_data_url)
        if not image_bytes:
            current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        attempt = CaptchaAttempt(
            InputAttemptId(uuid.uuid4().hex), context, observed.image,
            repr(self._provider_settings), time.monotonic() + 10,
        )
        current.ocr_attempt = attempt
        current.input_state = RecognizingInput(attempt)
        try:
            raw_answer = await asyncio.wait_for(
                asyncio.to_thread(provider.classification, image_bytes), 10,
            )
        except asyncio.CancelledError:
            if self._current(handle) is current:
                current.input_state = WaitingManualInput(context)
            raise
        except Exception:
            if self._current(handle) is current:
                current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        answer = self._captcha_answer(raw_answer)
        if (
            answer is None or time.monotonic() >= attempt.deadline_monotonic
            or self._current(handle) is not current
            or self._flow_context(handle) != before
            or current.active is not None
            or not isinstance(current.input_state, RecognizingInput)
            or current.input_state.attempt != attempt
            or self._purchase_authority(handle.tab) != authority
        ):
            if self._current(handle) is current:
                current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        try:
            selection_after = (
                await self._purchase_browser().observe_seat(handle.tab)
                if isinstance(plan, SeatActionPlan)
                else await self._purchase_browser().observe_quantity(handle.tab)
            )
        except Exception:
            current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        if (
            selection_after != selection
            or self._current(handle) is not current
            or self._flow_context(handle) != before
            or self._purchase_authority(handle.tab) != authority
        ):
            current.input_state = WaitingManualInput(context)
            return SelectionProgress.BLOCKED
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            context.document, before.policy_revision, before.control_revision,
        )
        claim = RunningEffect(
            ClaimId(uuid.uuid4().hex), EffectProposal(stamp, EffectKind.INPUT_WRITE)
        )
        current.input_observed = observed
        current.input_answer = answer
        current.input_state = WritingInput(attempt, claim.claim_id)
        current.active = _ActiveSelection("captcha_input", claim, plan.page_url, attempt)
        dispatched = await self._dispatch_selection(
            handle, claim,
            self._purchase_browser().commit_captcha_input(observed, attempt, answer, claim),
        )
        if not dispatched:
            active = current.active
            return (
                await self.advance_input(handle)
                if active is not None and active.receipt is not None
                and active.receipt.evidence is EffectEvidence.NO_EFFECT
                else SelectionProgress.AWAITING_EVIDENCE
            )
        return await self.advance_input(handle)

    async def _retire_unsubmitted_after_new_input(
        self, handle: SessionHandle, current: _Session,
        prior: _Submission, before: PlanningContext | None,
    ) -> bool:
        """Release a confirmed no-click attempt only for a proven new input."""
        active = current.active
        receipt = active.receipt if active is not None else None
        authority = self._purchase_authority(handle.tab)
        evidence = prior.attempt.input_evidence
        if (
            before is None or current.ready_context != before
            or active is None or active.stage != "submit"
            or prior.original is not active
            or prior.attempt.source is not SubmitSource.AUTOMATIC
            or receipt is None or receipt.evidence is not EffectEvidence.NO_EFFECT
            or authority is None or authority.auth_epoch != prior.attempt.key.auth_epoch
        ):
            return False
        try:
            observed = await self._purchase_browser().observe_captcha_reset(handle.tab)
        except Exception:
            return False
        plan = prior.attempt.plan
        if (
            self._current(handle) is not current
            or self._flow_context(handle) != before
            or current.active is not active or current.submission is not prior
            or self._purchase_authority(handle.tab) != authority
            or observed.page_url != plan.page_url
            or observed.document_id != plan.document_id
            or observed.image is None or not observed.field_usable
            or observed.dialog_visible
        ):
            return False
        new_image = observed.image != evidence.image
        manual_change = observed.manual_revision > evidence.manual_revision
        if not new_image and not manual_change:
            return False
        current.prior_submissions.append(prior)
        current.submission = None
        current.active = None
        current.manual_preparation = None
        current.input_revision += 1
        current.input_image = evidence.image
        current.input_state = None
        current.input_observed = None
        current.input_answer = None
        current.ocr_attempt = None
        current.required_manual_revision = (
            observed.manual_revision if new_image and not observed.field_empty else None
        )
        return True

    async def advance_submit(self, handle: SessionHandle) -> SelectionProgress:
        current = self._current(handle)
        if current is None:
            retired = self._retired_runs.get(handle.tab)
            if retired is not None and retired.handle == handle:
                return await self._reconcile_submit(handle, None)
            return SelectionProgress.BLOCKED
        if current.submission is not None and current.submission.terminal is not None:
            prior = current.submission
            assert prior.terminal is not None
            active = current.active
            before = self._flow_context(handle)
            unsubmitted_retired = (
                prior.terminal is SelectionProgress.SUBMIT_BLOCKED
                and await self._retire_unsubmitted_after_new_input(
                    handle, current, prior, before,
                )
            )
            if not unsubmitted_retired:
                if not (
                    prior.terminal is SelectionProgress.SUBMIT_BLOCKED
                    and prior.rejection_kind == "captcha"
                    and prior.captcha_reset_verified
                    and prior.verified_image is not None
                    and before is not None and active is not None
                    and active.stage == "captcha_reset"
                    and isinstance(active.claim, AwaitingEvidenceEffect)
                    and self._recovery_source_current(handle, before, current, active)
                    and self._recovery_dispatch_ready(handle, before, prior)
                ):
                    return prior.terminal
                # A known rejection and a proven new image close the original
                # operation. The next input and submit get fresh identities.
                current.prior_submissions.append(prior)
                current.submission = None
                current.active = None
                current.manual_preparation = None
                current.input_revision += 1
                current.input_image = prior.verified_image
                current.input_state = None
                current.input_observed = None
                current.input_answer = None
                current.ocr_attempt = None
                current.required_manual_revision = None
        if current.manual_preparation is not None:
            return await self._reconcile_manual_submit(handle, current)
        if current.active is not None and current.active.stage == "captcha_input":
            if await self.advance_input(handle) is not SelectionProgress.INPUT_READY:
                return SelectionProgress.AWAITING_EVIDENCE
        if current.active is not None and current.active.stage == "submit":
            return await self._reconcile_submit(handle, self._flow_context(handle))
        before = self._flow_context(handle)
        settings = self._flow_settings
        if before is None or settings is None:
            return SelectionProgress.BLOCKED
        if current.active is not None:
            return SelectionProgress.AWAITING_EVIDENCE
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
                and inputs.captcha_enabled
                and inputs.login_ready and inputs.button_ready
            )
            or inputs.dialog_visible
        ):
            return SelectionProgress.BLOCKED
        if not await self._autofilled_login_current(
            handle, before, current, plan, authority,
        ):
            return SelectionProgress.BLOCKED
        login_source = current.login_verified
        login_intent = login_source if isinstance(login_source, InlineLoginIntent) else None
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
        input_progress = await self.advance_input(handle)
        current = self._current(handle)
        if (
            input_progress is not SelectionProgress.INPUT_READY
            or current is None or not isinstance(current.input_state, ReadyInput)
            or self._flow_context(handle) != before
        ):
            return (
                SelectionProgress.AWAITING_EVIDENCE
                if input_progress is SelectionProgress.AWAITING_EVIDENCE
                else SelectionProgress.SUBMIT_BLOCKED
                if current is not None and current.prior_submissions
                else SelectionProgress.BLOCKED
            )
        evidence = current.input_state.evidence
        if evidence.context.auto_submit != settings.auto_submit:
            return SelectionProgress.BLOCKED
        try:
            selection_after = (
                await self._purchase_browser().observe_seat(handle.tab)
                if isinstance(plan, SeatActionPlan)
                else await self._purchase_browser().observe_quantity(handle.tab)
            )
        except Exception:
            return SelectionProgress.AWAITING_EVIDENCE
        if selection_after != selection or self._flow_context(handle) != before:
            return SelectionProgress.BLOCKED
        if not settings.auto_submit:
            try:
                key = PurchaseKey(
                    handle.tab, TenantKey(profile.key), authority.auth_epoch,
                    plan.performance_id, plan.group_id, plan.price_area_id,
                    submit_plan.page_kind,
                    plan.product_id,
                )
            except ValueError:
                return SelectionProgress.BLOCKED
            preparation = _ManualPreparation(
                submit_plan, evidence, key, "", before,
                self._policy.last_valid, self._flow_settings,
            )
            if (
                self._current(handle) is not current
                or self._flow_context(handle) != before
                or current.active is not None
                or current.ready_selection != plan
                or current.ready_context != before
                or current.login_verified != login_source
                or self._purchase_authority(handle.tab) != authority
            ):
                return SelectionProgress.BLOCKED
            current.manual_preparation = preparation
            try:
                prior_marker = (
                    current.prior_submissions[-1].attempt.manual_evidence
                    if current.prior_submissions else None
                )
                if prior_marker is None:
                    arm_token = await self._purchase_browser().arm_manual_submit(
                        handle.tab, submit_plan.page_url, submit_plan.document_id,
                    )
                else:
                    arm_token = await self._purchase_browser().arm_manual_submit(
                        handle.tab, submit_plan.page_url, submit_plan.document_id,
                        prior_marker,
                    )
            except Exception:
                arm_token = ""
            preparation.arm_token = arm_token
            owner: _Session | _RetiredOutcome | None = self._current(handle)
            if owner is None:
                retired = self._retired_runs.get(handle.tab)
                owner = retired if retired is not None and retired.handle == handle else None
            if owner is None or owner.manual_preparation is not preparation:
                return SelectionProgress.BLOCKED
            if not arm_token:
                owner.manual_preparation = None
                if (
                    isinstance(owner, _RetiredOutcome) and owner.active is None
                    and owner.submission is None and owner.refresh is None
                ):
                    self._retired_runs.pop(handle.tab, None)
                return SelectionProgress.BLOCKED
            if isinstance(owner, _Session):
                owner.input_answer = None
                owner.input_observed = None
            return SelectionProgress.AWAITING_EVIDENCE
        context = evidence.context
        captcha_observed = current.input_observed
        captcha_answer = current.input_answer
        if (
            captcha_observed is None or captcha_observed.image is None
            or captcha_observed.page_url != submit_plan.page_url
            or captcha_observed.document_id != submit_plan.document_id
            or (evidence.source is InputSource.RECOGNITION and captcha_answer is None)
        ):
            return SelectionProgress.BLOCKED
        stamp = OperationStamp(
            handle.tab, before.session_generation, OperationId(uuid.uuid4().hex),
            context.document, before.policy_revision, before.control_revision,
        )
        try:
            key = PurchaseKey(
                handle.tab, TenantKey(profile.key), authority.auth_epoch,
                plan.performance_id, plan.group_id, plan.price_area_id,
                submit_plan.page_kind,
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
            or current.login_verified != login_source
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
        current.submission = _Submission(
            attempt, current.active, time.monotonic(), self._policy.last_valid,
            self._flow_settings,
        )
        current.input_state = ConsumedInput(evidence, claim.claim_id)
        current.input_answer = None
        current.input_observed = None
        dispatched = await self._dispatch_selection(
            handle, claim, self._purchase_browser().dispatch_submit(
                submit_plan, claim, login_intent, login_credentials,
                captcha_observed, captcha_answer,
                login_source.token if isinstance(login_source, _RejectedManualLoginEvidence)
                else None,
            )
        )
        return (
            SelectionProgress.SUBMIT_DISPATCHED if dispatched
            else SelectionProgress.SUBMIT_BLOCKED
        )
