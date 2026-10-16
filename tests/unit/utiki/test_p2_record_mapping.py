"""D07 keeps every browser snapshot value when converting rows to records."""

from platforms.utiki.domain.snapshots import DATE_CONTROL_FINGERPRINT_FIELDS
from platforms.utiki.selection import (
    parse_area_snapshot,
    parse_date_snapshot,
    parse_quantity_snapshot,
    parse_seat_snapshot,
    seat_plan_matches_snapshot,
    select_seat_snapshot,
)
from tests.ctbc_support import (
    make_ctbc_area_snapshot,
    make_ctbc_date_snapshot,
    make_ctbc_quantity_snapshot,
    make_ctbc_seat_snapshot,
)


def test_d07_snapshot_record_fields_fingerprint_and_geometry_keep_wire_values() -> None:
    date_raw = make_ctbc_date_snapshot()
    date_control_wire = date_raw["rows"][0]["controls"][0]
    for key in DATE_CONTROL_FINGERPRINT_FIELDS:
        if key != "has_form":
            date_control_wire[key] = f"distinct-{key}"
    date = parse_date_snapshot(date_raw)
    assert date is not None
    assert (date.ok, date.page_url, date.document_id) == (
        date_raw["ok"], date_raw["page_url"], date_raw["document_id"],
    )
    assert len(date.rows) == len(date_raw["rows"])
    for row, wire in zip(date.rows, date_raw["rows"]):
        assert row.text == wire["text"]
        assert len(row.controls) == len(wire["controls"])
        for control, control_wire in zip(row.controls, wire["controls"]):
            assert control.actionable is control_wire["actionable"]
            assert control.fingerprint == tuple(
                (key, control_wire[key]) for key in DATE_CONTROL_FINGERPRINT_FIELDS
            )

    area_raw = make_ctbc_area_snapshot(include_back=False)
    area = parse_area_snapshot(area_raw)
    assert area is not None
    assert (area.ok, area.page_url, area.document_id) == (
        area_raw["ok"], area_raw["page_url"], area_raw["document_id"],
    )
    assert len(area.rows) == len(area_raw["rows"])
    assert len(area.areas) == len(area_raw["areas"])
    for row, wire in zip(area.rows, area_raw["rows"]):
        assert (row.area, row.price, row.status, row.rel) == (
            wire["area"], wire["price"], wire["status"], wire["rel"],
        )
    for item, wire in zip(area.areas, area_raw["areas"]):
        assert (item.area_id, item.href, item.title) == (
            wire["id"], wire["href"], wire["title"],
        )

    quantity_raw = make_ctbc_quantity_snapshot()
    quantity_raw["product_id"] = "PRODUCT_UNIQUE"
    quantity_raw["rows"][0]["text"] = "全票的獨立說明"
    presence_fields = (
        "performance_hidden_present", "product_hidden_present",
        "group_hidden_present", "price_area_hidden_present",
        "quantity_limit_present",
    )
    quantity_raw.update({field: False for field in presence_fields})
    quantity = parse_quantity_snapshot(quantity_raw)
    assert quantity is not None
    assert (
        quantity.ok, quantity.page_url, quantity.document_id,
        quantity.performance_id, quantity.group_id, quantity.price_area_id,
        quantity.quantity_limit_raw, quantity.product_id,
    ) == (
        quantity_raw["ok"], quantity_raw["page_url"], quantity_raw["document_id"],
        quantity_raw["performance_id"], quantity_raw["group_id"],
        quantity_raw["price_area_id"], quantity_raw["quantity_limit_raw"],
        quantity_raw["product_id"],
    )
    assert len(quantity.rows) == len(quantity_raw["rows"])
    for row, wire in zip(quantity.rows, quantity_raw["rows"]):
        assert (
            row.key, row.name, row.text, row.disabled, row.remaining_present,
            row.remaining_raw, row.value,
        ) == (
            wire["key"], wire["name"], wire["text"], wire["disabled"],
            wire["remaining_present"], wire["remaining_raw"], wire["value"],
        )
    for enabled_field in presence_fields:
        one_hot = dict(quantity_raw)
        one_hot[enabled_field] = True
        observed = parse_quantity_snapshot(one_hot)
        assert observed is not None
        assert tuple(getattr(observed, field) for field in presence_fields) == tuple(
            field == enabled_field for field in presence_fields
        )

    seat_raw = make_ctbc_seat_snapshot(selected=(0, 1))
    seat_raw["ticket_types"][0]["text"] = "全票顯示文字"
    seat_raw["ticket_types"][0]["type_name"] = "全票票種名稱"
    for wire in seat_raw["submitted_seats"]:
        wire["Z"] = "全票票種名稱"
    seat_raw["seats"][0].update({"width": 17, "height": 23})
    seat_raw["seats"][1].update({"width": 19, "height": 25})
    seat = parse_seat_snapshot(seat_raw)
    assert seat is not None
    assert (
        seat.ok, seat.page_url, seat.document_id, seat.performance_id,
        seat.group_id, seat.price_area_id, seat.product_id,
        seat.product_hidden_present, seat.quantity_limit_present,
        seat.quantity_limit_raw, seat.visual_selected_count,
    ) == (
        seat_raw["ok"], seat_raw["page_url"], seat_raw["document_id"],
        seat_raw["performance_id"], seat_raw["group_id"], seat_raw["price_area_id"],
        seat_raw["product_id"], seat_raw["product_hidden_present"],
        seat_raw["quantity_limit_present"], seat_raw["quantity_limit_raw"],
        seat_raw["visual_selected_count"],
    )
    assert len(seat.ticket_types) == len(seat_raw["ticket_types"])
    for row, wire in zip(seat.ticket_types, seat_raw["ticket_types"]):
        assert (
            row.fingerprint, row.text, row.disabled, row.type_id, row.type_name,
            row.counter_present, row.counter_raw,
        ) == (
            tuple(wire["fingerprint"]), wire["text"], wire["disabled"],
            wire["type_id"], wire["type_name"], wire["counter_present"],
            wire["counter_raw"],
        )
    assert len(seat.seats) == len(seat_raw["seats"])
    for row, wire in zip(seat.seats, seat_raw["seats"]):
        assert (
            row.fingerprint, row.title, row.row_index, row.cell_index,
            row.center_x, row.center_y, row.width, row.height,
            row.available, row.selected,
        ) == (
            tuple(wire["fingerprint"]), wire["title"], wire["row_index"],
            wire["cell_index"], wire["center_x"], wire["center_y"],
            wire["width"], wire["height"], wire["available"], wire["selected"],
        )
    assert [(item.a, item.y, item.z) for item in seat.submitted_seats] == [
        (wire["A"], wire["Y"], wire["Z"]) for wire in seat_raw["submitted_seats"]
    ]
    assert [(item.type_id, item.raw) for item in seat.counters] == [
        (wire["id"], wire["counter_raw"]) for wire in seat_raw["counters"]
    ]
    plan, reason = select_seat_snapshot(seat, "全票", 2)
    assert reason == "" and plan is not None
    assert plan.selected_seat_fingerprints == (
        tuple(seat_raw["seats"][0]["fingerprint"]),
        tuple(seat_raw["seats"][1]["fingerprint"]),
    )
    assert seat_plan_matches_snapshot(seat, plan)

    for parser, reason in (
        (parse_date_snapshot, "date-observation-failed"),
        (parse_area_snapshot, "area-observation-failed"),
        (parse_quantity_snapshot, "quantity-observation-failed"),
        (parse_seat_snapshot, "seat-observation-failed"),
    ):
        failed = parser({
            "ok": False,
            "page_url": "https://fixture.invalid/failed",
            "document_id": "failed-document",
            "reason": reason,
        })
        assert failed is not None
        assert (failed.ok, failed.page_url, failed.document_id, failed.reason) == (
            False, "https://fixture.invalid/failed", "failed-document", reason,
        )
