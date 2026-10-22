from dataclasses import replace

import pytest

from tests.ctbc_support import CTBC_TEST_PERFORMANCE_ID, make_ctbc_area_snapshot, selector_settings

from platforms.utiki.domain.snapshots import AreaRow, MapArea
from platforms.utiki.selection import parse_area_snapshot, select_area_snapshot


def test_area_rows_and_map_items_keep_every_wire_field_named() -> None:
    raw = make_ctbc_area_snapshot(include_back=False)
    raw["rows"][0]["status"] = "40"
    snapshot = parse_area_snapshot(raw)
    assert snapshot is not None
    assert snapshot.rows == (AreaRow("109區前排", "1600", "40", "front"),)
    assert snapshot.areas == (MapArea("front", raw["areas"][0]["href"], raw["areas"][0]["title"]),)
    plan = select_area_snapshot(snapshot, selector_settings(area_keyword='"109區前排"'), CTBC_TEST_PERFORMANCE_ID)
    assert plan is not None
    assert (plan.area, plan.price, plan.status, plan.rel) == ("109區前排", "1600", "40", "front")
    assert (
        plan.route, plan.performance_id, plan.price_area_id, plan.group_id,
        plan.stable_args, plan.action_status, plan.direct_destination,
    ) == (
        "0205", "P16D0B8Z", "PRICE_FRONT", "GROUP_FRONT",
        ("0205", "P16D0B8Z", "PRICE_FRONT", "GROUP_FRONT"), "45", True,
    )
    raw["rows"][0]["area"] = "CHANGED"
    assert snapshot.rows[0].area == "109區前排"


def test_area_parser_does_not_coerce_boolean_wire_values() -> None:
    raw = make_ctbc_area_snapshot(include_back=False)
    raw["rows"][0]["status"] = False
    assert parse_area_snapshot(raw) is None


def test_area_request_rejects_boolean_and_nonfinite_counts() -> None:
    for count in (True, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            replace(selector_settings(area_keyword='"109區前排"'), ticket_number=count)


def test_area_selection_requires_validated_typed_settings() -> None:
    snapshot = parse_area_snapshot(make_ctbc_area_snapshot(include_back=False))
    assert snapshot is not None
    assert select_area_snapshot(snapshot, {"area_auto_select": {}}, CTBC_TEST_PERFORMANCE_ID) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        replace(selector_settings(), area_fallback=1)


def test_oversized_area_status_is_unavailable_without_exception() -> None:
    for status in ("9" * 5000, "0" * 40 + "2"):
        raw = make_ctbc_area_snapshot(include_back=False, front_row_status=status)
        assert select_area_snapshot(raw, selector_settings(area_keyword='"109區前排"'), CTBC_TEST_PERFORMANCE_ID) is None
