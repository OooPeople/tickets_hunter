"""Pure, immutable ownership records for one submit attempt."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from ..profiles import PageKind, classify_utiki_page, get_utiki_tenant_profile, unique_query
from .effects import (
    ActiveEffect,
    AwaitingEvidenceEffect,
    ClaimedEffect,
    EffectKind,
    HeldEffect,
    RunningEffect,
)
from .identity import OperationStamp, PurchaseKey
from .inputs import InputReadyEvidence, PurchaseCycleId
from .plans import QuantityPlan, SeatActionPlan, SubmitPlan, submit_plan_is_valid


@dataclass(frozen=True, slots=True)
class SubmitAttemptId:
    token: str

    def __post_init__(self) -> None:
        if type(self.token) is not str or not self.token.strip():
            raise ValueError("submit attempt ID must be nonempty")


class SubmitSource(str, Enum):
    AUTOMATIC = "automatic"
    OBSERVED_MANUAL = "observed-manual"


@dataclass(frozen=True, slots=True)
class ManualSubmitEvidence:
    """A trusted button event observed after this Run armed the source document."""

    page_url: str
    document_id: str
    arm_token: str
    click_token: str

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value.strip()
            for value in (self.page_url, self.document_id, self.arm_token, self.click_token)
        ):
            raise ValueError("manual submit requires attributed click evidence")


@dataclass(frozen=True, slots=True)
class SubmitAttempt:
    attempt_id: SubmitAttemptId
    cycle: PurchaseCycleId
    key: PurchaseKey
    stamp: OperationStamp | None
    plan: SubmitPlan
    input_evidence: InputReadyEvidence
    source: SubmitSource = SubmitSource.AUTOMATIC
    manual_evidence: ManualSubmitEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, SubmitAttemptId):
            raise ValueError("submit attempt requires an attempt ID")
        if not isinstance(self.cycle, PurchaseCycleId):
            raise ValueError("submit attempt requires a cycle")
        if not isinstance(self.key, PurchaseKey) or not isinstance(self.source, SubmitSource):
            raise ValueError("submit attempt requires a purchase key and source")
        if self.source is SubmitSource.AUTOMATIC:
            if not isinstance(self.stamp, OperationStamp) or self.manual_evidence is not None:
                raise ValueError("automatic submit requires an original operation stamp")
        elif self.stamp is not None or not isinstance(self.manual_evidence, ManualSubmitEvidence):
            raise ValueError("manual submit requires observed evidence without a claim")
        if not isinstance(self.plan, SubmitPlan) or not isinstance(self.input_evidence, InputReadyEvidence):
            raise ValueError("submit attempt requires a plan and ready input")
        if not submit_plan_is_valid(self.plan):
            raise ValueError("submit plan is invalid")
        context = self.input_evidence.context
        selection = self.plan.selection_plan
        if self.source is SubmitSource.AUTOMATIC and not context.auto_submit:
            raise ValueError("submit attempt requires enabled auto-submit policy")
        if self.cycle != context.cycle:
            raise ValueError("submit cycle differs from input context")
        if self.key.tab != context.tab or (
            self.stamp is not None and self.key.tab != self.stamp.tab
        ):
            raise ValueError("submit tab differs from purchase or input context")
        if (
            self.stamp is not None and self.stamp.document != context.document
            or self.plan.document_id != context.document.token
            or not isinstance(selection, (QuantityPlan, SeatActionPlan))
            or selection.document_id != self.plan.document_id
        ):
            raise ValueError("submit document differs from selection or input context")
        if self.manual_evidence is not None and (
            self.manual_evidence.page_url != self.plan.page_url
            or self.manual_evidence.document_id != self.plan.document_id
        ):
            raise ValueError("manual submit source differs from prepared document")
        if not isinstance(self.plan.page_kind, PageKind) or self.plan.page_kind is not self.key.page_mode:
            raise ValueError("submit page mode differs from purchase key")
        profile = get_utiki_tenant_profile(self.plan.page_url)
        if profile is None or profile.key != self.key.tenant.token:
            raise ValueError("submit tenant differs from purchase key")
        if classify_utiki_page(self.plan.page_url) is not self.key.page_mode:
            raise ValueError("submit page route differs from purchase key")
        route_query = unique_query(self.plan.page_url)
        if route_query is None or route_query.get("PERFORMANCE_ID") != self.key.performance_id:
            raise ValueError("submit route identity differs from purchase key")
        if (
            route_query.get("GROUP_ID") != self.key.group_id
            or route_query.get("PERFORMANCE_PRICE_AREA_ID") != self.key.price_area_id
        ):
            raise ValueError("submit route identity differs from purchase key")
        if (
            self.key.page_mode is PageKind.RESERVED_SEAT
            and not isinstance(selection, SeatActionPlan)
        ) or (
            self.key.page_mode in (PageKind.GENERAL_ADMISSION, PageKind.SERVER_ASSIGNED_QUANTITY)
            and not isinstance(selection, QuantityPlan)
        ):
            raise ValueError("submit selection type differs from page mode")
        if isinstance(selection, QuantityPlan) and selection.page_type != self.key.page_mode.value:
            raise ValueError("submit selection page mode differs from purchase key")
        if self.plan.page_url != selection.page_url:
            raise ValueError("submit page URL differs from selection plan")
        for name, expected in (
            ("performance_id", self.key.performance_id),
            ("group_id", self.key.group_id),
            ("price_area_id", self.key.price_area_id),
            ("product_id", self.key.product_id),
        ):
            if getattr(selection, name) != expected:
                raise ValueError(f"submit selection {name} differs from purchase key")


def _validate_submit_claim(attempt: SubmitAttempt, claim: ActiveEffect) -> None:
    if not isinstance(attempt, SubmitAttempt):
        raise ValueError("submit state requires an attempt")
    if not isinstance(claim, (ClaimedEffect, RunningEffect, AwaitingEvidenceEffect, HeldEffect)):
        raise ValueError("submit state requires a claim")
    if (
        attempt.source is not SubmitSource.AUTOMATIC
        or claim.proposal.kind is not EffectKind.SUBMIT
        or claim.proposal.stamp != attempt.stamp
    ):
        raise ValueError("submit claim differs from original attempt")


@dataclass(frozen=True, slots=True)
class PreparedSubmit:
    attempt: SubmitAttempt

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, SubmitAttempt):
            raise ValueError("prepared submit requires an attempt")


@dataclass(frozen=True, slots=True)
class ClaimedSubmit:
    attempt: SubmitAttempt
    claim: ClaimedEffect | RunningEffect

    def __post_init__(self) -> None:
        if not isinstance(self.claim, (ClaimedEffect, RunningEffect)):
            raise ValueError("claimed submit requires a claim")
        _validate_submit_claim(self.attempt, self.claim)


@dataclass(frozen=True, slots=True)
class AwaitingSubmitOutcome:
    attempt: SubmitAttempt
    claim: AwaitingEvidenceEffect
    deadline_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.claim, AwaitingEvidenceEffect):
            raise ValueError("awaiting submit requires a claim")
        _validate_submit_claim(self.attempt, self.claim)
        if (
            type(self.deadline_monotonic) not in (int, float)
            or not math.isfinite(self.deadline_monotonic)
            or self.deadline_monotonic <= 0
        ):
            raise ValueError("awaiting submit requires a finite deadline")


@dataclass(frozen=True, slots=True)
class HeldSubmitOutcome:
    attempt: SubmitAttempt
    claim: HeldEffect

    def __post_init__(self) -> None:
        if not isinstance(self.claim, HeldEffect):
            raise ValueError("held submit requires a claim")
        _validate_submit_claim(self.attempt, self.claim)


SubmitState: TypeAlias = PreparedSubmit | ClaimedSubmit | AwaitingSubmitOutcome | HeldSubmitOutcome
