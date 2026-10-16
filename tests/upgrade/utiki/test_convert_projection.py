"""The offline projection cannot silently choose or erase profile data."""

from copy import deepcopy

import pytest

from tools.migrations.convert_utiki_config import convert_profile_mapping
from utiki_config import ConfigError, load_current_policy, parse_profile_json


def _canonical() -> dict[str, str]:
    return {
        "ticket_type_keyword": "全票",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }


def test_mixed_equal_projection_preserves_current_values_and_other_sections() -> None:
    profile: dict[str, object] = {
        "utiki": _canonical(),
        "ctbc": _canonical(),
        "accounts": {"ctbc_account": "fixture-only"},
        "other_platform": {"seat_keyword": "unchanged"},
    }
    original = deepcopy(profile)

    converted = convert_profile_mapping(profile)

    assert profile == original
    assert "ctbc" not in converted
    assert converted["utiki"] == _canonical()
    assert converted["accounts"] == original["accounts"]
    assert converted["other_platform"] == original["other_platform"]
    assert converted["accounts"] is not profile["accounts"]
    assert load_current_policy(converted).ticket_type_keyword == "全票"


def test_legacy_and_complementary_projection_keep_explicit_empty_value() -> None:
    legacy: dict[str, object] = {"ctbc": {"ticket_type_keyword": ""}}
    converted_legacy = convert_profile_mapping(legacy)
    assert converted_legacy["utiki"] == {
        "ticket_type_keyword": "",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }
    complementary: dict[str, object] = {
        "utiki": {"ticket_type_keyword": ""},
        "ctbc": {"seat_sorting": "display", "seat_selection_mode": "computer"},
    }
    converted_complementary = convert_profile_mapping(complementary)
    assert converted_complementary["utiki"] == {
        "ticket_type_keyword": "",
        "seat_sorting": "display",
        "seat_selection_mode": "computer",
    }
    assert load_current_policy(converted_complementary).seat_sorting == "display"


def test_mixed_conflict_requires_explicit_side_and_only_resolves_conflicts() -> None:
    profile: dict[str, object] = {
        "utiki": {"ticket_type_keyword": "", "seat_sorting": "row_seat"},
        "ctbc": {"ticket_type_keyword": "全票", "seat_selection_mode": "computer"},
    }
    before = deepcopy(profile)
    with pytest.raises(ConfigError) as error:
        convert_profile_mapping(profile)
    assert error.value.code == "CONFLICT_POLICY_REQUIRED"
    assert convert_profile_mapping(profile, conflict_policy="canonical")["utiki"] == {
        "ticket_type_keyword": "",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "computer",
    }
    assert convert_profile_mapping(profile, conflict_policy="legacy")["utiki"] == {
        "ticket_type_keyword": "全票",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "computer",
    }
    assert profile == before


def test_invalid_profile_does_not_produce_partial_projection() -> None:
    with pytest.raises(ConfigError, match="UNKNOWN_BEHAVIOR_KEY"):
        convert_profile_mapping({"ctbc": {"unexpected": "do-not-drop"}})
    with pytest.raises(ConfigError, match="INVALID_BEHAVIOR"):
        convert_profile_mapping({"ctbc": {"seat_sorting": 1}})
    with pytest.raises(ConfigError, match="DUPLICATE_JSON_KEY"):
        convert_profile_mapping(parse_profile_json('{"ctbc": {}, "ctbc": {}}'))


def test_canonical_projection_is_idempotent_without_legacy_mirror() -> None:
    original: dict[str, object] = {"utiki": _canonical(), "ticket_number": 2}
    once = convert_profile_mapping(original)
    twice = convert_profile_mapping(once)
    assert once == twice == original
    assert once is not original
