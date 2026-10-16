from copy import deepcopy

from tests.ctbc_support import make_ctbc_seat_snapshot

from platforms.utiki.selection import (
    parse_seat_snapshot,
    quantity_audit_is_valid,
    seat_selection_is_valid,
    select_seat_snapshot,
)


def test_valid_seat_fixture_still_selects_and_audits() -> None:
    raw = make_ctbc_seat_snapshot()
    plan, reason = select_seat_snapshot(raw, "全票", 2)
    assert reason == ""
    assert plan is not None and plan.action == "select"

    selected = make_ctbc_seat_snapshot(selected=(0, 1))
    assert seat_selection_is_valid(selected, 2, "FULL", "全票")


def test_seat_request_rejects_bool_and_nonfinite_count() -> None:
    raw = make_ctbc_seat_snapshot()
    for count in (True, float("nan"), float("inf")):
        plan, reason = select_seat_snapshot(raw, "全票", count)
        assert plan is None and reason == "seats-unavailable"
        assert not seat_selection_is_valid(
            make_ctbc_seat_snapshot(selected=(0, 1)), count, "FULL", "全票"
        )


def test_seat_wire_rejects_nonfinite_geometry_and_fingerprint() -> None:
    raw = make_ctbc_seat_snapshot()
    for field, value in (("center_x", float("nan")), ("width", float("inf"))):
        changed = deepcopy(raw)
        changed["seats"][0][field] = value
        assert parse_seat_snapshot(changed) is None
    changed = deepcopy(raw)
    changed["seats"][0]["fingerprint"][1] = float("nan")
    assert parse_seat_snapshot(changed) is None


def test_oversized_seat_counter_and_title_fail_without_exception() -> None:
    raw = make_ctbc_seat_snapshot()
    raw["counters"][0]["counter_raw"] = "0" * 40
    assert select_seat_snapshot(raw, "全票", 2) == (None, "invalid-snapshot")

    changed = make_ctbc_seat_snapshot()
    for seat in changed["seats"]:
        seat["title"] = "A區-1排-" + "9" * 5000 + "號"
    plan, reason = select_seat_snapshot(changed, "全票", 2)
    assert plan is None and reason == "seats-unavailable"


def test_fullwidth_seat_numbers_preserve_existing_adjacent_selection() -> None:
    raw = make_ctbc_seat_snapshot()
    for index, seat in enumerate(raw["seats"]):
        seat["title"] = f"A區-１排-{chr(ord('１') + index)}號"
    plan, reason = select_seat_snapshot(raw, "全票", 2)
    assert reason == ""
    assert plan is not None and plan.action == "select"
    assert len(plan.target_seat_fingerprints) == 2


def test_quantity_audit_rejects_invalid_counts_without_coercion() -> None:
    valid = [{"key": "FULL", "value": "2"}, {"key": "OTHER", "value": "0"}]
    assert quantity_audit_is_valid(valid, "FULL", 2)
    for bad in (True, float("inf"), "0" * 40, None):
        changed = deepcopy(valid)
        changed[1]["value"] = bad
        assert not quantity_audit_is_valid(changed, "FULL", 2)
    assert not quantity_audit_is_valid(valid, "FULL", True)
