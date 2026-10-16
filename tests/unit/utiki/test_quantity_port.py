"""Exercise the quantity capability with a controlled, non-browser adapter."""

import asyncio
from dataclasses import replace

import pytest

from platforms.utiki.application.quantity import (
    QuantityDispatchUncertain,
    commit_granted_quantity,
)
from platforms.utiki.application.ports import QuantityBrowserPort
from platforms.utiki.domain.effects import (
    ClaimId,
    DispatchStage,
    EffectEvidence,
    EffectKind,
    EffectProposal,
    EffectReceipt,
    EffectStatus,
    RunningEffect,
    receipt_matches,
)
from platforms.utiki.domain.identity import DocumentKey, OperationId, OperationStamp, TabKey
from platforms.utiki.domain.plans import QuantityPlan
from platforms.utiki.domain.snapshots import QuantitySnapshot
from platforms.utiki.selection import parse_quantity_snapshot, select_quantity_snapshot
from tests.ctbc_support import make_ctbc_quantity_snapshot


class FakeQuantityBrowser:
    def __init__(self, snapshot: QuantitySnapshot) -> None:
        self.snapshot = snapshot
        self.observed: list[OperationStamp] = []
        self.committed: list[tuple[QuantityPlan, RunningEffect]] = []

    async def observe_page(self, expected_context: OperationStamp) -> QuantitySnapshot:
        self.observed.append(expected_context)
        return self.snapshot

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        self.committed.append((plan, claim))
        return EffectReceipt(
            claim.claim_id,
            claim.proposal.stamp,
            claim.proposal.stamp.document,
            EffectStatus.APPLIED,
            EffectEvidence.LOCAL_MUTATION,
            DispatchStage.LOCAL_MUTATION,
        )


async def _exercise(port: QuantityBrowserPort, stamp: OperationStamp) -> None:
    observed = await port.observe_page(stamp)
    plan = select_quantity_snapshot(observed, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    assert plan.document_id == stamp.document.token
    claim = RunningEffect(ClaimId("quantity-claim"), EffectProposal(stamp, EffectKind.SELECT))
    receipt = await port.commit_quantity(plan, claim)
    assert isinstance(receipt, EffectReceipt)
    assert receipt_matches(claim, receipt)
    assert receipt.status is EffectStatus.APPLIED
    assert receipt.evidence is EffectEvidence.LOCAL_MUTATION


async def _exercise_application(port: QuantityBrowserPort, stamp: OperationStamp) -> None:
    observed = await port.observe_page(stamp)
    plan = select_quantity_snapshot(observed, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    claim = RunningEffect(ClaimId("application-claim"), EffectProposal(stamp, EffectKind.SELECT))
    receipt = await commit_granted_quantity(port, plan, claim)
    assert receipt_matches(claim, receipt)
    assert receipt.status is EffectStatus.APPLIED


def test_quantity_fake_observation_plan_claim_and_receipt() -> None:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    fake = FakeQuantityBrowser(snapshot)
    stamp = OperationStamp(
        TabKey(1, "tab-1"), 2, OperationId("quantity-op"),
        DocumentKey(snapshot.document_id), 3, 4,
    )

    asyncio.run(_exercise(fake, stamp))

    assert fake.observed == [stamp]
    assert len(fake.committed) == 1
    assert fake.committed[0][0].target_key == "FULL"
    assert fake.committed[0][1].proposal.stamp == stamp


def test_quantity_application_requires_running_matching_claim() -> None:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    plan = select_quantity_snapshot(snapshot, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    fake = FakeQuantityBrowser(snapshot)
    stamp = OperationStamp(
        TabKey(1, "tab-1"), 2, OperationId("quantity-op"),
        DocumentKey(snapshot.document_id), 3, 4,
    )
    asyncio.run(_exercise_application(fake, stamp))
    assert len(fake.committed) == 1

    wrong_kind = RunningEffect(ClaimId("other"), EffectProposal(stamp, EffectKind.SUBMIT))
    with pytest.raises(ValueError, match="running selection claim"):
        asyncio.run(commit_granted_quantity(fake, plan, wrong_kind))
    wrong_document = RunningEffect(
        ClaimId("other"),
        EffectProposal(replace(stamp, document=DocumentKey("other")), EffectKind.SELECT),
    )
    with pytest.raises(ValueError, match="claim document"):
        asyncio.run(commit_granted_quantity(fake, plan, wrong_document))
    assert len(fake.committed) == 1


def test_quantity_application_rejects_receipt_from_another_claim() -> None:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    plan = select_quantity_snapshot(snapshot, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    stamp = OperationStamp(
        TabKey(1, "tab-1"), 2, OperationId("quantity-op"),
        DocumentKey(snapshot.document_id), 3, 4,
    )
    claim = RunningEffect(ClaimId("actual"), EffectProposal(stamp, EffectKind.SELECT))

    class WrongReceipt(FakeQuantityBrowser):
        async def commit_quantity(
            self, committed_plan: QuantityPlan, committed_claim: RunningEffect
        ) -> EffectReceipt:
            receipt = await super().commit_quantity(committed_plan, committed_claim)
            return replace(receipt, claim_id=ClaimId("unrelated"))

    fake = WrongReceipt(snapshot)
    with pytest.raises(QuantityDispatchUncertain) as error:
        asyncio.run(commit_granted_quantity(fake, plan, claim))
    assert fake.committed == [(plan, claim)]
    assert error.value.claim is claim
    assert isinstance(error.value.receipt, EffectReceipt)
    assert error.value.receipt.claim_id == ClaimId("unrelated")


def test_quantity_application_preserves_claim_after_port_exception() -> None:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    plan = select_quantity_snapshot(snapshot, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    stamp = OperationStamp(
        TabKey(1, "tab-1"), 2, OperationId("quantity-op"),
        DocumentKey(snapshot.document_id), 3, 4,
    )
    claim = RunningEffect(ClaimId("actual"), EffectProposal(stamp, EffectKind.SELECT))

    class FailingAfterCommit(FakeQuantityBrowser):
        async def commit_quantity(
            self, committed_plan: QuantityPlan, committed_claim: RunningEffect
        ) -> EffectReceipt:
            self.committed.append((committed_plan, committed_claim))
            raise ValueError("transport lost after mutation")

    fake = FailingAfterCommit(snapshot)
    with pytest.raises(QuantityDispatchUncertain) as error:
        asyncio.run(commit_granted_quantity(fake, plan, claim))
    assert fake.committed == [(plan, claim)]
    assert error.value.claim is claim
    assert error.value.receipt is None
    assert isinstance(error.value.__cause__, ValueError)


def test_quantity_application_preserves_claim_after_port_cancellation() -> None:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    plan = select_quantity_snapshot(snapshot, "全票", 2)
    assert isinstance(plan, QuantityPlan)
    stamp = OperationStamp(
        TabKey(1, "tab-1"), 2, OperationId("quantity-op"),
        DocumentKey(snapshot.document_id), 3, 4,
    )
    claim = RunningEffect(ClaimId("actual"), EffectProposal(stamp, EffectKind.SELECT))

    class CancelAfterCommit(FakeQuantityBrowser):
        async def commit_quantity(
            self, committed_plan: QuantityPlan, committed_claim: RunningEffect
        ) -> EffectReceipt:
            self.committed.append((committed_plan, committed_claim))
            raise asyncio.CancelledError

    fake = CancelAfterCommit(snapshot)
    with pytest.raises(QuantityDispatchUncertain) as error:
        asyncio.run(commit_granted_quantity(fake, plan, claim))
    assert fake.committed == [(plan, claim)]
    assert error.value.claim is claim
    assert error.value.receipt is None
    assert isinstance(error.value.__cause__, asyncio.CancelledError)
