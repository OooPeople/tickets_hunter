"""Shared keyword rules retain the established parser and match semantics."""

import pytest

import keyword_rules
import util


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ('"VIP","1F"', ["VIP", "1F"]),
        ('"VIP Rock Area"', ["VIP Rock Area"]),
        ('"VIP",1', ["VIP", 1]),
        ("", []),
        ("invalid", []),
    ],
)
def test_keyword_fragment_parse_keeps_existing_shape(
    fragment: str, expected: list[object]
) -> None:
    assert keyword_rules.parse_keyword_string_to_array(fragment) == expected
    assert util.parse_keyword_string_to_array(fragment) == expected


def test_keyword_formatting_only_removes_full_width_spaces() -> None:
    assert keyword_rules.format_keyword_string("VIP　Rock") == "VIPRock"
    assert util.format_keyword_string("VIP　Rock") == "VIPRock"
    assert keyword_rules.format_keyword_string(None) is None
    assert keyword_rules.format_keyword_string("vip") != "VIP"


@pytest.mark.parametrize(
    ("fragment", "row_text", "matches"),
    [
        ('"VIP Rock","1F"', "1F seat", True),
        ('"VIP Rock","1F"', "VIP seat Rock", True),
        ('"VIP Rock","1F"', "VIP seat", False),
        ('"VIP"', "vip", False),
        ('"VIP　Rock"', "VIPRock seat", True),
        ('""', "anything", True),
        ('"VIP"', "", True),
        ("invalid", "seat", False),
    ],
)
def test_keyword_matching_or_and_empty_semantics(
    fragment: str, row_text: str, matches: bool
) -> None:
    assert keyword_rules.is_row_match_keyword(fragment, row_text) is matches
    assert util.is_row_match_keyword(fragment, row_text) is matches


def test_exclusion_requires_configured_keyword() -> None:
    assert not keyword_rules.is_keyword_excluded("", "VIP")
    assert keyword_rules.is_keyword_excluded('"VIP"', "VIP")
    assert not keyword_rules.is_keyword_excluded('"VIP"', "other")
    assert util.reset_row_text_if_match_keyword_exclude(
        {"keyword_exclude": '"VIP"'}, "VIP"
    )


def test_empty_json_group_keeps_legacy_match_all_exclusion() -> None:
    from platforms.ticketplus import _ticketplus_filter_excluded

    items = ["VIP", "other"]
    assert keyword_rules.is_row_match_keyword("[]", "VIP")
    assert util.reset_row_text_if_match_keyword_exclude(
        {"keyword_exclude": "[]"}, "VIP"
    )
    assert _ticketplus_filter_excluded(
        {"keyword_exclude": "[]"}, items, lambda item: item
    ) == []
    assert _ticketplus_filter_excluded(
        {"keyword_exclude": '"VIP"'}, items, lambda item: item
    ) == ["other"]


def test_nonstring_group_stops_before_later_positive_match() -> None:
    from platforms.ticketplus import _ticketplus_filter_excluded

    with pytest.raises(TypeError, match="keyword group"):
        util.is_row_match_keyword('1,"VIP"', "VIP")
    with pytest.raises(TypeError, match="keyword group"):
        _ticketplus_filter_excluded(
            {"keyword_exclude": '1,"VIP"'}, ["VIP"], lambda item: item
        )
