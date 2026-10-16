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
