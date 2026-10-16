"""Pure validation for the current uTiki behavior configuration."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Mapping, cast


SeatSorting = Literal[
    "display",
    "row_seat",
    "row_asc_aisle",
    "row_asc_seat_desc",
    "row_desc_seat_asc",
]
SeatSelectionMode = Literal["manual", "computer"]

BEHAVIOR_KEYS = frozenset(
    {"ticket_type_keyword", "seat_sorting", "seat_selection_mode"}
)
BEHAVIOR_ORDER = ("ticket_type_keyword", "seat_sorting", "seat_selection_mode")
SEAT_SORTINGS = frozenset(
    {
        "display",
        "row_seat",
        "row_asc_aisle",
        "row_asc_seat_desc",
        "row_desc_seat_asc",
    }
)
SEAT_SELECTION_MODES = frozenset({"manual", "computer"})


class ConfigError(ValueError):
    def __init__(self, code: str, key: str | None = None) -> None:
        self.code = code
        self.key = key
        super().__init__(code if key is None else f"{code}: {key}")


@dataclass(frozen=True, slots=True)
class UtikiBehavior:
    ticket_type_keyword: str
    seat_sorting: SeatSorting
    seat_selection_mode: SeatSelectionMode

    def __post_init__(self) -> None:
        if type(self.ticket_type_keyword) is not str:
            raise ConfigError("INVALID_BEHAVIOR", "ticket_type_keyword")
        if self.seat_sorting not in SEAT_SORTINGS:
            raise ConfigError("INVALID_BEHAVIOR", "seat_sorting")
        if self.seat_selection_mode not in SEAT_SELECTION_MODES:
            raise ConfigError("INVALID_BEHAVIOR", "seat_selection_mode")


DEFAULT_BEHAVIOR = UtikiBehavior("全票", "row_seat", "manual")


class UpgradeKind(str, Enum):
    CANONICAL_ONLY = "canonical-only"
    LEGACY_ONLY = "legacy-only"
    MIXED_EQUAL = "mixed-equal"
    MIXED_COMPLEMENTARY = "mixed-complementary"
    MIXED_CONFLICT = "mixed-conflict"


@dataclass(frozen=True, slots=True)
class UpgradeInspection:
    kind: UpgradeKind
    projected: UtikiBehavior | None
    conflicting_keys: tuple[str, ...] = ()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("DUPLICATE_JSON_KEY", key)
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ConfigError("INVALID_JSON_NUMBER")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError("INVALID_JSON_NUMBER")
    return result


def parse_profile_json(text: str) -> dict[str, object]:
    """Parse one profile without defaults, writes, or silent JSON extensions."""
    try:
        value: object = json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except json.JSONDecodeError as exc:
        raise ConfigError("INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ConfigError("INVALID_ROOT")
    return cast(dict[str, object], value)


def _check_schema(profile: Mapping[str, object]) -> None:
    if "schema_version" in profile:
        raise ConfigError("UNSUPPORTED_SCHEMA", "schema_version")


def _behavior_section(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise ConfigError("INVALID_SECTION", name)
    return cast(Mapping[str, object], value)


def _validated_values(value: object, name: str, *, complete: bool) -> dict[str, str]:
    section = _behavior_section(value, name)
    unknown = sorted(section.keys() - BEHAVIOR_KEYS)
    if unknown:
        raise ConfigError("UNKNOWN_BEHAVIOR_KEY", f"{name}.{unknown[0]}")
    if complete and section.keys() != BEHAVIOR_KEYS:
        raise ConfigError("INVALID_BEHAVIOR_KEYS", name)
    result: dict[str, str] = {}
    for key in BEHAVIOR_ORDER:
        if key not in section:
            continue
        candidate = section[key]
        if type(candidate) is not str:
            raise ConfigError("INVALID_BEHAVIOR", key)
        if key == "seat_sorting" and candidate not in SEAT_SORTINGS:
            raise ConfigError("INVALID_BEHAVIOR", key)
        if key == "seat_selection_mode" and candidate not in SEAT_SELECTION_MODES:
            raise ConfigError("INVALID_BEHAVIOR", key)
        result[key] = candidate
    return result


def _policy(values: Mapping[str, str]) -> UtikiBehavior:
    return UtikiBehavior(
        values["ticket_type_keyword"],
        cast(SeatSorting, values["seat_sorting"]),
        cast(SeatSelectionMode, values["seat_selection_mode"]),
    )


def load_current_policy(profile: Mapping[str, object]) -> UtikiBehavior:
    """Validate canonical behavior without interpreting the old mirror."""
    _check_schema(profile)
    if "ctbc" in profile:
        raise ConfigError("UPGRADE_REQUIRED")
    return _policy(_validated_values(profile.get("utiki"), "utiki", complete=True))


def inspect_upgrade_input(profile: Mapping[str, object]) -> UpgradeInspection:
    """Classify offline conversion inputs without changing or writing them."""
    _check_schema(profile)
    if "ctbc" not in profile:
        current = load_current_policy(profile)
        return UpgradeInspection(UpgradeKind.CANONICAL_ONLY, current)

    legacy = _validated_values(profile["ctbc"], "ctbc", complete=False)
    if "utiki" not in profile:
        values = {
            "ticket_type_keyword": DEFAULT_BEHAVIOR.ticket_type_keyword,
            "seat_sorting": DEFAULT_BEHAVIOR.seat_sorting,
            "seat_selection_mode": DEFAULT_BEHAVIOR.seat_selection_mode,
            **legacy,
        }
        return UpgradeInspection(UpgradeKind.LEGACY_ONLY, _policy(values))

    canonical = _validated_values(profile["utiki"], "utiki", complete=False)
    conflicts = tuple(
        key
        for key in BEHAVIOR_ORDER
        if key in canonical and key in legacy and canonical[key] != legacy[key]
    )
    if conflicts:
        return UpgradeInspection(UpgradeKind.MIXED_CONFLICT, None, conflicts)
    values = {
        "ticket_type_keyword": DEFAULT_BEHAVIOR.ticket_type_keyword,
        "seat_sorting": DEFAULT_BEHAVIOR.seat_sorting,
        "seat_selection_mode": DEFAULT_BEHAVIOR.seat_selection_mode,
        **legacy,
        **canonical,
    }
    kind = (
        UpgradeKind.MIXED_EQUAL
        if canonical.keys() == BEHAVIOR_KEYS and legacy.keys() == BEHAVIOR_KEYS
        else UpgradeKind.MIXED_COMPLEMENTARY
    )
    return UpgradeInspection(kind, _policy(values))
