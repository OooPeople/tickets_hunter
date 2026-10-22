"""Typed field observations at the untrusted browser-data boundary."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar


T = TypeVar("T")
_ASCII_COUNT = re.compile(r"[0-9]+\Z")
MAX_SAFE_COUNT = 9_007_199_254_740_991
MAX_SAFE_COUNT_DIGITS = len(str(MAX_SAFE_COUNT))


@dataclass(frozen=True, slots=True)
class MissingField:
    pass


@dataclass(frozen=True, slots=True)
class InvalidField:
    reason: str

    def __post_init__(self) -> None:
        if type(self.reason) is not str or not self.reason:
            raise ValueError("invalid observation needs a reason")


@dataclass(frozen=True, slots=True)
class PresentField(Generic[T]):
    value: T


FieldObservation: TypeAlias = MissingField | InvalidField | PresentField[T]


def parse_nonnegative_count(value: object) -> FieldObservation[int]:
    if value is None:
        return MissingField()
    if type(value) is int:
        if value < 0:
            return InvalidField("negative")
        if value > MAX_SAFE_COUNT:
            return InvalidField("out-of-range")
        return PresentField(value)
    if type(value) is str:
        text = value.strip()
        if not text:
            return InvalidField("empty")
        if _ASCII_COUNT.fullmatch(text):
            if len(text) > MAX_SAFE_COUNT_DIGITS:
                return InvalidField("out-of-range")
            count = int(text)
            if count > MAX_SAFE_COUNT:
                return InvalidField("out-of-range")
            return PresentField(count)
    return InvalidField("not-integer")


@dataclass(frozen=True, slots=True)
class QuantityRow:
    key: str
    name: str
    text: str
    disabled: bool
    remaining_present: bool
    remaining_raw: str
    value: str

    def __post_init__(self) -> None:
        for name in ("key", "name", "text", "remaining_raw", "value"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")
        if type(self.disabled) is not bool or type(self.remaining_present) is not bool:
            raise ValueError("quantity flags must be booleans")

    @property
    def remaining_observation(self) -> FieldObservation[int]:
        if not self.remaining_present:
            return MissingField() if not self.remaining_raw else InvalidField("presence-mismatch")
        return parse_nonnegative_count(self.remaining_raw)

    @property
    def value_observation(self) -> FieldObservation[int]:
        return parse_nonnegative_count(self.value)


@dataclass(frozen=True, slots=True)
class AreaRow:
    area: str
    price: str
    status: str
    rel: str

    def __post_init__(self) -> None:
        for name in ("area", "price", "status", "rel"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")


@dataclass(frozen=True, slots=True)
class MapArea:
    area_id: str
    href: str
    title: str

    def __post_init__(self) -> None:
        for name in ("area_id", "href", "title"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")


DATE_CONTROL_FINGERPRINT_FIELDS = (
    "tag_name", "id", "href", "resolved_href", "role", "type",
    "target", "download", "ping", "formaction", "has_form", "onclick",
)

DateFingerprint: TypeAlias = tuple[tuple[str, str | bool], ...]


@dataclass(frozen=True, slots=True)
class DateControl:
    actionable: bool
    fingerprint: DateFingerprint

    def __post_init__(self) -> None:
        if type(self.actionable) is not bool:
            raise ValueError("actionable must be a boolean")
        if type(self.fingerprint) is not tuple or len(self.fingerprint) != len(
            DATE_CONTROL_FINGERPRINT_FIELDS
        ):
            raise ValueError("fingerprint must contain every control field")
        for expected_name, item in zip(DATE_CONTROL_FINGERPRINT_FIELDS, self.fingerprint):
            if (
                type(item) is not tuple
                or len(item) != 2
                or item[0] != expected_name
                or type(item[1]) is not (bool if expected_name == "has_form" else str)
            ):
                raise ValueError(f"invalid control fingerprint field: {expected_name}")


@dataclass(frozen=True, slots=True)
class DateRow:
    text: str
    controls: tuple[DateControl, ...]

    def __post_init__(self) -> None:
        if type(self.text) is not str or type(self.controls) is not tuple or any(
            not isinstance(control, DateControl) for control in self.controls
        ):
            raise ValueError("date row must contain text and controls")


SeatFingerprint: TypeAlias = tuple[str | int | float | bool, ...]


def _valid_seat_fingerprint(value: object) -> bool:
    return (
        type(value) is tuple
        and bool(value)
        and all(type(item) in (str, int, float, bool) for item in value)
        and all(type(item) is not float or math.isfinite(item) for item in value)
    )


@dataclass(frozen=True, slots=True)
class TicketTypeRow:
    fingerprint: SeatFingerprint
    text: str
    disabled: bool
    type_id: str
    type_name: str
    counter_present: bool
    counter_raw: str

    def __post_init__(self) -> None:
        if not _valid_seat_fingerprint(self.fingerprint):
            raise ValueError("ticket type fingerprint is invalid")
        for name in ("text", "type_id", "type_name", "counter_raw"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")
        if type(self.disabled) is not bool or type(self.counter_present) is not bool:
            raise ValueError("ticket type flags must be booleans")


@dataclass(frozen=True, slots=True)
class SeatRow:
    fingerprint: SeatFingerprint
    title: str
    row_index: int
    cell_index: int
    center_x: int | float
    center_y: int | float
    width: int | float
    height: int | float
    available: bool
    selected: bool

    def __post_init__(self) -> None:
        if not _valid_seat_fingerprint(self.fingerprint) or type(self.title) is not str:
            raise ValueError("seat identity is invalid")
        if type(self.row_index) is not int or type(self.cell_index) is not int:
            raise ValueError("seat indexes must be integers")
        for name in ("center_x", "center_y", "width", "height"):
            value = getattr(self, name)
            if type(value) not in (int, float):
                raise ValueError(f"{name} must be numeric")
            try:
                if not math.isfinite(value):
                    raise ValueError(f"{name} must be finite")
            except OverflowError as error:
                raise ValueError(f"{name} must be finite") from error
        if type(self.available) is not bool or type(self.selected) is not bool:
            raise ValueError("seat flags must be booleans")


@dataclass(frozen=True, slots=True)
class SubmittedSeat:
    a: str
    y: str
    z: str
    key: str = ""

    def __post_init__(self) -> None:
        if any(type(getattr(self, name)) is not str for name in ("a", "y", "z", "key")):
            raise ValueError("submitted seat values must be strings")


@dataclass(frozen=True, slots=True)
class SeatCounter:
    type_id: str
    raw: str

    def __post_init__(self) -> None:
        if type(self.type_id) is not str or type(self.raw) is not str:
            raise ValueError("seat counter values must be strings")


@dataclass(frozen=True, slots=True)
class DateSnapshot:
    ok: bool
    page_url: str
    document_id: str
    rows: tuple[DateRow, ...]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AreaSnapshot:
    ok: bool
    page_url: str
    document_id: str
    rows: tuple[AreaRow, ...]
    areas: tuple[MapArea, ...]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class QuantitySnapshot:
    ok: bool
    page_url: str
    document_id: str
    performance_id: str
    group_id: str
    price_area_id: str
    quantity_limit_raw: str
    rows: tuple[QuantityRow, ...]
    product_id: str = ""
    reason: str = ""
    performance_hidden_present: bool = False
    product_hidden_present: bool = False
    group_hidden_present: bool = False
    price_area_hidden_present: bool = False
    quantity_limit_present: bool = False


@dataclass(frozen=True, slots=True)
class SeatSnapshot:
    ok: bool
    page_url: str
    document_id: str
    performance_id: str = ""
    group_id: str = ""
    price_area_id: str = ""
    product_id: str = ""
    product_hidden_present: bool = False
    quantity_limit_present: bool = False
    quantity_limit_raw: str = ""
    ticket_types: tuple[TicketTypeRow, ...] = ()
    seats: tuple[SeatRow, ...] = ()
    submitted_seats: tuple[SubmittedSeat, ...] = ()
    counters: tuple[SeatCounter, ...] = ()
    visual_selected_count: int = 0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DialogSnapshot:
    page_url: str
    document_id: str
    visible_count: int
    text: str
