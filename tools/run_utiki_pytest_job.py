#!/usr/bin/env python3
"""Launch a manifest-bound pytest job with the managed uTiki reporter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RESOURCE_LAYERS = {"browser-contract", "browser-isolation", "performance"}


class LaunchError(Exception):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LaunchError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(value, dict):
        raise LaunchError(f"JSON root must be an object: {path}")
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
        raise LaunchError("git command failed")
    return completed.stdout


def _mappings(
    manifest: dict[str, Any], job_id: str, target: str
) -> tuple[list[dict[str, str]], set[str], set[tuple[str, str]]]:
    managed: list[dict[str, str]] = []
    layers: set[str] = set()
    evidence: set[tuple[str, str]] = set()
    for case in manifest.get("cases", []):
        for execution in case.get("executions", []):
            if (
                execution.get("status") != "MAPPED"
                or execution.get("job_id") != job_id
                or execution.get("target") != target
            ):
                continue
            layers.add(execution["layer"])
            for item in execution.get("evidence", []):
                evidence.add((item["kind"], item["path"]))
            if execution.get("node_id") is not None:
                managed.append(
                    {
                        "case_id": case["id"],
                        "obligation_id": execution["obligation_id"],
                        "variant_id": execution["variant_id"],
                        "node_id": execution["node_id"],
                        "layer": execution["layer"],
                    }
                )
    if not managed:
        raise LaunchError("job/target has no managed pytest nodes")
    if len({item["node_id"] for item in managed}) != len(managed):
        raise LaunchError("managed pytest node_id is reused")
    return sorted(managed, key=lambda item: item["node_id"]), layers, evidence


def _reference(run_dir: Path, kind: str, path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(run_dir).as_posix()
    except ValueError as exc:
        raise LaunchError("run artifact/resource is outside run directory") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise LaunchError("run artifact/resource is not a regular file")
    return {"kind": kind, "path": relative, "sha256": _sha256(resolved)}


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
    parser.add_argument("--resource", nargs=2, action="append", default=[])
    parser.add_argument("--artifact", nargs=2, action="append", default=[])
    parser.add_argument("--marker-expression", default="not live")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        run_dir = arguments.run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        head = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
        if head != arguments.candidate_sha:
            raise LaunchError("candidate SHA does not equal repository HEAD")
        status = _git(repository, "status", "--porcelain=v1", "-z")
        if status:
            raise LaunchError("formal pytest evidence requires a clean working tree")
        working_tree_digest = hashlib.sha256(status).hexdigest()
        manifest = _load(arguments.execution_manifest)
        if manifest.get("phase") != arguments.phase:
            raise LaunchError("execution manifest phase mismatch")
        if manifest.get("catalog_sha256") != arguments.catalog_sha256:
            raise LaunchError("execution manifest catalog digest mismatch")
        managed, layers, expected_evidence = _mappings(
            manifest, arguments.job_id, arguments.target
        )
        manifest_digest = _sha256(arguments.execution_manifest)

        resources: list[dict[str, str]] = []
        resource_paths: dict[str, Path] = {}
        for kind, raw_path in arguments.resource:
            if kind in resource_paths:
                raise LaunchError("duplicate resource kind")
            resource_paths[kind] = Path(raw_path)
            resources.append(_reference(run_dir, kind, Path(raw_path)))
        resources_required = bool(layers & RESOURCE_LAYERS)
        if resources_required and set(resource_paths) != {"fixture", "script"}:
            raise LaunchError("browser/performance pytest job requires fixture and script")
        if not resources_required and resource_paths:
            raise LaunchError("non-browser pytest job may not manufacture resources")
        fixture_digest = (
            _sha256(resource_paths["fixture"].resolve())
            if resources_required
            else "NOT_APPLICABLE"
        )
        script_digest = (
            _sha256(resource_paths["script"].resolve())
            if resources_required
            else "NOT_APPLICABLE"
        )

        artifacts: list[dict[str, str]] = []
        actual_evidence: set[tuple[str, str]] = set()
        for kind, raw_path in arguments.artifact:
            reference = _reference(run_dir, kind, Path(raw_path))
            artifacts.append(reference)
            actual_evidence.add((kind, reference["path"]))
        if actual_evidence != expected_evidence:
            raise LaunchError("artifact set does not equal manifest evidence mappings")

        context = {
            "schema_version": 1,
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
            "mappings": managed,
        }
        context_path = run_dir / "pytest-context.json"
        junit_path = run_dir / "junit.xml"
        reporter_result_path = run_dir / "pytest-reporter-result.json"
        metadata_path = run_dir / "run-metadata.json"
        context_path.write_text(
            json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        reporter_root = Path(__file__).resolve().parent.parent
        environment = os.environ.copy()
        previous_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(reporter_root)
            if not previous_pythonpath
            else str(reporter_root) + os.pathsep + previous_pythonpath
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--rootdir",
            str(repository),
            *(["-c", str(repository / "pytest.ini")] if arguments.phase == "P2" else []),
            "-o",
            "addopts=",
            "--strict-config",
            "--strict-markers",
            "-m",
            arguments.marker_expression,
            "-p",
            "tools.utiki_pytest_reporter",
            "--utiki-reporter-context",
            str(context_path),
            "--utiki-managed-junit",
            str(junit_path),
            "--utiki-reporter-result",
            str(reporter_result_path),
            *[item["node_id"] for item in managed],
        ]
        started = datetime.now(timezone.utc)
        completed = subprocess.run(
            command,
            cwd=repository,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        finished = datetime.now(timezone.utc)
        (run_dir / "pytest.stdout").write_bytes(completed.stdout)
        (run_dir / "pytest.stderr").write_bytes(completed.stderr)
        if not junit_path.is_file() or not reporter_result_path.is_file():
            raise LaunchError("managed pytest reporter did not emit its outputs")
        reporter_result = _load(reporter_result_path)
        reporter_exit = 0 if reporter_result.get("status") == "PASS" else 1
        collected = reporter_result.get("collected_count")
        selected = reporter_result.get("selected_count")
        if not isinstance(collected, int) or not isinstance(selected, int):
            raise LaunchError("managed pytest reporter counts are invalid")

        system = platform.system().lower()
        os_name = {"darwin": "macos"}.get(system, system)
        machine = platform.machine().lower()
        architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
            machine, machine
        )
        browser_job = "browser-isolation" in layers
        isolation_reports = [
            item for item in artifacts if item["kind"] == "browser-isolation-report"
        ]
        metadata = {
            "schema_version": 1,
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "job_id": arguments.job_id,
            "phase": arguments.phase,
            "target": arguments.target,
            "candidate_sha": arguments.candidate_sha,
            "working_tree_digest": working_tree_digest,
            "working_tree_clean": True,
            "os": os_name,
            "architecture": architecture,
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
                "marker_expression": arguments.marker_expression,
            },
            "exit_codes": {
                "test": completed.returncode,
                "collector": 0 if collected > 0 else 1,
                "reporter": reporter_exit,
            },
            "collected_count": collected,
            "selected_count": selected,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "browser_enabled": browser_job,
            "isolation": {
                "required": browser_job,
                "verified": browser_job and bool(isolation_reports),
                "report_sha256": isolation_reports[0]["sha256"]
                if browser_job and isolation_reports
                else None,
            },
            "tool_versions": {
                "python": platform.python_version(),
                "pytest": importlib.metadata.version("pytest"),
                "reporter": "1",
            },
        }
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0 if completed.returncode == 0 and reporter_exit == 0 else 1
    except (LaunchError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"run_utiki_pytest_job: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
