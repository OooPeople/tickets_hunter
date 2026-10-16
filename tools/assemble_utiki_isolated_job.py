#!/usr/bin/env python3
"""Package already-produced isolated uTiki evidence without inventing results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


RESOURCE_LAYERS = {"browser-contract", "browser-isolation", "performance"}


class AssembleError(Exception):
    """Isolated evidence is incomplete, stale, or internally inconsistent."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssembleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssembleError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AssembleError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AssembleError(f"cannot digest file: {path}") from exc


def _git(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssembleError("git command failed")
    return completed.stdout


def _inside_file(run_dir: Path, raw_path: str | Path, *, label: str) -> Path:
    path = Path(raw_path)
    if path.is_symlink() or not path.is_file():
        raise AssembleError(f"{label} is absent or a symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise AssembleError(f"{label} is outside the run directory") from exc
    return resolved


def _mappings(
    manifest: dict[str, Any], job_id: str, target: str
) -> tuple[list[dict[str, str]], set[str], set[tuple[str, str]]]:
    managed: list[dict[str, str]] = []
    layers: set[str] = set()
    evidence: set[tuple[str, str]] = set()
    for case in manifest.get("cases", []):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise AssembleError("execution manifest contains an invalid case")
        executions = case.get("executions")
        if not isinstance(executions, list):
            raise AssembleError("execution manifest case has no executions")
        for execution in executions:
            if (
                not isinstance(execution, dict)
                or execution.get("status") != "MAPPED"
                or execution.get("job_id") != job_id
                or execution.get("target") != target
            ):
                continue
            node_id = execution.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                raise AssembleError("isolated pytest execution has no node_id")
            layer = execution.get("layer")
            if not isinstance(layer, str) or not layer:
                raise AssembleError("isolated execution has no layer")
            managed.append(
                {
                    "case_id": case["id"],
                    "obligation_id": execution["obligation_id"],
                    "variant_id": execution["variant_id"],
                    "node_id": node_id,
                    "layer": layer,
                }
            )
            layers.add(layer)
            raw_evidence = execution.get("evidence")
            if not isinstance(raw_evidence, list):
                raise AssembleError("execution evidence is not an array")
            for item in raw_evidence:
                if not isinstance(item, dict):
                    raise AssembleError("execution evidence entry is invalid")
                evidence.add((item["kind"], item["path"]))
    if not managed:
        raise AssembleError("job/target has no managed isolated nodes")
    if len({item["node_id"] for item in managed}) != len(managed):
        raise AssembleError("managed isolated node_id is reused")
    if not layers & RESOURCE_LAYERS:
        raise AssembleError("isolated assembler received a non-resource job")
    return sorted(managed, key=lambda item: item["node_id"]), layers, evidence


def _artifact(
    run_dir: Path, kind: str, raw_path: str
) -> tuple[dict[str, str], dict[str, Any], Path]:
    path = _inside_file(run_dir, raw_path, label=f"{kind} artifact")
    envelope = _load(path)
    if envelope.get("schema_version") != 1 or envelope.get("kind") != kind:
        raise AssembleError(f"{kind} artifact is not a formal evidence envelope")
    if envelope.get("status") != "PASS":
        raise AssembleError(f"{kind} evidence producer did not pass")
    return (
        {
            "kind": kind,
            "path": path.relative_to(run_dir).as_posix(),
            "sha256": _sha256(path),
        },
        envelope,
        path,
    )


def _resource(run_dir: Path, kind: str, raw_path: str) -> dict[str, str]:
    path = _inside_file(run_dir, raw_path, label=f"{kind} resource")
    return {
        "kind": kind,
        "path": path.relative_to(run_dir).as_posix(),
        "sha256": _sha256(path),
    }


def _properties(element: ElementTree.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    properties = element.find("properties")
    if properties is None:
        return result
    for item in properties.findall("property"):
        name = item.get("name")
        value = item.get("value")
        if not name or value is None or name in result:
            raise AssembleError("managed JUnit properties are invalid")
        result[name] = value
    return result


def _validate_junit(
    junit: Path,
    *,
    mappings: list[dict[str, str]],
    context: dict[str, Any],
    selected_count: int,
) -> None:
    try:
        root = ElementTree.parse(junit).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise AssembleError("managed JUnit is unreadable") from exc
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
        if len(suites) != 1:
            raise AssembleError("managed JUnit must contain exactly one suite")
        suite = suites[0]
    elif root.tag == "testsuite":
        suite = root
    else:
        raise AssembleError("managed JUnit root is invalid")
    suite_properties = _properties(suite)
    for key, value in context.items():
        if suite_properties.get(key) != str(value):
            raise AssembleError(f"managed JUnit suite context mismatch: {key}")
    tests = suite.findall("testcase")
    if len(tests) != selected_count:
        raise AssembleError("managed JUnit testcase count differs from reporter")
    expected = {
        (
            item["case_id"],
            item["obligation_id"],
            item["variant_id"],
            item["node_id"],
            item["layer"],
        )
        for item in mappings
    }
    observed: set[tuple[str, str, str, str, str]] = set()
    for testcase in tests:
        values = _properties(testcase)
        if "case_id" not in values:
            continue
        key = tuple(
            values.get(name, "")
            for name in ("case_id", "obligation_id", "variant_id", "node_id", "layer")
        )
        if key in observed:
            raise AssembleError("managed JUnit duplicates an execution key")
        observed.add(key)
        if values.get("outcome") != "passed" or values.get("wasxfail") != "false":
            raise AssembleError("managed JUnit contains a non-pass required result")
        if any(
            values.get(f"{stage}_outcome") != "passed"
            for stage in ("setup", "call", "teardown")
        ):
            raise AssembleError("managed JUnit contains a failed test stage")
    if observed != expected:
        raise AssembleError("managed JUnit execution set differs from manifest")


def _producer_output(envelope: dict[str, Any], envelope_path: Path) -> Path:
    outputs = envelope.get("outputs")
    if not isinstance(outputs, list):
        raise AssembleError("isolation envelope has no outputs")
    producer_outputs = [
        item
        for item in outputs
        if isinstance(item, dict) and item.get("kind") == "producer-report"
    ]
    if len(producer_outputs) != 1:
        raise AssembleError("isolation envelope does not bind one producer report")
    item = producer_outputs[0]
    if item.get("scope") != "evidence":
        raise AssembleError("isolation producer report has an invalid scope")
    relative = item.get("path")
    if not isinstance(relative, str) or not relative:
        raise AssembleError("isolation producer report path is invalid")
    path = (envelope_path.parent / relative).resolve()
    if envelope_path.parent.resolve() not in path.parents:
        raise AssembleError("isolation producer report escapes the evidence directory")
    if path.is_symlink() or not path.is_file() or _sha256(path) != item.get("sha256"):
        raise AssembleError("isolation producer report digest mismatch")
    return path


def _context_value(payload: dict[str, Any], name: str) -> Any:
    if name in payload:
        return payload[name]
    context = payload.get("context")
    return context.get(name) if isinstance(context, dict) else None


def _validate_isolation_reports(
    *,
    raw: dict[str, Any],
    inner: dict[str, Any],
    inner_path: Path,
    junit_path: Path,
    reporter_path: Path,
    expected_context: dict[str, Any],
    expected_negative_evidence: list[dict[str, str]],
) -> tuple[list[str], int, str, str]:
    if raw.get("schema_version") != 1 or raw.get("status") != "PASS":
        raise AssembleError("outer isolation report did not pass")
    for name, value in expected_context.items():
        if _context_value(raw, name) != value:
            raise AssembleError(f"outer isolation context mismatch: {name}")
    if raw.get("inner_report_sha256") != _sha256(inner_path):
        raise AssembleError("outer isolation report does not bind the inner report")
    if raw.get("junit_sha256") != _sha256(junit_path):
        raise AssembleError("outer isolation report does not bind managed JUnit")
    if raw.get("reporter_result_sha256") != _sha256(reporter_path):
        raise AssembleError("outer isolation report does not bind reporter result")
    if not isinstance(raw.get("runtime_evidence"), dict) or not raw["runtime_evidence"]:
        raise AssembleError("outer isolation report has no runtime evidence")
    workspace = raw["runtime_evidence"].get("candidate_workspace")
    if (
        not isinstance(workspace, dict)
        or workspace.get("source") != "git-archive"
        or workspace.get("candidate_sha") != expected_context["candidate_sha"]
        or workspace.get("source_checkout_mounted") is not False
        or workspace.get("ignored_or_untracked_files_included") is not False
        or not isinstance(workspace.get("tracked_file_count"), int)
        or workspace["tracked_file_count"] < 1
        or not isinstance(workspace.get("archive_sha256"), str)
        or len(workspace["archive_sha256"]) != 64
        or not isinstance(workspace.get("tree_sha256"), str)
        or len(workspace["tree_sha256"]) != 64
    ):
        raise AssembleError("outer isolation report has no sanitized candidate workspace")
    if not isinstance(raw.get("peer_audit"), dict) or not raw["peer_audit"]:
        raise AssembleError("outer isolation report has no peer audit")
    if expected_negative_evidence:
        if raw.get("negative_evidence_sha256") != expected_negative_evidence:
            raise AssembleError("outer isolation report does not bind negative evidence")
        if inner.get("negative_evidence") != expected_negative_evidence:
            raise AssembleError("inner isolation report does not bind negative evidence")
    if inner.get("schema_version") != 1 or inner.get("status") != "PENDING_OUTER_AUDIT":
        raise AssembleError("inner isolated run did not reach outer audit")
    pytest_result = inner.get("pytest")
    if not isinstance(pytest_result, dict) or pytest_result.get("exit_code") != 0:
        raise AssembleError("inner isolated pytest did not pass")
    command = pytest_result.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
        or "--strict-config" not in command
        or "--strict-markers" not in command
    ):
        raise AssembleError("inner isolated pytest command lacks strict flags")
    started = raw.get("started_at_unix")
    finished = raw.get("finished_at_unix")
    if (
        isinstance(started, bool)
        or not isinstance(started, (int, float))
        or isinstance(finished, bool)
        or not isinstance(finished, (int, float))
        or finished < started
    ):
        raise AssembleError("outer isolation timestamps are invalid")
    started_at = datetime.fromtimestamp(started, timezone.utc).isoformat()
    finished_at = datetime.fromtimestamp(finished, timezone.utc).isoformat()
    return command, 0, started_at, finished_at


def _marker_expression(command: list[str]) -> str:
    positions = [index for index, value in enumerate(command) if value == "-m"]
    # The first ``-m`` normally belongs to ``python -m pytest``.  The managed
    # launcher appends the pytest marker expression later in the command.
    if len(positions) < 2 or positions[-1] + 1 >= len(command):
        raise AssembleError("inner isolated pytest command has no marker expression")
    marker = command[positions[-1] + 1]
    if not marker:
        raise AssembleError("inner isolated pytest marker expression is empty")
    return marker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--reporter-result", type=Path, required=True)
    parser.add_argument("--inner-report", type=Path, required=True)
    parser.add_argument("--resource", nargs=2, action="append", default=[])
    parser.add_argument("--artifact", nargs=2, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        run_dir = arguments.run_dir.resolve()
        if _git(repository, "rev-parse", "HEAD").decode("ascii").strip() != arguments.candidate_sha:
            raise AssembleError("candidate SHA does not equal repository HEAD")
        status = _git(repository, "status", "--porcelain=v1", "-z")
        if status:
            raise AssembleError("formal evidence requires a clean working tree")
        run_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = run_dir / "run-metadata.json"
        if metadata_path.exists() or metadata_path.is_symlink():
            raise AssembleError("formal run metadata destination already exists")
        manifest_path = arguments.execution_manifest.resolve(strict=True)
        manifest = _load(manifest_path)
        manifest_digest = _sha256(manifest_path)
        if manifest.get("phase") != arguments.phase:
            raise AssembleError("execution manifest phase mismatch")
        if manifest.get("catalog_sha256") != arguments.catalog_sha256:
            raise AssembleError("execution manifest catalog digest mismatch")
        mappings, layers, expected_artifacts = _mappings(
            manifest, arguments.job_id, arguments.target
        )

        resources: list[dict[str, str]] = []
        seen_resources: set[str] = set()
        for kind, path in arguments.resource:
            if kind in seen_resources:
                raise AssembleError("duplicate resource kind")
            seen_resources.add(kind)
            resources.append(_resource(run_dir, kind, path))
        if seen_resources != {"fixture", "script"}:
            raise AssembleError("isolated job requires exactly fixture and script resources")
        fixture_digest = next(item["sha256"] for item in resources if item["kind"] == "fixture")
        script_digest = next(item["sha256"] for item in resources if item["kind"] == "script")

        artifacts: list[dict[str, str]] = []
        envelopes: dict[tuple[str, str], tuple[dict[str, Any], Path]] = {}
        for kind, path in arguments.artifact:
            value, envelope, envelope_path = _artifact(run_dir, kind, path)
            key = (kind, value["path"])
            if key in envelopes:
                raise AssembleError("duplicate artifact")
            artifacts.append(value)
            envelopes[key] = (envelope, envelope_path)
        if set(envelopes) != expected_artifacts:
            raise AssembleError("artifact set does not equal execution manifest")

        expected_context = {
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "job_id": arguments.job_id,
            "phase": arguments.phase,
            "target": arguments.target,
            "candidate_sha": arguments.candidate_sha,
            "catalog_sha256": arguments.catalog_sha256,
            "execution_manifest_sha256": manifest_digest,
        }
        for envelope, _ in envelopes.values():
            if envelope.get("context") != expected_context:
                raise AssembleError("evidence envelope context does not match this run")

        isolation_items = [
            (envelope, path)
            for (kind, _), (envelope, path) in envelopes.items()
            if kind == "browser-isolation-report"
        ]
        if len(isolation_items) != 1:
            raise AssembleError("isolated job must have one isolation evidence envelope")
        isolation_envelope, isolation_envelope_path = isolation_items[0]
        raw_path = _producer_output(isolation_envelope, isolation_envelope_path)
        raw = _load(raw_path)
        inner_path = _inside_file(run_dir, arguments.inner_report, label="inner report")
        inner = _load(inner_path)
        source_junit = _inside_file(run_dir, arguments.junit, label="managed JUnit")
        reporter_path = _inside_file(
            run_dir, arguments.reporter_result, label="reporter result"
        )
        reporter = _load(reporter_path)
        if set(reporter) != {
            "schema_version",
            "status",
            "initial_exit_code",
            "final_exit_code",
            "collected_count",
            "selected_count",
            "required_count",
            "missing_node_ids",
            "invalid_node_ids",
        }:
            raise AssembleError("managed reporter result schema is invalid")
        if reporter.get("schema_version") != 1 or reporter.get("status") != "PASS":
            raise AssembleError("managed reporter did not pass")
        collected = reporter.get("collected_count")
        selected = reporter.get("selected_count")
        required = reporter.get("required_count")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (collected, selected, required)
        ):
            raise AssembleError("managed reporter counts are invalid")
        if selected != required or required != len(mappings) or selected > collected:
            raise AssembleError("managed reporter execution count differs from manifest")
        if reporter.get("missing_node_ids") not in (None, []) or reporter.get(
            "invalid_node_ids"
        ) not in (None, []):
            raise AssembleError("managed reporter contains missing or invalid nodes")
        if reporter.get("final_exit_code") not in (None, 0):
            raise AssembleError("managed reporter preserved a failing exit code")
        if reporter.get("initial_exit_code") != 0 or reporter.get("final_exit_code") != 0:
            raise AssembleError("managed reporter exit code is not zero")

        suite_context = {
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "job_id": arguments.job_id,
            "phase": arguments.phase,
            "target": arguments.target,
            "candidate_sha": arguments.candidate_sha,
            "catalog_sha256": arguments.catalog_sha256,
            "execution_manifest_sha256": manifest_digest,
            "fixture_digest": fixture_digest,
            "script_digest": script_digest,
        }
        _validate_junit(
            source_junit,
            mappings=mappings,
            context=suite_context,
            selected_count=selected,
        )
        command, test_exit, started_at, finished_at = _validate_isolation_reports(
            raw=raw,
            inner=inner,
            inner_path=inner_path,
            junit_path=source_junit,
            reporter_path=reporter_path,
            expected_context=expected_context,
            expected_negative_evidence=sorted(
                [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in artifacts
                    if item["kind"] == "checker-exit-nonzero"
                ],
                key=lambda item: item["path"],
            ),
        )

        junit_path = run_dir / "junit.xml"
        if source_junit != junit_path:
            if junit_path.exists() or junit_path.is_symlink():
                raise AssembleError("formal JUnit destination already exists")
            shutil.copyfile(source_junit, junit_path)
        isolation_digest = next(
            item["sha256"]
            for item in artifacts
            if item["kind"] == "browser-isolation-report"
        )
        metadata = {
            "schema_version": 1,
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "job_id": arguments.job_id,
            "phase": arguments.phase,
            "target": arguments.target,
            "candidate_sha": arguments.candidate_sha,
            "working_tree_digest": hashlib.sha256(status).hexdigest(),
            "working_tree_clean": True,
            "os": "linux",
            "architecture": "x86_64",
            "layers": sorted(layers),
            "catalog_sha256": arguments.catalog_sha256,
            "execution_manifest_sha256": manifest_digest,
            "junit_sha256": _sha256(junit_path),
            "fixture_digest": fixture_digest,
            "script_digest": script_digest,
            "resources": sorted(resources, key=lambda item: item["kind"]),
            "artifacts": sorted(
                artifacts, key=lambda item: (item["kind"], item["path"])
            ),
            "effective_command": command,
            "effective_flags": {
                "strict_config": True,
                "strict_markers": True,
                "marker_expression": _marker_expression(command),
            },
            "exit_codes": {
                "test": test_exit,
                "collector": 0 if collected > 0 else 1,
                "reporter": 0,
            },
            "collected_count": collected,
            "selected_count": selected,
            "started_at": started_at,
            "finished_at": finished_at,
            "browser_enabled": True,
            "isolation": {
                "required": True,
                "verified": True,
                "report_sha256": isolation_digest,
            },
            "tool_versions": {
                "python": platform.python_version(),
                "pytest": "isolated-managed-reporter",
                "assembler": "1",
            },
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (
        AssembleError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ElementTree.ParseError,
    ) as exc:
        sys.stderr.write(f"assemble_utiki_isolated_job: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
