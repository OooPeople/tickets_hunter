#!/usr/bin/env python3
"""Compare two uTiki source benchmark JSON reports without pass thresholds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
BENCHMARK_NAME = "utiki-source"
STATISTICS = ("median_ns", "p95_ns", "p99_ns")
REQUIRED_DIGESTS = (
    "fixture_sha256",
    "config_sha256",
    "script_sha256",
)
REQUIRED_ENVIRONMENT_FIELDS = (
    "python",
    "os",
    "hardware",
    "scope",
    "mode",
    "hard_egress_isolation",
)


class IncompatibleReportsError(ValueError):
    """Raised when two reports do not share a trustworthy methodology."""


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_report(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read benchmark report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark report {path} must be a JSON object")
    return payload


def _validate_report(report: Mapping[str, Any], label: str) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise IncompatibleReportsError(f"{label} schema_version is unsupported")
    if report.get("benchmark") != BENCHMARK_NAME:
        raise IncompatibleReportsError(f"{label} benchmark name is unsupported")
    if not isinstance(report.get("digests"), Mapping):
        raise IncompatibleReportsError(f"{label} digests are missing")
    if not isinstance(report.get("metrics"), Mapping):
        raise IncompatibleReportsError(f"{label} metrics are missing")
    if not isinstance(report.get("config"), Mapping):
        raise IncompatibleReportsError(f"{label} config is missing")
    if _sha256_json(report["config"]) != report["digests"].get("config_sha256"):
        raise IncompatibleReportsError(f"{label} config digest does not match content")


def _change(baseline: int, candidate: int) -> dict[str, Any]:
    delta = candidate - baseline
    if delta > 0:
        direction = "regression"
    elif delta < 0:
        direction = "improvement"
    else:
        direction = "unchanged"
    return {
        "baseline_ns": baseline,
        "candidate_ns": candidate,
        "delta_ns": delta,
        "percent_change": (
            (delta / baseline) * 100.0 if baseline != 0 else None
        ),
        "direction": direction,
    }


def compare_reports(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare exact aggregates and report every positive latency delta."""

    _validate_report(baseline, "baseline")
    _validate_report(candidate, "candidate")
    mismatches = []
    for name in REQUIRED_DIGESTS:
        baseline_value = baseline["digests"].get(name)
        candidate_value = candidate["digests"].get(name)
        if baseline_value is None or baseline_value != candidate_value:
            mismatches.append({
                "field": f"digests.{name}",
                "baseline": baseline_value,
                "candidate": candidate_value,
            })
    if baseline.get("case_results") != candidate.get("case_results"):
        mismatches.append({
            "field": "case_results",
            "baseline": baseline.get("case_results"),
            "candidate": candidate.get("case_results"),
        })
    baseline_metrics = baseline["metrics"]
    candidate_metrics = candidate["metrics"]
    if set(baseline_metrics) != set(candidate_metrics):
        mismatches.append({
            "field": "metrics.keys",
            "baseline": sorted(baseline_metrics),
            "candidate": sorted(candidate_metrics),
        })
    if baseline.get("parameters") != candidate.get("parameters"):
        mismatches.append({
            "field": "parameters",
            "baseline": baseline.get("parameters"),
            "candidate": candidate.get("parameters"),
        })
    baseline_run = baseline.get("run", {})
    candidate_run = candidate.get("run", {})
    for name in REQUIRED_ENVIRONMENT_FIELDS:
        baseline_value = baseline_run.get(name)
        candidate_value = candidate_run.get(name)
        if name == "python":
            baseline_value = {
                key: baseline_value.get(key)
                for key in ("version", "implementation")
            } if isinstance(baseline_value, Mapping) else baseline_value
            candidate_value = {
                key: candidate_value.get(key)
                for key in ("version", "implementation")
            } if isinstance(candidate_value, Mapping) else candidate_value
        if baseline_value is None or baseline_value != candidate_value:
            mismatches.append({
                "field": f"run.{name}",
                "baseline": baseline_value,
                "candidate": candidate_value,
            })
    if mismatches:
        raise IncompatibleReportsError(json.dumps(mismatches, sort_keys=True))

    metric_comparisons: dict[str, Any] = {}
    regressions = []
    for metric_name in sorted(baseline_metrics):
        baseline_metric = baseline_metrics[metric_name]
        candidate_metric = candidate_metrics[metric_name]
        statistic_changes = {}
        for statistic in STATISTICS:
            baseline_value = baseline_metric.get(statistic)
            candidate_value = candidate_metric.get(statistic)
            if not isinstance(baseline_value, int) or not isinstance(
                candidate_value, int
            ):
                raise IncompatibleReportsError(
                    f"metric {metric_name} lacks integer {statistic} values"
                )
            change = _change(baseline_value, candidate_value)
            statistic_changes[statistic] = change
            if change["direction"] == "regression":
                regressions.append({
                    "metric": metric_name,
                    "statistic": statistic,
                    **change,
                })
        baseline_failures = int(baseline_metric.get("failure_count", 0))
        candidate_failures = int(candidate_metric.get("failure_count", 0))
        metric_comparisons[metric_name] = {
            "statistics": statistic_changes,
            "failures": {
                "baseline": baseline_failures,
                "candidate": candidate_failures,
                "delta": candidate_failures - baseline_failures,
            },
            "sample_count": {
                "baseline": int(baseline_metric.get("sample_count", 0)),
                "candidate": int(candidate_metric.get("sample_count", 0)),
            },
        }

    blocked = {
        "baseline": baseline.get("blocked_metrics", {}),
        "candidate": candidate.get("blocked_metrics", {}),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "comparison": "utiki-source",
        "comparable": True,
        "threshold_policy": (
            "informational-only; every positive delta is shown and no arbitrary "
            "percentage is treated as pass/fail"
        ),
        "baseline_run_id": baseline.get("run", {}).get("run_id"),
        "candidate_run_id": candidate.get("run", {}).get("run_id"),
        "digests": {
            name: baseline["digests"][name] for name in REQUIRED_DIGESTS
        },
        "subject_digests": {
            "baseline": baseline["digests"].get("subject_sha256"),
            "candidate": candidate["digests"].get("subject_sha256"),
        },
        "metrics": metric_comparisons,
        "regressions_observed": bool(regressions),
        "regressions": regressions,
        "warnings": [],
        "blocked_metrics": blocked,
    }


def _write_result(result: Mapping[str, Any], output: str) -> None:
    serialized = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(target)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_pos", nargs="?")
    parser.add_argument("candidate_pos", nargs="?")
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--output", default="-", help="JSON output path, or - for stdout")
    args = parser.parse_args(argv)
    positional = args.baseline_pos is not None or args.candidate_pos is not None
    flagged = args.baseline is not None or args.candidate is not None
    if positional == flagged:
        parser.error("provide either positional reports or --baseline/--candidate")
    if positional:
        if args.baseline_pos is None or args.candidate_pos is None:
            parser.error("both baseline and candidate reports are required")
        args.baseline = args.baseline_pos
        args.candidate = args.candidate_pos
    elif args.baseline is None or args.candidate is None:
        parser.error("both --baseline and --candidate are required")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = compare_reports(
            _load_report(args.baseline),
            _load_report(args.candidate),
        )
    except (ValueError, IncompatibleReportsError) as exc:
        sys.stderr.write(f"incompatible benchmark reports: {exc}\n")
        return 2
    _write_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
