"""Exercise the quantity capability with a controlled, non-browser adapter."""

import asyncio

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
