"""Narrow browser capabilities; a port never owns session state or grants effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..domain.effects import EffectReceipt, RunningEffect
from ..domain.identity import OperationStamp, TabKey
from ..domain.plans import QuantityPlan
from ..domain.snapshots import QuantitySnapshot


class CoreGate(str, Enum):
    PASSIVE = "passive"
    HEALTH_ONLY = "health-only"
    PURCHASE_ALLOWED = "purchase-allowed"
    PAUSED = "paused"
    CLOSING = "closing"


@dataclass(frozen=True, slots=True)
class CoreControl:
    revision: int
    gate: CoreGate

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision <= 0:
            raise ValueError("core control requires a positive revision")
        if not isinstance(self.gate, CoreGate):
            raise ValueError("core control requires a known gate")


class CoreControlPort(Protocol):
    """Read the trusted in-memory gate; a closed tab remains closing here."""

    def current_control(self, tab: TabKey) -> CoreControl:
        ...


@dataclass(frozen=True, slots=True)
class QuantityObservation:
    """A browser observation attributed to its actual source context."""

    source: OperationStamp
    snapshot: QuantitySnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.source, OperationStamp) or not isinstance(self.snapshot, QuantitySnapshot):
            raise ValueError("quantity observation requires typed source and snapshot")


class QuantityBrowserPort(Protocol):
    """One observation and one already-granted quantity mutation capability."""

    async def observe_page(self, expected_context: OperationStamp) -> QuantityObservation:
        """Return typed data with the actual tab, generation, and document source."""
        ...

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        """Perform the guarded mutation and report the original claim's result."""
        ...
