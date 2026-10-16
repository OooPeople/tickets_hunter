#!/usr/bin/env python3
"""Generate the reviewed uTiki execution manifest from its fixed catalog.

The required set comes only from the approved catalog.  The binding registry
may attach real pytest nodes and evidence producers to P1-due execution keys,
but it cannot add, remove, postpone, or waive a catalog obligation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any


SCHEMA_VERSION = 1
CATALOG_ID = "utiki-required-cases"
CATALOG_REVISION = "1.5.0"
PHASE = "P1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
PATH_TOKEN_RE = re.compile(r"[^A-Za-z0-9.-]")


class ManifestError(Exception):
    """A deterministic generator failure with a stable reason code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DuplicateJsonKeyError(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except DuplicateJsonKeyError as exc:
        raise ManifestError("DUPLICATE_JSON_KEY", f"{label}: {exc}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("INVALID_JSON", f"{label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("INVALID_SCHEMA", f"{label} root must be an object")
    return value, raw


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ManifestError(
            "INVALID_SCHEMA",
            f"{label} fields mismatch: missing={sorted(expected - actual)!r}, "
            f"unknown={sorted(actual - expected)!r}",
        )


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or SAFE_ID_RE.fullmatch(value) is None:
        raise ManifestError("INVALID_ID", f"{label} is invalid")
    return value


def _safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ManifestError("UNSAFE_PATH", f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or ".." in path.parts:
        raise ManifestError("UNSAFE_PATH", f"{label} is unsafe")
    return value


def _path_token(value: str) -> str:
    """Encode a reviewed ID as one deterministic path component."""
    token = PATH_TOKEN_RE.sub("_", value)
    if not token or token in {".", ".."}:
        raise ManifestError("INVALID_PATH_TOKEN", value)
    return token


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ManifestError("INVALID_SCHEMA", f"{label} must be a nonempty list")
    result = [_safe_id(item, label) for item in value]
    if len(result) != len(set(result)):
        raise ManifestError("DUPLICATE_ID", f"{label} contains duplicates")
    return result


def _catalog_profiles(catalog: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    raw_profiles = catalog.get("gate_profiles")
    if not isinstance(raw_profiles, dict) or PHASE not in raw_profiles:
        raise ManifestError("INVALID_CATALOG", "gate_profiles.P1 is absent")
    profiles: dict[str, dict[str, dict[str, Any]]] = {}
    for phase, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict) or not isinstance(raw_profile.get("required_jobs"), list):
            raise ManifestError("INVALID_CATALOG", f"invalid gate profile {phase}")
        jobs: dict[str, dict[str, Any]] = {}
        for raw_job in raw_profile["required_jobs"]:
            if not isinstance(raw_job, dict):
                raise ManifestError("INVALID_CATALOG", f"invalid job in {phase}")
            job_id = _safe_id(raw_job.get("job_id"), f"{phase}.job_id")
            if job_id in jobs:
                raise ManifestError("DUPLICATE_ID", f"duplicate {phase} job {job_id}")
            jobs[job_id] = {
                "targets": tuple(_string_list(raw_job.get("targets"), f"{phase}.{job_id}.targets")),
                "layers": tuple(_string_list(raw_job.get("layers"), f"{phase}.{job_id}.layers")),
            }
        profiles[phase] = jobs
    return profiles


def _condition_applies(obligation: dict[str, Any], target: str) -> bool:
    applies = obligation.get("applies_when")
    if not isinstance(applies, dict) or set(applies) != {"all"} or not isinstance(applies["all"], list):
        raise ManifestError("INVALID_CATALOG", "invalid applies_when")
    for condition in applies["all"]:
        if not isinstance(condition, dict) or set(condition) != {"field", "one_of"}:
            raise ManifestError("INVALID_CATALOG", "invalid applicability condition")
        if condition["field"] != "target":
            raise ManifestError(
                "TRUSTED_CONDITION_INPUT_REQUIRED",
                f"cannot derive condition {condition['field']!r}",
            )
        accepted = condition["one_of"]
        if not isinstance(accepted, list) or target not in accepted:
            return False
    return True


def _catalog_keys(
    catalog: dict[str, Any], profiles: dict[str, dict[str, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], set[tuple[str, str, str, str, str]]]:
    cases = catalog.get("cases")
    if not isinstance(cases, list) or len(cases) != 153:
        raise ManifestError("INVALID_CATALOG", "catalog must contain 153 cases")
    case_ids: set[str] = set()
    obligation_ids: set[str] = set()
    obligation_count = 0
    expanded: list[dict[str, Any]] = []
    due_keys: set[tuple[str, str, str, str, str]] = set()
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            raise ManifestError("INVALID_CATALOG", "catalog case must be an object")
        case_id = _safe_id(raw_case.get("id"), "catalog case id")
        if case_id in case_ids:
            raise ManifestError("DUPLICATE_ID", f"duplicate case {case_id}")
        case_ids.add(case_id)
        obligations = raw_case.get("obligations")
        if not isinstance(obligations, list) or not obligations:
            raise ManifestError("INVALID_CATALOG", f"{case_id} has no obligations")
        executions: list[dict[str, Any]] = []
        for obligation in obligations:
            if not isinstance(obligation, dict):
                raise ManifestError("INVALID_CATALOG", f"{case_id} obligation is invalid")
            obligation_count += 1
            obligation_id = _safe_id(obligation.get("obligation_id"), "obligation_id")
            if obligation_id in obligation_ids:
                raise ManifestError("DUPLICATE_ID", f"duplicate obligation {obligation_id}")
            obligation_ids.add(obligation_id)
            job_id = _safe_id(obligation.get("job_id"), f"{obligation_id}.job_id")
            layer = _safe_id(obligation.get("layer"), f"{obligation_id}.layer")
            gates = _string_list(obligation.get("required_gates"), f"{obligation_id}.required_gates")
            variants = _string_list(obligation.get("variants"), f"{obligation_id}.variants")
            evidence = _string_list(
                obligation.get("required_evidence"),
                f"{obligation_id}.required_evidence",
            )
            targets: set[str] = set()
            for gate in gates:
                try:
                    job = profiles[gate][job_id]
                except KeyError as exc:
                    raise ManifestError(
                        "INVALID_CATALOG", f"{obligation_id} has no profile for {gate}"
                    ) from exc
                if layer not in job["layers"]:
                    raise ManifestError(
                        "INVALID_CATALOG", f"{obligation_id} layer is absent from {gate}"
                    )
                targets.update(job["targets"])
            for variant_id in variants:
                for target in sorted(targets):
                    key = (case_id, obligation_id, variant_id, job_id, target)
                    p1_job = profiles[PHASE].get(job_id)
                    if PHASE not in gates or p1_job is None or target not in p1_job["targets"]:
                        status = "NOT_DUE"
                    elif _condition_applies(obligation, target):
                        status = "MAPPED"
                        due_keys.add(key)
                    else:
                        status = "NOT_APPLICABLE"
                    executions.append(
                        {
                            "key": key,
                            "obligation_id": obligation_id,
                            "variant_id": variant_id,
                            "job_id": job_id,
                            "target": target,
                            "layer": layer,
                            "status": status,
                            "required_evidence": tuple(evidence),
                        }
                    )
        expanded.append({"id": case_id, "executions": executions})
    if obligation_count != 341:
        raise ManifestError("INVALID_CATALOG", "catalog must contain 341 obligations")
    if sum(len(case["executions"]) for case in expanded) != 490:
        raise ManifestError("INVALID_CATALOG", "catalog must expand to 490 execution keys")
    if len(due_keys) != 63:
        raise ManifestError("INVALID_CATALOG", "P1 must contain 63 due execution keys")
    return expanded, due_keys


def _binding_key(binding: dict[str, Any], label: str) -> tuple[str, str, str, str, str]:
    return (
        _safe_id(binding.get("case_id"), f"{label}.case_id"),
        _safe_id(binding.get("obligation_id"), f"{label}.obligation_id"),
        _safe_id(binding.get("variant_id"), f"{label}.variant_id"),
        _safe_id(binding.get("job_id"), f"{label}.job_id"),
        _safe_id(binding.get("target"), f"{label}.target"),
    )


def _load_bindings(
    path: Path,
    catalog_digest: str,
    root: Path,
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    registry, _ = _load_json(path, "binding registry")
    _exact_keys(
        registry,
        {
            "schema_version",
            "registry_id",
            "phase",
            "catalog_id",
            "catalog_revision",
            "catalog_sha256",
            "review_status",
            "checker_exit_path_template",
            "readiness_notes",
            "bindings",
        },
        "binding registry",
    )
    expected = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": "utiki-p1-execution-bindings",
        "phase": PHASE,
        "catalog_id": CATALOG_ID,
        "catalog_revision": CATALOG_REVISION,
        "catalog_sha256": catalog_digest,
        "review_status": "READY_FOR_REVIEW",
    }
    for field, value in expected.items():
        if registry[field] != value:
            raise ManifestError("BINDING_CONTEXT_MISMATCH", f"registry.{field} is invalid")
    checker_exit_template = registry["checker_exit_path_template"]
    if checker_exit_template != "evidence/negative/{obligation_id}--{variant_id}.json":
        raise ManifestError(
            "BINDING_CONTEXT_MISMATCH",
            "registry.checker_exit_path_template is not the reviewed P1 policy",
        )
    readiness_notes = registry["readiness_notes"]
    if not isinstance(readiness_notes, dict) or set(readiness_notes) != {
        "browser-contract-isolated",
        "performance-contract",
    }:
        raise ManifestError(
            "BINDING_CONTEXT_MISMATCH",
            "registry.readiness_notes must explain both blocked P1 jobs",
        )
    if any(
        not isinstance(note, str) or not note.startswith("BLOCKED")
        for note in readiness_notes.values()
    ):
        raise ManifestError(
            "BINDING_CONTEXT_MISMATCH",
            "registry readiness notes must explicitly remain BLOCKED",
        )
    raw_bindings = registry["bindings"]
    if not isinstance(raw_bindings, list):
        raise ManifestError("INVALID_SCHEMA", "registry.bindings must be a list")
    result: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    seen_nodes: set[tuple[str, str, str]] = set()
    seen_negative_paths: dict[str, tuple[str, str, str, str, str]] = {}
    for index, raw_binding in enumerate(raw_bindings):
        label = f"binding[{index}]"
        if not isinstance(raw_binding, dict):
            raise ManifestError("INVALID_SCHEMA", f"{label} must be an object")
        _exact_keys(
            raw_binding,
            {
                "case_id",
                "obligation_id",
                "variant_id",
                "job_id",
                "target",
                "node_id",
                "producer",
                "formal_readiness",
                "evidence",
            },
            label,
        )
        key = _binding_key(raw_binding, label)
        if key in result:
            raise ManifestError("DUPLICATE_BINDING", f"duplicate binding {key!r}")
        node_id = raw_binding["node_id"]
        if node_id is not None:
            if not isinstance(node_id, str) or "::" not in node_id:
                raise ManifestError("INVALID_NODE_ID", f"{label}.node_id is invalid")
            node_path = _safe_path(node_id.split("::", 1)[0], f"{label}.node_id")
            if not (root / node_path).is_file():
                raise ManifestError("MISSING_NODE", f"{node_path} does not exist")
            node_key = (key[3], key[4], node_id)
            if node_key in seen_nodes:
                raise ManifestError("DUPLICATE_NODE_ID", f"node reused: {node_id}")
            seen_nodes.add(node_key)
        producer = _safe_path(raw_binding["producer"], f"{label}.producer")
        if not (root / producer).is_file():
            raise ManifestError("MISSING_PRODUCER", f"{producer} does not exist")
        readiness = raw_binding["formal_readiness"]
        if readiness not in {"READY", "BLOCKED_ENVIRONMENT"}:
            raise ManifestError("INVALID_READINESS", f"{label}.formal_readiness is invalid")
        raw_evidence = raw_binding["evidence"]
        if not isinstance(raw_evidence, list):
            raise ManifestError("INVALID_SCHEMA", f"{label}.evidence must be a list")
        evidence: list[dict[str, str]] = []
        evidence_kinds: set[str] = set()
        for evidence_index, raw_item in enumerate(raw_evidence):
            if not isinstance(raw_item, dict):
                raise ManifestError("INVALID_SCHEMA", f"{label}.evidence[{evidence_index}] invalid")
            _exact_keys(raw_item, {"kind", "path", "producer"}, f"{label}.evidence[{evidence_index}]")
            kind = _safe_id(raw_item["kind"], f"{label}.evidence.kind")
            evidence_path = _safe_path(raw_item["path"], f"{label}.evidence.path")
            if kind == "checker-exit-nonzero":
                if evidence_path != "evidence/checker-exit-nonzero.json":
                    raise ManifestError(
                        "INVALID_NEGATIVE_EVIDENCE_PATH",
                        f"{label} must use the reviewed expansion marker",
                    )
                evidence_path = _safe_path(
                    checker_exit_template.format(
                        obligation_id=_path_token(key[1]),
                        variant_id=_path_token(key[2]),
                    ),
                    f"{label}.evidence.path",
                )
                prior_key = seen_negative_paths.setdefault(evidence_path, key)
                if prior_key != key:
                    raise ManifestError(
                        "NEGATIVE_EVIDENCE_PATH_COLLISION",
                        f"{prior_key!r} and {key!r} map to {evidence_path}",
                    )
            evidence_producer = _safe_path(raw_item["producer"], f"{label}.evidence.producer")
            if not (root / evidence_producer).is_file():
                raise ManifestError("MISSING_PRODUCER", f"{evidence_producer} does not exist")
            if kind in evidence_kinds:
                raise ManifestError("DUPLICATE_EVIDENCE", f"{label} repeats {kind}")
            evidence_kinds.add(kind)
            evidence.append({"kind": kind, "path": evidence_path})
        result[key] = {
            "node_id": node_id,
            "evidence": sorted(evidence, key=lambda item: (item["kind"], item["path"])),
            "formal_readiness": readiness,
        }
    return result


def generate(
    catalog_path: Path,
    registry_path: Path,
    approved_catalog_sha256: str,
    root: Path,
) -> dict[str, Any]:
    if SHA256_RE.fullmatch(approved_catalog_sha256) is None:
        raise ManifestError("INVALID_APPROVED_DIGEST", "approved catalog digest is invalid")
    catalog, catalog_raw = _load_json(catalog_path, "required catalog")
    catalog_digest = hashlib.sha256(catalog_raw).hexdigest()
    if catalog_digest != approved_catalog_sha256:
        raise ManifestError("UNAPPROVED_CATALOG", "catalog bytes do not match the approved digest")
    expected_headers = {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": CATALOG_ID,
        "revision": CATALOG_REVISION,
        "plan_version": "1.5",
        "status": "APPROVED",
        "case_count": 153,
        "obligation_count": 341,
    }
    for field, value in expected_headers.items():
        if catalog.get(field) != value:
            raise ManifestError("INVALID_CATALOG", f"catalog.{field} is invalid")
    profiles = _catalog_profiles(catalog)
    expanded, due_keys = _catalog_keys(catalog, profiles)
    bindings = _load_bindings(registry_path, catalog_digest, root)
    missing = sorted(due_keys - set(bindings))
    extra = sorted(set(bindings) - due_keys)
    if missing or extra:
        raise ManifestError(
            "BINDING_SET_MISMATCH",
            f"missing={missing[:3]!r} ({len(missing)}), extra={extra[:3]!r} ({len(extra)})",
        )

    cases: list[dict[str, Any]] = []
    for case in expanded:
        executions: list[dict[str, Any]] = []
        for execution in case["executions"]:
            binding = bindings.get(execution["key"])
            if execution["status"] == "MAPPED":
                if binding is None:
                    raise ManifestError("MISSING_BINDING", repr(execution["key"]))
                required = set(execution["required_evidence"])
                node_id = binding["node_id"]
                if "junit" in required and node_id is None:
                    raise ManifestError("MISSING_NODE_ID", repr(execution["key"]))
                if "junit" not in required and node_id is not None:
                    raise ManifestError("FABRICATED_TESTCASE", repr(execution["key"]))
                actual_evidence = {item["kind"] for item in binding["evidence"]}
                if actual_evidence != required - {"junit"}:
                    raise ManifestError("EVIDENCE_SET_MISMATCH", repr(execution["key"]))
                evidence = binding["evidence"]
            else:
                if binding is not None:
                    raise ManifestError("EXTRA_BINDING", repr(execution["key"]))
                node_id = None
                evidence = []
            executions.append(
                {
                    "obligation_id": execution["obligation_id"],
                    "variant_id": execution["variant_id"],
                    "job_id": execution["job_id"],
                    "target": execution["target"],
                    "layer": execution["layer"],
                    "status": execution["status"],
                    "node_id": node_id,
                    "evidence": evidence,
                }
            )
        cases.append({"id": case["id"], "executions": executions})
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_id": CATALOG_ID,
        "catalog_revision": CATALOG_REVISION,
        "catalog_sha256": catalog_digest,
        "phase": PHASE,
        "cases": cases,
    }


def _serialized(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--required-catalog", type=Path, required=True)
    parser.add_argument("--approved-catalog-sha256", required=True)
    parser.add_argument("--binding-registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        root = arguments.root.resolve()
        manifest = generate(
            arguments.required_catalog,
            arguments.binding_registry,
            arguments.approved_catalog_sha256,
            root,
        )
        payload = _serialized(manifest)
        if arguments.check:
            try:
                existing = arguments.output.read_bytes()
            except OSError as exc:
                raise ManifestError("MANIFEST_MISSING", str(exc)) from exc
            if existing != payload:
                raise ManifestError("MANIFEST_NOT_REPRODUCIBLE", "persisted bytes differ")
        else:
            _write_atomic(arguments.output, payload)
        report = {
            "status": "PASS",
            "case_count": len(manifest["cases"]),
            "execution_count": sum(len(case["executions"]) for case in manifest["cases"]),
            "mapped_count": sum(
                execution["status"] == "MAPPED"
                for case in manifest["cases"]
                for execution in case["executions"]
            ),
            "output_sha256": hashlib.sha256(payload).hexdigest(),
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    except ManifestError as exc:
        print(
            json.dumps(
                {"status": "FAIL", "reason": exc.code, "detail": exc.detail},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
