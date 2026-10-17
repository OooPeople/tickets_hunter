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


SCHEMA_VERSION = 2
KINDS = {
    "architecture-report",
    "typecheck-report",
    "python-syntax-report",
    "browser-isolation-report",
    "production-origin-report",
    "benchmark-report",
    "bundle-report",
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
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise LaunchError(f"unsafe relative path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path == PurePosixPath(".")
        or str(path) != value
    ):
        raise LaunchError(f"unsafe relative path: {value!r}")
    return value


def _contained_path(root: Path, value: str, *, label: str) -> tuple[str, Path]:
    relative = _safe_relative(value)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise LaunchError(f"{label} may not traverse a symlink: {value!r}")
    path = current.resolve()
    if root not in path.parents:
        raise LaunchError(f"{label} escapes its trusted root: {value!r}")
    return relative, path


def _reference(
    kind: str,
    scope: str,
    value: str,
    *,
    repository: Path,
    artifact_root: Path,
) -> dict[str, str]:
    if scope == "repository":
        root = repository
    elif scope == "artifact":
        root = artifact_root
    else:
        raise LaunchError(f"unsupported reference scope: {scope}")
    relative, path = _contained_path(root, value, label="evidence reference")
    if not path.is_file():
        raise LaunchError(f"evidence reference is not a regular file: {value}")
    return {"kind": kind, "scope": scope, "path": relative, "sha256": _sha256(path)}


def _command_mentions_path(command: list[str], expected: Path, *, cwd: Path) -> bool:
    for value in command[1:]:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            if candidate.resolve() == expected:
                return True
        except OSError:
            continue
    return False


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
    parser.add_argument("--artifact-root", type=Path, required=True)
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
        help="Artifact-root-relative producer JSON, or the literal 'stdout'.",
    )
    parser.add_argument(
        "--producer-output",
        nargs=2,
        action="append",
        default=[],
        metavar=("KIND", "ARTIFACT_PATH"),
    )
    parser.add_argument("--rejection-token")
    parser.add_argument("producer_arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        artifact_root = arguments.artifact_root.resolve(strict=True)
        if not repository.is_dir() or not artifact_root.is_dir():
            raise LaunchError("repository root and artifact root must be directories")
        report_path = arguments.report.resolve()
        if artifact_root != report_path and artifact_root not in report_path.parents:
            raise LaunchError("report is outside the artifact root")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        stem = report_path.stem
        stdout_path = report_path.with_name(f"{stem}.stdout")
        stderr_path = report_path.with_name(f"{stem}.stderr")
        if any(path.exists() for path in (report_path, stdout_path, stderr_path)):
            raise LaunchError("evidence output already exists; refusing stale-output reuse")
        manifest = _load_json(arguments.execution_manifest)
        manifest_digest = _sha256(arguments.execution_manifest)
        producer_relative, producer_path = _contained_path(
            repository,
            arguments.producer,
            label="producer",
        )
        if not producer_path.is_file():
            raise LaunchError("producer is not a regular repository file")
        producer_digest = _sha256(producer_path)
        launcher_path = Path(__file__).resolve()
        try:
            launcher_relative = launcher_path.relative_to(repository).as_posix()
        except ValueError as exc:
            raise LaunchError("launcher is outside the repository") from exc
        launcher_digest = _sha256(launcher_path)

        command = [sys.executable, str(producer_path)]
        producer_arguments = list(arguments.producer_arguments)
        if producer_arguments and producer_arguments[0] == "--":
            producer_arguments.pop(0)
        command.extend(producer_arguments)
        expectation = (
            "NONZERO" if arguments.kind == "checker-exit-nonzero" else "ZERO"
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
        results = _execution_results(
            manifest,
            arguments.kind,
            arguments.job_id,
            arguments.target,
            selected_key,
        )
        if not arguments.producer_report:
            raise LaunchError("evidence requires --producer-report")
        producer_report_before: Path
        if expectation == "ZERO":
            if arguments.producer_report == "stdout":
                if (
                    arguments.kind != "architecture-report"
                    or arguments.job_id != "architecture-types"
                ):
                    raise LaunchError(
                        "only the reviewed architecture producer may use stdout as its report"
                    )
                producer_report_before = stdout_path
            else:
                _, producer_report_before = _contained_path(
                    artifact_root,
                    arguments.producer_report,
                    label="producer report",
                )
                if producer_report_before.exists():
                    raise LaunchError(
                        "producer report already exists; refusing stale-output reuse"
                    )
                if not _command_mentions_path(
                    command, producer_report_before, cwd=repository
                ):
                    raise LaunchError(
                        "producer command does not identify its report output"
                    )
        elif arguments.producer_report == "stdout":
            raise LaunchError("negative evidence requires a separate child transcript report")
        else:
            _, producer_report_before = _contained_path(
                artifact_root,
                arguments.producer_report,
                label="producer report",
            )
            if producer_report_before.exists():
                raise LaunchError(
                    "producer report already exists; refusing stale-output reuse"
                )
            if not _command_mentions_path(
                command, producer_report_before, cwd=repository
            ):
                raise LaunchError(
                    "negative producer command does not identify its transcript report"
                )
        if producer_report_before == report_path:
            raise LaunchError("producer report may not overwrite the evidence envelope")
        if (
            arguments.producer_report != "stdout"
            and producer_report_before in {stdout_path, stderr_path}
        ):
            raise LaunchError("producer report path must be distinct from captured output")
        declared_output_paths: dict[str, Path] = {}
        reserved_output_kinds = {"stdout", "stderr", "producer-report"}
        reserved_output_paths = {
            report_path,
            stdout_path,
            stderr_path,
            producer_report_before,
        }
        for kind, raw_path in arguments.producer_output:
            if kind in reserved_output_kinds or kind in declared_output_paths:
                raise LaunchError(f"duplicate/reserved producer output kind: {kind!r}")
            _, output_path = _contained_path(
                artifact_root,
                raw_path,
                label="producer output",
            )
            if output_path.exists():
                raise LaunchError(
                    "producer output already exists; refusing stale-output reuse"
                )
            if (
                output_path in reserved_output_paths
                or output_path in declared_output_paths.values()
            ):
                raise LaunchError("producer output paths must be unique")
            if not _command_mentions_path(command, output_path, cwd=repository):
                raise LaunchError("producer command does not identify a declared output")
            declared_output_paths[kind] = output_path
        inputs: list[dict[str, str]] = []
        input_paths: list[Path] = []
        input_kinds: set[str] = set()
        for kind, scope, raw_path in arguments.input:
            if kind in input_kinds:
                raise LaunchError(f"duplicate evidence input kind: {kind!r}")
            reference = _reference(
                kind,
                scope,
                raw_path,
                repository=repository,
                artifact_root=artifact_root,
            )
            root = repository if scope == "repository" else artifact_root
            _, input_path = _contained_path(root, raw_path, label="evidence input")
            if not _command_mentions_path(command, input_path, cwd=repository):
                raise LaunchError("producer command does not consume a declared input")
            input_kinds.add(kind)
            inputs.append(reference)
            input_paths.append(input_path)
        completed = subprocess.run(
            command,
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout_path.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)

        if _sha256(producer_path) != producer_digest:
            raise LaunchError("producer changed while evidence was being collected")
        if _sha256(launcher_path) != launcher_digest:
            raise LaunchError("launcher changed while evidence was being collected")
        if _sha256(arguments.execution_manifest) != manifest_digest:
            raise LaunchError("execution manifest changed while evidence was being collected")
        for reference, input_path in zip(inputs, input_paths, strict=True):
            if _sha256(input_path) != reference["sha256"]:
                raise LaunchError("declared input changed while evidence was being collected")

        expectation_met = (
            completed.returncode != 0 if expectation == "NONZERO" else completed.returncode == 0
        )
        outputs = [
            _reference(
                "stdout",
                "artifact",
                stdout_path.relative_to(artifact_root).as_posix(),
                repository=repository,
                artifact_root=artifact_root,
            ),
            _reference(
                "stderr",
                "artifact",
                stderr_path.relative_to(artifact_root).as_posix(),
                repository=repository,
                artifact_root=artifact_root,
            ),
        ]
        if arguments.producer_report == "stdout":
            producer_report = stdout_path
        elif arguments.producer_report:
            _, producer_report = _contained_path(
                artifact_root,
                arguments.producer_report,
                label="producer report",
            )
        else:
            raise LaunchError("evidence requires --producer-report")
        _load_json(producer_report)
        outputs.append(
            _reference(
                "producer-report",
                "artifact",
                producer_report.relative_to(artifact_root).as_posix(),
                repository=repository,
                artifact_root=artifact_root,
            )
        )
        for kind, output_path in declared_output_paths.items():
            outputs.append(
                _reference(
                    kind,
                    "artifact",
                    output_path.relative_to(artifact_root).as_posix(),
                    repository=repository,
                    artifact_root=artifact_root,
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
                "sha256": producer_digest,
            },
            "launcher": {
                "path": launcher_relative,
                "sha256": launcher_digest,
            },
            "inputs": inputs,
            "outputs": outputs,
            "expectation": expectation,
            "observed_exit_code": completed.returncode,
            "status": "PASS" if expectation_met else "FAIL",
            "results": results,
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
