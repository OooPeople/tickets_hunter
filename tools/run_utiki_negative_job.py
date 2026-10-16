#!/usr/bin/env python3
"""Produce every per-key negative evidence envelope for one P1 job/target.

The reviewed registry is checked against both the approved catalog and the
generated execution manifest before any probe runs.  Each probe consumes its
own concrete input directory and is launched separately through
``run_utiki_evidence.py``.  Isolated probes cannot run without the hard-egress
environment and a copied, digest-bound isolation proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from tools.utiki_negative_contract import (
        NegativeContractError,
        validate_registry_contract,
        validate_request_contract,
    )
except ModuleNotFoundError:  # Direct execution from tools/.
    from utiki_negative_contract import (  # type: ignore[no-redef]
        NegativeContractError,
        validate_registry_contract,
        validate_request_contract,
    )


SCHEMA_VERSION = 1
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9.-]")


class JobError(Exception):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JobError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise JobError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise JobError(f"cannot digest {path}") from exc


def _key(value: dict[str, Any]) -> tuple[str, str, str, str, str]:
    names = ("case_id", "obligation_id", "variant_id", "job_id", "target")
    values = tuple(value.get(name) for name in names)
    if not all(isinstance(item, str) and item for item in values):
        raise JobError("negative probe key is invalid")
    return values  # type: ignore[return-value]


def _catalog_negative_keys(catalog: dict[str, Any], phase: str) -> set[tuple[str, str, str, str, str]]:
    profiles = catalog.get("gate_profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(phase), dict):
        raise JobError("catalog gate profile is missing")
    targets: dict[str, list[str]] = {}
    for job in profiles[phase].get("required_jobs", []):
        if not isinstance(job, dict) or not isinstance(job.get("targets"), list):
            raise JobError("catalog job profile is invalid")
        targets[job.get("job_id")] = job["targets"]
    result: set[tuple[str, str, str, str, str]] = set()
    for case in catalog.get("cases", []):
        if not isinstance(case, dict):
            raise JobError("catalog case is invalid")
        case_id = case.get("id")
        for obligation in case.get("obligations", []):
            if (
                not isinstance(obligation, dict)
                or phase not in obligation.get("required_gates", [])
                or "checker-exit-nonzero" not in obligation.get("required_evidence", [])
            ):
                continue
            job_id = obligation.get("job_id")
            variants = obligation.get("variants")
            if job_id not in targets or not isinstance(variants, list):
                raise JobError("catalog negative obligation is invalid")
            applies = obligation.get("applies_when")
            conditions = applies.get("all") if isinstance(applies, dict) else None
            if not isinstance(conditions, list):
                raise JobError("catalog applicability is invalid")
            for target in targets[job_id]:
                applicable = True
                for condition in conditions:
                    if (
                        not isinstance(condition, dict)
                        or condition.get("field") != "target"
                        or not isinstance(condition.get("one_of"), list)
                    ):
                        raise JobError("catalog negative condition is untrusted")
                    if target not in condition["one_of"]:
                        applicable = False
                if applicable:
                    for variant in variants:
                        result.add(
                            (case_id, obligation.get("obligation_id"), variant, job_id, target)
                        )
    if not result:
        raise JobError("catalog has no due negative probes")
    return result


def _manifest_negative(
    manifest: dict[str, Any],
) -> tuple[set[tuple[str, str, str, str, str]], dict[tuple[str, str, str, str, str], str]]:
    keys: set[tuple[str, str, str, str, str]] = set()
    paths: dict[tuple[str, str, str, str, str], str] = {}
    for case in manifest.get("cases", []):
        for execution in case.get("executions", []):
            if execution.get("status") != "MAPPED":
                continue
            for evidence in execution.get("evidence", []):
                if evidence.get("kind") != "checker-exit-nonzero":
                    continue
                value = {
                    "case_id": case.get("id"),
                    "obligation_id": execution.get("obligation_id"),
                    "variant_id": execution.get("variant_id"),
                    "job_id": execution.get("job_id"),
                    "target": execution.get("target"),
                }
                key = _key(value)
                path = evidence.get("path")
                if key in keys or not isinstance(path, str):
                    raise JobError("manifest negative evidence is duplicate/invalid")
                pure = PurePosixPath(path)
                if pure.is_absolute() or ".." in pure.parts or str(pure) != path:
                    raise JobError("manifest negative evidence path is unsafe")
                keys.add(key)
                paths[key] = path
    return keys, paths


def _registry(
    payload: dict[str, Any], catalog_digest: str
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    expected_top = {
        "schema_version",
        "registry_id",
        "phase",
        "catalog_id",
        "catalog_revision",
        "catalog_sha256",
        "probes",
    }
    if set(payload) != expected_top:
        raise JobError("negative registry fields mismatch")
    if (
        payload["schema_version"] != SCHEMA_VERSION
        or payload["registry_id"] != "utiki-p1-negative-probes"
        or payload["phase"] != "P1"
        or payload["catalog_sha256"] != catalog_digest
    ):
        raise JobError("negative registry identity/catalog digest mismatch")
    result: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    expected_entry = {
        "argument_contract",
        "case_id",
        "obligation_id",
        "variant_id",
        "job_id",
        "target",
        "environment",
        "mutation",
        "payload_contract",
        "rejection_token",
        "child_kind",
        "child_producer",
        "input_kinds",
    }
    for value in payload["probes"]:
        if not isinstance(value, dict) or set(value) != expected_entry:
            raise JobError("negative registry entry fields mismatch")
        key = _key(value)
        if key in result:
            raise JobError("negative registry has a duplicate key")
        if value["environment"] not in {"host", "isolated"}:
            raise JobError("negative registry environment is invalid")
        if not isinstance(value["input_kinds"], list) or not value["input_kinds"]:
            raise JobError("negative registry input kinds are empty")
        validate_registry_contract(value)
        result[key] = value
    return result


def _token(value: str) -> str:
    token = SAFE_TOKEN_RE.sub("_", value)
    if not token or token in {".", ".."}:
        raise JobError("cannot encode probe key as a path")
    return token


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--required-catalog", type=Path, required=True)
    parser.add_argument("--approved-catalog-sha256", required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--probe-registry", type=Path, required=True)
    parser.add_argument("--probe-input-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--isolation-proof", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        catalog_path = arguments.required_catalog.resolve(strict=True)
        catalog_digest = _sha256(catalog_path)
        if (
            catalog_digest != arguments.approved_catalog_sha256
            or catalog_digest != arguments.catalog_sha256
        ):
            raise JobError("approved/runtime catalog digest mismatch")
        catalog = _load(catalog_path)
        manifest_path = arguments.execution_manifest.resolve(strict=True)
        manifest = _load(manifest_path)
        if (
            manifest.get("phase") != arguments.phase
            or manifest.get("catalog_sha256") != catalog_digest
        ):
            raise JobError("execution manifest context mismatch")
        catalog_keys = _catalog_negative_keys(catalog, arguments.phase)
        manifest_keys, evidence_paths = _manifest_negative(manifest)
        registry_path = arguments.probe_registry.resolve(strict=True)
        registry = _registry(_load(registry_path), catalog_digest)
        if manifest_keys != catalog_keys or set(registry) != catalog_keys:
            raise JobError("catalog/manifest/negative registry key sets differ")
        isolated = {key for key, value in registry.items() if value["environment"] == "isolated"}
        if len(isolated) != 12:
            raise JobError("P1 negative registry must contain exactly 12 isolated keys")

        selected = sorted(
            key for key in catalog_keys if key[3] == arguments.job_id and key[4] == arguments.target
        )
        if not selected:
            raise JobError("selected job/target has no negative probes")
        if any(registry[key]["environment"] == "isolated" for key in selected):
            if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
                raise JobError("isolated negative job cannot run on the host")
            if arguments.isolation_proof is None:
                raise JobError("isolated negative job requires an isolation proof")
        elif arguments.isolation_proof is not None:
            raise JobError("host negative job may not claim an isolation proof")

        run_dir = arguments.run_dir.resolve()
        input_root = arguments.probe_input_root.resolve(strict=True)
        launcher = repository / "tools" / "run_utiki_evidence.py"
        producer = repository / "tools" / "run_utiki_negative_probe.py"
        if not launcher.is_file() or not producer.is_file():
            raise JobError("formal evidence launchers are missing")
        registry_relative = registry_path.relative_to(repository).as_posix()
        for key in selected:
            entry = registry[key]
            name = f"{_token(key[1])}--{_token(key[2])}"
            source_dir = input_root / name
            source_request = source_dir / "probe.json"
            if not source_request.is_file():
                raise JobError(f"concrete probe input is missing: {name}")
            report = run_dir / evidence_paths[key]
            report.parent.mkdir(parents=True, exist_ok=True)
            copied_dir = report.parent / f"{name}.probe"
            if copied_dir.exists():
                raise JobError(f"probe input destination already exists: {name}")
            shutil.copytree(source_dir, copied_dir, symlinks=False)
            copied_request = copied_dir / "probe.json"
            request = _load(copied_request)
            if _key(request) != key:
                raise JobError(f"concrete probe key mismatch: {name}")
            validate_request_contract(
                entry,
                request,
                probe_root=copied_dir.resolve(),
                repository_root=repository,
            )
            producer_report = report.with_name(f"{report.stem}.producer.json")
            input_relative = copied_request.relative_to(report.parent).as_posix()
            producer_report_relative = producer_report.relative_to(report.parent).as_posix()
            command = [
                sys.executable,
                str(launcher),
                "--repository-root",
                str(repository),
                "--execution-manifest",
                str(manifest_path),
                "--report",
                str(report),
                "--kind",
                "checker-exit-nonzero",
                "--run-id",
                arguments.run_id,
                "--attempt",
                str(arguments.attempt),
                "--job-id",
                arguments.job_id,
                "--phase",
                arguments.phase,
                "--target",
                arguments.target,
                "--candidate-sha",
                arguments.candidate_sha,
                "--catalog-sha256",
                arguments.catalog_sha256,
                "--producer",
                "tools/run_utiki_negative_probe.py",
                "--case-id",
                key[0],
                "--obligation-id",
                key[1],
                "--variant-id",
                key[2],
                "--input",
                "injected-probe",
                "evidence",
                input_relative,
                "--input",
                "probe-registry",
                "repository",
                registry_relative,
                "--producer-report",
                producer_report_relative,
                "--rejection-token",
                entry["rejection_token"],
                "--",
                "--repository-root",
                str(repository),
                "--probe-registry",
                str(registry_path),
                "--probe-input",
                str(copied_request),
                "--producer-report",
                str(producer_report),
            ]
            if entry["environment"] == "isolated":
                proof_copy = copied_dir / "isolation-proof.json"
                shutil.copy2(arguments.isolation_proof, proof_copy)
                command.extend(["--isolation-proof", str(proof_copy)])
            completed = subprocess.run(
                command,
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace")
                raise JobError(f"negative probe launch failed for {name}: {detail.strip()}")
            if not report.is_file():
                raise JobError(f"negative evidence envelope is missing: {name}")
        return 0
    except (JobError, NegativeContractError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write(f"run_utiki_negative_job: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
