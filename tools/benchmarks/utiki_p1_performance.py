#!/usr/bin/env python3
"""Shared schema and validation for the uTiki P1 performance baseline."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
REPORT_KIND = "utiki-p1-performance"
JOB_ID = "performance-contract"
TARGET = "isolated-linux-x64"
PHASE = "P1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SOURCE_CASES = ("date", "area", "seat", "quantity")
RESULT_KEYS = (
    ("V12", "V12.perf.baseline-source-cold", "baseline-source-cold"),
    ("V12", "V12.perf.baseline-source-warm", "baseline-source-warm"),
    (
        "V12",
        "V12.perf.baseline-cdp-command-count",
        "baseline-cdp-command-count",
    ),
    (
        "V12",
        "V12.perf.baseline-event-loop-lag",
        "baseline-event-loop-lag",
    ),
    ("V13", "V13.perf.source-cold-start", "source-cold-start"),
)
NODE_IDS = (
    "tests/performance/utiki/test_p1_performance_contract.py::"
    "test_v12_baseline_source_cold",
    "tests/performance/utiki/test_p1_performance_contract.py::"
    "test_v12_baseline_source_warm",
    "tests/performance/utiki/test_p1_performance_contract.py::"
    "test_v12_baseline_cdp_command_count",
    "tests/performance/utiki/test_p1_performance_contract.py::"
    "test_v12_baseline_event_loop_lag",
    "tests/performance/utiki/test_p1_performance_contract.py::"
    "test_v13_source_cold_start",
)


class PerformanceReportError(ValueError):
    """Raised when performance evidence is incomplete or self-inconsistent."""


def duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PerformanceReportError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PerformanceReportError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PerformanceReportError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise PerformanceReportError(f"cannot digest file: {path}") from exc
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise PerformanceReportError(
            f"{label} fields mismatch: {sorted(actual ^ expected)}"
        )


def load_contract(path: Path) -> dict[str, Any]:
    value = load_json(path)
    _exact_keys(
        value,
        {
            "schema_version",
            "contract_id",
            "job_id",
            "target",
            "runtime",
            "canary",
            "measurement",
            "semantic_baseline",
            "required_execution_results",
        },
        "performance contract",
    )
    if value["schema_version"] != SCHEMA_VERSION:
        raise PerformanceReportError("unsupported performance contract schema")
    if value["job_id"] != JOB_ID or value["target"] != TARGET:
        raise PerformanceReportError("performance contract context is invalid")
    runtime = value["runtime"]
    if not isinstance(runtime, dict):
        raise PerformanceReportError("performance runtime contract is absent")
    required_runtime = {
        "kind": "docker-linux-container",
        "image_must_be_digest_pinned": True,
        "build_evidence_required": True,
        "network_mode": "none",
        "read_only_root": True,
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "privileged": False,
        "repository_mount": "read-only",
        "artifact_mount": "write-only-purpose",
    }
    if runtime != required_runtime:
        raise PerformanceReportError("performance runtime contract is unsafe")
    canary = value["canary"]
    if not isinstance(canary, dict) or canary != {
        "controlled_peer_network": "internal-only",
        "positive_control_required": True,
        "required_inner_probes": [
            "loopback-positive-control",
            "only-loopback-interface",
            "no-default-ipv4-route",
            "no-default-ipv6-route",
            "driver-direct-ipv4",
            "driver-direct-ipv6",
            "driver-dns",
            "driver-udp",
            "child-process-netns",
        ],
        "forbidden_peer_paths": ["/driver-http", "/driver-ipv6", "/child-http"],
        "forbidden_peer_events": [
            "PEER_EVENT udp kind=dns-query",
            "PEER_EVENT udp kind=quic",
        ],
    }:
        raise PerformanceReportError("performance canary contract changed")
    measurement = value["measurement"]
    required_measurement = {
        "source_mode": "all",
        "seed": 1701,
        "warm_samples": 300,
        "warmup_per_batch": 20,
        "batches": 3,
        "cold_samples_per_case": 3,
        "event_loop_samples": 300,
        "browser_warmup": 5,
        "browser_samples": 30,
        "source_cases": list(SOURCE_CASES),
        "production_origin": "https://tix.ctbcsports.com",
        "production_path": "/BROTHERS/UTK0201_",
        "unknown_request_action": "deny-and-fail",
        "live_authorization_used": False,
    }
    if measurement != required_measurement:
        raise PerformanceReportError("performance measurement contract changed")
    semantic_baseline = value["semantic_baseline"]
    if semantic_baseline != {
        "source_case_results": {
            "area": "b19b4557991c69cc677e4860efdc87dcb7bba4c989df70ab0db24deedf91041d",
            "date": "dc7d892478f42f861c82097ea07a7db7ffe31b48fce9387c309826a2968eecff",
            "quantity": "8f2cdf62b1266ab3025343419058332e6b1c2c72bb3e3a1c01c45b18f45be8b3",
            "seat": "99f3513d1481a78f1c47b890cade7f212257b92be19ea9432b568de65d14f342",
        }
    }:
        raise PerformanceReportError("performance semantic baseline changed")
    required_results = value["required_execution_results"]
    if not isinstance(required_results, list):
        raise PerformanceReportError("performance result contract is absent")
    actual_results = []
    for item in required_results:
        if not isinstance(item, dict):
            raise PerformanceReportError("performance result contract is invalid")
        _exact_keys(
            item,
            {"case_id", "obligation_id", "variant_id"},
            "performance result contract",
        )
        actual_results.append(
            (item["case_id"], item["obligation_id"], item["variant_id"])
        )
    if tuple(actual_results) != RESULT_KEYS:
        raise PerformanceReportError("performance result contract does not match P1")
    return value


def summarize_samples(samples: Sequence[int], unit: str) -> dict[str, Any]:
    values = [int(value) for value in samples]
    if not values or any(value < 0 for value in values):
        raise PerformanceReportError("sample set must contain non-negative values")
    ordered = sorted(values)

    def percentile(value: float) -> int:
        rank = max(1, math.ceil(value * len(ordered)))
        return ordered[rank - 1]

    return {
        "unit": unit,
        "sample_count": len(values),
        "samples": values,
        "failure_count": 0,
        "failures": [],
        "median": int(statistics.median(values)),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "min": min(values),
        "max": max(values),
    }


def _validate_summary(
    summary: Any,
    *,
    label: str,
    unit: str,
    sample_count: int,
) -> None:
    if not isinstance(summary, dict):
        raise PerformanceReportError(f"{label} summary is absent")
    _exact_keys(
        summary,
        {
            "unit",
            "sample_count",
            "samples",
            "failure_count",
            "failures",
            "median",
            "p95",
            "p99",
            "min",
            "max",
        },
        label,
    )
    samples = summary["samples"]
    if (
        summary["unit"] != unit
        or summary["sample_count"] != sample_count
        or not isinstance(samples, list)
        or len(samples) != sample_count
        or summary["failure_count"] != 0
        or summary["failures"] != []
        or any(type(value) is not int or value < 0 for value in samples)
    ):
        raise PerformanceReportError(f"{label} samples or failures are invalid")
    if summarize_samples(samples, unit) != summary:
        raise PerformanceReportError(f"{label} aggregate does not match raw samples")


def _validate_source_metric(metric: Any, label: str, samples: int) -> None:
    if not isinstance(metric, dict):
        raise PerformanceReportError(f"source metric is missing: {label}")
    values = metric.get("samples_ns")
    if (
        metric.get("unit") != "ns"
        or metric.get("sample_count") != samples
        or not isinstance(values, list)
        or len(values) != samples
        or any(type(value) is not int or value < 0 for value in values)
        or metric.get("failure_count") != 0
        or metric.get("failures") != []
    ):
        raise PerformanceReportError(f"source metric is incomplete: {label}")
    ordered = sorted(values)
    expected = {
        "median_ns": int(statistics.median(values)),
        "p95_ns": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "p99_ns": ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)],
        "min_ns": min(values),
        "max_ns": max(values),
    }
    if any(metric.get(name) != value for name, value in expected.items()):
        raise PerformanceReportError(f"source metric aggregate is invalid: {label}")


def validate_source_report(
    report: Any,
    *,
    candidate_sha: str,
    contract: Mapping[str, Any],
) -> None:
    if not isinstance(report, dict):
        raise PerformanceReportError("source benchmark report is absent")
    if report.get("schema_version") != 1 or report.get("benchmark") != "utiki-source":
        raise PerformanceReportError("source benchmark schema is invalid")
    run = report.get("run")
    parameters = report.get("parameters")
    metrics = report.get("metrics")
    if not isinstance(run, dict) or not isinstance(parameters, dict):
        raise PerformanceReportError("source benchmark context is absent")
    if not isinstance(metrics, dict):
        raise PerformanceReportError("source benchmark metrics are absent")
    git = run.get("git")
    os_value = run.get("os")
    if (
        not isinstance(git, dict)
        or git.get("candidate_sha") != candidate_sha
        or git.get("dirty") is not False
        or run.get("hard_egress_isolation") is not True
    ):
        raise PerformanceReportError(
            "source benchmark is not a clean hard-isolated candidate run"
        )
    if (
        not isinstance(os_value, dict)
        or os_value.get("system") != "Linux"
        or os_value.get("machine") not in {"x86_64", "amd64"}
    ):
        raise PerformanceReportError("source benchmark did not run on Linux x64")
    expected = contract["measurement"]
    parameter_expectations = {
        "mode": expected["source_mode"],
        "seed": expected["seed"],
        "warmup_per_batch": expected["warmup_per_batch"],
        "warm_samples_total": expected["warm_samples"],
        "batches": expected["batches"],
        "cold_samples_per_case": expected["cold_samples_per_case"],
        "event_loop_samples_total": expected["event_loop_samples"],
    }
    if any(parameters.get(key) != value for key, value in parameter_expectations.items()):
        raise PerformanceReportError("source benchmark parameters do not match P1")
    expected_metrics: set[str] = {"event_loop.scheduling_lag"}
    for case in SOURCE_CASES:
        expected_metrics.update(
            {
                f"{case}.cold_import",
                f"{case}.cold_first_operation",
                f"{case}.warm_parse_select_plan",
            }
        )
    if set(metrics) != expected_metrics:
        raise PerformanceReportError("source benchmark metric set is incomplete")
    for case in SOURCE_CASES:
        _validate_source_metric(
            metrics[f"{case}.cold_import"],
            f"{case}.cold_import",
            expected["cold_samples_per_case"],
        )
        _validate_source_metric(
            metrics[f"{case}.cold_first_operation"],
            f"{case}.cold_first_operation",
            expected["cold_samples_per_case"],
        )
        _validate_source_metric(
            metrics[f"{case}.warm_parse_select_plan"],
            f"{case}.warm_parse_select_plan",
            expected["warm_samples"],
        )
    _validate_source_metric(
        metrics["event_loop.scheduling_lag"],
        "event_loop.scheduling_lag",
        expected["event_loop_samples"],
    )
    case_results = report.get("case_results")
    if not isinstance(case_results, dict) or set(case_results) != set(SOURCE_CASES):
        raise PerformanceReportError("source benchmark semantic outputs are incomplete")
    if any(
        not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None
        for value in case_results.values()
    ):
        raise PerformanceReportError("source semantic output digest is invalid")
    if case_results != contract["semantic_baseline"]["source_case_results"]:
        raise PerformanceReportError("source semantic outputs differ from P0")
    source_digests = report.get("digests")
    subject_files = report.get("subject_files")
    if (
        not isinstance(source_digests, dict)
        or not isinstance(source_digests.get("subject_sha256"), str)
        or SHA256_PATTERN.fullmatch(source_digests["subject_sha256"]) is None
        or not isinstance(subject_files, list)
        or subject_files != sorted(subject_files)
        or len(subject_files) != len(set(subject_files))
        or not {
            "src/platforms/utiki/profiles.py",
            "src/platforms/utiki/selection.py",
        }.issubset(subject_files)
    ):
        raise PerformanceReportError("source benchmark subject graph is incomplete")


def validate_raw_report(
    report: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    expected_candidate_sha: str | None = None,
) -> None:
    _exact_keys(
        report,
        {
            "schema_version",
            "report_kind",
            "status",
            "context",
            "environment",
            "method",
            "digests",
            "production_subjects",
            "isolation",
            "source",
            "browser",
            "execution_results",
        },
        "performance report",
    )
    if report["schema_version"] != SCHEMA_VERSION or report["report_kind"] != REPORT_KIND:
        raise PerformanceReportError("performance report schema is invalid")
    if report["status"] != "PENDING_OUTER_AUDIT":
        raise PerformanceReportError("performance report bypassed outer isolation audit")
    context = report["context"]
    if not isinstance(context, dict):
        raise PerformanceReportError("performance context is absent")
    _exact_keys(
        context,
        {"run_id", "attempt", "phase", "job_id", "target", "candidate_sha"},
        "performance context",
    )
    candidate_sha = context["candidate_sha"]
    if (
        context["phase"] != PHASE
        or context["job_id"] != JOB_ID
        or context["target"] != TARGET
        or not isinstance(context["run_id"], str)
        or not context["run_id"]
        or type(context["attempt"]) is not int
        or context["attempt"] < 1
        or not isinstance(candidate_sha, str)
        or GIT_SHA_PATTERN.fullmatch(candidate_sha) is None
        or (expected_candidate_sha is not None and candidate_sha != expected_candidate_sha)
    ):
        raise PerformanceReportError("performance context does not match P1")
    method = report["method"]
    if not isinstance(method, dict) or method != contract["measurement"]:
        raise PerformanceReportError("performance method does not match the contract")
    digests = report["digests"]
    required_digests = {
        "contract_sha256",
        "execution_manifest_sha256",
        "fixture_sha256",
        "production_script_sha256",
        "source_benchmark_script_sha256",
        "inner_harness_sha256",
        "semantic_output_sha256",
            "production_subject_manifest_sha256",
    }
    if not isinstance(digests, dict) or set(digests) != required_digests:
        raise PerformanceReportError("performance digest set is incomplete")
    if any(
        not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None
        for value in digests.values()
    ):
        raise PerformanceReportError("performance digest is invalid")
    subjects = report.get("production_subjects")
    if not isinstance(subjects, list) or not subjects:
        raise PerformanceReportError("production subject manifest is absent")
    normalized_subjects: list[dict[str, str]] = []
    for item in subjects:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item["path"], str)
            or not item["path"].startswith("src/")
            or not isinstance(item["sha256"], str)
            or SHA256_PATTERN.fullmatch(item["sha256"]) is None
        ):
            raise PerformanceReportError("production subject entry is invalid")
        normalized_subjects.append(item)
    if normalized_subjects != sorted(normalized_subjects, key=lambda item: item["path"]):
        raise PerformanceReportError("production subject manifest is not sorted")
    paths = [item["path"] for item in normalized_subjects]
    if len(paths) != len(set(paths)) or not {
        "src/platforms/utiki/dom.py",
        "src/platforms/utiki/profiles.py",
        "src/platforms/utiki/selection.py",
        "src/util.py",
    }.issubset(paths):
        raise PerformanceReportError("production subject graph is incomplete")
    if sha256_json(normalized_subjects) != digests[
        "production_subject_manifest_sha256"
    ]:
        raise PerformanceReportError("production subject manifest digest mismatch")
    isolation = report["isolation"]
    if isolation != {
        "mode": "linux-container-network-none",
        "only_loopback_interface": True,
        "no_default_ipv4_route": True,
        "no_default_ipv6_route": True,
        "inner_probe_status": "PASS",
        "outer_peer_audit_required": True,
    }:
        raise PerformanceReportError("inner hard-isolation evidence is incomplete")
    environment = report["environment"]
    if (
        not isinstance(environment, dict)
        or environment.get("os") != "linux"
        or environment.get("architecture") != "x86_64"
        or not isinstance(environment.get("python"), str)
        or not environment.get("python")
        or not isinstance(environment.get("browser"), str)
        or not environment.get("browser")
    ):
        raise PerformanceReportError("performance environment is incomplete")
    validate_source_report(
        report["source"], candidate_sha=candidate_sha, contract=contract
    )
    browser = report["browser"]
    if not isinstance(browser, dict):
        raise PerformanceReportError("browser benchmark is absent")
    _exact_keys(
        browser,
        {
            "origin",
            "path",
            "classifier",
            "interception_stage",
            "interception_installed",
            "classifier_patched",
            "allowlist_entries",
            "navigation_fulfilled_requests",
            "rejected_unknown_requests",
            "live_authorization_used",
            "semantic_output_sha256",
            "command_labels",
            "metrics",
        },
        "browser benchmark",
    )
    expected_method = contract["measurement"]
    if (
        browser["origin"] != expected_method["production_origin"]
        or browser["path"] != expected_method["production_path"]
        or browser["classifier"] != "DATE"
        or browser["interception_stage"] != "Request"
        or browser["interception_installed"] is not True
        or browser["classifier_patched"] is not False
        or browser["allowlist_entries"] != 1
        or browser["navigation_fulfilled_requests"] != 1
        or browser["rejected_unknown_requests"] != 0
        or browser["live_authorization_used"] is not False
        or browser["semantic_output_sha256"] != digests["semantic_output_sha256"]
        or not isinstance(browser["command_labels"], dict)
        or not browser["command_labels"]
    ):
        raise PerformanceReportError("production-origin browser contract is invalid")
    browser_metrics = browser["metrics"]
    if not isinstance(browser_metrics, dict) or set(browser_metrics) != {
        "observation_latency",
        "cdp_commands_per_observation",
        "ticketing_requests_per_observation",
    }:
        raise PerformanceReportError("browser metric set is incomplete")
    browser_samples = expected_method["browser_samples"]
    _validate_summary(
        browser_metrics["observation_latency"],
        label="browser observation latency",
        unit="ns",
        sample_count=browser_samples,
    )
    _validate_summary(
        browser_metrics["cdp_commands_per_observation"],
        label="browser CDP command count",
        unit="count",
        sample_count=browser_samples,
    )
    _validate_summary(
        browser_metrics["ticketing_requests_per_observation"],
        label="browser ticketing request count",
        unit="count",
        sample_count=browser_samples,
    )
    cdp_samples = browser_metrics["cdp_commands_per_observation"]["samples"]
    if any(value <= 0 for value in cdp_samples):
        raise PerformanceReportError("browser observation recorded no CDP command")
    if any(
        value != 0
        for value in browser_metrics["ticketing_requests_per_observation"]["samples"]
    ):
        raise PerformanceReportError("browser observation emitted a ticketing request")
    results = report["execution_results"]
    if not isinstance(results, list) or len(results) != len(RESULT_KEYS):
        raise PerformanceReportError("performance execution results are incomplete")
    actual_results: list[tuple[str, str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            raise PerformanceReportError("performance execution result is invalid")
        _exact_keys(
            item,
            {"case_id", "obligation_id", "variant_id", "job_id", "target", "status"},
            "performance execution result",
        )
        if item["job_id"] != JOB_ID or item["target"] != TARGET or item["status"] != "PASS":
            raise PerformanceReportError("performance execution result did not pass")
        actual_results.append(
            (item["case_id"], item["obligation_id"], item["variant_id"])
        )
    if tuple(actual_results) != RESULT_KEYS:
        raise PerformanceReportError("performance execution result set is wrong")


def execution_results() -> list[dict[str, str]]:
    return [
        {
            "case_id": case_id,
            "obligation_id": obligation_id,
            "variant_id": variant_id,
            "job_id": JOB_ID,
            "target": TARGET,
            "status": "PASS",
        }
        for case_id, obligation_id, variant_id in RESULT_KEYS
    ]
