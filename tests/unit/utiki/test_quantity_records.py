from copy import deepcopy

from tests.ctbc_support import make_ctbc_quantity_snapshot

from platforms.utiki.domain.snapshots import InvalidField, MissingField, PresentField, QuantityRow
from platforms.utiki.selection import parse_quantity_snapshot, select_quantity_snapshot


def test_quantity_row_has_named_fields_and_preserves_snapshot_values() -> None:
    raw = make_ctbc_quantity_snapshot()
    raw["rows"][0].update({"text": "全票專屬說明", "value": "1"})
    snapshot = parse_quantity_snapshot(raw)
    assert snapshot is not None
    assert snapshot.rows[0] == QuantityRow(
        key="FULL", name="全票", text="全票專屬說明", disabled=False,
        remaining_present=True, remaining_raw="5", value="1",
    )
    assert snapshot.rows[0].remaining_observation == PresentField(5)
    assert snapshot.rows[0].value_observation == PresentField(1)
    plan = select_quantity_snapshot(snapshot, "全票", 2)
    assert plan is not None
    assert (plan.target_key, plan.target_name, plan.target_remaining_present) == (
        "FULL", "全票", True,
    )
    raw["rows"][0]["key"] = "CHANGED"
    assert snapshot.rows[0].key != "CHANGED"


def test_missing_empty_and_invalid_remaining_are_not_equivalent() -> None:
    raw = make_ctbc_quantity_snapshot()
    raw["rows"][0]["remaining_present"] = False
    raw["rows"][0]["remaining_raw"] = ""
    missing = parse_quantity_snapshot(raw)
    assert missing is not None
    assert missing.rows[0].remaining_observation == MissingField()
    assert select_quantity_snapshot(missing, "全票", 2) is not None

    raw["rows"][0]["remaining_present"] = True
    empty = parse_quantity_snapshot(raw)
    assert empty is not None
    assert empty.rows[0].remaining_observation == InvalidField("empty")
    assert select_quantity_snapshot(empty, "全票", 2) is None

    raw["rows"][0]["remaining_raw"] = "__invalid__"
    duplicate = parse_quantity_snapshot(raw)
    assert duplicate is not None
    assert duplicate.rows[0].remaining_observation == InvalidField("not-integer")
    assert select_quantity_snapshot(duplicate, "全票", 2) is None


def test_quantity_codec_rejects_invalid_types_and_oversized_count() -> None:
    raw = make_ctbc_quantity_snapshot()
    bad_type = deepcopy(raw)
    bad_type["rows"][0]["remaining_raw"] = True
    assert parse_quantity_snapshot(bad_type) is None

    raw["rows"][0]["remaining_raw"] = "9" * 5000
    assert select_quantity_snapshot(raw, "全票", 2) is None

    raw = make_ctbc_quantity_snapshot()
    raw["quantity_limit_present"] = True
    raw["quantity_limit_raw"] = ""
    assert select_quantity_snapshot(raw, "全票", 2) is None


def test_boolean_or_nonfinite_requested_count_never_becomes_a_plan() -> None:
    raw = make_ctbc_quantity_snapshot()
    assert select_quantity_snapshot(raw, "全票", True) is None
    assert select_quantity_snapshot(raw, "全票", float("inf")) is None

    raw["quantity_limit_raw"] = False
    assert parse_quantity_snapshot(raw) is None
