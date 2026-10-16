#!/usr/bin/env python3
"""Build a digest-bound run index for one uTiki gate phase.

The phase checker remains authoritative for semantic validation.  This tool
only accepts the exact job/target set from the approved catalog, verifies that
each downloaded job directory belongs to the same run context, and records
the actual JUnit and metadata byte digests.  It never invents missing job
results or rewrites a producer's metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*-attempt-[1-9][0-9]*\Z")


class IndexError(Exception):
    """The requested run index cannot be built safely."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IndexError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IndexError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise IndexError(f"{label} root must be an object")
    return value


def _digest(path: Path, *, label: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise IndexError(f"cannot digest {label}: {path}") from exc


def _regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise IndexError(f"{label} must be a regular non-symlink file: {path}")
    return path.resolve()


def _safe_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise IndexError(f"{label} is not a safe identifier: {value!r}")
    return value


def _required_pairs(catalog: dict[str, Any], phase: str) -> set[tuple[str, str]]:
    if catalog.get("schema_version") != SCHEMA_VERSION:
        raise IndexError("catalog schema_version is unsupported")
    if catalog.get("status") != "APPROVED":
        raise IndexError("catalog is not approved")
    profiles = catalog.get("gate_profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(phase), dict):
        raise IndexError(f"catalog has no gate profile for {phase}")
    jobs = profiles[phase].get("required_jobs")
    if not isinstance(jobs, list) or not jobs:
        raise IndexError(f"catalog gate profile {phase} has no required jobs")
    pairs: set[tuple[str, str]] = set()
    for index, raw_job in enumerate(jobs):
        if not isinstance(raw_job, dict):
            raise IndexError(f"catalog required_jobs[{index}] is not an object")
        if set(raw_job) != {"job_id", "targets", "layers"}:
            raise IndexError(f"catalog required_jobs[{index}] fields mismatch")
        job_id = _safe_id(raw_job.get("job_id", ""), label="catalog job_id")
        targets = raw_job.get("targets")
        if not isinstance(targets, list) or not targets:
            raise IndexError(f"catalog job {job_id} has no targets")
        for raw_target in targets:
            if not isinstance(raw_target, str):
                raise IndexError(f"catalog job {job_id} has a non-string target")
            pair = (job_id, _safe_id(raw_target, label="catalog target"))
            if pair in pairs:
                raise IndexError(f"catalog duplicates required job/target: {pair}")
            pairs.add(pair)
    return pairs


def _relative_file(path: Path, root: Path, *, label: str) -> str:
    resolved = _regular_file(path, label=label)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise IndexError(f"{label} is outside the run-index directory: {path}") from exc


def _validate_metadata(
    metadata: dict[str, Any],
    *,
    job_id: str,
    target: str,
    run_id: str,
    attempt: int,
    phase: str,
    candidate_sha: str,
    catalog_sha256: str,
    manifest_sha256: str,
    junit_sha256: str,
) -> None:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "target": target,
        "run_id": run_id,
        "attempt": attempt,
        "phase": phase,
        "candidate_sha": candidate_sha,
        "catalog_sha256": catalog_sha256,
        "execution_manifest_sha256": manifest_sha256,
        "junit_sha256": junit_sha256,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise IndexError(
                f"run metadata context mismatch for {job_id}/{target}: {key}"
            )
    if metadata.get("working_tree_clean") is not True:
        raise IndexError(f"formal run is not clean for {job_id}/{target}")
    exit_codes = metadata.get("exit_codes")
    if not isinstance(exit_codes, dict) or not exit_codes:
        raise IndexError(f"run metadata has no exit codes for {job_id}/{target}")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value != 0
        for value in exit_codes.values()
    ):
        raise IndexError(f"required producer failed for {job_id}/{target}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required-catalog", type=Path, required=True)
    parser.add_argument("--approved-catalog-sha256", required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run",
        nargs=3,
        action="append",
        default=[],
        metavar=("JOB_ID", "TARGET", "DIRECTORY"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if not SHA256_RE.fullmatch(arguments.approved_catalog_sha256):
            raise IndexError("approved catalog digest must be a lowercase SHA-256")
        if not GIT_SHA_RE.fullmatch(arguments.candidate_sha):
            raise IndexError("candidate SHA must be an exact lowercase Git SHA")
        if arguments.attempt < 1:
            raise IndexError("attempt must be positive")
        if (
            not RUN_ID_RE.fullmatch(arguments.run_id)
            or not arguments.run_id.endswith(f"-attempt-{arguments.attempt}")
        ):
            raise IndexError("run-id must end with the exact attempt")
        output = arguments.output.resolve()
        if arguments.output.is_symlink() or output.exists():
            raise IndexError("output already exists or is a symlink")
        root = output.parent
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)

        catalog_path = _regular_file(arguments.required_catalog, label="catalog")
        catalog_digest = _digest(catalog_path, label="catalog")
        if catalog_digest != arguments.approved_catalog_sha256:
            raise IndexError("catalog digest does not equal the approved digest")
        catalog = _load_json(catalog_path, label="catalog")
        required_pairs = _required_pairs(catalog, arguments.phase)

        manifest_path = _regular_file(
            arguments.execution_manifest, label="execution manifest"
        )
        manifest = _load_json(manifest_path, label="execution manifest")
        manifest_digest = _digest(manifest_path, label="execution manifest")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise IndexError("execution manifest schema_version is unsupported")
        if manifest.get("phase") != arguments.phase:
            raise IndexError("execution manifest phase mismatch")
        if manifest.get("catalog_sha256") != catalog_digest:
            raise IndexError("execution manifest catalog digest mismatch")

        actual: dict[tuple[str, str], Path] = {}
        for raw_job, raw_target, raw_directory in arguments.run:
            pair = (
                _safe_id(raw_job, label="job_id"),
                _safe_id(raw_target, label="target"),
            )
            if pair in actual:
                raise IndexError(f"duplicate run entry: {pair}")
            directory = Path(raw_directory)
            if directory.is_symlink() or not directory.is_dir():
                raise IndexError(f"run directory is absent or a symlink: {directory}")
            resolved = directory.resolve()
            if resolved != root and root not in resolved.parents:
                raise IndexError(f"run directory is outside the index root: {directory}")
            actual[pair] = resolved
        if set(actual) != required_pairs:
            raise IndexError(
                "job/target set mismatch: "
                f"missing={sorted(required_pairs - set(actual))!r}, "
                f"extra={sorted(set(actual) - required_pairs)!r}"
            )

        runs: list[dict[str, str]] = []
        for (job_id, target), directory in sorted(actual.items()):
            junit_path = _regular_file(directory / "junit.xml", label="JUnit")
            metadata_path = _regular_file(
                directory / "run-metadata.json", label="run metadata"
            )
            junit_digest = _digest(junit_path, label="JUnit")
            metadata = _load_json(metadata_path, label="run metadata")
            _validate_metadata(
                metadata,
                job_id=job_id,
                target=target,
                run_id=arguments.run_id,
                attempt=arguments.attempt,
                phase=arguments.phase,
                candidate_sha=arguments.candidate_sha,
                catalog_sha256=catalog_digest,
                manifest_sha256=manifest_digest,
                junit_sha256=junit_digest,
            )
            runs.append(
                {
                    "job_id": job_id,
                    "target": target,
                    "junit_path": _relative_file(
                        junit_path, root, label="JUnit"
                    ),
                    "junit_sha256": junit_digest,
                    "run_metadata_path": _relative_file(
                        metadata_path, root, label="run metadata"
                    ),
                    "run_metadata_sha256": _digest(
                        metadata_path, label="run metadata"
                    ),
                }
            )

        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "phase": arguments.phase,
            "candidate_sha": arguments.candidate_sha,
            "catalog_sha256": catalog_digest,
            "execution_manifest_sha256": manifest_digest,
            "runs": runs,
        }
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        return 0
    except (IndexError, OSError) as exc:
        sys.stderr.write(f"build_utiki_run_index: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
