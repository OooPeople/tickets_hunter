#!/usr/bin/env python3
"""Assemble JUnit and run metadata for a manifest-defined non-pytest job.

The producer commands must already have run through ``run_utiki_evidence.py``.
This tool never creates testcase successes.  It is only valid when every due
mapping for the selected job/target has no JUnit node and no ``junit`` evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


class AssembleError(Exception):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssembleError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(value, dict):
        raise AssembleError(f"JSON root must be an object: {path}")
    return value


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


def _properties(parent: ElementTree.Element, values: dict[str, Any]) -> None:
    properties = ElementTree.SubElement(parent, "properties")
    for name, value in values.items():
        ElementTree.SubElement(properties, "property", name=name, value=str(value))


def _job_contract(
    manifest: dict[str, Any], job_id: str, target: str
) -> tuple[set[str], set[tuple[str, str]], int]:
    layers: set[str] = set()
    evidence: set[tuple[str, str]] = set()
    due = 0
    for case in manifest.get("cases", []):
        for execution in case.get("executions", []):
            if (
                execution.get("status") != "MAPPED"
                or execution.get("job_id") != job_id
                or execution.get("target") != target
            ):
                continue
            due += 1
            layers.add(execution["layer"])
            if execution.get("node_id") is not None:
                raise AssembleError("non-pytest assembler cannot accept a JUnit node")
            for item in execution.get("evidence", []):
                if item.get("kind") == "junit":
                    raise AssembleError("non-pytest assembler cannot accept JUnit evidence")
                evidence.add((item["kind"], item["path"]))
    if due == 0:
        raise AssembleError("job/target has no due non-pytest execution")
    return layers, evidence, due


def _artifact(run_dir: Path, kind: str, raw_path: str) -> dict[str, str]:
    path = Path(raw_path).resolve(strict=True)
    try:
        relative = path.relative_to(run_dir).as_posix()
    except ValueError as exc:
        raise AssembleError("artifact is outside run directory") from exc
    if path.is_symlink() or not path.is_file():
        raise AssembleError("artifact is not a regular file")
    envelope = _load(path)
    if envelope.get("schema_version") != 2 or envelope.get("kind") != kind:
        raise AssembleError("artifact is not a formal evidence envelope")
    if envelope.get("status") != "PASS":
        raise AssembleError("evidence producer did not pass")
    return {"kind": kind, "path": relative, "sha256": _sha256(path)}


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
    parser.add_argument("--artifact", nargs=2, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        run_dir = arguments.run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        if _git(repository, "rev-parse", "HEAD").decode("ascii").strip() != arguments.candidate_sha:
            raise AssembleError("candidate SHA does not equal repository HEAD")
        status = _git(repository, "status", "--porcelain=v1", "-z")
        if status:
            raise AssembleError("formal evidence requires a clean working tree")
        manifest = _load(arguments.execution_manifest)
        if manifest.get("phase") != arguments.phase:
            raise AssembleError("execution manifest phase mismatch")
        if manifest.get("catalog_sha256") != arguments.catalog_sha256:
            raise AssembleError("execution manifest catalog digest mismatch")
        manifest_digest = _sha256(arguments.execution_manifest)
        layers, expected_artifacts, due = _job_contract(
            manifest, arguments.job_id, arguments.target
        )
        if not expected_artifacts:
            raise AssembleError("non-pytest job has no producer evidence")
        if layers & {"browser-contract", "browser-isolation", "performance"}:
            raise AssembleError("resource-bearing job must use its dedicated launcher")

        artifacts: list[dict[str, str]] = []
        actual_artifacts: set[tuple[str, str]] = set()
        for kind, path in arguments.artifact:
            value = _artifact(run_dir, kind, path)
            key = (kind, value["path"])
            if key in actual_artifacts:
                raise AssembleError("duplicate artifact")
            actual_artifacts.add(key)
            artifacts.append(value)
        if actual_artifacts != expected_artifacts:
            raise AssembleError("artifact set does not equal execution manifest")
        for artifact in artifacts:
            envelope = _load(run_dir / artifact["path"])
            context = envelope.get("context")
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
            if context != expected_context:
                raise AssembleError("artifact context does not match this run")

        suite_context = {
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "job_id": arguments.job_id,
            "phase": arguments.phase,
            "target": arguments.target,
            "candidate_sha": arguments.candidate_sha,
            "catalog_sha256": arguments.catalog_sha256,
            "execution_manifest_sha256": manifest_digest,
            "fixture_digest": "NOT_APPLICABLE",
            "script_digest": "NOT_APPLICABLE",
        }
        suite = ElementTree.Element(
            "testsuite", name=f"{arguments.job_id}-{arguments.target}", tests="0"
        )
        _properties(suite, suite_context)
        junit_path = run_dir / "junit.xml"
        ElementTree.ElementTree(suite).write(
            junit_path, encoding="utf-8", xml_declaration=True
        )
        now = datetime.now(timezone.utc).isoformat()
        system = platform.system().lower()
        os_name = {"darwin": "macos"}.get(system, system)
        machine = platform.machine().lower()
        architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
            machine, machine
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
            "os": os_name,
            "architecture": architecture,
            "layers": sorted(layers),
            "catalog_sha256": arguments.catalog_sha256,
            "execution_manifest_sha256": manifest_digest,
            "junit_sha256": _sha256(junit_path),
            "fixture_digest": "NOT_APPLICABLE",
            "script_digest": "NOT_APPLICABLE",
            "resources": [],
            "artifacts": sorted(
                artifacts, key=lambda item: (item["kind"], item["path"])
            ),
            "effective_command": [str(Path(__file__).resolve()), *sys.argv[1:]],
            "effective_flags": {
                "strict_config": None,
                "strict_markers": None,
                "marker_expression": None,
            },
            "exit_codes": {"test": 0, "collector": 0, "reporter": 0},
            "collected_count": 0,
            "selected_count": 0,
            "started_at": now,
            "finished_at": now,
            "browser_enabled": False,
            "isolation": {
                "required": False,
                "verified": False,
                "report_sha256": None,
            },
            "tool_versions": {
                "python": platform.python_version(),
                "assembler": "1",
                "due_execution_count": str(due),
            },
        }
        (run_dir / "run-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    except (AssembleError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"assemble_utiki_non_pytest_job: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
