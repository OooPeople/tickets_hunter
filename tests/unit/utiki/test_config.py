import copy
import json

import pytest

from utiki_config import (
    DEFAULT_BEHAVIOR,
    ConfigError,
    SEAT_SELECTION_MODES,
    SEAT_SORTINGS,
    UpgradeKind,
    UtikiBehavior,
    inspect_upgrade_input,
    load_current_policy,
    parse_profile_json,
)


def _canonical() -> dict[str, str]:
    return {
        "ticket_type_keyword": "全票",
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }


def test_canonical_only_loads_without_mutating_profile() -> None:
    profile: dict[str, object] = {"utiki": _canonical()}
    before = copy.deepcopy(profile)

    policy = load_current_policy(profile)

    assert policy == UtikiBehavior("全票", "row_seat", "manual")
    assert profile == before


def test_mixed_equal_requires_offline_upgrade_without_comparing_values() -> None:
    profile: dict[str, object] = {"utiki": _canonical(), "ctbc": _canonical()}
    before = copy.deepcopy(profile)

    with pytest.raises(ConfigError) as error:
        load_current_policy(profile)

    assert error.value.code == "UPGRADE_REQUIRED"
    assert profile == before


def test_legacy_only_is_upgrade_required_and_defaults_only_missing_keys() -> None:
    profile: dict[str, object] = {"ctbc": {"ticket_type_keyword": ""}}
    before = copy.deepcopy(profile)

    with pytest.raises(ConfigError) as error:
        load_current_policy(profile)
    inspection = inspect_upgrade_input(profile)

    assert error.value.code == "UPGRADE_REQUIRED"
    assert inspection.kind is UpgradeKind.LEGACY_ONLY
    assert inspection.projected == UtikiBehavior("", "row_seat", "manual")
    assert profile == before


def test_mixed_equal_can_be_classified_without_runtime_conversion() -> None:
    profile: dict[str, object] = {"utiki": _canonical(), "ctbc": _canonical()}

    inspection = inspect_upgrade_input(profile)

    assert inspection.kind is UpgradeKind.MIXED_EQUAL
    assert inspection.projected == UtikiBehavior("全票", "row_seat", "manual")
    assert inspection.conflicting_keys == ()


def test_mixed_complementary_keeps_explicit_empty_keyword() -> None:
    profile: dict[str, object] = {
        "utiki": {"ticket_type_keyword": ""},
        "ctbc": {"seat_sorting": "display", "seat_selection_mode": "computer"},
    }
    before = copy.deepcopy(profile)

    inspection = inspect_upgrade_input(profile)

    assert inspection.kind is UpgradeKind.MIXED_COMPLEMENTARY
    assert inspection.projected == UtikiBehavior("", "display", "computer")
    assert profile == before


def test_mixed_conflict_does_not_select_a_side_implicitly() -> None:
    legacy = _canonical()
    legacy["seat_sorting"] = "display"
    profile: dict[str, object] = {"utiki": _canonical(), "ctbc": legacy}
    before = copy.deepcopy(profile)

    inspection = inspect_upgrade_input(profile)

    assert inspection.kind is UpgradeKind.MIXED_CONFLICT
    assert inspection.projected is None
    assert inspection.conflicting_keys == ("seat_sorting",)
    assert profile == before


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ('{"utiki": {}, "utiki": {}}', "DUPLICATE_JSON_KEY"),
        ('{"utiki": ', "INVALID_JSON"),
        ('["not an object"]', "INVALID_ROOT"),
        ('{"utiki": {"seat_sorting": NaN}}', "INVALID_JSON_NUMBER"),
        ('{"ticket_number": 1e999}', "INVALID_JSON_NUMBER"),
    ],
)
def test_corrupt_profile_json_is_rejected_without_fallback(
    raw: str, code: str
) -> None:
    with pytest.raises(ConfigError) as error:
        parse_profile_json(raw)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("profile", "code"),
    [
        ({"utiki": {"ticket_type_keyword": "全票"}}, "INVALID_BEHAVIOR_KEYS"),
        ({"utiki": {**_canonical(), "seat_sorting": "invalid"}}, "INVALID_BEHAVIOR"),
        ({"utiki": {**_canonical(), "seat_selection_mode": True}}, "INVALID_BEHAVIOR"),
        ({"utiki": _canonical(), "schema_version": 999}, "UNSUPPORTED_SCHEMA"),
    ],
)
def test_current_policy_rejects_unsupported_schema_without_defaults(
    profile: dict[str, object], code: str
) -> None:
    before = copy.deepcopy(profile)
    with pytest.raises(ConfigError) as error:
        load_current_policy(profile)
    assert error.value.code == code
    assert profile == before


def test_unknown_legacy_key_is_not_silently_discarded() -> None:
    profile: dict[str, object] = {"ctbc": {"ticket_type_keyword": "全票", "unexpected": "x"}}

    with pytest.raises(ConfigError) as error:
        inspect_upgrade_input(profile)

    assert error.value.code == "UNKNOWN_BEHAVIOR_KEY"
    assert error.value.key == "ctbc.unexpected"


def test_unused_utiki_section_remains_opaque_for_other_platforms() -> None:
    raw = json.dumps({"homepage": "other-platform", "ctbc": {"unknown": "keep"}})

    parsed = parse_profile_json(raw)

    assert parsed["ctbc"] == {"unknown": "keep"}


def test_current_defaults_and_choices_match_existing_settings() -> None:
    from settings import (
        UTIKI_SEAT_SELECTION_MODES,
        UTIKI_SEAT_SORTINGS,
        get_default_config,
    )

    existing = get_default_config()["utiki"]
    assert existing == {
        "ticket_type_keyword": DEFAULT_BEHAVIOR.ticket_type_keyword,
        "seat_sorting": DEFAULT_BEHAVIOR.seat_sorting,
        "seat_selection_mode": DEFAULT_BEHAVIOR.seat_selection_mode,
    }
    assert set(UTIKI_SEAT_SORTINGS) == SEAT_SORTINGS
    assert set(UTIKI_SEAT_SELECTION_MODES) == SEAT_SELECTION_MODES


def test_runtime_does_not_parse_legacy_values_before_upgrade_required() -> None:
    profile: dict[str, object] = {"utiki": _canonical(), "ctbc": object()}

    with pytest.raises(ConfigError) as error:
        load_current_policy(profile)

    assert error.value.code == "UPGRADE_REQUIRED"
