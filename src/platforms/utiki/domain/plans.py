"""Immutable page actions and submit intents for the uTiki family."""

from __future__ import annotations

from dataclasses import dataclass

from ..profiles import PageKind
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
