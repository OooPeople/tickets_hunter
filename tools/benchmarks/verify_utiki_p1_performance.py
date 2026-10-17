#!/usr/bin/env python3
"""Verify one outer-audited uTiki P1 performance run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Mapping
from xml.etree import ElementTree

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.benchmarks.utiki_p1_performance import (
    JOB_ID,
    PHASE,
    RESULT_KEYS,
    TARGET,
    PerformanceReportError,
    execution_results,
    load_contract,
    load_json,
    performance_phase,
    result_keys,
    sha256_file,
    validate_raw_report,
    write_json,
)


def _property_map(parent: ElementTree.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    properties = parent.find("properties")
    if properties is None:
        raise PerformanceReportError("managed JUnit properties are absent")
    for element in properties.findall("property"):
        name = element.get("name")
        value = element.get("value")
        if not name or value is None or name in values:
            raise PerformanceReportError("managed JUnit property is invalid")
        values[name] = value
    return values


def _validate_junit(
    path: Path,
    *,
    context: Mapping[str, Any],
    execution_manifest_sha256: str,
    phase: str = "P1",
) -> None:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise PerformanceReportError("managed performance JUnit is invalid") from exc
    required_results = result_keys(phase)
    if root.tag != "testsuite" or root.get("tests") != str(len(required_results)):
        raise PerformanceReportError("managed performance JUnit count is invalid")
    suite = _property_map(root)
    expected_suite = {
        "run_id": str(context["run_id"]),
        "attempt": str(context["attempt"]),
        "job_id": JOB_ID,
        "phase": phase,
        "target": TARGET,
        "candidate_sha": str(context["candidate_sha"]),
        "catalog_sha256": str(context["catalog_sha256"]),
        "execution_manifest_sha256": execution_manifest_sha256,
    }
    if any(suite.get(name) != value for name, value in expected_suite.items()):
        raise PerformanceReportError("managed performance JUnit context mismatch")
    testcases = root.findall("testcase")
    if len(testcases) != len(required_results):
        raise PerformanceReportError("managed performance JUnit testcase count is wrong")
    actual: list[tuple[str, str, str]] = []
    for testcase in testcases:
        if testcase.find("failure") is not None or testcase.find("error") is not None:
            raise PerformanceReportError("managed performance JUnit contains a failure")
        if testcase.find("skipped") is not None:
            raise PerformanceReportError("managed performance JUnit contains a skip")
        properties = _property_map(testcase)
        if (
            properties.get("outcome") != "passed"
            or properties.get("wasxfail") != "false"
            or properties.get("setup_outcome") != "passed"
            or properties.get("call_outcome") != "passed"
            or properties.get("teardown_outcome") != "passed"
        ):
            raise PerformanceReportError("managed performance testcase did not pass")
        actual.append(
            (
                properties.get("case_id", ""),
                properties.get("obligation_id", ""),
                properties.get("variant_id", ""),
            )
        )
    if set(actual) != set(required_results) or len(actual) != len(set(actual)):
        raise PerformanceReportError("managed performance execution keys are wrong")


def _validate_execution_results(value: Any, phase: str = "P1") -> None:
    if value != execution_results(phase):
        raise PerformanceReportError("outer performance execution results are wrong")


def _validate_outer(
    report: Mapping[str, Any],
    *,
    raw_report: Path,
    junit: Path,
    reporter_result: Path,
    phase: str = "P1",
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "report_kind",
        "status",
        "run_id",
        "attempt",
        "phase",
        "job_id",
        "target",
        "candidate_sha",
        "catalog_sha256",
        "execution_manifest_sha256",
        "context",
        "execution_results",
        "contract",
        "execution_manifest",
        "image",
        "image_build_evidence",
        "runtime_evidence",
        "peer_audit",
        "inner_report_sha256",
        "benchmark_report_sha256",
        "junit_sha256",
        "reporter_result_sha256",
        "started_at_unix",
        "finished_at_unix",
        "reasons",
    }
    if set(report) != expected_fields:
        raise PerformanceReportError("outer performance isolation fields mismatch")
    if (
        report.get("schema_version") != 1
        or report.get("report_kind")
        != "utiki-browser-isolation-launcher"
        or report.get("status") != "PASS"
    ):
        raise PerformanceReportError("outer performance isolation did not pass")
    context = report.get("context")
    if (
        not isinstance(context, dict)
        or set(context)
        != {
            "run_id",
            "attempt",
            "phase",
            "job_id",
            "target",
            "candidate_sha",
            "catalog_sha256",
        }
        or context.get("phase") != phase
        or context.get("job_id") != JOB_ID
        or context.get("target") != TARGET
    ):
        raise PerformanceReportError("outer performance context is invalid")
    for name in (
        "run_id",
        "attempt",
        "phase",
        "job_id",
        "target",
        "candidate_sha",
        "catalog_sha256",
    ):
        if report.get(name) != context.get(name):
            raise PerformanceReportError(
                f"outer performance top-level context mismatch: {name}"
            )
    if report.get("execution_manifest_sha256") != report.get(
        "execution_manifest", {}
    ).get("sha256"):
        raise PerformanceReportError(
            "outer performance execution manifest context mismatch"
        )
    _validate_execution_results(report.get("execution_results"), phase)
    peer = report.get("peer_audit")
    if (
        not isinstance(peer, dict)
        or peer.get("outbound_count") != 0
        or peer.get("forbidden_paths_observed") != []
        or peer.get("forbidden_events_observed") != []
        or peer.get("positive_control_counts")
        != {"tcp_ipv4": 1, "tcp_ipv6": 1, "udp_dns": 1}
    ):
        raise PerformanceReportError("outer controlled-peer audit is incomplete")
    runtime = report.get("runtime_evidence")
    container = runtime.get("isolated_container") if isinstance(runtime, dict) else None
    if (
        not isinstance(container, dict)
        or container.get("network_mode") != "none"
        or container.get("privileged") is not False
        or container.get("readonly_rootfs") is not True
        or "ALL" not in container.get("cap_drop", [])
    ):
        raise PerformanceReportError("outer isolated container evidence is invalid")
    if report.get("benchmark_report_sha256") != sha256_file(raw_report):
        raise PerformanceReportError("outer benchmark digest mismatch")
    if report.get("junit_sha256") != sha256_file(junit):
        raise PerformanceReportError("outer JUnit digest mismatch")
    if report.get("reporter_result_sha256") != sha256_file(reporter_result):
        raise PerformanceReportError("outer reporter-result digest mismatch")
    reporter = load_json(reporter_result)
    if (
        reporter.get("status") != "PASS"
        or reporter.get("required_count") != len(result_keys(phase))
        or reporter.get("selected_count") != len(result_keys(phase))
    ):
        raise PerformanceReportError("managed performance reporter result is invalid")
    return context


def verify(
    *,
    contract_path: Path,
    isolation_path: Path,
    raw_report_path: Path,
    junit_path: Path,
    reporter_result_path: Path,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    phase = performance_phase(contract)
    isolation = load_json(isolation_path)
    raw = load_json(raw_report_path)
    context = _validate_outer(
        isolation,
        raw_report=raw_report_path,
        junit=junit_path,
        reporter_result=reporter_result_path,
        phase=phase,
    )
    validate_raw_report(
        raw,
        contract=contract,
        expected_candidate_sha=context["candidate_sha"],
    )
    contract_evidence = isolation.get("contract")
    if (
        not isinstance(contract_evidence, dict)
        or contract_evidence.get("contract_id") != contract["contract_id"]
        or contract_evidence.get("sha256") != sha256_file(contract_path)
        or raw["digests"]["contract_sha256"] != sha256_file(contract_path)
    ):
        raise PerformanceReportError("performance contract digest mismatch")
    raw_context = raw["context"]
    for name in ("run_id", "attempt", "phase", "job_id", "target", "candidate_sha"):
        if raw_context.get(name) != context.get(name):
            raise PerformanceReportError(
                f"inner/outer performance context mismatch: {name}"
            )
    manifest_sha = isolation.get("execution_manifest", {}).get("sha256")
    if manifest_sha != raw["digests"]["execution_manifest_sha256"]:
        raise PerformanceReportError("inner/outer execution manifest digest mismatch")
    _validate_junit(
        junit_path,
        context=context,
        execution_manifest_sha256=manifest_sha,
        phase=phase,
    )
    if phase == "P2" and raw["digests"]["hot_path_script_sha256"] != sha256_file(
        REPOSITORY_ROOT / "tools" / "benchmarks" / "utiki_p2_hot_path.py"
    ):
        raise PerformanceReportError("P2 hot-path producer digest mismatch")
    return {
        "schema_version": 1,
        "report_kind": f"utiki-{phase.lower()}-performance-verification",
        "status": "PASS",
        "context": context,
        "execution_results": execution_results(phase),
        "method": raw["method"],
        "hard_egress_isolation": {
            "verified": True,
            "mode": "linux-container-network-none",
            "peer_outbound_count": 0,
            "isolation_report_sha256": sha256_file(isolation_path),
            "image_id": isolation["image"]["id"],
            "image_build_evidence_sha256": isolation["image_build_evidence"][
                "sha256"
            ],
        },
        "digests": {
            **raw["digests"],
            "raw_benchmark_report_sha256": sha256_file(raw_report_path),
            "junit_sha256": sha256_file(junit_path),
            "reporter_result_sha256": sha256_file(reporter_result_path),
        },
        "source": {
            "run": raw["source"]["run"],
            "parameters": raw["source"]["parameters"],
            "case_results": raw["source"]["case_results"],
            "metrics": raw["source"]["metrics"],
        },
        "browser": raw["browser"],
        **({"hot_path": raw["hot_path"]} if phase == "P2" else {}),
        "threshold_policy": (
            ("P1 baseline establishment: " if phase == "P1" else "P2: ")
            + "fixed sample counts and zero failures; "
            "no arbitrary percentage threshold"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--isolation-report", type=Path, required=True)
    parser.add_argument("--raw-benchmark-report", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--reporter-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = verify(
            contract_path=arguments.contract,
            isolation_path=arguments.isolation_report,
            raw_report_path=arguments.raw_benchmark_report,
            junit_path=arguments.junit,
            reporter_result_path=arguments.reporter_result,
        )
        if arguments.output.exists() or arguments.output.is_symlink():
            raise PerformanceReportError("verified performance output already exists")
        write_json(arguments.output, result)
    except (PerformanceReportError, OSError, ValueError) as exc:
        print(f"verify_utiki_p1_performance: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
