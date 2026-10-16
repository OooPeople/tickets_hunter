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


_POLICY_BLOCK_CODES = frozenset({
    "NOT_LOADED",
    "DUPLICATE_JSON_KEY",
    "INVALID_JSON_NUMBER",
    "INVALID_JSON",
    "INVALID_ROOT",
    "UNSUPPORTED_SCHEMA",
    "INVALID_SECTION",
    "UNKNOWN_BEHAVIOR_KEY",
    "INVALID_BEHAVIOR_KEYS",
    "INVALID_BEHAVIOR",
    "UPGRADE_REQUIRED",
    "INVALID_CONFIG",
})


@dataclass(frozen=True, slots=True, repr=False)
class CurrentPolicyState:
    last_valid: UtikiBehavior | None
    policy_revision: int
    control_revision: int
    block_reason: str | None

    def __post_init__(self) -> None:
        if self.last_valid is not None and not isinstance(self.last_valid, UtikiBehavior):
            raise ValueError("last valid policy must be typed")
        for name in ("policy_revision", "control_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.last_valid is None and self.policy_revision != 0:
            raise ValueError("policy revision requires a valid policy")
        if self.last_valid is not None and (
            self.policy_revision == 0 or self.control_revision == 0
        ):
            raise ValueError("valid policy requires positive revisions")
        if self.block_reason is None and self.last_valid is None:
            raise ValueError("active policy state requires a valid policy")
        if self.block_reason is not None and (
            type(self.block_reason) is not str or self.block_reason not in _POLICY_BLOCK_CODES
        ):
            raise ValueError("block reason must be a known code")

    @property
    def allows_new_mutation(self) -> bool:
        return self.block_reason is None


def apply_current_policy_reload(
    previous: CurrentPolicyState | None, raw_text: str
) -> CurrentPolicyState:
    """Retain attribution data after invalid reload, but revoke new effects."""
    if previous is not None and not isinstance(previous, CurrentPolicyState):
        raise ValueError("reload requires a current policy state")
    state = previous or CurrentPolicyState(None, 0, 0, "NOT_LOADED")
    try:
        policy = load_current_policy(parse_profile_json(raw_text))
    except ConfigError as exc:
        reason = exc.code if exc.code in _POLICY_BLOCK_CODES else "INVALID_CONFIG"
        return CurrentPolicyState(
            state.last_valid,
            state.policy_revision,
            state.control_revision + int(state.allows_new_mutation),
            reason,
        )
    return CurrentPolicyState(
        policy,
        state.policy_revision + int(policy != state.last_valid),
        state.control_revision + int(not state.allows_new_mutation),
        None,
    )


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
