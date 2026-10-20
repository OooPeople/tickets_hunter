"""Cross-process refresh pacing for ticketing platform families.

The coordinator deliberately exposes polling APIs instead of sleeping until a
permit is available.  Callers can therefore keep their main loop responsive to
pause/stop requests while SQLite provides the short, atomic critical section
needed by independent bot processes.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import util


UTK_SPORTS_SCOPE = "utk-sports"
_SCHEMA_VERSION = 4


@dataclass(frozen=True)
class OpeningBurstRequest:
    """One process's request to join a scheduled first-pass campaign."""

    campaign_id: str
    participant_id: str
    scheduled_at: float
    window_until: float
    interval_seconds: float
    max_participants: int


@dataclass(frozen=True)
class RefreshDecision:
    """Result of one non-blocking refresh-permit poll."""

    granted: bool
    status: str
    retry_after_seconds: float
    request_id: str
    permit_id: str = ""
    sequence: int = 0
    is_recovery_probe: bool = False
    is_opening_burst: bool = False
    campaign_id: str = ""
    dispatch_token: str = ""
    dispatch_expires_at: float = 0.0
    cooldown_until: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class RefreshReservation:
    """Result of reserving the FIFO head before browser-side preflight."""

    reserved: bool
    status: str
    retry_after_seconds: float
    request_id: str
    reservation_id: str = ""
    is_recovery_probe: bool = False
    is_opening_burst: bool = False
    campaign_id: str = ""
    expires_at: float = 0.0
    cooldown_until: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class RateLimitReport:
    """Result of recording one HTTP 429/rate-limit observation."""

    accepted: bool
    new_incident: bool
    consecutive_incidents: int
    cooldown_until: float
    retry_after_seconds: float
    error: str = ""


@dataclass(frozen=True)
class ProbeCompletion:
    """Result of completing a recovery-probe lease."""

    accepted: bool
    recovery_required: bool
    error: str = ""


class SharedRefreshCoordinator:
    """SQLite-backed FIFO refresh coordinator shared by local processes.

    A coordinator object represents one process owner.  Multiple objects and
    processes may point at the same database.  All state transitions use
    ``BEGIN IMMEDIATE`` transactions; a process crash releases SQLite's file
    lock and rolls back the unfinished transaction.
    """

    def __init__(
        self,
        database_path: Optional[os.PathLike[str] | str] = None,
        *,
        owner_token: Optional[str] = None,
        clock: Callable[[], float] = time.time,
        random_uniform: Callable[[float, float], float] = random.SystemRandom().uniform,
        stale_request_seconds: float = 5.0,
        reservation_lease_seconds: float = 5.0,
        opening_reservation_lease_seconds: float = 0.75,
        commit_receipt_seconds: float = 300.0,
        recovery_probe_lease_seconds: float = 30.0,
        incident_dedupe_seconds: float = 10.0,
        incident_reset_seconds: float = 900.0,
        poll_retry_seconds: float = 0.25,
        database_timeout_seconds: float = 0.5,
        fail_closed_retry_seconds: float = 5.0,
        dispatch_lease_seconds: float = 1.0,
        claimed_dispatch_lease_seconds: float = 2.0,
    ):
        if database_path is None:
            database_path = (
                Path(util.get_app_root())
                / ".runtime"
                / "refresh_coordinator.sqlite3"
            )
        self.database_path = Path(database_path)
        self.owner_token = owner_token or f"{os.getpid()}-{uuid.uuid4().hex}"
        self._clock = clock
        self._random_uniform = random_uniform
        self.stale_request_seconds = max(float(stale_request_seconds), 0.1)
        self.reservation_lease_seconds = max(
            float(reservation_lease_seconds), 0.1
        )
        self.opening_reservation_lease_seconds = min(
            self.reservation_lease_seconds,
            max(float(opening_reservation_lease_seconds), 0.1),
        )
        self.commit_receipt_seconds = max(float(commit_receipt_seconds), 1.0)
        self.recovery_probe_lease_seconds = max(
            float(recovery_probe_lease_seconds), 0.1
        )
        self.incident_dedupe_seconds = max(float(incident_dedupe_seconds), 0.0)
        self.incident_reset_seconds = max(
            float(incident_reset_seconds), self.incident_dedupe_seconds
        )
        self.poll_retry_seconds = max(float(poll_retry_seconds), 0.01)
        self.database_timeout_seconds = max(float(database_timeout_seconds), 0.01)
        self.fail_closed_retry_seconds = max(float(fail_closed_retry_seconds), 0.1)
        self.dispatch_lease_seconds = max(float(dispatch_lease_seconds), 0.25)
        self.claimed_dispatch_lease_seconds = max(
            float(claimed_dispatch_lease_seconds),
            self.dispatch_lease_seconds,
        )

    def _request_id(
        self,
        scope: str,
        instance_id: str,
        request_token: Optional[str] = None,
    ) -> str:
        # owner_token contains a per-process UUID.  A caller-supplied token
        # distinguishes successive logical requests from the same browser tab
        # so an asynchronous cancel for the old request cannot delete the new
        # FIFO entry (the classic ABA race).  Omitting it keeps the public API
        # backwards compatible for simple callers and tests.
        suffix = f":{request_token}" if request_token else ""
        return f"{self.owner_token}:{scope}:{instance_id}{suffix}"

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.database_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            f"PRAGMA busy_timeout={int(self.database_timeout_seconds * 1000)}"
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        # Schema discovery and additive migration must be one writer-critical
        # section. Two processes can start together against an old database;
        # without this lock both may observe the same missing column and race
        # the ALTER TABLE.
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._ensure_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version > _SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"unsupported refresh coordinator schema version {user_version}"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_bucket (
                scope TEXT PRIMARY KEY,
                next_allowed_at REAL NOT NULL DEFAULT 0,
                cooldown_until REAL NOT NULL DEFAULT 0,
                consecutive_429 INTEGER NOT NULL DEFAULT 0,
                last_429_at REAL NOT NULL DEFAULT 0,
                recovery_required INTEGER NOT NULL DEFAULT 0,
                probe_owner TEXT NOT NULL DEFAULT '',
                probe_expires_at REAL NOT NULL DEFAULT 0,
                last_recovery_at REAL NOT NULL DEFAULT 0,
                last_dispatch_at REAL NOT NULL DEFAULT 0,
                last_dispatch_gap REAL NOT NULL DEFAULT 0,
                sequence INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_queue (
                scope TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                owner_token TEXT NOT NULL,
                process_id INTEGER NOT NULL,
                enqueued_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                min_interval_seconds REAL NOT NULL,
                jitter_min_seconds REAL NOT NULL,
                jitter_max_seconds REAL NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '',
                campaign_participant_id TEXT NOT NULL DEFAULT '',
                is_opening_burst INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scope, instance_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS refresh_queue_fifo "
            "ON refresh_queue(scope, enqueued_at, request_id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_reservation (
                scope TEXT PRIMARY KEY,
                reservation_id TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL UNIQUE,
                owner_token TEXT NOT NULL,
                reserved_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                is_recovery_probe INTEGER NOT NULL DEFAULT 0,
                campaign_id TEXT NOT NULL DEFAULT '',
                campaign_participant_id TEXT NOT NULL DEFAULT '',
                is_opening_burst INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_commit_receipt (
                reservation_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                request_id TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                permit_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                is_recovery_probe INTEGER NOT NULL DEFAULT 0,
                is_opening_burst INTEGER NOT NULL DEFAULT 0,
                campaign_id TEXT NOT NULL DEFAULT '',
                dispatch_token TEXT NOT NULL DEFAULT '',
                dispatched_at REAL NOT NULL DEFAULT 0,
                dispatch_status TEXT NOT NULL DEFAULT 'authorized',
                committed_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS refresh_commit_receipt_expiry "
            "ON refresh_commit_receipt(expires_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_opening_campaign (
                scope TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                scheduled_at REAL NOT NULL,
                window_until REAL NOT NULL,
                interval_seconds REAL NOT NULL,
                max_participants INTEGER NOT NULL,
                grant_count INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                ended_at REAL NOT NULL DEFAULT 0,
                end_reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (scope, campaign_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS refresh_opening_campaign_state "
            "ON refresh_opening_campaign(scope, state, window_until)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_opening_grant (
                scope TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                permit_id TEXT NOT NULL,
                granted_at REAL NOT NULL,
                PRIMARY KEY (scope, campaign_id, participant_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_dispatch_lease (
                scope TEXT PRIMARY KEY,
                permit_id TEXT NOT NULL UNIQUE,
                dispatch_token TEXT NOT NULL UNIQUE,
                owner_token TEXT NOT NULL,
                request_id TEXT NOT NULL,
                effective_interval_seconds REAL NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '',
                is_opening_burst INTEGER NOT NULL DEFAULT 0,
                authorized_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        bucket_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(refresh_bucket)")
        }
        if "last_recovery_at" not in bucket_columns:
            connection.execute(
                "ALTER TABLE refresh_bucket "
                "ADD COLUMN last_recovery_at REAL NOT NULL DEFAULT 0"
            )
        if "last_dispatch_at" not in bucket_columns:
            connection.execute(
                "ALTER TABLE refresh_bucket "
                "ADD COLUMN last_dispatch_at REAL NOT NULL DEFAULT 0"
            )
        if "last_dispatch_gap" not in bucket_columns:
            connection.execute(
                "ALTER TABLE refresh_bucket "
                "ADD COLUMN last_dispatch_gap REAL NOT NULL DEFAULT 0"
            )
        additive_columns = {
            "refresh_queue": (
                ("campaign_id", "TEXT NOT NULL DEFAULT ''"),
                ("campaign_participant_id", "TEXT NOT NULL DEFAULT ''"),
                ("is_opening_burst", "INTEGER NOT NULL DEFAULT 0"),
            ),
            "refresh_reservation": (
                ("campaign_id", "TEXT NOT NULL DEFAULT ''"),
                ("campaign_participant_id", "TEXT NOT NULL DEFAULT ''"),
                ("is_opening_burst", "INTEGER NOT NULL DEFAULT 0"),
            ),
            "refresh_commit_receipt": (
                ("is_opening_burst", "INTEGER NOT NULL DEFAULT 0"),
                ("campaign_id", "TEXT NOT NULL DEFAULT ''"),
                ("dispatch_token", "TEXT NOT NULL DEFAULT ''"),
                ("dispatched_at", "REAL NOT NULL DEFAULT 0"),
                ("dispatch_status", "TEXT NOT NULL DEFAULT 'authorized'"),
            ),
        }
        for table_name, columns in additive_columns.items():
            existing_columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            for column_name, definition in columns:
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN "
                        f"{column_name} {definition}"
                    )
        if user_version < _SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    @staticmethod
    def _validate_identity(scope: str, instance_id: str) -> tuple[str, str]:
        scope_value = str(scope or "").strip()
        instance_value = str(instance_id or "").strip()
        if not scope_value or len(scope_value) > 128:
            raise ValueError("scope must contain 1-128 characters")
        if not instance_value or len(instance_value) > 128:
            raise ValueError("instance_id must contain 1-128 characters")
        return scope_value, instance_value

    @staticmethod
    def _validate_pacing(
        min_interval_seconds: float,
        jitter_range_seconds: Sequence[float],
    ) -> tuple[float, float, float]:
        min_interval = float(min_interval_seconds)
        if min_interval < 0:
            raise ValueError("min_interval_seconds must not be negative")
        if len(jitter_range_seconds) != 2:
            raise ValueError("jitter_range_seconds must contain two values")
        jitter_min = float(jitter_range_seconds[0])
        jitter_max = float(jitter_range_seconds[1])
        if jitter_min < 0 or jitter_max < jitter_min:
            raise ValueError("invalid jitter range")
        return min_interval, jitter_min, jitter_max

    @staticmethod
    def _bucket(connection: sqlite3.Connection, scope: str, now: float) -> sqlite3.Row:
        connection.execute(
            "INSERT OR IGNORE INTO refresh_bucket(scope, updated_at) VALUES (?, ?)",
            (scope, now),
        )
        return connection.execute(
            "SELECT * FROM refresh_bucket WHERE scope = ?", (scope,)
        ).fetchone()

    def _error_decision(self, request_id: str, exc: Exception) -> RefreshDecision:
        return RefreshDecision(
            granted=False,
            status="error",
            retry_after_seconds=self.fail_closed_retry_seconds,
            request_id=request_id,
            error=f"{type(exc).__name__}: {exc}",
        )

    def _error_reservation(
        self, request_id: str, exc: Exception
    ) -> RefreshReservation:
        return RefreshReservation(
            reserved=False,
            status="error",
            retry_after_seconds=self.fail_closed_retry_seconds,
            request_id=request_id,
            error=f"{type(exc).__name__}: {exc}",
        )

    @staticmethod
    def _validate_request_token(request_token: Optional[str]) -> Optional[str]:
        if request_token is None:
            return None
        value = str(request_token).strip()
        if not value or len(value) > 128:
            raise ValueError("request_token must contain 1-128 characters")
        return value

    @staticmethod
    def _validate_opening_burst(
        opening_burst: Optional[OpeningBurstRequest],
    ) -> Optional[OpeningBurstRequest]:
        if opening_burst is None:
            return None
        if not isinstance(opening_burst, OpeningBurstRequest):
            raise ValueError("opening_burst must be an OpeningBurstRequest")
        campaign_id = str(opening_burst.campaign_id or "").strip()
        participant_id = str(opening_burst.participant_id or "").strip()
        if not campaign_id or len(campaign_id) > 128:
            raise ValueError("opening burst campaign_id must contain 1-128 characters")
        if not participant_id or len(participant_id) > 128:
            raise ValueError(
                "opening burst participant_id must contain 1-128 characters"
            )
        scheduled_at = float(opening_burst.scheduled_at)
        window_until = float(opening_burst.window_until)
        interval = float(opening_burst.interval_seconds)
        maximum = opening_burst.max_participants
        if (
            not all(math.isfinite(item) for item in (
                scheduled_at, window_until, interval
            ))
            or window_until <= scheduled_at
            or interval <= 0
        ):
            raise ValueError("invalid opening burst timing")
        if isinstance(maximum, bool) or int(maximum) != maximum:
            raise ValueError("opening burst max_participants must be an integer")
        maximum = int(maximum)
        if maximum < 1 or maximum > 16:
            raise ValueError("opening burst max_participants must be 1-16")
        if window_until - scheduled_at < (maximum - 1) * interval:
            raise ValueError("opening burst window is too short")
        return OpeningBurstRequest(
            campaign_id=campaign_id,
            participant_id=participant_id,
            scheduled_at=scheduled_at,
            window_until=window_until,
            interval_seconds=interval,
            max_participants=maximum,
        )

    def _cleanup_expired_dispatches(
        self, connection: sqlite3.Connection, scope: str, now: float
    ) -> None:
        leases = connection.execute(
            "SELECT * FROM refresh_dispatch_lease "
            "WHERE scope = ? AND expires_at <= ?",
            (scope, now),
        ).fetchall()
        for lease in leases:
            bucket = self._bucket(connection, scope, now)
            assumed_dispatch_at = float(lease["expires_at"])
            effective_gap = float(lease["effective_interval_seconds"])
            previous_dispatch_at = float(bucket["last_dispatch_at"])
            last_dispatch_at = max(previous_dispatch_at, assumed_dispatch_at)
            last_dispatch_gap = (
                effective_gap
                if assumed_dispatch_at >= previous_dispatch_at
                else float(bucket["last_dispatch_gap"])
            )
            next_allowed_at = max(
                float(bucket["next_allowed_at"]),
                assumed_dispatch_at + effective_gap,
            )
            connection.execute(
                """
                UPDATE refresh_bucket
                SET last_dispatch_at = ?, last_dispatch_gap = ?,
                    next_allowed_at = ?, updated_at = ?
                WHERE scope = ?
                """,
                (
                    last_dispatch_at,
                    last_dispatch_gap,
                    next_allowed_at,
                    now,
                    scope,
                ),
            )
            connection.execute(
                """
                UPDATE refresh_commit_receipt
                SET dispatched_at = CASE WHEN dispatched_at > 0
                                         THEN dispatched_at ELSE ? END,
                    dispatch_status = CASE WHEN dispatched_at > 0
                                           THEN dispatch_status
                                           ELSE 'expired_uncertain' END
                WHERE permit_id = ? AND dispatch_token = ?
                """,
                (
                    assumed_dispatch_at,
                    str(lease["permit_id"]),
                    str(lease["dispatch_token"]),
                ),
            )
        if leases:
            connection.execute(
                "DELETE FROM refresh_dispatch_lease "
                "WHERE scope = ? AND expires_at <= ?",
                (scope, now),
            )

    @staticmethod
    def _cleanup_expired_reservations(
        connection: sqlite3.Connection, scope: str, now: float
    ) -> None:
        """Drop an abandoned burst head as soon as its short lease expires."""

        expired_burst = connection.execute(
            """
            SELECT request_id FROM refresh_reservation
            WHERE scope = ? AND expires_at <= ? AND is_opening_burst = 1
            """,
            (scope, now),
        ).fetchall()
        for row in expired_burst:
            connection.execute(
                "DELETE FROM refresh_queue WHERE scope = ? AND request_id = ?",
                (scope, str(row["request_id"])),
            )
        connection.execute(
            "DELETE FROM refresh_reservation WHERE scope = ? AND expires_at <= ?",
            (scope, now),
        )

    @staticmethod
    def _refresh_opening_campaigns(
        connection: sqlite3.Connection, scope: str, now: float
    ) -> None:
        connection.execute(
            """
            UPDATE refresh_opening_campaign
            SET state = 'expired', ended_at = ?, updated_at = ?,
                end_reason = CASE WHEN end_reason = '' THEN 'window_expired'
                                  ELSE end_reason END
            WHERE scope = ? AND state IN ('active', 'exhausted')
              AND window_until <= ?
            """,
            (now, now, scope, now),
        )
        connection.execute(
            """
            UPDATE refresh_queue
            SET campaign_id = '', campaign_participant_id = '',
                is_opening_burst = 0
            WHERE scope = ? AND campaign_id <> ''
              AND campaign_id NOT IN (
                  SELECT campaign_id FROM refresh_opening_campaign
                  WHERE scope = ? AND state IN ('active', 'exhausted')
                    AND window_until > ?
              )
            """,
            (scope, scope, now),
        )
        retention_cutoff = now - 604800.0
        connection.execute(
            """
            DELETE FROM refresh_opening_grant
            WHERE scope = ? AND campaign_id IN (
                SELECT campaign_id FROM refresh_opening_campaign
                WHERE scope = ? AND window_until < ?
            )
            """,
            (scope, scope, retention_cutoff),
        )
        connection.execute(
            "DELETE FROM refresh_opening_campaign "
            "WHERE scope = ? AND window_until < ?",
            (scope, retention_cutoff),
        )

    @staticmethod
    def _active_opening_campaign(
        connection: sqlite3.Connection, scope: str, now: float
    ) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM refresh_opening_campaign
            WHERE scope = ? AND state IN ('active', 'exhausted')
              AND scheduled_at <= ? AND window_until > ?
            ORDER BY scheduled_at ASC, campaign_id ASC
            LIMIT 1
            """,
            (scope, now, now),
        ).fetchone()

    def _prepare_opening_burst(
        self,
        connection: sqlite3.Connection,
        scope: str,
        request: Optional[OpeningBurstRequest],
        bucket: sqlite3.Row,
        now: float,
    ) -> tuple[Optional[sqlite3.Row], bool, str]:
        if request is None or now < request.scheduled_at or now >= request.window_until:
            return None, False, ""
        campaign = connection.execute(
            "SELECT * FROM refresh_opening_campaign "
            "WHERE scope = ? AND campaign_id = ?",
            (scope, request.campaign_id),
        ).fetchone()
        if campaign is None:
            overlap = connection.execute(
                """
                SELECT campaign_id FROM refresh_opening_campaign
                WHERE scope = ? AND state IN ('active', 'exhausted')
                  AND scheduled_at < ? AND window_until > ?
                LIMIT 1
                """,
                (scope, request.window_until, request.scheduled_at),
            ).fetchone()
            if overlap is not None:
                return None, False, "campaign_overlap"
            blocked = (
                float(bucket["cooldown_until"]) > now
                or bool(bucket["recovery_required"])
            )
            state = "aborted" if blocked else "active"
            reason = "rate_limit_recovery" if blocked else ""
            connection.execute(
                """
                INSERT INTO refresh_opening_campaign(
                    scope, campaign_id, scheduled_at, window_until,
                    interval_seconds, max_participants, grant_count, state,
                    created_at, updated_at, ended_at, end_reason
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    scope,
                    request.campaign_id,
                    request.scheduled_at,
                    request.window_until,
                    request.interval_seconds,
                    request.max_participants,
                    state,
                    now,
                    now,
                    now if blocked else 0.0,
                    reason,
                ),
            )
            campaign = connection.execute(
                "SELECT * FROM refresh_opening_campaign "
                "WHERE scope = ? AND campaign_id = ?",
                (scope, request.campaign_id),
            ).fetchone()
        same_spec = bool(
            campaign
            and float(campaign["scheduled_at"]) == request.scheduled_at
            and float(campaign["window_until"]) == request.window_until
            and float(campaign["interval_seconds"]) == request.interval_seconds
            and int(campaign["max_participants"]) == request.max_participants
        )
        if not same_spec:
            return campaign, False, "campaign_conflict"
        if str(campaign["state"]) != "active":
            return campaign, False, str(campaign["state"])
        already_granted = connection.execute(
            """
            SELECT 1 FROM refresh_opening_grant
            WHERE scope = ? AND campaign_id = ? AND participant_id = ?
            """,
            (scope, request.campaign_id, request.participant_id),
        ).fetchone()
        eligible = (
            already_granted is None
            and int(campaign["grant_count"]) < int(campaign["max_participants"])
        )
        return campaign, eligible, "" if eligible else "already_served"

    def reserve_refresh_sync(
        self,
        scope: str,
        instance_id: str,
        *,
        min_interval_seconds: float,
        jitter_range_seconds: Sequence[float] = (0.1, 0.3),
        request_token: Optional[str] = None,
        opening_burst: Optional[OpeningBurstRequest] = None,
    ) -> RefreshReservation:
        """Reserve the FIFO head without yet authorizing a network request.

        The queue row remains present and pacing is not consumed until
        :meth:`commit_refresh_sync` succeeds.  This lets callers perform DOM and
        pause-state preflight before the final, short SQLite critical section.
        """

        request_id = self._request_id(
            str(scope or ""), str(instance_id or ""), request_token
        )
        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            request_token = self._validate_request_token(request_token)
            min_interval, jitter_min, jitter_max = self._validate_pacing(
                min_interval_seconds, jitter_range_seconds
            )
            opening_burst = self._validate_opening_burst(opening_burst)
            request_id = self._request_id(scope, instance_id, request_token)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                # The transaction lock is the linearization point.  Re-read
                # wall time after acquiring it so a request that waited across
                # the scheduled boundary is classified in the correct phase.
                now = float(self._clock())
                bucket = self._bucket(connection, scope, now)
                self._cleanup_expired_dispatches(connection, scope, now)
                self._refresh_opening_campaigns(connection, scope, now)
                bucket = self._bucket(connection, scope, now)
                connection.execute(
                    "DELETE FROM refresh_commit_receipt WHERE expires_at <= ?",
                    (now,),
                )
                self._cleanup_expired_reservations(connection, scope, now)
                # A live reservation protects its queue row even when a slow
                # browser preflight outlives the normal heartbeat interval.
                connection.execute(
                    """
                    DELETE FROM refresh_queue
                    WHERE scope = ? AND heartbeat_at < ?
                      AND request_id NOT IN (
                          SELECT request_id FROM refresh_reservation
                          WHERE scope = ? AND expires_at > ?
                      )
                    """,
                    (scope, now - self.stale_request_seconds, scope, now),
                )

                if (
                    opening_burst is not None
                    and now < opening_burst.scheduled_at
                ):
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="opening_not_started",
                        retry_after_seconds=max(
                            0.01,
                            min(
                                self.poll_retry_seconds,
                                opening_burst.scheduled_at - now,
                            ),
                        ),
                        request_id=request_id,
                    )

                campaign, burst_eligible, _ = self._prepare_opening_burst(
                    connection, scope, opening_burst, bucket, now
                )
                campaign_id = (
                    str(campaign["campaign_id"])
                    if campaign is not None and opening_burst is not None
                    else ""
                )
                participant_id = (
                    opening_burst.participant_id
                    if campaign_id and opening_burst is not None
                    else ""
                )

                existing = connection.execute(
                    "SELECT * FROM refresh_queue WHERE scope = ? AND instance_id = ?",
                    (scope, instance_id),
                ).fetchone()
                if existing is not None and existing["owner_token"] != self.owner_token:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="instance_busy",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )

                if existing is not None and existing["request_id"] != request_id:
                    active_old = connection.execute(
                        """
                        SELECT 1 FROM refresh_reservation
                        WHERE scope = ? AND request_id = ? AND owner_token = ?
                          AND expires_at > ?
                        """,
                        (scope, existing["request_id"], self.owner_token, now),
                    ).fetchone()
                    if active_old is not None:
                        connection.commit()
                        return RefreshReservation(
                            reserved=False,
                            status="reservation_active",
                            retry_after_seconds=self.poll_retry_seconds,
                            request_id=request_id,
                        )
                    connection.execute(
                        "DELETE FROM refresh_queue WHERE request_id = ? "
                        "AND owner_token = ?",
                        (existing["request_id"], self.owner_token),
                    )
                    existing = None

                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO refresh_queue(
                            scope, instance_id, request_id, owner_token, process_id,
                            enqueued_at, heartbeat_at, min_interval_seconds,
                            jitter_min_seconds, jitter_max_seconds, campaign_id,
                            campaign_participant_id, is_opening_burst
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope,
                            instance_id,
                            request_id,
                            self.owner_token,
                            os.getpid(),
                            now,
                            now,
                            min_interval,
                            jitter_min,
                            jitter_max,
                            campaign_id if burst_eligible else "",
                            participant_id if burst_eligible else "",
                            int(burst_eligible),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE refresh_queue
                        SET heartbeat_at = ?, min_interval_seconds = ?,
                            jitter_min_seconds = ?, jitter_max_seconds = ?,
                            campaign_id = ?, campaign_participant_id = ?,
                            is_opening_burst = ?
                        WHERE scope = ? AND instance_id = ? AND owner_token = ?
                        """,
                        (
                            now,
                            min_interval,
                            jitter_min,
                            jitter_max,
                            campaign_id if burst_eligible else "",
                            participant_id if burst_eligible else "",
                            int(burst_eligible),
                            scope,
                            instance_id,
                            self.owner_token,
                        ),
                    )

                live_dispatch = connection.execute(
                    "SELECT expires_at FROM refresh_dispatch_lease WHERE scope = ?",
                    (scope,),
                ).fetchone()
                if live_dispatch is not None:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="dispatch_active",
                        retry_after_seconds=max(
                            0.01,
                            min(
                                self.poll_retry_seconds,
                                float(live_dispatch["expires_at"]) - now,
                            ),
                        ),
                        request_id=request_id,
                    )

                cooldown_until = float(bucket["cooldown_until"])
                if cooldown_until > now:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="cooldown",
                        retry_after_seconds=max(
                            0.01, min(self.poll_retry_seconds, cooldown_until - now)
                        ),
                        request_id=request_id,
                        cooldown_until=cooldown_until,
                    )

                if bucket["probe_owner"] and float(bucket["probe_expires_at"]) <= now:
                    connection.execute(
                        """
                        UPDATE refresh_bucket
                        SET probe_owner = '', probe_expires_at = 0, updated_at = ?
                        WHERE scope = ?
                        """,
                        (now, scope),
                    )
                    bucket = self._bucket(connection, scope, now)

                probe_owner = str(bucket["probe_owner"] or "")
                probe_expires_at = float(bucket["probe_expires_at"])
                if probe_owner:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status=(
                            "recovery_probe_owned"
                            if probe_owner.startswith(f"{request_id}:")
                            else "recovery_probe_active"
                        ),
                        retry_after_seconds=max(
                            0.01,
                            min(self.poll_retry_seconds, probe_expires_at - now),
                        ),
                        request_id=request_id,
                    )

                active = connection.execute(
                    "SELECT * FROM refresh_reservation WHERE scope = ?",
                    (scope,),
                ).fetchone()
                if active is not None:
                    if (
                        active["request_id"] == request_id
                        and active["owner_token"] == self.owner_token
                    ):
                        connection.commit()
                        return RefreshReservation(
                            reserved=True,
                            status="reserved",
                            retry_after_seconds=0.0,
                            request_id=request_id,
                            reservation_id=str(active["reservation_id"]),
                            is_recovery_probe=bool(active["is_recovery_probe"]),
                            is_opening_burst=bool(active["is_opening_burst"]),
                            campaign_id=str(active["campaign_id"] or ""),
                            expires_at=float(active["expires_at"]),
                        )
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="reservation_active",
                        retry_after_seconds=max(
                            0.01,
                            min(
                                self.poll_retry_seconds,
                                float(active["expires_at"]) - now,
                            ),
                        ),
                        request_id=request_id,
                    )

                is_probe = bool(bucket["recovery_required"])
                active_campaign = self._active_opening_campaign(
                    connection, scope, now
                )
                is_opening_burst = bool(
                    not is_probe
                    and burst_eligible
                    and active_campaign is not None
                    and campaign_id == str(active_campaign["campaign_id"])
                )
                if active_campaign is not None and not is_probe and not is_opening_burst:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="opening_barrier",
                        retry_after_seconds=max(
                            0.01,
                            min(
                                self.poll_retry_seconds,
                                float(active_campaign["window_until"]) - now,
                            ),
                        ),
                        request_id=request_id,
                    )

                if is_opening_burst:
                    head = connection.execute(
                        """
                        SELECT * FROM refresh_queue
                        WHERE scope = ? AND campaign_id = ?
                          AND is_opening_burst = 1
                        ORDER BY enqueued_at ASC, request_id ASC
                        LIMIT 1
                        """,
                        (scope, campaign_id),
                    ).fetchone()
                else:
                    head = connection.execute(
                        """
                        SELECT * FROM refresh_queue
                        WHERE scope = ?
                        ORDER BY enqueued_at ASC, request_id ASC
                        LIMIT 1
                        """,
                        (scope,),
                    ).fetchone()
                if head is None or head["request_id"] != request_id:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="queued",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )

                incoming_gap = (
                    float(campaign["interval_seconds"])
                    if is_opening_burst and campaign is not None
                    else min_interval + jitter_min
                )
                pairwise_allowed_at = float(bucket["last_dispatch_at"]) + max(
                    float(bucket["last_dispatch_gap"]), incoming_gap
                )
                next_allowed_at = max(
                    float(bucket["next_allowed_at"]), pairwise_allowed_at
                )
                if next_allowed_at > now:
                    connection.commit()
                    return RefreshReservation(
                        reserved=False,
                        status="pacing",
                        retry_after_seconds=max(
                            0.01,
                            min(self.poll_retry_seconds, next_allowed_at - now),
                        ),
                        request_id=request_id,
                    )

                reservation_id = f"{request_id}:reservation:{uuid.uuid4().hex}"
                lease_seconds = (
                    self.opening_reservation_lease_seconds
                    if is_opening_burst
                    else self.reservation_lease_seconds
                )
                expires_at = now + lease_seconds
                connection.execute(
                    """
                    INSERT INTO refresh_reservation(
                        scope, reservation_id, request_id, owner_token,
                        reserved_at, expires_at, is_recovery_probe, campaign_id,
                        campaign_participant_id, is_opening_burst
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope,
                        reservation_id,
                        request_id,
                        self.owner_token,
                        now,
                        expires_at,
                        int(is_probe),
                        campaign_id if is_opening_burst else "",
                        participant_id if is_opening_burst else "",
                        int(is_opening_burst),
                    ),
                )
                connection.commit()
                return RefreshReservation(
                    reserved=True,
                    status="reserved",
                    retry_after_seconds=0.0,
                    request_id=request_id,
                    reservation_id=reservation_id,
                    is_recovery_probe=is_probe,
                    is_opening_burst=is_opening_burst,
                    campaign_id=campaign_id if is_opening_burst else "",
                    expires_at=expires_at,
                )
        except Exception as exc:
            return self._error_reservation(request_id, exc)

    async def reserve_refresh(
        self,
        scope: str,
        instance_id: str,
        *,
        min_interval_seconds: float,
        jitter_range_seconds: Sequence[float] = (0.1, 0.3),
        request_token: Optional[str] = None,
        opening_burst: Optional[OpeningBurstRequest] = None,
    ) -> RefreshReservation:
        return await asyncio.to_thread(
            self.reserve_refresh_sync,
            scope,
            instance_id,
            min_interval_seconds=min_interval_seconds,
            jitter_range_seconds=jitter_range_seconds,
            request_token=request_token,
            opening_burst=opening_burst,
        )

    def commit_refresh_sync(
        self,
        scope: str,
        instance_id: str,
        reservation_id: str,
        *,
        request_token: Optional[str] = None,
    ) -> RefreshDecision:
        """Authorize one short-lived dispatch and atomically consume its quota."""

        request_id = self._request_id(
            str(scope or ""), str(instance_id or ""), request_token
        )
        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            request_token = self._validate_request_token(request_token)
            request_id = self._request_id(scope, instance_id, request_token)
            reservation_id = str(reservation_id or "").strip()
            if not reservation_id:
                raise ValueError("reservation_id is required")
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                # The write lock can be delayed long enough to cross a short
                # opening window or reservation lease.  Classify the commit
                # using the time at which this transaction actually owns the
                # state, not the stale time from before waiting on SQLite.
                now = float(self._clock())
                self._cleanup_expired_dispatches(connection, scope, now)
                self._refresh_opening_campaigns(connection, scope, now)
                connection.execute(
                    "DELETE FROM refresh_commit_receipt WHERE expires_at <= ?",
                    (now,),
                )
                receipt = connection.execute(
                    "SELECT * FROM refresh_commit_receipt WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if receipt is not None:
                    connection.commit()
                    if (
                        receipt["scope"] == scope
                        and receipt["request_id"] == request_id
                        and receipt["owner_token"] == self.owner_token
                    ):
                        # A replay must never authorize a delayed second browser
                        # dispatch, even though it exposes the original receipt.
                        return RefreshDecision(
                            granted=False,
                            status="already_committed",
                            retry_after_seconds=0.0,
                            request_id=request_id,
                            permit_id=str(receipt["permit_id"]),
                            sequence=int(receipt["sequence"]),
                            is_recovery_probe=bool(receipt["is_recovery_probe"]),
                            is_opening_burst=bool(receipt["is_opening_burst"]),
                            campaign_id=str(receipt["campaign_id"] or ""),
                            dispatch_token=str(receipt["dispatch_token"] or ""),
                        )
                    return RefreshDecision(
                        granted=False,
                        status="reservation_mismatch",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )

                reservation = connection.execute(
                    "SELECT * FROM refresh_reservation WHERE scope = ?",
                    (scope,),
                ).fetchone()
                if reservation is None or (
                    reservation["reservation_id"] != reservation_id
                    or reservation["request_id"] != request_id
                    or reservation["owner_token"] != self.owner_token
                ):
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="reservation_missing",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )
                if float(reservation["expires_at"]) <= now:
                    if bool(reservation["is_opening_burst"]):
                        connection.execute(
                            "DELETE FROM refresh_queue "
                            "WHERE scope = ? AND request_id = ?",
                            (scope, request_id),
                        )
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="reservation_expired",
                        retry_after_seconds=0.0,
                        request_id=request_id,
                    )

                queue_row = connection.execute(
                    """
                    SELECT * FROM refresh_queue
                    WHERE scope = ? AND instance_id = ? AND request_id = ?
                      AND owner_token = ?
                    """,
                    (scope, instance_id, request_id, self.owner_token),
                ).fetchone()
                if queue_row is None:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="reservation_revoked",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )

                bucket = self._bucket(connection, scope, now)
                live_dispatch = connection.execute(
                    "SELECT expires_at FROM refresh_dispatch_lease WHERE scope = ?",
                    (scope,),
                ).fetchone()
                if live_dispatch is not None:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="dispatch_active",
                        retry_after_seconds=max(
                            0.01, float(live_dispatch["expires_at"]) - now
                        ),
                        request_id=request_id,
                    )
                cooldown_until = float(bucket["cooldown_until"])
                if cooldown_until > now:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="cooldown",
                        retry_after_seconds=max(0.01, cooldown_until - now),
                        request_id=request_id,
                        cooldown_until=cooldown_until,
                    )

                if bucket["probe_owner"] and float(bucket["probe_expires_at"]) <= now:
                    connection.execute(
                        """
                        UPDATE refresh_bucket
                        SET probe_owner = '', probe_expires_at = 0, updated_at = ?
                        WHERE scope = ?
                        """,
                        (now, scope),
                    )
                    bucket = self._bucket(connection, scope, now)

                is_probe = bool(reservation["is_recovery_probe"])
                is_opening_burst = bool(reservation["is_opening_burst"])
                campaign_id = str(reservation["campaign_id"] or "")
                participant_id = str(
                    reservation["campaign_participant_id"] or ""
                )
                phase_changed = is_probe != bool(bucket["recovery_required"])
                if phase_changed or bucket["probe_owner"]:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="reservation_revoked",
                        retry_after_seconds=self.poll_retry_seconds,
                        request_id=request_id,
                    )

                active_campaign = self._active_opening_campaign(
                    connection, scope, now
                )
                campaign = None
                if is_opening_burst:
                    campaign = connection.execute(
                        "SELECT * FROM refresh_opening_campaign "
                        "WHERE scope = ? AND campaign_id = ?",
                        (scope, campaign_id),
                    ).fetchone()
                    prior_grant = connection.execute(
                        """
                        SELECT 1 FROM refresh_opening_grant
                        WHERE scope = ? AND campaign_id = ? AND participant_id = ?
                        """,
                        (scope, campaign_id, participant_id),
                    ).fetchone()
                    valid_campaign = bool(
                        campaign is not None
                        and str(campaign["state"]) == "active"
                        and float(campaign["scheduled_at"]) <= now
                        and float(campaign["window_until"]) > now
                        and int(campaign["grant_count"])
                        < int(campaign["max_participants"])
                        and prior_grant is None
                    )
                    if not valid_campaign:
                        connection.execute(
                            "DELETE FROM refresh_reservation "
                            "WHERE reservation_id = ?",
                            (reservation_id,),
                        )
                        connection.commit()
                        return RefreshDecision(
                            granted=False,
                            status="opening_ineligible",
                            retry_after_seconds=self.poll_retry_seconds,
                            request_id=request_id,
                        )
                elif active_campaign is not None and not is_probe:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="opening_barrier",
                        retry_after_seconds=max(
                            0.01,
                            float(active_campaign["window_until"]) - now,
                        ),
                        request_id=request_id,
                    )

                if is_opening_burst and campaign is not None:
                    effective_interval = float(campaign["interval_seconds"])
                else:
                    jitter_min = float(queue_row["jitter_min_seconds"])
                    jitter_max = float(queue_row["jitter_max_seconds"])
                    jitter = float(self._random_uniform(jitter_min, jitter_max))
                    jitter = min(max(jitter, jitter_min), jitter_max)
                    effective_interval = (
                        float(queue_row["min_interval_seconds"]) + jitter
                    )
                pairwise_allowed_at = float(bucket["last_dispatch_at"]) + max(
                    float(bucket["last_dispatch_gap"]), effective_interval
                )
                next_allowed_at = max(
                    float(bucket["next_allowed_at"]), pairwise_allowed_at
                )
                if next_allowed_at > now:
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    connection.commit()
                    return RefreshDecision(
                        granted=False,
                        status="pacing",
                        retry_after_seconds=max(0.01, next_allowed_at - now),
                        request_id=request_id,
                    )
                next_allowed_at = now + effective_interval
                sequence = int(bucket["sequence"]) + 1
                permit_id = f"{request_id}:{sequence}:{uuid.uuid4().hex}"
                dispatch_token = uuid.uuid4().hex
                dispatch_expires_at = now + self.dispatch_lease_seconds
                probe_expires_at = (
                    now + self.recovery_probe_lease_seconds if is_probe else 0.0
                )
                connection.execute(
                    """
                    UPDATE refresh_bucket
                    SET next_allowed_at = ?, sequence = ?, probe_owner = ?,
                        probe_expires_at = ?, updated_at = ?
                    WHERE scope = ?
                    """,
                    (
                        next_allowed_at,
                        sequence,
                        permit_id if is_probe else "",
                        probe_expires_at,
                        now,
                        scope,
                    ),
                )
                if is_opening_burst and campaign is not None:
                    connection.execute(
                        """
                        INSERT INTO refresh_opening_grant(
                            scope, campaign_id, participant_id, request_id,
                            permit_id, granted_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope,
                            campaign_id,
                            participant_id,
                            request_id,
                            permit_id,
                            now,
                        ),
                    )
                    next_count = int(campaign["grant_count"]) + 1
                    state = (
                        "exhausted"
                        if next_count >= int(campaign["max_participants"])
                        else "active"
                    )
                    connection.execute(
                        """
                        UPDATE refresh_opening_campaign
                        SET grant_count = ?, state = ?, updated_at = ?
                        WHERE scope = ? AND campaign_id = ?
                        """,
                        (next_count, state, now, scope, campaign_id),
                    )
                    connection.execute(
                        """
                        UPDATE refresh_queue
                        SET campaign_id = '', campaign_participant_id = '',
                            is_opening_burst = 0
                        WHERE scope = ? AND campaign_id = ?
                          AND campaign_participant_id = ?
                        """,
                        (scope, campaign_id, participant_id),
                    )
                connection.execute(
                    "DELETE FROM refresh_queue WHERE request_id = ?",
                    (request_id,),
                )
                connection.execute(
                    "DELETE FROM refresh_reservation WHERE reservation_id = ?",
                    (reservation_id,),
                )
                connection.execute(
                    """
                    INSERT INTO refresh_commit_receipt(
                        reservation_id, scope, request_id, owner_token, permit_id,
                        sequence, is_recovery_probe, is_opening_burst,
                        campaign_id, dispatch_token, dispatched_at,
                        dispatch_status, committed_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                              'authorized', ?, ?)
                    """,
                    (
                        reservation_id,
                        scope,
                        request_id,
                        self.owner_token,
                        permit_id,
                        sequence,
                        int(is_probe),
                        int(is_opening_burst),
                        campaign_id if is_opening_burst else "",
                        dispatch_token,
                        now,
                        now + self.commit_receipt_seconds,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO refresh_dispatch_lease(
                        scope, permit_id, dispatch_token, owner_token, request_id,
                        effective_interval_seconds, campaign_id,
                        is_opening_burst, authorized_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope,
                        permit_id,
                        dispatch_token,
                        self.owner_token,
                        request_id,
                        effective_interval,
                        campaign_id if is_opening_burst else "",
                        int(is_opening_burst),
                        now,
                        dispatch_expires_at,
                    ),
                )
                connection.commit()
                return RefreshDecision(
                    granted=True,
                    status="granted",
                    retry_after_seconds=0.0,
                    request_id=request_id,
                    permit_id=permit_id,
                    sequence=sequence,
                    is_recovery_probe=is_probe,
                    is_opening_burst=is_opening_burst,
                    campaign_id=campaign_id if is_opening_burst else "",
                    dispatch_token=dispatch_token,
                    dispatch_expires_at=dispatch_expires_at,
                )
        except Exception as exc:
            return self._error_decision(request_id, exc)

    async def commit_refresh(
        self,
        scope: str,
        instance_id: str,
        reservation_id: str,
        *,
        request_token: Optional[str] = None,
    ) -> RefreshDecision:
        return await asyncio.to_thread(
            self.commit_refresh_sync,
            scope,
            instance_id,
            reservation_id,
            request_token=request_token,
        )

    def claim_dispatch_sync(
        self, scope: str, permit_id: str, dispatch_token: str
    ) -> float | bool:
        """Claim the exact lease once and return its extended expiry epoch."""

        try:
            scope = str(scope or "").strip()
            permit_id = str(permit_id or "").strip()
            dispatch_token = str(dispatch_token or "").strip()
            if not scope or not permit_id or not dispatch_token:
                raise ValueError("scope, permit_id and dispatch_token are required")
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                # Re-sample after acquiring the write lock so an expired
                # dispatch/campaign cannot be claimed with a pre-lock time.
                now = float(self._clock())
                self._cleanup_expired_dispatches(connection, scope, now)
                lease = connection.execute(
                    """
                    SELECT * FROM refresh_dispatch_lease
                    WHERE scope = ? AND permit_id = ? AND dispatch_token = ?
                      AND owner_token = ? AND expires_at > ?
                    """,
                    (
                        scope,
                        permit_id,
                        dispatch_token,
                        self.owner_token,
                        now,
                    ),
                ).fetchone()
                if lease is None:
                    connection.commit()
                    return False
                bucket = self._bucket(connection, scope, now)
                receipt = connection.execute(
                    "SELECT is_recovery_probe, dispatch_status, campaign_id "
                    "FROM refresh_commit_receipt "
                    "WHERE permit_id = ? AND dispatch_token = ?",
                    (permit_id, dispatch_token),
                ).fetchone()
                is_probe = bool(receipt and receipt["is_recovery_probe"])
                campaign_aborted = False
                campaign_id = str(receipt["campaign_id"] or "") if receipt else ""
                if campaign_id:
                    campaign = connection.execute(
                        "SELECT state, window_until FROM refresh_opening_campaign "
                        "WHERE scope = ? AND campaign_id = ?",
                        (scope, campaign_id),
                    ).fetchone()
                    campaign_aborted = bool(
                        campaign is None
                        or str(campaign["state"]) in {"aborted", "expired"}
                        or float(campaign["window_until"]) <= now
                    )
                invalid_phase = bool(
                    float(bucket["cooldown_until"]) > now
                    or (bool(bucket["recovery_required"]) and not is_probe)
                    or campaign_aborted
                )
                if invalid_phase:
                    if receipt and receipt["dispatch_status"] == "authorized":
                        connection.execute(
                            "DELETE FROM refresh_dispatch_lease "
                            "WHERE scope = ? AND permit_id = ? "
                            "AND dispatch_token = ?",
                            (scope, permit_id, dispatch_token),
                        )
                        connection.execute(
                            "UPDATE refresh_commit_receipt "
                            "SET dispatch_status = 'revoked' "
                            "WHERE permit_id = ? AND dispatch_token = ? "
                            "AND dispatch_status = 'authorized'",
                            (permit_id, dispatch_token),
                        )
                    connection.commit()
                    return False
                claimed = connection.execute(
                    """
                    UPDATE refresh_commit_receipt
                    SET dispatch_status = 'claimed'
                    WHERE permit_id = ? AND dispatch_token = ?
                      AND owner_token = ? AND dispatch_status = 'authorized'
                    """,
                    (permit_id, dispatch_token, self.owner_token),
                )
                if claimed.rowcount != 1:
                    connection.commit()
                    return False
                claimed_expires_at = max(
                    float(lease["expires_at"]),
                    now + self.claimed_dispatch_lease_seconds,
                )
                connection.execute(
                    """
                    UPDATE refresh_dispatch_lease
                    SET expires_at = ?
                    WHERE scope = ? AND permit_id = ? AND dispatch_token = ?
                      AND owner_token = ?
                    """,
                    (
                        claimed_expires_at,
                        scope,
                        permit_id,
                        dispatch_token,
                        self.owner_token,
                    ),
                )
                connection.commit()
                return claimed_expires_at
        except Exception:
            return False

    async def claim_dispatch(
        self, scope: str, permit_id: str, dispatch_token: str
    ) -> float | bool:
        return await asyncio.to_thread(
            self.claim_dispatch_sync, scope, permit_id, dispatch_token
        )

    def complete_dispatch_sync(
        self,
        scope: str,
        permit_id: str,
        dispatch_token: str,
        *,
        possibly_dispatched: bool,
    ) -> bool:
        """Acknowledge the command result without refunding an authorized permit."""

        try:
            scope = str(scope or "").strip()
            permit_id = str(permit_id or "").strip()
            dispatch_token = str(dispatch_token or "").strip()
            if not scope or not permit_id or not dispatch_token:
                raise ValueError("scope, permit_id and dispatch_token are required")
            now = float(self._clock())
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                lease = connection.execute(
                    """
                    SELECT * FROM refresh_dispatch_lease
                    WHERE scope = ? AND permit_id = ? AND dispatch_token = ?
                      AND owner_token = ?
                    """,
                    (scope, permit_id, dispatch_token, self.owner_token),
                ).fetchone()
                if lease is None:
                    receipt = connection.execute(
                        """
                        SELECT dispatched_at FROM refresh_commit_receipt
                        WHERE scope = ? AND permit_id = ? AND dispatch_token = ?
                          AND owner_token = ?
                        """,
                        (scope, permit_id, dispatch_token, self.owner_token),
                    ).fetchone()
                    connection.commit()
                    return bool(receipt and float(receipt["dispatched_at"]) > 0)
                if possibly_dispatched:
                    bucket = self._bucket(connection, scope, now)
                    effective_gap = float(lease["effective_interval_seconds"])
                    previous_dispatch_at = float(bucket["last_dispatch_at"])
                    last_dispatch_at = max(previous_dispatch_at, now)
                    last_dispatch_gap = (
                        effective_gap
                        if now >= previous_dispatch_at
                        else float(bucket["last_dispatch_gap"])
                    )
                    connection.execute(
                        """
                        UPDATE refresh_bucket
                        SET last_dispatch_at = ?, last_dispatch_gap = ?,
                            next_allowed_at = MAX(next_allowed_at, ?),
                            updated_at = ?
                        WHERE scope = ?
                        """,
                        (
                            last_dispatch_at,
                            last_dispatch_gap,
                            now + effective_gap,
                            now,
                            scope,
                        ),
                    )
                if possibly_dispatched:
                    connection.execute(
                        """
                        UPDATE refresh_commit_receipt
                        SET dispatched_at = CASE WHEN dispatched_at > 0
                                                 THEN dispatched_at ELSE ? END,
                            dispatch_status = CASE WHEN dispatched_at > 0
                                                   THEN dispatch_status
                                                   ELSE 'uncertain' END
                        WHERE permit_id = ? AND dispatch_token = ?
                        """,
                        (now, permit_id, dispatch_token),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE refresh_commit_receipt
                        SET dispatch_status = CASE WHEN dispatched_at > 0
                                                   THEN dispatch_status
                                                   ELSE 'not_dispatched' END
                        WHERE permit_id = ? AND dispatch_token = ?
                        """,
                        (permit_id, dispatch_token),
                    )
                connection.execute(
                    "DELETE FROM refresh_dispatch_lease "
                    "WHERE scope = ? AND permit_id = ? AND dispatch_token = ?",
                    (scope, permit_id, dispatch_token),
                )
                connection.commit()
                return True
        except Exception:
            return False

    async def complete_dispatch(
        self,
        scope: str,
        permit_id: str,
        dispatch_token: str,
        *,
        possibly_dispatched: bool,
    ) -> bool:
        return await asyncio.to_thread(
            self.complete_dispatch_sync,
            scope,
            permit_id,
            dispatch_token,
            possibly_dispatched=possibly_dispatched,
        )

    def abort_opening_campaigns_sync(self, scope: str, reason: str) -> bool:
        """Permanently close active opening campaigns without touching cooldown."""

        try:
            scope = str(scope or "").strip()
            reason = str(reason or "disabled")[:128]
            if not scope:
                raise ValueError("scope is required")
            now = float(self._clock())
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE refresh_opening_campaign
                    SET state = 'aborted', ended_at = ?, updated_at = ?,
                        end_reason = ?
                    WHERE scope = ? AND state IN ('active', 'exhausted')
                      AND window_until > ?
                    """,
                    (now, now, reason, scope, now),
                )
                connection.execute(
                    "DELETE FROM refresh_reservation "
                    "WHERE scope = ? AND is_opening_burst = 1",
                    (scope,),
                )
                # A claimed browser command may already be crossing the CDP
                # boundary. Keep that lease so completion or expiry performs
                # conservative dispatch accounting. Only never-claimed
                # authorizations can be revoked without consuming a gap.
                connection.execute(
                    """
                    UPDATE refresh_commit_receipt
                    SET dispatch_status = 'revoked'
                    WHERE scope = ? AND is_opening_burst = 1
                      AND dispatch_status = 'authorized'
                    """,
                    (scope,),
                )
                connection.execute(
                    """
                    DELETE FROM refresh_dispatch_lease
                    WHERE scope = ? AND is_opening_burst = 1
                      AND permit_id IN (
                          SELECT permit_id FROM refresh_commit_receipt
                          WHERE scope = ? AND dispatch_status = 'revoked'
                      )
                    """,
                    (scope, scope),
                )
                connection.execute(
                    """
                    UPDATE refresh_queue
                    SET campaign_id = '', campaign_participant_id = '',
                        is_opening_burst = 0
                    WHERE scope = ? AND is_opening_burst = 1
                    """,
                    (scope,),
                )
                connection.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

    async def abort_opening_campaigns(self, scope: str, reason: str) -> bool:
        return await asyncio.to_thread(
            self.abort_opening_campaigns_sync, scope, reason
        )

    def poll_refresh_sync(
        self,
        scope: str,
        instance_id: str,
        *,
        min_interval_seconds: float,
        jitter_range_seconds: Sequence[float] = (0.1, 0.3),
        request_token: Optional[str] = None,
        opening_burst: Optional[OpeningBurstRequest] = None,
    ) -> RefreshDecision:
        """Poll once for a refresh permit without sleeping.

        Callers should retry after ``retry_after_seconds``.  A database or
        validation failure is fail-closed and never returns a permit.
        """

        reservation = self.reserve_refresh_sync(
            scope,
            instance_id,
            min_interval_seconds=min_interval_seconds,
            jitter_range_seconds=jitter_range_seconds,
            request_token=request_token,
            opening_burst=opening_burst,
        )
        if not reservation.reserved:
            return RefreshDecision(
                granted=False,
                status=reservation.status,
                retry_after_seconds=reservation.retry_after_seconds,
                request_id=reservation.request_id,
                is_recovery_probe=reservation.is_recovery_probe,
                cooldown_until=reservation.cooldown_until,
                error=reservation.error,
            )
        decision = self.commit_refresh_sync(
            scope,
            instance_id,
            reservation.reservation_id,
            request_token=request_token,
        )
        if not decision.granted:
            return decision
        claimed = self.claim_dispatch_sync(
            scope, decision.permit_id, decision.dispatch_token
        )
        if not claimed:
            return RefreshDecision(
                granted=False,
                status="dispatch_lost",
                retry_after_seconds=self.fail_closed_retry_seconds,
                request_id=decision.request_id,
                permit_id=decision.permit_id,
                sequence=decision.sequence,
                is_recovery_probe=decision.is_recovery_probe,
                is_opening_burst=decision.is_opening_burst,
                campaign_id=decision.campaign_id,
            )
        self.complete_dispatch_sync(
            scope,
            decision.permit_id,
            decision.dispatch_token,
            possibly_dispatched=True,
        )
        return decision

    async def poll_refresh(
        self,
        scope: str,
        instance_id: str,
        *,
        min_interval_seconds: float,
        jitter_range_seconds: Sequence[float] = (0.1, 0.3),
        request_token: Optional[str] = None,
        opening_burst: Optional[OpeningBurstRequest] = None,
    ) -> RefreshDecision:
        return await asyncio.to_thread(
            self.poll_refresh_sync,
            scope,
            instance_id,
            min_interval_seconds=min_interval_seconds,
            jitter_range_seconds=jitter_range_seconds,
            request_token=request_token,
            opening_burst=opening_burst,
        )

    @staticmethod
    def _cooldown_seconds(
        consecutive_incidents: int,
        initial_cooldown_seconds: float,
        max_cooldown_seconds: float,
    ) -> float:
        initial = max(float(initial_cooldown_seconds), 0.1)
        maximum = max(float(max_cooldown_seconds), initial)
        return min(initial * (2 ** max(consecutive_incidents - 1, 0)), maximum)

    def report_rate_limited_sync(
        self,
        scope: str,
        instance_id: str,
        *,
        initial_cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 240.0,
        retry_after_seconds: Optional[float] = None,
        probe_permit_id: Optional[str] = None,
        document_started_at: Optional[float] = None,
    ) -> RateLimitReport:
        """Record one observed rate-limit document.

        The first observation from normal service starts a shared cooldown.
        Further observations during recovery are deduplicated unless
        ``probe_permit_id`` owns the current recovery-probe lease; only that
        probe may escalate the cooldown to its next level.
        """
        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            initial = max(float(initial_cooldown_seconds), 0.1)
            maximum = max(float(max_cooldown_seconds), initial)
            retry_after = (
                max(float(retry_after_seconds), 0.0)
                if retry_after_seconds is not None
                else 0.0
            )
            document_started = (
                max(float(document_started_at), 0.0)
                if document_started_at is not None
                else 0.0
            )
            now = float(self._clock())
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                bucket = self._bucket(connection, scope, now)
                last_429_at = float(bucket["last_429_at"])
                recovery_required = bool(bucket["recovery_required"])
                probe_owner = str(bucket["probe_owner"] or "")
                valid_probe_failure = bool(
                    recovery_required
                    and probe_permit_id
                    and probe_owner == str(probe_permit_id)
                    and float(bucket["probe_expires_at"]) > now
                )
                stale_pre_recovery_document = bool(
                    not recovery_required
                    and document_started > 0
                    and document_started
                    <= float(bucket["last_recovery_at"] or 0.0)
                )
                # A rate-limit page opens an incident only from normal service,
                # or when the one leased recovery probe proves the block still
                # exists.  Other tabs can finish an already in-flight reload
                # during cooldown; those observations must not repeatedly
                # extend/escalate the shared penalty.
                # recovery_required is the atomic incident phase.  The first
                # clear->recovery transition wins the transaction; concurrent
                # reports then observe recovery_required and are duplicates.
                # Do not wall-clock-dedupe a later clear-state incident: a
                # successful probe may legitimately be followed by a fresh
                # 429 even when a user configured a short cooldown.
                is_new_incident = valid_probe_failure or (
                    not recovery_required and not stale_pre_recovery_document
                )
                consecutive = int(bucket["consecutive_429"])
                cooldown_until = float(bucket["cooldown_until"])

                if is_new_incident:
                    if (
                        last_429_at <= 0
                        or now - last_429_at > self.incident_reset_seconds
                    ):
                        consecutive = 1
                    else:
                        consecutive += 1
                    delay = self._cooldown_seconds(consecutive, initial, maximum)
                    cooldown_until = max(cooldown_until, now + delay)
                    last_429_at = now

                if is_new_incident and retry_after > 0:
                    cooldown_until = max(cooldown_until, now + retry_after)

                if is_new_incident:
                    # Outstanding requests belong to the incident that just
                    # triggered the block.  Clear them so all callers rejoin
                    # FIFO only after the shared cooldown expires.
                    connection.execute(
                        "DELETE FROM refresh_reservation WHERE scope = ?", (scope,)
                    )
                    connection.execute(
                        "DELETE FROM refresh_queue WHERE scope = ?", (scope,)
                    )
                    connection.execute(
                        """
                        UPDATE refresh_commit_receipt
                        SET dispatch_status = 'revoked'
                        WHERE scope = ? AND dispatch_status = 'authorized'
                        """,
                        (scope,),
                    )
                    connection.execute(
                        """
                        DELETE FROM refresh_dispatch_lease
                        WHERE scope = ? AND permit_id IN (
                            SELECT permit_id FROM refresh_commit_receipt
                            WHERE scope = ? AND dispatch_status = 'revoked'
                        )
                        """,
                        (scope, scope),
                    )
                    connection.execute(
                        """
                        UPDATE refresh_opening_campaign
                        SET state = 'aborted', ended_at = ?, updated_at = ?,
                            end_reason = 'rate_limited'
                        WHERE scope = ? AND state IN ('active', 'exhausted')
                          AND window_until > ?
                        """,
                        (now, now, scope, now),
                    )
                    connection.execute(
                        """
                        UPDATE refresh_bucket
                        SET cooldown_until = ?, consecutive_429 = ?, last_429_at = ?,
                            recovery_required = 1, probe_owner = '',
                            probe_expires_at = 0, updated_at = ?
                        WHERE scope = ?
                        """,
                        (cooldown_until, consecutive, last_429_at, now, scope),
                    )
                connection.commit()
                return RateLimitReport(
                    accepted=True,
                    new_incident=is_new_incident,
                    consecutive_incidents=consecutive,
                    cooldown_until=cooldown_until,
                    retry_after_seconds=max(0.0, cooldown_until - now),
                )
        except Exception as exc:
            now = float(self._clock())
            return RateLimitReport(
                accepted=False,
                new_incident=False,
                consecutive_incidents=0,
                cooldown_until=now + self.fail_closed_retry_seconds,
                retry_after_seconds=self.fail_closed_retry_seconds,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def report_rate_limited(
        self,
        scope: str,
        instance_id: str,
        *,
        initial_cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 240.0,
        retry_after_seconds: Optional[float] = None,
        probe_permit_id: Optional[str] = None,
        document_started_at: Optional[float] = None,
    ) -> RateLimitReport:
        return await asyncio.to_thread(
            self.report_rate_limited_sync,
            scope,
            instance_id,
            initial_cooldown_seconds=initial_cooldown_seconds,
            max_cooldown_seconds=max_cooldown_seconds,
            retry_after_seconds=retry_after_seconds,
            probe_permit_id=probe_permit_id,
            document_started_at=document_started_at,
        )

    def complete_recovery_probe_sync(
        self,
        scope: str,
        instance_id: str,
        permit_id: str,
        *,
        success: bool,
        retry_delay_seconds: float = 1.0,
    ) -> ProbeCompletion:
        """Complete the single probe issued after a shared cooldown.

        A successful probe reopens normal grants.  A non-429 failure releases
        the lease but retains recovery mode, allowing another probe after the
        short retry delay.  A new 429 must be reported with
        :meth:`report_rate_limited` instead.
        """

        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            if not permit_id:
                raise ValueError("permit_id is required")
            now = float(self._clock())
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                bucket = self._bucket(connection, scope, now)
                if (
                    str(bucket["probe_owner"] or "") != str(permit_id)
                    or float(bucket["probe_expires_at"]) <= now
                ):
                    connection.commit()
                    return ProbeCompletion(
                        accepted=False,
                        recovery_required=bool(bucket["recovery_required"]),
                        error="probe lease is expired or not owned by this permit",
                    )

                recovery_required = 0 if success else 1
                next_allowed_at = float(bucket["next_allowed_at"])
                if not success:
                    next_allowed_at = max(
                        next_allowed_at, now + max(float(retry_delay_seconds), 0.1)
                    )
                connection.execute(
                    """
                    UPDATE refresh_bucket
                    SET recovery_required = ?, probe_owner = '',
                        probe_expires_at = 0, next_allowed_at = ?,
                        last_recovery_at = CASE WHEN ? THEN ? ELSE last_recovery_at END,
                        updated_at = ?
                    WHERE scope = ?
                    """,
                    (
                        recovery_required,
                        next_allowed_at,
                        int(bool(success)),
                        now,
                        now,
                        scope,
                    ),
                )
                connection.commit()
                return ProbeCompletion(
                    accepted=True,
                    recovery_required=bool(recovery_required),
                )
        except Exception as exc:
            return ProbeCompletion(
                accepted=False,
                recovery_required=True,
                error=f"{type(exc).__name__}: {exc}",
            )

    async def complete_recovery_probe(
        self,
        scope: str,
        instance_id: str,
        permit_id: str,
        *,
        success: bool,
        retry_delay_seconds: float = 1.0,
    ) -> ProbeCompletion:
        return await asyncio.to_thread(
            self.complete_recovery_probe_sync,
            scope,
            instance_id,
            permit_id,
            success=success,
            retry_delay_seconds=retry_delay_seconds,
        )

    def renew_recovery_probe_sync(self, scope: str, permit_id: str) -> Optional[bool]:
        """Extend a still-owned probe lease while its navigation is in flight."""
        try:
            scope = str(scope or "").strip()
            if not scope or not permit_id:
                raise ValueError("scope and permit_id are required")
            now = float(self._clock())
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                bucket = self._bucket(connection, scope, now)
                if (
                    str(bucket["probe_owner"] or "") != str(permit_id)
                    or float(bucket["probe_expires_at"]) <= now
                ):
                    connection.commit()
                    return False
                connection.execute(
                    """
                    UPDATE refresh_bucket
                    SET probe_expires_at = ?, updated_at = ?
                    WHERE scope = ? AND probe_owner = ? AND probe_expires_at > ?
                    """,
                    (
                        now + self.recovery_probe_lease_seconds,
                        now,
                        scope,
                        str(permit_id),
                        now,
                    ),
                )
                connection.commit()
                return True
        except Exception:
            # None means ownership could not be checked; callers should retry
            # instead of abandoning the lease after one transient DB error.
            return None

    async def renew_recovery_probe(
        self, scope: str, permit_id: str
    ) -> Optional[bool]:
        return await asyncio.to_thread(
            self.renew_recovery_probe_sync, scope, permit_id
        )

    def release_refresh_sync(
        self,
        scope: str,
        instance_id: str,
        reservation_id: str,
        *,
        request_token: Optional[str] = None,
        drop_request: bool = True,
    ) -> bool:
        """Release only the exact reservation, optionally removing its request.

        Both the reservation ID and logical request ID are matched, so a late
        release for an old request cannot remove a replacement (ABA safety).
        """

        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            request_token = self._validate_request_token(request_token)
            reservation_id = str(reservation_id or "").strip()
            if not reservation_id:
                raise ValueError("reservation_id is required")
            request_id = self._request_id(scope, instance_id, request_token)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    DELETE FROM refresh_reservation
                    WHERE scope = ? AND reservation_id = ? AND request_id = ?
                      AND owner_token = ?
                    """,
                    (scope, reservation_id, request_id, self.owner_token),
                )
                changed = cursor.rowcount > 0
                if drop_request:
                    queue_cursor = connection.execute(
                        """
                        DELETE FROM refresh_queue
                        WHERE scope = ? AND instance_id = ? AND request_id = ?
                          AND owner_token = ?
                        """,
                        (scope, instance_id, request_id, self.owner_token),
                    )
                    changed = queue_cursor.rowcount > 0 or changed
                connection.commit()
                return changed
        except Exception:
            return False

    async def release_refresh(
        self,
        scope: str,
        instance_id: str,
        reservation_id: str,
        *,
        request_token: Optional[str] = None,
        drop_request: bool = True,
    ) -> bool:
        return await asyncio.to_thread(
            self.release_refresh_sync,
            scope,
            instance_id,
            reservation_id,
            request_token=request_token,
            drop_request=drop_request,
        )

    def cancel_request_sync(
        self,
        scope: str,
        instance_id: str,
        *,
        request_token: Optional[str] = None,
    ) -> bool:
        try:
            scope, instance_id = self._validate_identity(scope, instance_id)
            request_token = self._validate_request_token(request_token)
            request_id = self._request_id(scope, instance_id, request_token)
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                reservation_cursor = connection.execute(
                    """
                    DELETE FROM refresh_reservation
                    WHERE scope = ? AND request_id = ? AND owner_token = ?
                    """,
                    (scope, request_id, self.owner_token),
                )
                cursor = connection.execute(
                    """
                    DELETE FROM refresh_queue
                    WHERE scope = ? AND instance_id = ? AND request_id = ?
                      AND owner_token = ?
                    """,
                    (scope, instance_id, request_id, self.owner_token),
                )
                connection.commit()
                return cursor.rowcount > 0 or reservation_cursor.rowcount > 0
        except Exception:
            return False

    async def cancel_request(
        self,
        scope: str,
        instance_id: str,
        *,
        request_token: Optional[str] = None,
    ) -> bool:
        return await asyncio.to_thread(
            self.cancel_request_sync,
            scope,
            instance_id,
            request_token=request_token,
        )

    def snapshot_sync(self, scope: str) -> dict:
        """Return a read-only state snapshot for diagnostics and tests."""

        scope = str(scope or "").strip()
        try:
            with closing(self._connect()) as connection:
                bucket = connection.execute(
                    "SELECT * FROM refresh_bucket WHERE scope = ?", (scope,)
                ).fetchone()
                queue = connection.execute(
                    """
                    SELECT scope, instance_id, request_id, owner_token, process_id,
                           enqueued_at, heartbeat_at, min_interval_seconds,
                           jitter_min_seconds, jitter_max_seconds, campaign_id,
                           campaign_participant_id, is_opening_burst
                    FROM refresh_queue WHERE scope = ?
                    ORDER BY enqueued_at ASC, request_id ASC
                    """,
                    (scope,),
                ).fetchall()
                reservation = connection.execute(
                    "SELECT * FROM refresh_reservation WHERE scope = ?", (scope,)
                ).fetchone()
                campaigns = connection.execute(
                    "SELECT * FROM refresh_opening_campaign WHERE scope = ? "
                    "ORDER BY scheduled_at, campaign_id",
                    (scope,),
                ).fetchall()
                grants = connection.execute(
                    "SELECT * FROM refresh_opening_grant WHERE scope = ? "
                    "ORDER BY granted_at, participant_id",
                    (scope,),
                ).fetchall()
                dispatch = connection.execute(
                    "SELECT * FROM refresh_dispatch_lease WHERE scope = ?",
                    (scope,),
                ).fetchone()
                receipts = connection.execute(
                    "SELECT * FROM refresh_commit_receipt WHERE scope = ? "
                    "ORDER BY committed_at, permit_id",
                    (scope,),
                ).fetchall()
                return {
                    "ok": True,
                    "bucket": dict(bucket) if bucket is not None else None,
                    "queue": [dict(row) for row in queue],
                    "reservation": (
                        dict(reservation) if reservation is not None else None
                    ),
                    "campaigns": [dict(row) for row in campaigns],
                    "grants": [dict(row) for row in grants],
                    "dispatch": dict(dispatch) if dispatch is not None else None,
                    "receipts": [dict(row) for row in receipts],
                }
        except Exception as exc:
            return {
                "ok": False,
                "bucket": None,
                "queue": [],
                "reservation": None,
                "campaigns": [],
                "grants": [],
                "dispatch": None,
                "receipts": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def snapshot(self, scope: str) -> dict:
        return await asyncio.to_thread(self.snapshot_sync, scope)


_shared_coordinator: Optional[SharedRefreshCoordinator] = None


def get_shared_refresh_coordinator() -> SharedRefreshCoordinator:
    """Return this process's coordinator owner for the shared runtime DB."""

    global _shared_coordinator
    if _shared_coordinator is None:
        _shared_coordinator = SharedRefreshCoordinator()
    return _shared_coordinator


__all__ = [
    "OpeningBurstRequest",
    "ProbeCompletion",
    "RateLimitReport",
    "RefreshDecision",
    "RefreshReservation",
    "SharedRefreshCoordinator",
    "UTK_SPORTS_SCOPE",
    "get_shared_refresh_coordinator",
]
