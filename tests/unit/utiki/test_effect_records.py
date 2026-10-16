from dataclasses import FrozenInstanceError

import pytest

from platforms.utiki.domain.identity import DocumentKey, OperationId, OperationStamp, TabKey


def _stamp() -> OperationStamp:
    return OperationStamp(TabKey(2, "target"), 3, OperationId("op-1"), DocumentKey("doc-1"), 4, 5)


def test_claimed_effect_has_exact_owner_and_cannot_be_mutated() -> None:
    from platforms.utiki.domain.effects import ClaimId, ClaimedEffect, EffectKind, EffectProposal

    proposal = EffectProposal(_stamp(), EffectKind.SUBMIT)
    assert proposal.destructive
    claimed = ClaimedEffect(ClaimId("claim-1"), proposal)

    assert claimed.proposal.stamp.operation == OperationId("op-1")
    with pytest.raises(FrozenInstanceError):
        claimed.claim_id = ClaimId("other")  # type: ignore[misc]


def test_effect_states_cannot_be_built_without_a_claim() -> None:
    from platforms.utiki.domain.effects import (
        AwaitingEvidenceEffect,
        ClaimId,
        EffectKind,
        EffectProposal,
        HeldEffect,
        HeldReason,
        RunningEffect,
    )

    proposal = EffectProposal(_stamp(), EffectKind.SUBMIT)
    for constructor in (RunningEffect, AwaitingEvidenceEffect):
        with pytest.raises(ValueError, match="claim"):
            constructor(None, proposal)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim"):
        HeldEffect(None, proposal, HeldReason.UNCERTAIN_DISPATCH)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reason"):
        HeldEffect(ClaimId("claim-1"), proposal, "")


def test_receipt_rejects_impossible_status_and_evidence_combinations() -> None:
    from platforms.utiki.domain.effects import (
        ClaimId,
        DispatchStage,
        EffectEvidence,
        EffectReceipt,
        EffectStatus,
    )

    with pytest.raises(ValueError, match="combination"):
        EffectReceipt(
            ClaimId("claim-1"), _stamp(), DocumentKey("doc-1"),
            EffectStatus.APPLIED, EffectEvidence.NO_EFFECT, DispatchStage.BEFORE_DISPATCH,
        )
    with pytest.raises(ValueError, match="combination"):
        EffectReceipt(
            ClaimId("claim-1"), _stamp(), DocumentKey("doc-1"),
            EffectStatus.STALE, EffectEvidence.REQUEST_POSSIBLE, DispatchStage.REQUEST_BOUNDARY,
        )
    with pytest.raises(ValueError, match="typed"):
        EffectReceipt(
            ClaimId("claim-1"), _stamp(), DocumentKey("doc-1"),
            "unknown", "request-possible", "request-boundary",  # type: ignore[arg-type]
        )


def test_receipt_correlates_to_original_claim_not_current_document() -> None:
    from platforms.utiki.domain.effects import (
        ClaimId,
        ClaimedEffect,
        DispatchStage,
        EffectEvidence,
        EffectKind,
        EffectProposal,
        EffectReceipt,
        EffectStatus,
        receipt_matches,
    )

    claim = ClaimedEffect(ClaimId("claim-1"), EffectProposal(_stamp(), EffectKind.SUBMIT))
    receipt = EffectReceipt(
        ClaimId("claim-1"), _stamp(), DocumentKey("doc-after"),
        EffectStatus.UNKNOWN, EffectEvidence.REQUEST_POSSIBLE, DispatchStage.REQUEST_BOUNDARY,
    )
    wrong_claim = ClaimedEffect(ClaimId("claim-2"), claim.proposal)

    assert receipt_matches(claim, receipt)
    assert not receipt_matches(wrong_claim, receipt)
    assert receipt.observed_document == DocumentKey("doc-after")
