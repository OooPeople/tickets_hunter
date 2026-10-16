"""Narrow browser capabilities; a port never owns session state or grants effects."""

from __future__ import annotations

from typing import Protocol

from ..domain.effects import EffectReceipt, RunningEffect
from ..domain.identity import OperationStamp
from ..domain.plans import QuantityPlan
from ..domain.snapshots import QuantitySnapshot


class QuantityBrowserPort(Protocol):
    """One observation and one already-granted quantity mutation capability."""

    async def observe_page(self, expected_context: OperationStamp) -> QuantitySnapshot:
        """Return a typed observation for the requested tab and document."""
        ...

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        """Perform the guarded mutation and report the original claim's result."""
        ...
