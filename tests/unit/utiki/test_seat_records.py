from copy import deepcopy
from dataclasses import replace

import pytest

from tests.ctbc_support import make_ctbc_seat_snapshot

from platforms.utiki.domain.snapshots import (
    SeatCounter,
    SeatRow,
    SubmittedSeat,
    TicketTypeRow,
)
from platforms.utiki.selection import (
    parse_seat_snapshot,
    seat_plan_matches_snapshot,
    select_seat_snapshot,
)


def test_seat_records_preserve_type_geometry_payload_and_counter() -> None:
    raw = make_ctbc_seat_snapshot(selected=(0, 1))
    snapshot = parse_seat_snapshot(raw)
    assert snapshot is not None
    assert snapshot.ticket_types == (
        TicketTypeRow(
            ("ticket", "FULL", "setType('FULL')"), "全票", False,
            "FULL", "全票", True, "2",
        ),
    )
    assert snapshot.seats[0] == SeatRow(
        ("seat", 0, "A區-1排-1號"), "A區-1排-1號", 1, 0,
        10, 10, 20, 20, False, True,
    )
    assert snapshot.seats[1] == SeatRow(
        ("seat", 1, "A區-1排-2號"), "A區-1排-2號", 1, 1,
        30, 10, 20, 20, False, True,
    )
    assert snapshot.submitted_seats == (
        SubmittedSeat("S", "FULL", "全票"),
        SubmittedSeat("S", "FULL", "全票"),
    )
    assert snapshot.counters == (SeatCounter("FULL", "2"), SeatCounter("OTHER", "0"))

    plan, reason = select_seat_snapshot(snapshot, "全票", 2)
    assert reason == ""
    assert plan is not None and plan.action == "ready"
    assert (plan.ticket_type_fingerprint, plan.ticket_type_id, plan.ticket_type_name) == (
        snapshot.ticket_types[0].fingerprint, "FULL", "全票",
    )
    assert plan.selected_seat_fingerprints == (
        snapshot.seats[0].fingerprint, snapshot.seats[1].fingerprint,
    )
    assert seat_plan_matches_snapshot(snapshot, plan)
    assert not seat_plan_matches_snapshot(snapshot, replace(plan, ticket_type_id="OTHER"))

    raw["ticket_types"][0]["text"] = "changed"
    raw["seats"][0]["center_x"] = 999
    raw["submitted_seats"][0]["Y"] = "OTHER"
    raw["counters"][0]["counter_raw"] = "99"
    assert snapshot.ticket_types[0].text == "全票"
    assert snapshot.seats[0].center_x == 10
    assert snapshot.submitted_seats[0].y == "FULL"
    assert snapshot.counters[0].raw == "2"


def test_seat_record_constructor_and_wire_reject_invalid_values() -> None:
    raw = make_ctbc_seat_snapshot()
    snapshot = parse_seat_snapshot(raw)
    assert snapshot is not None
    with pytest.raises(ValueError):
        replace(snapshot.seats[0], width=float("nan"))
    with pytest.raises(ValueError):
        replace(snapshot.ticket_types[0], fingerprint=("ticket", float("inf")))

    changed = deepcopy(raw)
    changed["seats"][0]["selected"] = 1
    assert parse_seat_snapshot(changed) is None


def test_invalid_first_seat_collection_does_not_parse_later_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = make_ctbc_seat_snapshot()
    assert parse_seat_snapshot(raw) is not None
    raw["ticket_types"] = None

    def unexpected_seat_parse(_item: object) -> None:
        raise AssertionError("later seat collection was parsed")

    monkeypatch.setattr("platforms.utiki.selection._freeze_seat", unexpected_seat_parse)
    assert parse_seat_snapshot(raw) is None
