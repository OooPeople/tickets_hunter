"""Atomic effective policy for one uTiki Run.

The pure current profile validator lives at the root configuration boundary so
Settings and the Run consume the same typed values without reverse imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from utiki_config import (
    ConfigError, FlowSettings as FlowSettings, UtikiBehavior, load_current_profile_policy,
    parse_profile_json,
)


@dataclass(frozen=True, slots=True, repr=False)
class EffectivePolicy:
    """The behavior and every currently relevant profile option change together."""

    last_valid: UtikiBehavior | None
    flow: FlowSettings | None
    policy_revision: int
    control_revision: int
    block_reason: str | None

    def __post_init__(self) -> None:
        if (self.last_valid is None) != (self.flow is None):
            raise ValueError("effective policy requires behavior and flow together")
        if self.last_valid is not None and not isinstance(self.last_valid, UtikiBehavior):
            raise ValueError("effective behavior must be typed")
        if self.flow is not None and not isinstance(self.flow, FlowSettings):
            raise ValueError("effective flow must be typed")
        if type(self.policy_revision) is not int or self.policy_revision < 0:
            raise ValueError("invalid effective policy revision")
        if type(self.control_revision) is not int or self.control_revision < 0:
            raise ValueError("invalid effective control revision")
        if self.last_valid is None and self.policy_revision != 0:
            raise ValueError("policy revision requires a validated value")
        if self.last_valid is not None and (
            self.policy_revision == 0 or self.control_revision == 0
        ):
            raise ValueError("validated policy requires positive revisions")
        if self.block_reason is None and self.last_valid is None:
            raise ValueError("active policy requires a validated value")
        if self.block_reason is not None and (
            type(self.block_reason) is not str or not self.block_reason
        ):
            raise ValueError("invalid policy block reason")

    @property
    def allows_new_mutation(self) -> bool:
        return self.block_reason is None


def load_effective_policy(profile: Mapping[str, object]) -> tuple[UtikiBehavior, FlowSettings]:
    """Use the same pure current validator as profile save."""
    return load_current_profile_policy(profile)


def apply_effective_policy_reload(
    previous: EffectivePolicy | None, raw_text: str,
) -> EffectivePolicy:
    """Replace the whole semantic snapshot or block new effects atomically."""
    old = previous or EffectivePolicy(None, None, 0, 0, "NOT_LOADED")
    if not isinstance(old, EffectivePolicy):
        raise ValueError("reload requires an effective policy")
    try:
        behavior, flow = load_effective_policy(parse_profile_json(raw_text))
    except ConfigError as exc:
        return EffectivePolicy(
            old.last_valid, old.flow, old.policy_revision,
            old.control_revision + int(old.allows_new_mutation), exc.code,
        )
    changed = (behavior, flow) != (old.last_valid, old.flow)
    return EffectivePolicy(
        behavior, flow, old.policy_revision + int(changed),
        old.control_revision + int(not old.allows_new_mutation), None,
    )
