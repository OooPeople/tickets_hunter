"""Check the bounded P2 hot-path report with real source measurements."""

import copy
from pathlib import Path
import subprocess
import sys

import pytest

from tools.benchmarks.utiki_p1_performance import (
    PerformanceReportError,
    load_contract,
    node_ids,
    validate_hot_path_report,
)
from tools.benchmarks.utiki_p2_hot_path import measure_hot_path


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "ci" / "utiki-p2-performance-isolation.json"


def test_p2_hot_path_legal_report_and_single_difference_rejections() -> None:
    contract = load_contract(CONTRACT)
    report = measure_hot_path(**contract["hot_path"])
    validate_hot_path_report(report, contract=contract)

    copied = copy.deepcopy(report)
    copied["v08"]["forbidden_calls"]["deepcopy"] = 1
    with pytest.raises(PerformanceReportError, match="V08 large-seat retention"):
        validate_hot_path_report(copied, contract=contract)

    retained = copy.deepcopy(report)
    retained["v08"]["pending_tasks_after"] += 1
    with pytest.raises(PerformanceReportError, match="V08 large-seat retention"):
        validate_hot_path_report(retained, contract=contract)

    reprocessed = copy.deepcopy(report)
    reprocessed["v07"]["forbidden_calls"]["parse_profile_json"] = 1
    with pytest.raises(PerformanceReportError, match="V07 high-frequency policy"):
        validate_hot_path_report(reprocessed, contract=contract)


def test_p2_managed_performance_nodes_collect_exactly_seven() -> None:
    required_nodes = node_ids("P2")
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-o", "addopts=",
            "--strict-config", "--strict-markers", "--collect-only", "-q",
            *required_nodes,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    collected = [line for line in completed.stdout.splitlines() if "::test_v" in line]
    assert collected == list(required_nodes)
