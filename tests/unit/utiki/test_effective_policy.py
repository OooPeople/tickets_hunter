"""The isolated owner receives one atomic, validated policy snapshot."""

import json

from platforms.utiki.application.flow_settings import apply_effective_policy_reload


def _profile() -> dict[str, object]:
    return {
        "utiki": {
            "ticket_type_keyword": "全票", "seat_sorting": "row_seat",
            "seat_selection_mode": "manual",
        },
        "date_auto_select": {
            "enable": True, "date_keyword": "9/10", "mode": "from top to bottom",
        },
        "area_auto_select": {
            "enable": True, "area_keyword": "前排", "mode": "from top to bottom",
        },
        "date_auto_fallback": False,
        "area_auto_fallback": False,
        "keyword_exclude": "",
        "ticket_number": 2,
        "advanced": {
            "disable_adjacent_seat": False, "auto_reload_page_interval": 5,
        },
        "ocr_captcha": {
            "enable": True, "force_submit": True, "beta": True,
            "image_source": "canvas", "use_universal": True,
            "path": "synthetic-model",
        },
        "refresh_datetime": "",
    }


def _load(previous, profile):
    return apply_effective_policy_reload(
        previous, json.dumps(profile, ensure_ascii=False),
    )


def test_effective_policy_tracks_all_current_decision_sources_atomically() -> None:
    profile = _profile()
    first = _load(None, profile)
    assert first.allows_new_mutation and first.policy_revision == 1
    assert first.flow is not None and first.flow.auto_submit
    assert first.flow.ticket_number == 2
    assert _load(first, {**profile, "unrelated_platform": {"changed": True}}) == first

    for section, key, value in (
        (None, "ticket_number", 4),
        ("date_auto_select", "date_keyword", "10/10"),
        ("area_auto_select", "enable", False),
        ("advanced", "disable_adjacent_seat", True),
        ("advanced", "auto_reload_page_interval", 8),
        ("ocr_captcha", "enable", False),
        ("ocr_captcha", "force_submit", False),
    ):
        changed = _profile()
        target = changed if section is None else changed[section]
        assert isinstance(target, dict)
        target[key] = value
        next_state = _load(first, changed)
        assert next_state.policy_revision == 2
        assert next_state.control_revision == 1
        assert next_state.flow != first.flow
        if section == "ocr_captcha" and key == "enable":
            assert next_state.flow is not None and not next_state.flow.auto_submit


def test_invalid_reload_blocks_new_work_without_erasing_prior_effects() -> None:
    first = _load(None, _profile())
    broken = _profile()
    broken["ticket_number"] = True
    blocked = _load(first, broken)
    assert not blocked.allows_new_mutation
    assert blocked.policy_revision == first.policy_revision
    assert blocked.control_revision == first.control_revision + 1
    assert blocked.last_valid == first.last_valid and blocked.flow == first.flow
    assert _load(blocked, _profile()).allows_new_mutation


def test_force_submit_without_ocr_does_not_enable_automatic_submit() -> None:
    profile = _profile()
    ocr = profile["ocr_captcha"]
    assert isinstance(ocr, dict)
    ocr["enable"] = False
    state = _load(None, profile)
    assert state.flow is not None and not state.flow.auto_submit


def test_unknown_selection_mode_blocks_policy_instead_of_defaulting_to_first_row() -> None:
    valid = _load(None, _profile())
    for section in ("date_auto_select", "area_auto_select"):
        profile = _profile()
        selector = profile[section]
        assert isinstance(selector, dict)
        selector["mode"] = "unrecognized"
        blocked = _load(valid, profile)
        assert not blocked.allows_new_mutation
        assert blocked.last_valid == valid.last_valid
        assert blocked.flow == valid.flow


def test_invalid_reload_and_legacy_mirror_keep_original_policy_and_revisions() -> None:
    initial = _load(None, _profile())
    blocked = apply_effective_policy_reload(initial, '{"utiki":')
    repeated = apply_effective_policy_reload(blocked, '{"utiki":')
    assert blocked.block_reason == "INVALID_JSON"
    assert not blocked.allows_new_mutation
    assert blocked.last_valid == initial.last_valid
    assert blocked.policy_revision == initial.policy_revision
    assert blocked.control_revision == initial.control_revision + 1
    assert repeated.control_revision == blocked.control_revision

    mirrored = _profile()
    mirrored["ctbc"] = dict(mirrored["utiki"])
    legacy = _load(repeated, mirrored)
    assert legacy.block_reason == "UPGRADE_REQUIRED"
    assert legacy.last_valid == initial.last_valid

    restored = _load(legacy, _profile())
    assert restored.allows_new_mutation
    assert restored.policy_revision == initial.policy_revision
    assert restored.control_revision == blocked.control_revision + 1


def test_new_valid_behavior_changes_revision_only_after_successful_reload() -> None:
    initial = _load(None, _profile())
    invalid = _load(initial, {**_profile(), "utiki": {}})
    changed_profile = _profile()
    behavior = changed_profile["utiki"]
    assert isinstance(behavior, dict)
    behavior["ticket_type_keyword"] = "半票"
    changed = _load(invalid, changed_profile)
    assert invalid.block_reason == "INVALID_BEHAVIOR_KEYS"
    assert invalid.policy_revision == initial.policy_revision
    assert changed.policy_revision == initial.policy_revision + 1
    assert _load(changed, changed_profile) == changed


def test_invalid_initial_policy_and_diagnostics_do_not_expose_values() -> None:
    initial = apply_effective_policy_reload(None, '{"ctbc": {}}')
    assert initial.block_reason == "UPGRADE_REQUIRED"
    assert initial.policy_revision == 0
    profile = _profile()
    behavior = profile["utiki"]
    assert isinstance(behavior, dict)
    behavior["ticket_type_keyword"] = "fixture-secret-keyword"
    active = _load(None, profile)
    blocked = apply_effective_policy_reload(active, '{"utiki":')
    assert "fixture-secret-keyword" not in repr(blocked)
