"""Current-policy reload never authorizes effects with invalid fresh input."""

import json

import pytest

from utiki_config import CurrentPolicyState, UtikiBehavior, apply_current_policy_reload


def _profile(keyword: str = "全票") -> str:
    return json.dumps(
        {"utiki": {
            "ticket_type_keyword": keyword,
            "seat_sorting": "row_seat",
            "seat_selection_mode": "manual",
        }},
        ensure_ascii=False,
    )


def test_invalid_reload_retains_attribution_policy_but_blocks_new_mutation() -> None:
    active = apply_current_policy_reload(None, _profile())
    blocked = apply_current_policy_reload(active, '{"utiki":')

    assert active.allows_new_mutation
    assert active.last_valid is not None
    assert active.policy_revision == 1
    assert blocked.last_valid is active.last_valid
    assert blocked.policy_revision == active.policy_revision
    assert blocked.control_revision == active.control_revision + 1
    assert blocked.block_reason == "INVALID_JSON"
    assert not blocked.allows_new_mutation


def test_legacy_mirror_reloads_never_fallback_even_when_equal() -> None:
    active = apply_current_policy_reload(None, _profile())
    mirror = json.loads(_profile())
    mirror["ctbc"] = mirror["utiki"].copy()

    blocked = apply_current_policy_reload(active, json.dumps(mirror))

    assert blocked.block_reason == "UPGRADE_REQUIRED"
    assert blocked.last_valid is active.last_valid
    assert not blocked.allows_new_mutation


def test_valid_same_policy_recovers_gate_without_recompiling_revision() -> None:
    active = apply_current_policy_reload(None, _profile())
    blocked = apply_current_policy_reload(active, '{"utiki":')
    repeated_invalid = apply_current_policy_reload(blocked, '{"utiki":')
    restored = apply_current_policy_reload(repeated_invalid, _profile())

    assert repeated_invalid.control_revision == blocked.control_revision
    assert restored.allows_new_mutation
    assert restored.block_reason is None
    assert restored.policy_revision == active.policy_revision
    assert restored.control_revision == blocked.control_revision + 1
    assert restored.last_valid is not None
    assert restored.last_valid.ticket_type_keyword == "全票"


def test_new_valid_policy_changes_revision_only_after_successful_load() -> None:
    active = apply_current_policy_reload(None, _profile())
    invalid = apply_current_policy_reload(active, '{"utiki": {}}')
    changed = apply_current_policy_reload(invalid, _profile("半票"))
    repeated = apply_current_policy_reload(changed, _profile("半票"))

    assert invalid.policy_revision == active.policy_revision
    assert invalid.block_reason == "INVALID_BEHAVIOR_KEYS"
    assert changed.allows_new_mutation
    assert changed.policy_revision == active.policy_revision + 1
    assert repeated.policy_revision == changed.policy_revision
    assert repeated.control_revision == changed.control_revision


def test_invalid_initial_input_stays_blocked_without_default_fallback() -> None:
    state = apply_current_policy_reload(None, '{"ctbc": {}}')

    assert state.last_valid is None
    assert state.block_reason == "UPGRADE_REQUIRED"
    assert state.policy_revision == 0
    assert not state.allows_new_mutation


def test_invalid_state_combinations_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="active policy state"):
        CurrentPolicyState(None, 0, 0, None)
    with pytest.raises(ValueError, match="policy_revision"):
        CurrentPolicyState(None, True, 0, "NOT_LOADED")
    with pytest.raises(ValueError, match="policy revision requires"):
        CurrentPolicyState(None, 7, 3, "NOT_LOADED")
    policy = UtikiBehavior("fixture-secret-keyword", "row_seat", "manual")
    with pytest.raises(ValueError, match="positive revisions"):
        CurrentPolicyState(policy, 0, 0, None)
    with pytest.raises(ValueError, match="known code"):
        CurrentPolicyState(policy, 1, 1, "INVALID_JSON password=fixture-private-value")
    valid = CurrentPolicyState(policy, 1, 1, None)
    assert "fixture-secret-keyword" not in repr(valid)


def test_invalid_reload_representation_does_not_expose_prior_policy_value() -> None:
    active = apply_current_policy_reload(None, _profile("fixture-secret-keyword"))
    blocked = apply_current_policy_reload(active, '{"utiki":')

    assert blocked.block_reason == "INVALID_JSON"
    assert "fixture-secret-keyword" not in repr(blocked)
