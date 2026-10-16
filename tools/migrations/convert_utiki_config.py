"""Offline-only profile projection; file replacement is a separate step."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Literal, cast

from utiki_config import (
    DEFAULT_BEHAVIOR,
    ConfigError,
    UpgradeKind,
    inspect_upgrade_input,
    load_current_policy,
)


ConflictPolicy = Literal["canonical", "legacy"]


def convert_profile_mapping(
    profile: dict[str, object], *, conflict_policy: ConflictPolicy | None = None
) -> dict[str, object]:
    """Return canonical behavior while keeping every unrelated parsed value."""
    if conflict_policy not in (None, "canonical", "legacy"):
        raise ConfigError("INVALID_CONFLICT_POLICY")
    inspection = inspect_upgrade_input(profile)
    if inspection.kind is UpgradeKind.MIXED_CONFLICT:
        if conflict_policy is None:
            raise ConfigError("CONFLICT_POLICY_REQUIRED")
        canonical = cast(dict[str, str], profile["utiki"])
        legacy = cast(dict[str, str], profile["ctbc"])
        values = asdict(DEFAULT_BEHAVIOR)
        if conflict_policy == "canonical":
            values.update(legacy)
            values.update(canonical)
        else:
            values.update(canonical)
            values.update(legacy)
        policy = load_current_policy({"utiki": values})
    else:
        projected = inspection.projected
        if projected is None:
            raise ConfigError("INVALID_UPGRADE_INPUT")
        policy = projected
    converted = deepcopy(profile)
    converted.pop("ctbc", None)
    converted["utiki"] = asdict(policy)
    return converted
