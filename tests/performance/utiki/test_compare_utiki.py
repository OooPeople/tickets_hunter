import copy
import hashlib
import json

import pytest

from tools.benchmarks import compare_utiki


def make_report(*, median=100, p95=150, p99=200):
    config = {"mode": "fixed"}
    config_digest = hashlib.sha256(json.dumps(
        config,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "benchmark": "utiki-source",
        "run": {
            "run_id": "run",
            "python": {"version": "3.11.15", "implementation": "CPython"},
            "os": {"system": "test", "release": "1", "machine": "x", "processor": "x"},
            "hardware": {
                "machine": "x",
                "processor": "x",
                "logical_cpu_count": 1,
                "physical_memory_bytes": 1,
            },
            "scope": "offline-source-only",
            "mode": "warm",
            "hard_egress_isolation": False,
        },
        "digests": {
            "fixture_sha256": "fixture",
            "config_sha256": config_digest,
            "script_sha256": "script",
            "subject_sha256": "subject",
        },
        "parameters": {"samples": 3},
        "config": config,
        "case_results": {"date": "plan"},
        "metrics": {
            "date.warm_parse_select_plan": {
                "median_ns": median,
                "p95_ns": p95,
                "p99_ns": p99,
                "sample_count": 3,
                "failure_count": 0,
            },
        },
        "blocked_metrics": {
            "cdp_commands_per_transaction": {
                "status": "BLOCKED",
                "reason": "no isolated browser",
            },
        },
    }


def test_compare_reports_exposes_regression_without_a_pass_threshold():
    baseline = make_report()
    candidate = make_report(median=110, p95=140, p99=250)
    candidate["run"]["run_id"] = "candidate"
    candidate["digests"]["subject_sha256"] = "changed-subject"

    result = compare_utiki.compare_reports(baseline, candidate)
    metric = result["metrics"]["date.warm_parse_select_plan"]
    assert result["comparable"] is True
    assert result["regressions_observed"] is True
    assert metric["statistics"]["median_ns"]["direction"] == "regression"
    assert metric["statistics"]["p95_ns"]["direction"] == "improvement"
    assert metric["statistics"]["p99_ns"]["delta_ns"] == 50
    assert "informational-only" in result["threshold_policy"]


@pytest.mark.parametrize(
    "digest_name",
    ["fixture_sha256", "config_sha256", "script_sha256"],
)
def test_compare_reports_rejects_methodology_digest_mismatch(digest_name):
    baseline = make_report()
    candidate = copy.deepcopy(baseline)
    candidate["digests"][digest_name] = "different"
    with pytest.raises(compare_utiki.IncompatibleReportsError):
        compare_utiki.compare_reports(baseline, candidate)


def test_compare_reports_rejects_sampling_parameter_mismatch():
    baseline = make_report()
    candidate = copy.deepcopy(baseline)
    candidate["parameters"] = {"samples": 9}
    with pytest.raises(compare_utiki.IncompatibleReportsError):
        compare_utiki.compare_reports(baseline, candidate)


def test_compare_reports_rejects_environment_mismatch():
    baseline = make_report()
    candidate = copy.deepcopy(baseline)
    candidate["run"]["os"]["machine"] = "different"
    with pytest.raises(compare_utiki.IncompatibleReportsError):
        compare_utiki.compare_reports(baseline, candidate)


def test_flagged_cli_contract_is_accepted():
    args = compare_utiki._parse_args([
        "--baseline", "baseline.json", "--candidate", "candidate.json"
    ])
    assert args.baseline == "baseline.json"
    assert args.candidate == "candidate.json"


def test_compare_reports_rejects_config_content_tampering():
    baseline = make_report()
    candidate = copy.deepcopy(baseline)
    candidate["config"]["mode"] = "tampered"
    with pytest.raises(compare_utiki.IncompatibleReportsError):
        compare_utiki.compare_reports(baseline, candidate)
