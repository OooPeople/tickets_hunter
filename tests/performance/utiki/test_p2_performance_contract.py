"""Managed P2 assertions for the hard-isolated hot-path report."""

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
        pytest.fail("P2 performance nodes require the hard-isolation launcher")
    report_value = os.environ.get("TICKETS_HUNTER_P1_PERFORMANCE_REPORT", "")
    contract_value = os.environ.get("TICKETS_HUNTER_P1_PERFORMANCE_CONTRACT", "")
    candidate_sha = os.environ.get("TICKETS_HUNTER_CANDIDATE_SHA", "")
    if not report_value or not contract_value or not candidate_sha:
        pytest.fail("P2 performance evidence context is incomplete")
    report = load_json(Path(report_value))
    contract = load_contract(Path(contract_value))
    if report.get("context", {}).get("phase") != "P2":
        pytest.fail("P2 performance report phase is invalid")
    validate_raw_report(report, contract=contract, expected_candidate_sha=candidate_sha)
    return report


def test_v07_unchanged_policy_hot_path():
    v07 = _validated_report()["hot_path"]["v07"]
    assert all(count == 0 for count in v07["forbidden_calls"].values())
    assert v07["latency"]["failure_count"] == 0


def test_v08_large_seat_hot_path():
    v08 = _validated_report()["hot_path"]["v08"]
    assert v08["max_reference_delta"] == 0
    assert v08["pending_tasks_before"] == v08["pending_tasks_after"]
    assert v08["valid_latency"]["failure_count"] == 0
    assert v08["invalid_latency"]["failure_count"] == 0
