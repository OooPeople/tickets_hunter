"""Isolated host seam: read-only health precedes schedule and mutation gates."""

from __future__ import annotations

from typing import Protocol

from .engine import SessionHandle, UtikiEngine
from .refresh_contract import RefreshDocument, RefreshPhase, RefreshProgress, RefreshReason


class RefreshHealthHost(Protocol):
    async def verify_health_without_mutation(self, document: RefreshDocument) -> bool: ...


class IsolatedRefreshHost:
    def __init__(self, engine: UtikiEngine, health_host: RefreshHealthHost) -> None:
        self.engine = engine
        self.health_host = health_host

    async def tick(
        self, handle: SessionHandle, *, schedule_due: bool,
        reason: RefreshReason = RefreshReason.SCHEDULED,
        apply_local_interval: bool = True,
    ) -> RefreshProgress:
        document = await self.engine.observe_health(handle)
        if document is None:
            return RefreshProgress.BLOCKED
        self.engine.note_schedule_gate(handle, schedule_due)
        healthy = await self.health_host.verify_health_without_mutation(document)
        intent = self.engine.refresh_intent(handle)
        if intent is not None and intent.phase not in {
            RefreshPhase.ABANDONED, RefreshPhase.COMPLETE, RefreshPhase.BLOCKED_429,
        }:
            return await self.engine.advance_refresh(
                handle, intent.reason, schedule_due=schedule_due,
                apply_local_interval=intent.apply_local_interval,
            )
        if document.classification == "blocked":
            reason = RefreshReason.RATE_LIMIT_RECOVERY
        elif not healthy:
            return RefreshProgress.BLOCKED
        if reason is RefreshReason.SCHEDULED and not schedule_due:
            return RefreshProgress.WAITING
        return await self.engine.advance_refresh(
            handle, reason, schedule_due=schedule_due,
            apply_local_interval=apply_local_interval,
        )
