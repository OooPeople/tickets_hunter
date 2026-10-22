"""One Run's refresh evidence and the existing coordinator's narrow interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..domain.effects import EffectReceipt, RunningEffect
from ..domain.identity import TabKey


class RefreshPhase(str, Enum):
    QUEUED = "queued"
    RESERVE_UNKNOWN = "reserve-unknown"
    RESERVED = "reserved"
    COMMIT_UNKNOWN = "commit-unknown"
    PERMITTED = "permitted"
    RELOAD_POSSIBLE = "reload-possible"
    AWAITING_CLEARANCE = "awaiting-clearance"
    BLOCKED_429 = "blocked-429"
    CLEANUP_PENDING = "cleanup-pending"
    ABANDONED = "abandoned"
    COMPLETE = "complete"


class RefreshProgress(str, Enum):
    BLOCKED = "blocked"
    WAITING = "waiting"
    RESERVE_UNKNOWN = "reserve-unknown"
    COMMIT_UNKNOWN = "commit-unknown"
    SETTINGS_UNKNOWN = "settings-unknown"
    CLEANUP_PENDING = "cleanup-pending"
    RELOAD_POSSIBLE = "reload-possible"
    AWAITING_CLEARANCE = "awaiting-clearance"
    COMPLETE = "complete"


class RefreshReason(str, Enum):
    SCHEDULED = "scheduled"
    HEALTH = "health"
    RATE_LIMIT_RECOVERY = "rate-limit-recovery"
    KNOWN_REJECTION = "known-rejection"


@dataclass(slots=True)
class RefreshIntent:
    """One original request and its coordinator/browser facts within a Run."""

    token: str
    instance_id: str
    request_id: str
    source_url: str
    source_document: str
    generation: int
    policy_revision: int
    control_revision: int
    reason: RefreshReason
    apply_local_interval: bool
    eligible_at: float
    phase: RefreshPhase = RefreshPhase.QUEUED
    reservation_id: str = ""
    permit_id: str = ""
    is_probe: bool = False
    claim: RunningEffect | None = None
    receipt: EffectReceipt | None = None
    cleanup_attempts: int = 0
    next_retry_at: float = 0.0
    submitted_at: float = 0.0
    new_document: str = ""
    probe_lost: bool = False
    probe_unknown: bool = False
    probe_timeout: bool = False
    probe_started_at: float = 0.0
    next_probe_renew_at: float = 0.0
    cleanup_probe: bool = False


@dataclass(frozen=True, slots=True)
class RefreshDocument:
    tab: TabKey
    url: str
    document_id: str
    classification: str
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if (not isinstance(self.tab, TabKey) or not self.url or not self.document_id
            or self.classification not in {"normal", "blocked", "unverified", "unknown"}
            or self.started_at < 0):
            raise ValueError("refresh document requires current typed evidence")


@dataclass(frozen=True, slots=True)
class RefreshSettings:
    enabled: bool
    min_interval_seconds: float
    initial_cooldown_seconds: float
    max_cooldown_seconds: float


@dataclass(frozen=True, slots=True)
class ReservedRefresh:
    reserved: bool
    status: str
    retry_after_seconds: float
    request_id: str
    reservation_id: str
    is_recovery_probe: bool
    error: str


@dataclass(frozen=True, slots=True)
class CommittedRefresh:
    granted: bool
    status: str
    request_id: str
    permit_id: str
    is_recovery_probe: bool
    error: str


@dataclass(frozen=True, slots=True)
class ReportedRateLimit:
    accepted: bool
    new_incident: bool
    consecutive_incidents: int
    error: str


@dataclass(frozen=True, slots=True)
class CompletedProbe:
    accepted: bool
    recovery_required: bool
    error: str


class RefreshCoordinatorPort(Protocol):
    """Exact SQLite request and probe operations; no combined poll method."""

    def settings(self, *, force: bool = False) -> RefreshSettings: ...
    async def reserve(self, instance_id: str, token: str, minimum: float) -> ReservedRefresh: ...
    async def commit(self, instance_id: str, reservation_id: str, token: str) -> CommittedRefresh: ...
    async def release(self, instance_id: str, reservation_id: str, token: str) -> bool: ...
    async def cancel(self, instance_id: str, token: str) -> bool: ...
    async def renew(self, permit_id: str) -> bool | None: ...
    async def complete_probe(self, instance_id: str, permit_id: str, success: bool) -> CompletedProbe: ...
    async def report_429(
        self, instance_id: str, started_at: float, initial: float,
        maximum: float, probe_permit_id: str | None = None,
    ) -> ReportedRateLimit: ...


class RefreshBrowserPort(Protocol):
    bound_tab: TabKey
    async def observe_refresh_document(self, tab: TabKey) -> RefreshDocument: ...
    async def dispatch_reload(self, claim: RunningEffect, expected_url: str) -> EffectReceipt: ...
