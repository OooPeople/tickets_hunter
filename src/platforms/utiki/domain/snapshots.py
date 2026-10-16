"""Typed field observations at the untrusted browser-data boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar


T = TypeVar("T")
_ASCII_COUNT = re.compile(r"[0-9]+\Z")
_MAX_SAFE_COUNT = 9_007_199_254_740_991
_MAX_SAFE_COUNT_DIGITS = len(str(_MAX_SAFE_COUNT))


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
        if value > _MAX_SAFE_COUNT:
            return InvalidField("out-of-range")
        return PresentField(value)
    if type(value) is str:
        text = value.strip()
        if not text:
            return InvalidField("empty")
        if _ASCII_COUNT.fullmatch(text):
            if len(text) > _MAX_SAFE_COUNT_DIGITS:
                return InvalidField("out-of-range")
            count = int(text)
            if count > _MAX_SAFE_COUNT:
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
