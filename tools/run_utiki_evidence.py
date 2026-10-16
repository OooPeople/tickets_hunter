#!/usr/bin/env python3
"""Run one evidence producer and emit a digest-bound uTiki evidence envelope.

This launcher is intentionally mechanical.  It derives execution keys from the
reviewed execution manifest, executes the named repository producer, captures
the real stdout/stderr bytes and observed exit code, and binds every declared
input/output by digest.  Semantic acceptance remains the responsibility of
``verify_utiki_ci_results.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
KINDS = {
    "architecture-report",
    "typecheck-report",
    "python-syntax-report",
    "browser-isolation-report",
    "production-origin-report",
    "benchmark-report",
    "checker-exit-nonzero",
}


class LaunchError(Exception):
    pass


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LaunchError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LaunchError(f"JSON root is not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LaunchError(f"cannot digest file: {path}") from exc


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) != value:
        raise LaunchError(f"unsafe relative path: {value!r}")
    return value


def _reference(
    kind: str,
    scope: str,
    value: str,
    *,
    repository: Path,
    evidence_dir: Path,
) -> dict[str, str]:
    relative = _safe_relative(value)
    if scope == "repository":
        root = repository
    elif scope == "evidence":
        root = evidence_dir
    else:
        raise LaunchError(f"unsupported reference scope: {scope}")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise LaunchError(f"evidence reference is not a regular file: {value}")
    return {"kind": kind, "scope": scope, "path": relative, "sha256": _sha256(path)}


def _execution_results(
    manifest: dict[str, Any],
    kind: str,
    job_id: str,
    target: str,
    selected_key: tuple[str, str, str] | None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise LaunchError("execution manifest has no cases array")
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise LaunchError("execution manifest contains an invalid case")
        executions = case.get("executions")
        if not isinstance(executions, list):
            raise LaunchError("execution manifest case has no executions array")
        for execution in executions:
            if not isinstance(execution, dict):
                raise LaunchError("execution manifest contains an invalid execution")
            if (
                execution.get("status") != "MAPPED"
                or execution.get("job_id") != job_id
                or execution.get("target") != target
            ):
                continue
            evidence = execution.get("evidence")
            if not isinstance(evidence, list):
                raise LaunchError("execution evidence is not an array")
            if not any(
                isinstance(item, dict) and item.get("kind") == kind
                for item in evidence
            ):
                continue
            key = (
                case["id"],
                execution.get("obligation_id"),
                execution.get("variant_id"),
                job_id,
                target,
            )
            if not all(isinstance(item, str) and item for item in key):
                raise LaunchError("execution key is invalid")
            if selected_key is not None and key[:3] != selected_key:
                continue
            if key in seen:
                raise LaunchError("execution manifest contains duplicate evidence key")
            seen.add(key)
            results.append(
                {
                    "case_id": key[0],
                    "obligation_id": key[1],
                    "variant_id": key[2],
                    "job_id": key[3],
                    "target": key[4],
                    "status": "PASS",
                }
            )
    if not results:
        raise LaunchError(f"manifest maps no {kind} evidence for {job_id}/{target}")
    return sorted(
        results,
        key=lambda item: (
            item["case_id"], item["obligation_id"], item["variant_id"]
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--kind", choices=sorted(KINDS), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--obligation-id")
    parser.add_argument("--variant-id")
    parser.add_argument(
        "--input",
        nargs=3,
        action="append",
        default=[],
        metavar=("KIND", "SCOPE", "PATH"),
    )
    parser.add_argument(
        "--producer-report",
        help="Evidence-relative producer JSON, or the literal 'stdout'.",
    )
    parser.add_argument("--rejection-token")
    parser.add_argument("producer_arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        report_path = arguments.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_dir = report_path.parent
        manifest = _load_json(arguments.execution_manifest)
        manifest_digest = _sha256(arguments.execution_manifest)
        producer_relative = _safe_relative(arguments.producer)
        producer_path = repository / producer_relative
        if producer_path.is_symlink() or not producer_path.is_file():
            raise LaunchError("producer is not a regular repository file")
        launcher_path = Path(__file__).resolve()
        try:
            launcher_relative = launcher_path.relative_to(repository).as_posix()
        except ValueError as exc:
            raise LaunchError("launcher is outside the repository") from exc

        command = [sys.executable, str(producer_path)]
        producer_arguments = list(arguments.producer_arguments)
        if producer_arguments and producer_arguments[0] == "--":
            producer_arguments.pop(0)
        command.extend(producer_arguments)
        expectation = (
            "NONZERO" if arguments.kind == "checker-exit-nonzero" else "ZERO"
        )
        if not arguments.producer_report:
            raise LaunchError("evidence requires --producer-report")
        if expectation == "ZERO":
            if arguments.producer_report != "stdout":
                producer_report_before = evidence_dir / _safe_relative(
                    arguments.producer_report
                )
                if producer_report_before.exists():
                    raise LaunchError(
                        "producer report already exists; refusing stale-output reuse"
                    )
                if str(producer_report_before.resolve()) not in command:
                    raise LaunchError(
                        "producer command does not identify its report output"
                    )
        elif arguments.producer_report == "stdout":
            raise LaunchError("negative evidence requires a separate child transcript report")
        else:
            producer_report_before = evidence_dir / _safe_relative(
                arguments.producer_report
            )
            if producer_report_before.exists():
                raise LaunchError(
                    "producer report already exists; refusing stale-output reuse"
                )
            if str(producer_report_before.resolve()) not in command:
                raise LaunchError(
                    "negative producer command does not identify its transcript report"
                )
        for _, scope, raw_path in arguments.input:
            input_path = (
                repository / raw_path if scope == "repository" else evidence_dir / raw_path
            ).resolve()
            if str(input_path) not in command:
                raise LaunchError("producer command does not consume a declared input")
        completed = subprocess.run(
            command,
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stem = report_path.stem
        stdout_path = evidence_dir / f"{stem}.stdout"
        stderr_path = evidence_dir / f"{stem}.stderr"
        stdout_path.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)

        expectation_met = (
            completed.returncode != 0 if expectation == "NONZERO" else completed.returncode == 0
        )
        if expectation == "NONZERO" and not arguments.rejection_token:
            raise LaunchError("negative evidence requires --rejection-token")
        if expectation == "ZERO" and arguments.rejection_token is not None:
            raise LaunchError("positive evidence may not declare a rejection token")
        selectors = (
            arguments.case_id,
            arguments.obligation_id,
            arguments.variant_id,
        )
        if expectation == "NONZERO" and not all(selectors):
            raise LaunchError(
                "negative evidence requires one explicit case/obligation/variant key"
            )
        if expectation == "ZERO" and any(selectors):
            raise LaunchError("positive shared evidence may not use a partial key selector")
        selected_key = selectors if all(selectors) else None

        inputs = [
            _reference(
                kind,
                scope,
                path,
                repository=repository,
                evidence_dir=evidence_dir,
            )
            for kind, scope, path in arguments.input
        ]
        outputs = [
            _reference(
                "stdout",
                "evidence",
                stdout_path.name,
                repository=repository,
                evidence_dir=evidence_dir,
            ),
            _reference(
                "stderr",
                "evidence",
                stderr_path.name,
                repository=repository,
                evidence_dir=evidence_dir,
            ),
        ]
        if arguments.producer_report == "stdout":
            producer_report = stdout_path
        elif arguments.producer_report:
            producer_report = evidence_dir / _safe_relative(arguments.producer_report)
        else:
            raise LaunchError("evidence requires --producer-report")
        _load_json(producer_report)
        outputs.append(
            _reference(
                "producer-report",
                "evidence",
                producer_report.relative_to(evidence_dir).as_posix(),
                repository=repository,
                evidence_dir=evidence_dir,
            )
        )

        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": arguments.kind,
            "context": {
                "run_id": arguments.run_id,
                "attempt": arguments.attempt,
                "job_id": arguments.job_id,
                "phase": arguments.phase,
                "target": arguments.target,
                "candidate_sha": arguments.candidate_sha,
                "catalog_sha256": arguments.catalog_sha256,
                "execution_manifest_sha256": manifest_digest,
            },
            "producer": {
                "path": producer_relative,
                "sha256": _sha256(producer_path),
                "command": command,
            },
            "launcher": {
                "path": launcher_relative,
                "sha256": _sha256(launcher_path),
            },
            "inputs": inputs,
            "outputs": outputs,
            "expectation": expectation,
            "observed_exit_code": completed.returncode,
            "status": "PASS" if expectation_met else "FAIL",
            "results": _execution_results(
                manifest,
                arguments.kind,
                arguments.job_id,
                arguments.target,
                selected_key,
            ),
            "rejection_token": arguments.rejection_token,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if expectation_met else 1
    except LaunchError as exc:
        sys.stderr.write(f"run_utiki_evidence: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
