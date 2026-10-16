"""Application boundary for an already-granted quantity selection."""

from __future__ import annotations

import asyncio

from .ports import QuantityBrowserPort
from ..domain.effects import EffectKind, EffectReceipt, RunningEffect, receipt_matches
from ..domain.plans import QuantityPlan


class QuantityDispatchUncertain(RuntimeError):
    """The port was invoked; only the original claim can resolve the outcome."""

    def __init__(self, claim: RunningEffect, receipt: EffectReceipt | None = None) -> None:
        self.claim = claim
        self.receipt = receipt
        super().__init__("quantity commit outcome uncertain after port invocation")


async def commit_granted_quantity(
    port: QuantityBrowserPort, plan: QuantityPlan, claim: RunningEffect
) -> EffectReceipt:
    """Dispatch one selection without claiming authority to grant or retry it."""
    if not isinstance(plan, QuantityPlan):
        raise ValueError("quantity commit requires a typed plan")
    if not isinstance(claim, RunningEffect) or claim.proposal.kind is not EffectKind.SELECT:
        raise ValueError("quantity commit requires a running selection claim")
    if claim.proposal.stamp.document.token != plan.document_id:
        raise ValueError("quantity plan differs from claim document")
    try:
        receipt = await port.commit_quantity(plan, claim)
    except asyncio.CancelledError as exc:
        raise QuantityDispatchUncertain(claim) from exc
    except Exception as exc:
        raise QuantityDispatchUncertain(claim) from exc
    if not isinstance(receipt, EffectReceipt):
        raise QuantityDispatchUncertain(claim)
    if not receipt_matches(claim, receipt):
        raise QuantityDispatchUncertain(claim, receipt)
    return receipt
