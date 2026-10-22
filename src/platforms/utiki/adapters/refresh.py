"""Thin mapping to the existing SQLite refresh coordinator and global settings."""

from __future__ import annotations

from typing import Callable, cast

from global_settings import get_global_settings_path, load_global_settings
from refresh_coordinator import (
    UTK_SPORTS_SCOPE, SharedRefreshCoordinator, get_shared_refresh_coordinator,
)

from ..application.refresh_contract import (
    CommittedRefresh, CompletedProbe, RefreshSettings, ReportedRateLimit,
    ReservedRefresh,
)


class SqliteRefreshAdapter:
    def __init__(
        self, coordinator: SharedRefreshCoordinator | None = None,
        settings_reader: Callable[[bool], dict[str, object]] | None = None,
    ) -> None:
        self._coordinator = coordinator or get_shared_refresh_coordinator()
        self._settings_reader = settings_reader or self._read_settings

    @staticmethod
    def _read_settings(force: bool) -> dict[str, object]:
        value = load_global_settings(
            get_global_settings_path() if force else None,
        )["utk_sports"]
        return dict(value)

    def settings(self, *, force: bool = False) -> RefreshSettings:
        value = self._settings_reader(force)
        enabled = value["enabled"]
        minimum = value["min_interval_seconds"]
        initial = value["initial_cooldown_seconds"]
        maximum = value["max_cooldown_seconds"]
        if (type(enabled) is not bool or type(minimum) not in (int, float)
            or type(initial) not in (int, float)
            or type(maximum) not in (int, float)):
            raise ValueError("shared refresh settings returned invalid values")
        return RefreshSettings(
            enabled, float(cast(float, minimum)),
            float(cast(float, initial)), float(cast(float, maximum)),
        )

    async def reserve(self, instance_id: str, token: str, minimum: float) -> ReservedRefresh:
        result = await self._coordinator.reserve_refresh(
            UTK_SPORTS_SCOPE, instance_id,
            min_interval_seconds=minimum, jitter_range_seconds=(0.1, 0.3),
            request_token=token,
        )
        return ReservedRefresh(
            result.reserved, result.status, result.retry_after_seconds,
            result.request_id, result.reservation_id, result.is_recovery_probe,
            result.error,
        )

    async def commit(self, instance_id: str, reservation_id: str, token: str) -> CommittedRefresh:
        result = await self._coordinator.commit_refresh(
            UTK_SPORTS_SCOPE, instance_id, reservation_id,
            request_token=token,
        )
        return CommittedRefresh(
            result.granted, result.status, result.request_id, result.permit_id,
            result.is_recovery_probe, result.error,
        )

    async def release(self, instance_id: str, reservation_id: str, token: str) -> bool:
        result = await self._coordinator.release_refresh(
            UTK_SPORTS_SCOPE, instance_id, reservation_id,
            request_token=token, drop_request=True,
        )
        if type(result) is not bool:
            raise ValueError("coordinator returned invalid release outcome")
        return result

    async def cancel(self, instance_id: str, token: str) -> bool:
        result = await self._coordinator.cancel_request(
            UTK_SPORTS_SCOPE, instance_id, request_token=token,
        )
        if type(result) is not bool:
            raise ValueError("coordinator returned invalid cancel outcome")
        return result

    async def renew(self, permit_id: str) -> bool | None:
        result = await self._coordinator.renew_recovery_probe(
            UTK_SPORTS_SCOPE, permit_id,
        )
        if result is not None and type(result) is not bool:
            raise ValueError("coordinator returned invalid renewal outcome")
        return result

    async def complete_probe(self, instance_id: str, permit_id: str, success: bool) -> CompletedProbe:
        result = await self._coordinator.complete_recovery_probe(
            UTK_SPORTS_SCOPE, instance_id, permit_id, success=success,
        )
        return CompletedProbe(result.accepted, result.recovery_required, result.error)

    async def report_429(
        self, instance_id: str, started_at: float, initial: float,
        maximum: float, probe_permit_id: str | None = None,
    ) -> ReportedRateLimit:
        result = await self._coordinator.report_rate_limited(
            UTK_SPORTS_SCOPE, instance_id,
            initial_cooldown_seconds=initial,
            max_cooldown_seconds=maximum,
            probe_permit_id=probe_permit_id,
            document_started_at=started_at,
        )
        return ReportedRateLimit(
            result.accepted, result.new_incident,
            result.consecutive_incidents, result.error,
        )
