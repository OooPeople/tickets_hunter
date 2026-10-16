"""Immutable page actions and submit intents for the uTiki family."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..profiles import PageKind, classify_utiki_page
from .snapshots import DateFingerprint, SeatFingerprint


@dataclass(frozen=True, slots=True)
class DateActionPlan:
    row_index: int
    control_index: int
    text: str
    action_kind: str
    onclick: str
    control_fingerprint: DateFingerprint
    target_url: str = ""
    performance_id: str = ""
    product_id: str = ""
    tenant_path: str = ""
    host: str = ""
    map_namespace: str = ""
    fast_area_prefetch: bool = False
    action_token: str = ""


@dataclass(frozen=True, slots=True)
class AreaActionPlan:
    text: str
    area: str
    price: str
    status: str
    rel: str
    action_name: str
    route: str
    performance_id: str
    price_area_id: str
    group_id: str
    stable_args: tuple[str, ...]
    action_status: str
    direct_destination: bool


@dataclass(frozen=True, slots=True)
class QuantityPlan:
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    quantity_limit: int
    target_key: str
    target_name: str
    ticket_number: int
    page_type: str = "general_admission"
    product_id: str = ""
    performance_hidden_present: bool = False
    product_hidden_present: bool = False
    group_hidden_present: bool = False
    price_area_hidden_present: bool = False
    quantity_limit_present: bool = False
    target_remaining_present: bool = False


@dataclass(frozen=True, slots=True)
class SeatActionPlan:
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    product_id: str
    action: str
    ticket_number: int
    ticket_type_fingerprint: SeatFingerprint
    ticket_type_id: str
    ticket_type_name: str
    selected_seat_fingerprints: tuple[SeatFingerprint, ...] = ()
    target_seat_fingerprints: tuple[SeatFingerprint, ...] = ()
    require_physical_adjacency: bool = False


@dataclass(frozen=True, slots=True)
class SubmitPlan:
    page_url: str
    document_id: str
    page_kind: PageKind
    payload_fingerprint: tuple[object, ...]
    selection_plan: SeatActionPlan | QuantityPlan


def _seat_submit_payload(plan: object) -> tuple[object, ...] | None:
    """Build the final payload identity for a reserved-seat plan."""
    if not isinstance(plan, SeatActionPlan) or plan.action not in {"select", "ready"}:
        return None
    expected = (
        plan.target_seat_fingerprints
        if plan.action == "select"
        else plan.selected_seat_fingerprints
    )
    unused = (
        plan.selected_seat_fingerprints
        if plan.action == "select"
        else plan.target_seat_fingerprints
    )
    fingerprints = (plan.ticket_type_fingerprint, *expected)
    token = r"[A-Za-z0-9_-]+"
    if (
        classify_utiki_page(plan.page_url) != PageKind.RESERVED_SEAT
        or not isinstance(plan.document_id, str) or not plan.document_id
        or type(plan.ticket_number) is not int or plan.ticket_number <= 0
        or not isinstance(plan.selected_seat_fingerprints, tuple)
        or not isinstance(plan.target_seat_fingerprints, tuple) or unused
        or len(expected) != plan.ticket_number
        or any(not isinstance(item, tuple) or not item for item in fingerprints)
        or any(
            any(not isinstance(value, (str, int, float, bool)) for value in item)
            for item in fingerprints
        )
        or len(set(expected)) != len(expected)
        or any(
            not isinstance(value, str) or not re.fullmatch(token, value)
            for value in (
                plan.performance_id, plan.group_id,
                plan.price_area_id, plan.product_id,
            )
        )
        or not isinstance(plan.ticket_type_id, str) or not plan.ticket_type_id
        or not isinstance(plan.ticket_type_name, str) or not plan.ticket_type_name
        or type(plan.require_physical_adjacency) is not bool
    ):
        return None
    return (
        "seat", plan.ticket_number, plan.ticket_type_fingerprint,
        plan.ticket_type_id, plan.ticket_type_name, expected,
        plan.require_physical_adjacency,
    )


def _quantity_submit_payload(plan: object) -> tuple[object, ...] | None:
    """Build the final payload identity for a quantity plan."""
    if not isinstance(plan, QuantityPlan):
        return None
    page_kinds = {
        "general_admission": PageKind.GENERAL_ADMISSION,
        "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
    }
    page_kind = page_kinds.get(plan.page_type)
    token = r"[A-Za-z0-9_-]+"
    bool_fields = (
        plan.performance_hidden_present, plan.product_hidden_present,
        plan.group_hidden_present, plan.price_area_hidden_present,
        plan.quantity_limit_present, plan.target_remaining_present,
    )
    if (
        page_kind is None or classify_utiki_page(plan.page_url) != page_kind
        or not isinstance(plan.document_id, str) or not plan.document_id
        or type(plan.ticket_number) is not int or plan.ticket_number <= 0
        or type(plan.quantity_limit) is not int or plan.quantity_limit < 0
        or not isinstance(plan.target_name, str) or not plan.target_name
        or not isinstance(plan.performance_id, str)
        or not re.fullmatch(token, plan.performance_id)
        or not isinstance(plan.price_area_id, str)
        or not re.fullmatch(token, plan.price_area_id)
        or not isinstance(plan.target_key, str)
        or not re.fullmatch(token, plan.target_key)
        or not isinstance(plan.group_id, str)
        or (plan.group_id and not re.fullmatch(token, plan.group_id))
        or not isinstance(plan.product_id, str)
        or (plan.product_id and not re.fullmatch(token, plan.product_id))
        or bool(plan.product_id) != plan.product_hidden_present
        or (
            page_kind is PageKind.SERVER_ASSIGNED_QUANTITY
            and (not plan.group_id or not plan.product_id)
        )
        or any(type(value) is not bool for value in bool_fields)
    ):
        return None
    return (
        "quantity", plan.page_type, plan.target_key, plan.target_name,
        plan.ticket_number, plan.quantity_limit, bool_fields,
    )


def build_submit_plan(selection_plan: object) -> SubmitPlan | None:
    """Freeze a non-sensitive final submit intent from a selection plan."""
    if isinstance(selection_plan, SeatActionPlan):
        page_kind: PageKind | None = PageKind.RESERVED_SEAT
        payload = _seat_submit_payload(selection_plan)
    elif isinstance(selection_plan, QuantityPlan):
        page_kind = {
            "general_admission": PageKind.GENERAL_ADMISSION,
            "server_assigned_quantity": PageKind.SERVER_ASSIGNED_QUANTITY,
        }.get(selection_plan.page_type)
        payload = _quantity_submit_payload(selection_plan)
    else:
        return None
    if page_kind is None or payload is None:
        return None
    return SubmitPlan(
        page_url=selection_plan.page_url,
        document_id=selection_plan.document_id,
        page_kind=page_kind,
        payload_fingerprint=payload,
        selection_plan=selection_plan,
    )


def submit_plan_is_valid(plan: object) -> bool:
    """Rebuild the payload identity to reject a changed submit intent."""
    if not isinstance(plan, SubmitPlan):
        return False
    expected = build_submit_plan(plan.selection_plan)
    return bool(
        expected is not None
        and plan.page_url == expected.page_url
        and plan.document_id == expected.document_id
        and plan.page_kind is expected.page_kind
        and plan.payload_fingerprint == expected.payload_fingerprint
    )
