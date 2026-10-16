"""Managed assertion nodes for the isolated P1 performance baseline."""

import os
from pathlib import Path

import pytest

from tools.benchmarks.utiki_p1_performance import (
    load_contract,
    load_json,
    validate_raw_report,
)


pytestmark = pytest.mark.browser


def _validated_report():
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.fail("P1 performance nodes require the hard-isolation launcher")
    report_value = os.environ.get("TICKETS_HUNTER_P1_PERFORMANCE_REPORT", "")
    contract_value = os.environ.get("TICKETS_HUNTER_P1_PERFORMANCE_CONTRACT", "")
    candidate_sha = os.environ.get("TICKETS_HUNTER_CANDIDATE_SHA", "")
    if not report_value or not contract_value or not candidate_sha:
        pytest.fail("P1 performance evidence context is incomplete")
    report = load_json(Path(report_value))
    contract = load_contract(Path(contract_value))
    validate_raw_report(
        report,
        contract=contract,
        expected_candidate_sha=candidate_sha,
    )
    return report


def test_v12_baseline_source_cold():
    report = _validated_report()
    metrics = report["source"]["metrics"]

    for case in ("date", "area", "seat", "quantity"):
        assert metrics[f"{case}.cold_import"]["sample_count"] == 3
        assert metrics[f"{case}.cold_first_operation"]["sample_count"] == 3
        assert metrics[f"{case}.cold_import"]["failure_count"] == 0
        assert metrics[f"{case}.cold_first_operation"]["failure_count"] == 0


def test_v12_baseline_source_warm():
    report = _validated_report()
    metrics = report["source"]["metrics"]

    for case in ("date", "area", "seat", "quantity"):
        metric = metrics[f"{case}.warm_parse_select_plan"]
        assert metric["sample_count"] == 300
        assert metric["failure_count"] == 0
        assert metric["p99_ns"] >= metric["median_ns"]


def test_v12_baseline_cdp_command_count():
    report = _validated_report()
    browser = report["browser"]
    commands = browser["metrics"]["cdp_commands_per_observation"]
    requests = browser["metrics"]["ticketing_requests_per_observation"]

    assert commands["sample_count"] == 30
    assert all(value > 0 for value in commands["samples"])
    assert requests["samples"] == [0] * 30
    assert browser["navigation_fulfilled_requests"] == 1


def test_v12_baseline_event_loop_lag():
    report = _validated_report()
    metric = report["source"]["metrics"]["event_loop.scheduling_lag"]

    assert metric["sample_count"] == 300
    assert metric["failure_count"] == 0
    assert metric["p99_ns"] >= metric["p95_ns"] >= metric["median_ns"]


def test_v13_source_cold_start():
    report = _validated_report()
    metrics = report["source"]["metrics"]

    assert report["source"]["parameters"]["mode"] == "all"
    assert all(
        metrics[f"{case}.cold_import"]["sample_count"] == 3
        and metrics[f"{case}.cold_first_operation"]["sample_count"] == 3
        for case in ("date", "area", "seat", "quantity")
    )
