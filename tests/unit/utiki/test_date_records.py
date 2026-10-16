from dataclasses import replace

import pytest

from tests.ctbc_support import config, make_ctbc_date_snapshot

from platforms.utiki.domain.snapshots import DateControl, DateRow
from platforms.utiki.selection import (
    DATE_CONTROL_FINGERPRINT_FIELDS,
    date_plan_matches_snapshot,
    parse_date_snapshot,
    select_date_snapshot,
)


def test_date_row_control_and_plan_preserve_every_wire_field() -> None:
    raw = make_ctbc_date_snapshot()
    snapshot = parse_date_snapshot(raw)
    assert snapshot is not None
    row = snapshot.rows[0]
    assert isinstance(row, DateRow)
    assert row.text == raw["rows"][0]["text"]
    assert len(row.controls) == 1
    control = row.controls[0]
    assert isinstance(control, DateControl)
    assert control.actionable is True
    assert tuple(key for key, _ in control.fingerprint) == DATE_CONTROL_FINGERPRINT_FIELDS
    assert dict(control.fingerprint) == {
        key: raw["rows"][0]["controls"][0][key]
        for key in DATE_CONTROL_FINGERPRINT_FIELDS
    }

    plan = select_date_snapshot(snapshot, config(date_keyword='"9/10"'))
    assert plan is not None
    assert (plan.row_index, plan.control_index, plan.text) == (0, 0, row.text)
    assert (plan.action_kind, plan.onclick, plan.control_fingerprint) == (
        "normal", raw["rows"][0]["controls"][0]["onclick"], control.fingerprint,
    )
    assert (
        plan.target_url, plan.performance_id, plan.product_id,
        plan.tenant_path, plan.host, plan.map_namespace,
        plan.fast_area_prefetch, plan.action_token,
    ) == (
        "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
        "PERFORMANCE_ID=P16D0B8Z&PRODUCT_ID=P16ANF0L",
        "P16D0B8Z", "P16ANF0L", "/BROTHERS", "tix.ctbcsports.com",
        "CTBC_SPORTS", True, "",
    )
    assert date_plan_matches_snapshot(snapshot, plan)
    assert not date_plan_matches_snapshot(snapshot, replace(plan, onclick="other"))
    raw["rows"][0]["text"] = "changed"
    raw["rows"][0]["controls"][0]["onclick"] = "alert('x')"
    assert snapshot.rows[0].text == row.text
    assert date_plan_matches_snapshot(snapshot, plan)


def test_date_control_rejects_non_boolean_actionability() -> None:
    raw = make_ctbc_date_snapshot()
    raw["rows"][0]["controls"][0]["actionable"] = 1
    assert parse_date_snapshot(raw) is None

def test_date_control_rejects_invalid_direct_fingerprint_construction() -> None:
    raw = make_ctbc_date_snapshot()
    snapshot = parse_date_snapshot(raw)
    assert snapshot is not None
    control = snapshot.rows[0].controls[0]
    for fingerprint in (
        (("tag_name", True),),
        control.fingerprint[:-1],
        (("id", "buy_btn"),) + control.fingerprint[1:],
        tuple(
            (key, True if key == "tag_name" else value)
            for key, value in control.fingerprint
        ),
    ):
        with pytest.raises(ValueError):
            replace(control, fingerprint=fingerprint)
