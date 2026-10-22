"""Immutable ownership and factual evidence for page mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .identity import DocumentKey, OperationStamp


class EffectKind(str, Enum):
    SELECT = "select"
    INPUT_WRITE = "input-write"
    CAPTCHA_RESET = "captcha-reset"
    SUBMIT = "submit"
    RELOAD = "reload"
    NAVIGATE = "navigate"
    DISMISS_DIALOG = "dismiss-dialog"


class EffectStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"
    UNKNOWN = "unknown"


class EffectEvidence(str, Enum):
    NO_EFFECT = "no-effect"
    LOCAL_MUTATION = "local-mutation"
    REQUEST_DISPATCHED = "request-dispatched"
    REQUEST_POSSIBLE = "request-possible"


class DispatchStage(str, Enum):
    BEFORE_DISPATCH = "before-dispatch"
    LOCAL_MUTATION = "local-mutation"
    REQUEST_BOUNDARY = "request-boundary"


class HeldReason(str, Enum):
    UNCERTAIN_DISPATCH = "uncertain-dispatch"
    UNMATCHED_COMPLETION = "unmatched-completion"
    CONTROL_REVOKED = "control-revoked"
    NO_EFFECT_CONFIRMED = "no-effect-confirmed"


_RECEIPT_COMBINATIONS = {
    EffectEvidence.NO_EFFECT: (
        DispatchStage.BEFORE_DISPATCH,
        frozenset({EffectStatus.REJECTED, EffectStatus.STALE}),
    ),
    EffectEvidence.LOCAL_MUTATION: (
        DispatchStage.LOCAL_MUTATION,
        frozenset(EffectStatus),
    ),
    EffectEvidence.REQUEST_DISPATCHED: (
        DispatchStage.REQUEST_BOUNDARY,
        frozenset({EffectStatus.APPLIED, EffectStatus.UNKNOWN}),
    ),
    EffectEvidence.REQUEST_POSSIBLE: (
        DispatchStage.REQUEST_BOUNDARY,
        frozenset({EffectStatus.UNKNOWN}),
    ),
}


@dataclass(frozen=True, slots=True)
class ClaimId:
    token: str

    def __post_init__(self) -> None:
        if type(self.token) is not str or not self.token.strip():
            raise ValueError("claim ID must be a nonempty string")


@dataclass(frozen=True, slots=True)
class EffectProposal:
    stamp: OperationStamp
    kind: EffectKind
    parent_recovery_id: str | None = None

    @property
    def destructive(self) -> bool:
        """Every proposal here mutates the page and requires exclusive ownership."""
        return True

    def __post_init__(self) -> None:
        if not isinstance(self.stamp, OperationStamp):
            raise ValueError("effect proposal requires an operation stamp")
        if not isinstance(self.kind, EffectKind):
            raise ValueError("effect proposal requires a known kind")
        if self.parent_recovery_id is not None and (
            type(self.parent_recovery_id) is not str or not self.parent_recovery_id.strip()
        ):
            raise ValueError("parent recovery ID must be nonempty")


def _validate_owner(claim_id: ClaimId, proposal: EffectProposal) -> None:
    if not isinstance(claim_id, ClaimId):
        raise ValueError("effect state requires a claim ID")
    if not isinstance(proposal, EffectProposal):
        raise ValueError("effect state requires a proposal")


@dataclass(frozen=True, slots=True)
class ClaimedEffect:
    claim_id: ClaimId
    proposal: EffectProposal

    def __post_init__(self) -> None:
        _validate_owner(self.claim_id, self.proposal)


@dataclass(frozen=True, slots=True)
class RunningEffect:
    claim_id: ClaimId
    proposal: EffectProposal

    def __post_init__(self) -> None:
        _validate_owner(self.claim_id, self.proposal)


@dataclass(frozen=True, slots=True)
class AwaitingEvidenceEffect:
    claim_id: ClaimId
    proposal: EffectProposal

    def __post_init__(self) -> None:
        _validate_owner(self.claim_id, self.proposal)


@dataclass(frozen=True, slots=True)
class HeldEffect:
    claim_id: ClaimId
    proposal: EffectProposal
    reason: HeldReason

    def __post_init__(self) -> None:
        _validate_owner(self.claim_id, self.proposal)
        if not isinstance(self.reason, HeldReason):
            raise ValueError("held effect requires a reason code")


ActiveEffect: TypeAlias = (
    ClaimedEffect | RunningEffect | AwaitingEvidenceEffect | HeldEffect
)


@dataclass(frozen=True, slots=True)
class EffectReceipt:
    claim_id: ClaimId
    stamp: OperationStamp
    observed_document: DocumentKey
    status: EffectStatus
    evidence: EffectEvidence
    stage: DispatchStage

    def __post_init__(self) -> None:
        if not isinstance(self.claim_id, ClaimId):
            raise ValueError("effect receipt requires a claim ID")
        if not isinstance(self.stamp, OperationStamp):
            raise ValueError("effect receipt requires an operation stamp")
        if not isinstance(self.observed_document, DocumentKey):
            raise ValueError("effect receipt requires an observed document")
        if (
            not isinstance(self.status, EffectStatus)
            or not isinstance(self.evidence, EffectEvidence)
            or not isinstance(self.stage, DispatchStage)
        ):
            raise ValueError("effect receipt requires typed status, evidence, and stage")
        expected = _RECEIPT_COMBINATIONS.get(self.evidence)
        if expected is None or self.stage != expected[0] or self.status not in expected[1]:
            raise ValueError("invalid effect status/evidence/stage combination")


def receipt_matches(owner: ActiveEffect, receipt: EffectReceipt) -> bool:
    """Correlate a completion to its original claim, not the current page."""
    return owner.claim_id == receipt.claim_id and owner.proposal.stamp == receipt.stamp
