#encoding=utf-8
"""Mutable records for the workflow-owned uTiki tab runtime.

This module defines data and creates fresh records. Tab identity, the registry,
background task ownership, and cleanup remain in ``workflow``.
"""

from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict


class DocumentSnapshotCache(TypedDict):
    url: str
    checked_at: float
    snapshot: dict[str, Any]


class UnknownDocument(TypedDict):
    document_id: str
    first_seen_at: float


class DocumentState(TypedDict):
    snapshot: DocumentSnapshotCache | None
    last_document: str
    unknown_document: UnknownDocument | None


class PendingNavigation(TypedDict, total=False):
    # Fast/area and native-date records share only part of this shape.
    kind: str
    phase: str
    nonce: str
    source_url: str
    source_document_id: str
    area_url: str
    destination_url: str
    route: str | None
    selection_mode: str
    performance_id: str | None
    product_id: str | None
    group_id: str | None
    price_area_id: str | None
    config_fingerprint: str
    tenant_path: str
    host: str
    action_kind: str
    created_at: float
    reload_requested_at: float
    return_source_url: str
    marker_expires_at: float


class ServerAssignmentAuthorization(TypedDict):
    purchase_key: tuple[str, ...]
    product_id: str


class TransitionState(TypedDict):
    pending_navigation: PendingNavigation | None
    server_assignment_authorized: ServerAssignmentAuthorization | None


class ActiveSubmit(TypedDict):
    purchase_key: tuple[str, ...]
    nonce: str
    dispatched_at: float
    phase: str
    source_url: str
    source_document_id: str
    document_id: str
    page_kind: str
    product_id: str
    payload_fingerprint: str


class PurchaseOutcome(TypedDict):
    # Some fields exist only for one dialog/recovery outcome.
    kind: str
    text: str
    reason: str
    source_url: str
    source_document_id: str
    nonce: str
    dom_document_id: NotRequired[str]
    product_id: NotRequired[str]
    page_type: NotRequired[str]
    dialog_dismissed: NotRequired[bool]
    clear_phase: NotRequired[str]
    clear_result: NotRequired[str]
    reload_dispatched: NotRequired[bool]
    recovery_document_id: NotRequired[str]
    fingerprint: NotRequired[str]
    reset_phase: NotRequired[str]
    reset_source_document_id: NotRequired[str]
    reset_before_token: NotRequired[str]


class LastSubmit(TypedDict):
    purchase_key: tuple[str, ...]
    nonce: str


class PurchaseState(TypedDict):
    active_submit: ActiveSubmit | None
    outcomes: dict[tuple[str, ...], PurchaseOutcome]
    last_submit: LastSubmit | None
    checkout_notified: set[tuple[Any, ...]]


class ReloadRequest(TypedDict):
    document_id: str | None
    source_document_token: str | None
    expected_url: str | None
    eligible_at: float
    deadline_at: float
    next_poll_at: float
    reason: str
    allow_blocked_recovery: bool
    request_token: str
    phase: str
    paused: bool
    reservation_id: str
    shared_enqueued: bool
    pause_cleanup_done: bool
    pause_cleanup_retry_at: float
    dispatched_at: NotRequired[float]
    completed_document_token: NotRequired[str]


class RecoveryProbe(TypedDict, total=False):
    permit_id: str
    source_document_id: str
    started_at: float
    next_renew_at: float
    lease_task: asyncio.Task[Any]
    abandon_requested: bool
    pause_cleanup_done: bool
    pause_cleanup_retry_at: float


class RefreshState(TypedDict):
    reload_request: ReloadRequest | None
    rate_limit_observation: str | None
    rate_limit_retry: float
    recovery_probe: RecoveryProbe | None
    reload_event: bool


class TabRuntime(TypedDict):
    document: DocumentState
    # Present in 944e934, with no production keys assigned in this batch.
    action: dict[str, Any]
    transition: TransitionState
    purchase: PurchaseState
    refresh: RefreshState


def new_document_state() -> DocumentState:
    # Empty until a canonical non-zero performance time origin is observed.
    return {"snapshot": None, "last_document": "", "unknown_document": None}


def new_transition_state() -> TransitionState:
    return {"pending_navigation": None, "server_assignment_authorized": None}


def new_purchase_state() -> PurchaseState:
    return {
        "active_submit": None,
        "outcomes": {},
        "last_submit": None,
        "checkout_notified": set(),
    }


def new_refresh_state() -> RefreshState:
    return {
        "reload_request": None,
        "rate_limit_observation": None,
        "rate_limit_retry": 0.0,
        "recovery_probe": None,
        "reload_event": False,
    }


def new_tab_runtime() -> TabRuntime:
    """Build the existing five child records, fresh for one workflow tab."""
    return {
        "document": new_document_state(),
        "action": {},
        "transition": new_transition_state(),
        "purchase": new_purchase_state(),
        "refresh": new_refresh_state(),
    }
