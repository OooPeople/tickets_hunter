"""Pure validation for the current uTiki behavior configuration."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Mapping, cast
from urllib.parse import urlsplit


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
_SELECTION_MODES = frozenset({
    "from top to bottom", "from bottom to top", "center", "random",
})
_MAX_SAFE_COUNT = 9_007_199_254_740_991
UTIKI_SPORTS_HOSTS = frozenset({"tix.ctbcsports.com", "tix.wdragons.com"})


def is_supported_utiki_sports_url(url: str | None) -> bool:
    """Match the supported HTTPS origins without accepting userinfo or alternate ports."""
    try:
        parsed = urlsplit(str(url or ""))
        return bool(
            parsed.scheme.lower() == "https"
            and (parsed.hostname or "").lower() in UTIKI_SPORTS_HOSTS
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


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


@dataclass(frozen=True, slots=True)
class FlowSettings:
    """Validated current flow values shared by Settings and the Run owner."""

    date_enabled: bool
    area_enabled: bool
    ticket_number: int
    date_keyword: str = ""
    date_mode: str = "from top to bottom"
    date_fallback: bool = False
    area_keyword: str = ""
    area_mode: str = "from top to bottom"
    area_fallback: bool = False
    keyword_exclude: str = ""
    allow_non_adjacent: bool = False
    ocr_enabled: bool = False
    force_submit: bool = False
    ocr_beta: bool = False
    ocr_image_source: str = ""
    ocr_use_universal: bool = False
    ocr_path: str = ""
    refresh_interval: int | float = 0
    refresh_datetime: str = ""

    def __post_init__(self) -> None:
        for name in (
            "date_enabled", "area_enabled", "date_fallback", "area_fallback",
            "allow_non_adjacent", "ocr_enabled", "force_submit", "ocr_beta",
            "ocr_use_universal",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if type(self.ticket_number) is not int or not 0 < self.ticket_number <= _MAX_SAFE_COUNT:
            raise ValueError("ticket number must be a positive bounded integer")
        for name in (
            "date_keyword", "date_mode", "area_keyword", "area_mode",
            "keyword_exclude", "ocr_image_source", "ocr_path", "refresh_datetime",
        ):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")
        if self.date_mode not in _SELECTION_MODES or self.area_mode not in _SELECTION_MODES:
            raise ValueError("selection mode must be a current supported value")
        if type(self.refresh_interval) not in (int, float) or not 0 <= self.refresh_interval <= 3600:
            raise ValueError("refresh interval must be a bounded nonnegative number")
        if self.refresh_datetime.strip():
            format_string = (
                "%Y/%m/%d %H:%M:%S" if "/" in self.refresh_datetime
                else "%H:%M:%S"
            )
            try:
                datetime.strptime(self.refresh_datetime.strip(), format_string)
            except ValueError as exc:
                raise ValueError("refresh datetime must use a current supported format") from exc

    @property
    def auto_submit(self) -> bool:
        return self.ocr_enabled and self.force_submit

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


def _section(profile: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = profile.get(name)
    if not isinstance(value, dict):
        raise ConfigError("INVALID_SECTION", name)
    return value


def _required(mapping: Mapping[str, object], key: str, kind: type, section: str) -> object:
    value = mapping.get(key)
    if type(value) is not kind:
        raise ConfigError("INVALID_CONFIG", f"{section}.{key}")
    return value


def load_current_profile_policy(
    profile: Mapping[str, object],
) -> tuple[UtikiBehavior, FlowSettings]:
    """Validate the full current profile without defaults or migration."""
    behavior = load_current_policy(profile)
    date = _section(profile, "date_auto_select")
    area = _section(profile, "area_auto_select")
    advanced = _section(profile, "advanced")
    ocr = _section(profile, "ocr_captcha")
    fields = {
        "date_enabled": _required(date, "enable", bool, "date_auto_select"),
        "area_enabled": _required(area, "enable", bool, "area_auto_select"),
        "ticket_number": _required(profile, "ticket_number", int, "profile"),
        "date_keyword": _required(date, "date_keyword", str, "date_auto_select"),
        "date_mode": _required(date, "mode", str, "date_auto_select"),
        "date_fallback": _required(profile, "date_auto_fallback", bool, "profile"),
        "area_keyword": _required(area, "area_keyword", str, "area_auto_select"),
        "area_mode": _required(area, "mode", str, "area_auto_select"),
        "area_fallback": _required(profile, "area_auto_fallback", bool, "profile"),
        "keyword_exclude": _required(profile, "keyword_exclude", str, "profile"),
        "allow_non_adjacent": _required(advanced, "disable_adjacent_seat", bool, "advanced"),
        "ocr_enabled": _required(ocr, "enable", bool, "ocr_captcha"),
        "force_submit": _required(ocr, "force_submit", bool, "ocr_captcha"),
        "ocr_beta": _required(ocr, "beta", bool, "ocr_captcha"),
        "ocr_image_source": _required(ocr, "image_source", str, "ocr_captcha"),
        "ocr_use_universal": _required(ocr, "use_universal", bool, "ocr_captcha"),
        "ocr_path": _required(ocr, "path", str, "ocr_captcha"),
        "refresh_datetime": _required(profile, "refresh_datetime", str, "profile"),
    }
    interval = advanced.get("auto_reload_page_interval")
    if type(interval) not in (int, float):
        raise ConfigError("INVALID_CONFIG", "advanced.auto_reload_page_interval")
    fields["refresh_interval"] = interval
    try:
        return behavior, FlowSettings(**fields)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ConfigError("INVALID_CONFIG", "utiki-effective-policy") from exc


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
