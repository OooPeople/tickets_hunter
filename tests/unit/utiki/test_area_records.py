from tests.ctbc_support import CTBC_TEST_PERFORMANCE_ID, config, make_ctbc_area_snapshot

from platforms.utiki.domain.snapshots import AreaRow, MapArea
from platforms.utiki.selection import parse_area_snapshot, select_area_snapshot


def test_area_rows_and_map_items_keep_every_wire_field_named() -> None:
    raw = make_ctbc_area_snapshot(include_back=False)
    raw["rows"][0]["status"] = "40"
    snapshot = parse_area_snapshot(raw)
    assert snapshot is not None
    assert snapshot.rows == (AreaRow("109區前排", "1600", "40", "front"),)
    assert snapshot.areas == (MapArea("front", raw["areas"][0]["href"], raw["areas"][0]["title"]),)
    plan = select_area_snapshot(snapshot, config(area_keyword='"109區前排"'), CTBC_TEST_PERFORMANCE_ID)
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
    raw = make_ctbc_area_snapshot(include_back=False)
    for count in (True, float("nan"), float("inf")):
        cfg = config(area_keyword='"109區前排"')
        cfg["ticket_number"] = count
        assert select_area_snapshot(raw, cfg, CTBC_TEST_PERFORMANCE_ID) is None


def test_area_selection_rejects_malformed_config_without_action() -> None:
    snapshot = parse_area_snapshot(make_ctbc_area_snapshot(include_back=False))
    assert snapshot is not None
    for key, malformed_value in (
        ("area_auto_select", "invalid"),
        ("area_auto_select", {"area_keyword": 1}),
        ("area_auto_select", {"mode": 1}),
        ("area_auto_fallback", 1),
    ):
        settings = config(area_keyword='"109區前排"')
        settings[key] = malformed_value
        assert select_area_snapshot(snapshot, settings, CTBC_TEST_PERFORMANCE_ID) is None


def test_oversized_area_status_is_unavailable_without_exception() -> None:
    for status in ("9" * 5000, "0" * 40 + "2"):
        raw = make_ctbc_area_snapshot(include_back=False, front_row_status=status)
        assert select_area_snapshot(raw, config(area_keyword='"109區前排"'), CTBC_TEST_PERFORMANCE_ID) is None
