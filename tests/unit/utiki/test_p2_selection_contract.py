"""Focused P2 selection controls over existing production pure functions."""

from copy import deepcopy

import pytest

import util
from platforms.utiki.profiles import PageKind, classify_utiki_page
from platforms.utiki.selection import (
    choose_candidate_index,
    date_plan_matches_snapshot,
    parse_date_snapshot,
    select_date_snapshot,
    select_quantity_snapshot,
)
from tests.ctbc_support import config, make_ctbc_date_snapshot, make_ctbc_quantity_snapshot


def _three_dates() -> object:
    raw = make_ctbc_date_snapshot()
    original = raw["rows"][0]
    raw["rows"] = []
    for index in range(3):
        row = deepcopy(original)
        row["text"] = f"2026/09/{10 + index} 測試場次"
        row["controls"][0]["onclick"] = (
            "location.href='UTK0204_?"
            f"PERFORMANCE_ID=P16D0B8{index}&PRODUCT_ID=P16ANF0L'"
        )
        raw["rows"].append(row)
    return raw


def test_d01_empty_and_unmatched_candidates_have_no_action_plan() -> None:
    raw = _three_dates()
    settings = config(date_keyword='"不存在"')
    assert choose_candidate_index([], '"任何"', "from top to bottom") is None
    assert select_date_snapshot({**raw, "rows": []}, settings) is None
    assert select_date_snapshot(raw, settings) is None
    settings["date_auto_fallback"] = True
    fallback = select_date_snapshot(raw, settings)
    assert fallback is not None and fallback.row_index == 0


def test_d02_keyword_priority_and_and_expression() -> None:
    candidates = [{"text": "VIP 一般席"}, {"text": "VIP 家庭席"}]
    assert choose_candidate_index(
        candidates, '"家庭席","一般席"', "from top to bottom"
    ) == 1
    assert choose_candidate_index(
        candidates, '"VIP 家庭席"', "from top to bottom"
    ) == 1
    assert choose_candidate_index(
        candidates, '"VIP 不存在"', "from top to bottom"
    ) is None


def test_d02_exclusion_removes_candidate_before_priority() -> None:
    raw = _three_dates()
    settings = config(date_keyword='"測試場次"')
    settings["keyword_exclude"] = '"09/10"'
    plan = select_date_snapshot(raw, settings)
    assert plan is not None and plan.row_index == 1


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("from top to bottom", 0),
        ("from bottom to top", 2),
        ("center", 1),
        ("random", 1),
    ],
)
def test_d03_selection_mode_freezes_plan_without_resampling(
    mode: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _three_dates()
    snapshot = parse_date_snapshot(raw)
    assert snapshot is not None
    settings = config(date_keyword="")
    settings["date_auto_select"]["mode"] = mode
    if mode == "random":
        monkeypatch.setattr(util.random, "randint", lambda _lower, _upper: 1)
    plan = select_date_snapshot(snapshot, settings)
    assert plan is not None and plan.row_index == expected

    def no_resample(_lower: int, _upper: int) -> int:
        raise AssertionError("audit must not sample again")

    monkeypatch.setattr(util.random, "randint", no_resample)
    assert date_plan_matches_snapshot(snapshot, plan)


def test_d04_duplicate_row_identity_fails_closed() -> None:
    raw = make_ctbc_quantity_snapshot()
    valid = select_quantity_snapshot(raw, "全票", 2)
    assert valid is not None
    raw["rows"].append(deepcopy(raw["rows"][0]))
    assert select_quantity_snapshot(raw, "全票", 2) is None


def test_d04_duplicate_query_and_empty_identity_fail_closed() -> None:
    raw = make_ctbc_date_snapshot()
    assert select_date_snapshot(raw, config(date_keyword="")) is not None
    duplicate = raw["page_url"] + "&PRODUCT_ID=P16ANF0L"
    assert classify_utiki_page(duplicate) is PageKind.UNKNOWN
    assert select_date_snapshot({**raw, "page_url": duplicate}, config(date_keyword="")) is None
    assert select_date_snapshot({**raw, "document_id": ""}, config(date_keyword="")) is None
