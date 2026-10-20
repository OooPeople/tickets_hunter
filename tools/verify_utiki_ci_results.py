#!/usr/bin/env python3
"""Verify uTiki CI evidence against the independently approved catalog.

The checker deliberately treats the required catalog as specification input.  It
does not infer required cases from pytest collection, the execution manifest, or
the JUnit document.  See the v1.6 refactor plan, sections 16.11 and 17.5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NoReturn
from xml.etree import ElementTree

try:
    from tools.utiki_negative_contract import (
        NegativeContractError,
        probe_semantic_output,
        validate_probe_execution_contract,
        validate_registry_contract,
        validate_probe_output_contract,
        validate_request_contract,
    )
except ModuleNotFoundError:  # Direct execution from tools/.
    from utiki_negative_contract import (  # type: ignore[no-redef]
        NegativeContractError,
        probe_semantic_output,
        validate_probe_execution_contract,
        validate_registry_contract,
        validate_probe_output_contract,
        validate_request_contract,
    )


SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 2
NEGATIVE_TRANSCRIPT_SCHEMA_VERSION = 2
CATALOG_ID = "utiki-required-cases"
CATALOG_REVISION = "1.6.0"
PLAN_VERSION = "1.6"
PLAN_SHA256 = "f2e7e88edfbb3f40f9d8775d77c95ac48745ad9133c08d4762334f449b302220"
PHASES = ("P0", "P1", "P2", "P3a", "P3b", "P4", "P5", "P6a", "P6b", "P6c", "P7", "P8")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BASE_IMAGE_RE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\[\]-]*\Z")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*-attempt-[1-9][0-9]*\Z")
TARGET_PLATFORM = {
    "source-linux-x64": ("linux", "x86_64"),
    "isolated-linux-x64": ("linux", "x86_64"),
    "windows-x64": ("windows", "x86_64"),
    "macos-arm64": ("macos", "arm64"),
}

EXPECTED_CASE_IDS = frozenset(
    f"{prefix}{number:02d}"
    for prefix, count in {
        "D": 10,
        "N": 8,
        "S": 13,
        "R": 12,
        "L": 6,
        "C": 6,
        "A": 6,
        "U": 12,
        "V": 16,
        "X": 16,
        "T": 12,
        "I": 12,
        "H": 9,
    }.items()
    for number in range(1, count + 1)
) | {"H11", "H12", "P02", "P04", "P05"}


class GateError(Exception):
    """A deterministic, user-facing gate rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def fail(code: str, message: str) -> NoReturn:
    raise GateError(code, message)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("DUPLICATE_JSON_KEY", f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        fail("INPUT_READ_ERROR", f"cannot read {label} {path}: {error}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except UnicodeDecodeError as error:
        fail("INVALID_UTF8", f"{label} is not UTF-8: {error}")
    except json.JSONDecodeError as error:
        fail("INVALID_JSON", f"invalid {label} JSON: {error}")
    if not isinstance(value, dict):
        fail("INVALID_SCHEMA", f"{label} root must be an object")
    return value, raw


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest_file(path: Path, *, label: str) -> str:
    try:
        return digest_bytes(path.read_bytes())
    except OSError as error:
        fail("EVIDENCE_READ_ERROR", f"cannot read {label} {path}: {error}")


def require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("INVALID_SCHEMA", f"{label} must be an object")
    return value


def require_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail("INVALID_SCHEMA", f"{label} must be an array")
    return value


def require_str(value: Any, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        fail("INVALID_SCHEMA", f"{label} must be a non-empty string")
    return value


def require_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail("INVALID_SCHEMA", f"{label} must be an integer >= {minimum}")
    return value


def require_bool(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        fail("INVALID_SCHEMA", f"{label} must be a boolean")
    return value


def require_exact_keys(value: dict[str, Any], keys: set[str], *, label: str) -> None:
    actual = set(value)
    if actual != keys:
        fail(
            "INVALID_SCHEMA",
            f"{label} keys mismatch: missing={sorted(keys - actual)!r}, unknown={sorted(actual - keys)!r}",
        )


def require_sha256(value: Any, *, label: str) -> str:
    text = require_str(value, label=label)
    if not SHA256_RE.fullmatch(text):
        fail("INVALID_DIGEST", f"{label} must be a lowercase SHA-256")
    return text


def require_git_sha(value: Any, *, label: str) -> str:
    text = require_str(value, label=label)
    if not GIT_SHA_RE.fullmatch(text):
        fail("INVALID_CANDIDATE_SHA", f"{label} must be a lowercase 40-character Git SHA")
    return text


def require_safe_id(value: Any, *, label: str) -> str:
    text = require_str(value, label=label)
    if not SAFE_ID_RE.fullmatch(text):
        fail("INVALID_IDENTIFIER", f"unsafe {label}: {text!r}")
    return text


def unique_strings(value: Any, *, label: str, nonempty: bool = True) -> list[str]:
    items = require_list(value, label=label)
    if nonempty and not items:
        fail("INVALID_SCHEMA", f"{label} must not be empty")
    result = [require_safe_id(item, label=f"{label}[]") for item in items]
    if len(result) != len(set(result)):
        fail("DUPLICATE_ID", f"{label} contains duplicate values")
    return result


def safe_relative_path(root: Path, raw_path: Any, *, label: str) -> Path:
    text = require_str(raw_path, label=label)
    if "\\" in text or "\x00" in text or ":" in text:
        fail("UNSAFE_PATH", f"{label} is not a portable POSIX path: {text!r}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts or pure == PurePosixPath(".") or str(pure) != text:
        fail("UNSAFE_PATH", f"{label} must be a contained relative path: {text!r}")
    resolved_root = root.resolve()
    unresolved = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            fail("UNSAFE_PATH", f"{label} may not traverse a symlink: {text!r}")
    resolved = unresolved.resolve()
    if resolved_root not in resolved.parents:
        fail("UNSAFE_PATH", f"{label} escapes evidence directory: {text!r}")
    return resolved


def parse_timestamp(value: Any, *, label: str) -> datetime:
    text = require_str(value, label=label)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        fail("INVALID_TIMESTAMP", f"{label} is not ISO-8601")
    if parsed.tzinfo is None:
        fail("INVALID_TIMESTAMP", f"{label} must include a timezone")
    return parsed


@dataclass(frozen=True)
class Obligation:
    case_id: str
    obligation_id: str
    gates: tuple[str, ...]
    job_id: str
    layer: str
    variants: tuple[str, ...]
    conditions: tuple[tuple[str, tuple[Any, ...]], ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True, order=True)
class ExecutionKey:
    case_id: str
    obligation_id: str
    variant_id: str
    job_id: str
    target: str


@dataclass(frozen=True)
class Mapping:
    key: ExecutionKey
    layer: str
    status: str
    node_id: str | None
    evidence: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CatalogModel:
    raw: dict[str, Any]
    digest: str
    obligations: tuple[Obligation, ...]
    profiles: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]]
    repository_root: Path


def _approval_record_path(catalog_path: Path, catalog: dict[str, Any]) -> Path:
    governance = require_dict(catalog.get("governance"), label="catalog.governance")
    record = require_str(governance.get("approval_record"), label="catalog.governance.approval_record")
    relative = Path(record)
    if relative.is_absolute() or ".." in relative.parts:
        fail("INVALID_APPROVAL_RECORD", "approval record must be repository-relative")
    candidates = [Path.cwd() / relative]
    # Normal repository placement is <root>/ci/catalog.json.
    if len(catalog_path.resolve().parents) >= 2:
        candidates.append(catalog_path.resolve().parent.parent / relative)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    fail("REVIEW_REQUIRED", f"approved catalog record not found: {record}")


def validate_catalog(path: Path, approved_digest: str) -> CatalogModel:
    if not SHA256_RE.fullmatch(approved_digest):
        fail("INVALID_APPROVED_DIGEST", "--approved-catalog-sha256 must be a lowercase SHA-256")
    catalog, raw = read_json(path, label="required catalog")
    actual_digest = digest_bytes(raw)
    if actual_digest != approved_digest:
        fail("UNAPPROVED_CATALOG", f"catalog digest {actual_digest} does not equal approved digest {approved_digest}")

    if catalog.get("schema_version") != SCHEMA_VERSION:
        fail("UNKNOWN_SCHEMA_VERSION", "unsupported catalog schema_version")
    expected_scalars = {
        "catalog_id": CATALOG_ID,
        "revision": CATALOG_REVISION,
        "plan_version": PLAN_VERSION,
        "plan_sha256": PLAN_SHA256,
        "status": "APPROVED",
        "case_count": 143,
        "obligation_count": 320,
    }
    for field, expected in expected_scalars.items():
        if catalog.get(field) != expected:
            fail("INVALID_CATALOG", f"catalog.{field} must equal {expected!r}")

    approval_path = _approval_record_path(path, catalog)
    try:
        approval_text = approval_path.read_text(encoding="utf-8")
    except OSError as error:
        fail("REVIEW_REQUIRED", f"cannot read approval record: {error}")
    if approved_digest not in approval_text or "APPROVED" not in approval_text:
        fail("REVIEW_REQUIRED", "approval record does not bind the supplied digest to APPROVED")

    cases = require_list(catalog.get("cases"), label="catalog.cases")
    case_ids: list[str] = []
    obligations: list[Obligation] = []
    obligation_ids: set[str] = set()
    for case_index, case_value in enumerate(cases):
        case = require_dict(case_value, label=f"catalog.cases[{case_index}]")
        case_id = require_safe_id(case.get("id"), label=f"catalog.cases[{case_index}].id")
        case_ids.append(case_id)
        unique_strings(case.get("spec_refs"), label=f"catalog case {case_id}.spec_refs")
        require_str(case.get("scenario"), label=f"catalog case {case_id}.scenario")
        require_str(case.get("invariant"), label=f"catalog case {case_id}.invariant")
        entries = require_list(case.get("obligations"), label=f"catalog case {case_id}.obligations")
        if not entries:
            fail("EMPTY_OBLIGATION", f"catalog case {case_id} has no obligations")
        for obligation_index, obligation_value in enumerate(entries):
            label = f"catalog case {case_id}.obligations[{obligation_index}]"
            obligation = require_dict(obligation_value, label=label)
            obligation_id = require_safe_id(obligation.get("obligation_id"), label=f"{label}.obligation_id")
            if obligation_id in obligation_ids:
                fail("DUPLICATE_ID", f"duplicate obligation_id: {obligation_id}")
            obligation_ids.add(obligation_id)
            gates = tuple(unique_strings(obligation.get("required_gates"), label=f"{label}.required_gates"))
            if not set(gates).issubset(PHASES):
                fail("INVALID_CATALOG", f"{obligation_id} has an unknown gate")
            variants = tuple(unique_strings(obligation.get("variants"), label=f"{label}.variants"))
            job_id = require_safe_id(obligation.get("job_id"), label=f"{label}.job_id")
            layer = require_safe_id(obligation.get("layer"), label=f"{label}.layer")
            evidence = tuple(unique_strings(obligation.get("required_evidence"), label=f"{label}.required_evidence"))
            if obligation.get("result_policy") != "PASS_ONLY":
                fail("INVALID_CATALOG", f"{obligation_id} must use PASS_ONLY")
            applies = require_dict(obligation.get("applies_when"), label=f"{label}.applies_when")
            require_exact_keys(applies, {"all"}, label=f"{label}.applies_when")
            condition_items: list[tuple[str, tuple[Any, ...]]] = []
            for condition_index, condition_value in enumerate(require_list(applies["all"], label=f"{label}.applies_when.all")):
                condition = require_dict(condition_value, label=f"{label}.condition[{condition_index}]")
                require_exact_keys(condition, {"field", "one_of"}, label=f"{label}.condition[{condition_index}]")
                field = require_safe_id(condition["field"], label=f"{label}.condition.field")
                accepted = require_list(condition["one_of"], label=f"{label}.condition.one_of")
                if not accepted or any(not isinstance(item, (str, bool, int)) for item in accepted):
                    fail("INVALID_CATALOG", f"{obligation_id} has an invalid condition value")
                condition_items.append((field, tuple(accepted)))
            obligations.append(
                Obligation(
                    case_id=case_id,
                    obligation_id=obligation_id,
                    gates=gates,
                    job_id=job_id,
                    layer=layer,
                    variants=variants,
                    conditions=tuple(condition_items),
                    evidence=evidence,
                )
            )

    if len(case_ids) != len(set(case_ids)):
        fail("DUPLICATE_ID", "catalog contains duplicate case IDs")
    if set(case_ids) != EXPECTED_CASE_IDS or len(case_ids) != 143:
        fail("CATALOG_CASE_SET_MISMATCH", "catalog case IDs do not equal the fixed v1.6 set")
    if len(obligations) != 320:
        fail("CATALOG_OBLIGATION_SET_MISMATCH", "catalog must contain exactly 320 obligations")

    profiles: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {}
    profile_values = require_dict(catalog.get("gate_profiles"), label="catalog.gate_profiles")
    if set(profile_values) != set(PHASES):
        fail("INVALID_CATALOG", "catalog gate_profiles must list every phase exactly once")
    for phase in PHASES:
        profile = require_dict(profile_values[phase], label=f"catalog.gate_profiles.{phase}")
        jobs: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        for job_value in require_list(profile.get("required_jobs"), label=f"catalog.gate_profiles.{phase}.required_jobs"):
            job = require_dict(job_value, label=f"catalog.gate_profiles.{phase}.job")
            job_id = require_safe_id(job.get("job_id"), label=f"catalog.gate_profiles.{phase}.job_id")
            if job_id in jobs:
                fail("DUPLICATE_ID", f"duplicate job {job_id} in {phase}")
            targets = tuple(unique_strings(job.get("targets"), label=f"catalog {phase}/{job_id}.targets"))
            layers = tuple(unique_strings(job.get("layers"), label=f"catalog {phase}/{job_id}.layers"))
            jobs[job_id] = (targets, layers)
        profiles[phase] = jobs

    for obligation in obligations:
        for phase in obligation.gates:
            if obligation.job_id not in profiles[phase]:
                fail("INVALID_CATALOG", f"{obligation.obligation_id}: job missing from gate profile {phase}")
            if obligation.layer not in profiles[phase][obligation.job_id][1]:
                fail("INVALID_CATALOG", f"{obligation.obligation_id}: layer missing from gate profile {phase}")

    repository_root = path.resolve().parent.parent
    return CatalogModel(
        catalog,
        actual_digest,
        tuple(obligations),
        profiles,
        repository_root,
    )


def _all_targets(catalog: CatalogModel, obligation: Obligation) -> tuple[str, ...]:
    targets: set[str] = set()
    for phase in obligation.gates:
        targets.update(catalog.profiles[phase][obligation.job_id][0])
    if not targets:
        fail("INVALID_CATALOG", f"{obligation.obligation_id} has no target in its required gates")
    return tuple(sorted(targets))


def _condition_applies(obligation: Obligation, target: str) -> bool:
    context: dict[str, Any] = {"target": target}
    for field, accepted in obligation.conditions:
        if field not in context:
            fail(
                "REVIEWED_CONDITION_EVIDENCE_REQUIRED",
                f"{obligation.obligation_id}: checker-derived condition {field!r} has no trusted derivation input",
            )
        if context[field] not in accepted:
            return False
    return True


def expected_mapping_status(catalog: CatalogModel, obligation: Obligation, phase: str, target: str) -> str:
    if phase not in obligation.gates:
        return "NOT_DUE"
    phase_job = catalog.profiles[phase].get(obligation.job_id)
    if phase_job is None or target not in phase_job[0]:
        return "NOT_DUE"
    return "MAPPED" if _condition_applies(obligation, target) else "NOT_APPLICABLE"


def validate_execution_manifest(
    path: Path, catalog: CatalogModel, phase: str
) -> tuple[dict[str, Any], str, dict[ExecutionKey, Mapping]]:
    manifest, raw = read_json(path, label="execution manifest")
    manifest_digest = digest_bytes(raw)
    require_exact_keys(
        manifest,
        {"schema_version", "catalog_id", "catalog_revision", "catalog_sha256", "phase", "cases"},
        label="execution manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        fail("UNKNOWN_SCHEMA_VERSION", "unsupported execution manifest schema_version")
    expected_headers = {
        "catalog_id": CATALOG_ID,
        "catalog_revision": CATALOG_REVISION,
        "catalog_sha256": catalog.digest,
        "phase": phase,
    }
    for field, expected in expected_headers.items():
        if manifest[field] != expected:
            fail("MANIFEST_CONTEXT_MISMATCH", f"execution manifest {field} does not equal {expected!r}")

    cases = require_list(manifest["cases"], label="execution manifest.cases")
    manifest_case_ids: list[str] = []
    actual: dict[ExecutionKey, Mapping] = {}
    for case_index, case_value in enumerate(cases):
        case = require_dict(case_value, label=f"execution manifest.cases[{case_index}]")
        require_exact_keys(case, {"id", "executions"}, label=f"execution manifest.cases[{case_index}]")
        case_id = require_safe_id(case["id"], label=f"execution manifest.cases[{case_index}].id")
        manifest_case_ids.append(case_id)
        for mapping_index, mapping_value in enumerate(require_list(case["executions"], label=f"manifest case {case_id}.executions")):
            label = f"manifest case {case_id}.executions[{mapping_index}]"
            item = require_dict(mapping_value, label=label)
            require_exact_keys(
                item,
                {"obligation_id", "variant_id", "job_id", "target", "layer", "status", "node_id", "evidence"},
                label=label,
            )
            obligation_id = require_safe_id(item["obligation_id"], label=f"{label}.obligation_id")
            variant_id = require_safe_id(item["variant_id"], label=f"{label}.variant_id")
            job_id = require_safe_id(item["job_id"], label=f"{label}.job_id")
            target = require_safe_id(item["target"], label=f"{label}.target")
            layer = require_safe_id(item["layer"], label=f"{label}.layer")
            status = require_str(item["status"], label=f"{label}.status")
            if status not in {"MAPPED", "NOT_APPLICABLE", "NOT_DUE"}:
                fail("INVALID_MANIFEST_STATUS", f"{label}.status is not allowed")
            node_raw = item["node_id"]
            node_id = None if node_raw is None else require_str(node_raw, label=f"{label}.node_id")
            evidence_items: list[tuple[str, str]] = []
            for evidence_index, evidence_value in enumerate(require_list(item["evidence"], label=f"{label}.evidence")):
                evidence = require_dict(evidence_value, label=f"{label}.evidence[{evidence_index}]")
                require_exact_keys(evidence, {"kind", "path"}, label=f"{label}.evidence[{evidence_index}]")
                kind = require_safe_id(evidence["kind"], label=f"{label}.evidence.kind")
                path_text = require_str(evidence["path"], label=f"{label}.evidence.path")
                relative = Path(path_text)
                if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
                    fail("UNSAFE_PATH", f"{label}.evidence.path is unsafe")
                evidence_items.append((kind, path_text))
            if len(evidence_items) != len(set(evidence_items)):
                fail("DUPLICATE_EVIDENCE", f"{label} has duplicate evidence mappings")
            evidence_kinds = [kind for kind, _ in evidence_items]
            if len(evidence_kinds) != len(set(evidence_kinds)):
                fail("DUPLICATE_EVIDENCE", f"{label} maps one evidence kind more than once")
            key = ExecutionKey(case_id, obligation_id, variant_id, job_id, target)
            if key in actual:
                fail("DUPLICATE_EXECUTION_KEY", f"duplicate execution key: {key}")
            actual[key] = Mapping(key, layer, status, node_id, tuple(evidence_items))

    if len(manifest_case_ids) != len(set(manifest_case_ids)):
        fail("DUPLICATE_ID", "execution manifest contains duplicate case IDs")
    if set(manifest_case_ids) != EXPECTED_CASE_IDS or len(manifest_case_ids) != 143:
        fail("MANIFEST_CASE_SET_MISMATCH", "execution manifest case IDs do not match the required catalog")

    expected: dict[ExecutionKey, tuple[Obligation, str]] = {}
    for obligation in catalog.obligations:
        for variant in obligation.variants:
            for target in _all_targets(catalog, obligation):
                key = ExecutionKey(obligation.case_id, obligation.obligation_id, variant, obligation.job_id, target)
                expected[key] = (obligation, expected_mapping_status(catalog, obligation, phase, target))
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        unknown = sorted(set(actual) - set(expected))
        fail(
            "EXECUTION_SET_MISMATCH",
            f"execution manifest mapping mismatch: missing={missing[:3]!r} ({len(missing)}), unknown={unknown[:3]!r} ({len(unknown)})",
        )

    seen_nodes: set[tuple[str, str, str]] = set()
    for key, mapping in actual.items():
        obligation, expected_status = expected[key]
        if mapping.layer != obligation.layer:
            fail("WRONG_LAYER", f"{key}: expected layer {obligation.layer}, got {mapping.layer}")
        if mapping.status != expected_status:
            fail("FORGED_APPLICABILITY", f"{key}: expected {expected_status}, got {mapping.status}")
        expected_non_junit = set(obligation.evidence) - {"junit"}
        actual_non_junit = {kind for kind, _ in mapping.evidence}
        if mapping.status == "MAPPED":
            if actual_non_junit != expected_non_junit:
                fail("EVIDENCE_SET_MISMATCH", f"{key}: non-JUnit evidence kinds do not match catalog")
            if "junit" in obligation.evidence:
                if mapping.node_id is None:
                    fail("MISSING_NODE_ID", f"{key}: required JUnit mapping has no node_id")
                node_key = (key.job_id, key.target, mapping.node_id)
                if node_key in seen_nodes:
                    fail("DUPLICATE_NODE_ID", f"node_id reused in a job/target: {mapping.node_id}")
                seen_nodes.add(node_key)
            elif mapping.node_id is not None:
                fail("FABRICATED_TESTCASE", f"{key}: non-pytest evidence may not map a node_id")
        else:
            if mapping.node_id is not None or mapping.evidence:
                fail("INVALID_NON_EXECUTED_MAPPING", f"{key}: {mapping.status} must not carry node/evidence")
    return manifest, manifest_digest, actual


def _validate_context_property(properties: dict[str, str], field: str, expected: str, *, label: str) -> None:
    if properties.get(field) != expected:
        fail("JUNIT_CONTEXT_MISMATCH", f"{label} property {field!r} does not equal {expected!r}")


def _xml_properties(parent: ElementTree.Element, *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    properties = parent.find("properties")
    if properties is None:
        return result
    for prop in properties.findall("property"):
        name = prop.get("name")
        value = prop.get("value")
        if not name or value is None:
            fail("INVALID_JUNIT", f"{label} has a property without name/value")
        if name in result:
            fail("DUPLICATE_JUNIT_PROPERTY", f"{label} has duplicate property {name!r}")
        result[name] = value
    return result


def parse_bool_property(value: str, *, label: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    fail("INVALID_JUNIT", f"{label} must be 'true' or 'false'")


def validate_junit(
    path: Path,
    metadata: dict[str, Any],
    mappings: dict[ExecutionKey, Mapping],
    manifest_digest: str,
) -> tuple[int, int]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        fail("INPUT_READ_ERROR", f"cannot read JUnit {path}: {error}")
    if digest_bytes(raw) != metadata["junit_sha256"]:
        fail("JUNIT_DIGEST_MISMATCH", "JUnit digest does not match run metadata")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        fail("UNSAFE_XML", "JUnit DTD/entity declarations are forbidden")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        fail("INVALID_JUNIT", f"cannot parse JUnit: {error}")
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
    else:
        fail("INVALID_JUNIT", "JUnit root must be testsuite or testsuites")
    if not suites:
        fail("INVALID_JUNIT", "JUnit contains no testsuite")

    context = {
        "run_id": metadata["run_id"],
        "attempt": str(metadata["attempt"]),
        "job_id": metadata["job_id"],
        "phase": metadata["phase"],
        "target": metadata["target"],
        "candidate_sha": metadata["candidate_sha"],
        "catalog_sha256": metadata["catalog_sha256"],
        "execution_manifest_sha256": manifest_digest,
        "fixture_digest": metadata["fixture_digest"],
        "script_digest": metadata["script_digest"],
    }
    for suite_index, suite in enumerate(suites):
        properties = _xml_properties(suite, label=f"testsuite[{suite_index}]")
        for field, expected in context.items():
            _validate_context_property(properties, field, expected, label=f"testsuite[{suite_index}]")

    expected = {
        key: mapping
        for key, mapping in mappings.items()
        if mapping.status == "MAPPED"
        and key.job_id == metadata["job_id"]
        and key.target == metadata["target"]
        and mapping.node_id is not None
    }
    actual: dict[ExecutionKey, str] = {}
    managed_names = {
        "case_id",
        "obligation_id",
        "variant_id",
        "node_id",
        "run_id",
        "attempt",
        "job_id",
        "phase",
        "target",
        "layer",
        "candidate_sha",
        "catalog_sha256",
        "execution_manifest_sha256",
        "fixture_digest",
        "script_digest",
        "outcome",
        "wasxfail",
        "setup_outcome",
        "call_outcome",
        "teardown_outcome",
        "markers",
    }
    total_testcases = 0
    for suite_index, suite in enumerate(suites):
        for testcase_index, testcase in enumerate(suite.findall("testcase")):
            total_testcases += 1
            label = f"testsuite[{suite_index}].testcase[{testcase_index}]"
            properties = _xml_properties(testcase, label=label)
            present = managed_names & set(properties)
            if not present:
                continue
            if present != managed_names:
                fail("OLD_OR_PARTIAL_JUNIT", f"{label} lacks required managed properties")
            key = ExecutionKey(
                properties["case_id"],
                properties["obligation_id"],
                properties["variant_id"],
                properties["job_id"],
                properties["target"],
            )
            if key not in expected:
                fail("UNKNOWN_JUNIT_RESULT", f"{label} declares an unknown or inapplicable execution key: {key}")
            if key in actual:
                fail(
                    "DUPLICATE_RESULT",
                    f"DUPLICATE_JUNIT_RESULT: duplicate JUnit execution result: {key}",
                )
            mapping = expected[key]
            if properties["node_id"] != mapping.node_id:
                fail("JUNIT_NODE_MISMATCH", f"{label} node_id does not match execution manifest")
            if properties["layer"] != mapping.layer:
                fail("WRONG_LAYER", f"{label} layer does not match catalog")
            for field, expected_value in context.items():
                _validate_context_property(properties, field, expected_value, label=label)
            if testcase.find("skipped") is not None:
                fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} is skipped")
            if testcase.find("failure") is not None or testcase.find("error") is not None:
                fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} failed or errored")
            if properties["outcome"] != "passed":
                fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} outcome is not passed")
            if parse_bool_property(properties["wasxfail"], label=f"{label}.wasxfail"):
                fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} was xfail/xpass")
            for stage in ("setup_outcome", "call_outcome", "teardown_outcome"):
                if properties[stage] != "passed":
                    fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} {stage} is not passed")
            try:
                markers = json.loads(properties["markers"], object_pairs_hook=_reject_duplicate_pairs)
            except json.JSONDecodeError as error:
                fail("INVALID_JUNIT", f"{label} markers is invalid JSON: {error}")
            if not isinstance(markers, list) or any(not isinstance(marker, str) for marker in markers):
                fail("INVALID_JUNIT", f"{label} markers must be a string array")
            if any(marker == "xfail" or marker.startswith("xfail(") for marker in markers):
                fail("REQUIRED_OUTCOME_NOT_PASS", f"{label} carries an xfail marker")
            actual[key] = properties["node_id"]

    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        fail(
            "EMPTY_OR_MISSING_RESULT",
            f"JUnit lacks {len(missing)} required results: {missing[:3]!r}",
        )
    return len(expected), total_testcases


def _validate_digest_entries(
    metadata_dir: Path,
    values: Any,
    *,
    label: str,
    required_keys: set[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], Path]:
    entries: dict[tuple[str, str], Path] = {}
    for index, value in enumerate(require_list(values, label=label)):
        item = require_dict(value, label=f"{label}[{index}]")
        require_exact_keys(item, {"kind", "path", "sha256"}, label=f"{label}[{index}]")
        kind = require_safe_id(item["kind"], label=f"{label}[{index}].kind")
        raw_path = require_str(item["path"], label=f"{label}[{index}].path")
        key = (kind, raw_path)
        if key in entries:
            fail("DUPLICATE_EVIDENCE", f"{label} duplicates {key}")
        expected_digest = require_sha256(item["sha256"], label=f"{label}[{index}].sha256")
        path = safe_relative_path(metadata_dir, raw_path, label=f"{label}[{index}].path")
        if digest_file(path, label=f"{label} {key}") != expected_digest:
            fail("EVIDENCE_DIGEST_MISMATCH", f"{label} digest mismatch for {key}")
        entries[key] = path
    if required_keys is not None and set(entries) != required_keys:
        fail(
            "EVIDENCE_SET_MISMATCH",
            f"{label} set mismatch: missing={sorted(required_keys - set(entries))!r}, extra={sorted(set(entries) - required_keys)!r}",
        )
    return entries


EVIDENCE_PRODUCERS = {
    "architecture-report": "tools/check_utiki_architecture.py",
    "typecheck-report": "tools/run_utiki_typecheck.py",
    "python-syntax-report": "tools/verify_modified_python.py",
    "browser-isolation-report": "tools/run_utiki_browser_isolation.py",
    "production-origin-report": "tools/run_utiki_browser_isolation.py",
    "benchmark-report": "tools/benchmarks/compare_utiki.py",
    "checker-exit-nonzero": "tools/run_utiki_negative_probe.py",
}
PERFORMANCE_EVIDENCE_PRODUCERS = {
    "benchmark-report": "tools/benchmarks/verify_utiki_p1_performance.py",
    "browser-isolation-report": "tools/run_utiki_performance_isolation.py",
}
EVIDENCE_LAUNCHER = "tools/run_utiki_evidence.py"
RESOURCE_LAYERS = frozenset({"browser-contract", "browser-isolation", "performance"})
EVIDENCE_INPUT_CONTRACTS: dict[
    tuple[str, str], dict[str, tuple[str, str | None]]
] = {
    ("architecture-types", "architecture-report"): {
        "core-inventory": (
            "repository",
            "docs/02-development/utiki-refactor/core-integration-inventory.md",
        ),
    },
    ("architecture-types", "typecheck-report"): {
        "typed-manifest": (
            "repository",
            "tests/fixtures/utiki/typecheck-p1/manifest.txt",
        ),
        "mypy-config": (
            "repository",
            "tests/fixtures/utiki/typecheck-p1/mypy.ini",
        ),
        "coverage-context": (
            "repository",
            "tests/fixtures/utiki/typecheck-p1/context.json",
        ),
    },
    ("modified-python-syntax", "python-syntax-report"): {
        "run-context": ("artifact", "evidence/run-context.json"),
        "modified-python-manifest": (
            "artifact",
            "evidence/modified-python-manifest.json",
        ),
    },
    ("browser-contract-isolated", "browser-isolation-report"): {
        "isolation-contract": ("repository", "ci/utiki-browser-isolation.json"),
        "execution-manifest": (
            "repository",
            "tests/fixtures/utiki/manifest.json",
        ),
        "image-build": ("artifact", "evidence/image-build.json"),
    },
    ("browser-contract-isolated", "production-origin-report"): {
        "isolation-report": ("artifact", "evidence/browser-isolation-raw.json"),
        "origin-report": ("artifact", "evidence/production-origin-raw.json"),
    },
    ("performance-contract", "browser-isolation-report"): {
        "performance-contract": (
            "repository",
            "ci/utiki-performance-isolation.json",
        ),
        "execution-manifest": (
            "repository",
            "tests/fixtures/utiki/manifest.json",
        ),
        "image-build": ("artifact", "evidence/image-build.json"),
    },
    ("performance-contract", "benchmark-report"): {
        "performance-contract": (
            "repository",
            "ci/utiki-performance-isolation.json",
        ),
        "performance-isolation-report": (
            "artifact",
            "evidence/performance-isolation-raw.json",
        ),
        "raw-benchmark-report": (
            "artifact",
            "evidence/raw-benchmark-report.json",
        ),
        "managed-junit": ("artifact", "evidence/performance.xml"),
        "reporter-result": (
            "artifact",
            "evidence/performance-reporter-result.json",
        ),
    },
}


def _expected_evidence_producer(kind: str, job_id: str) -> str | None:
    if job_id == "performance-contract":
        return PERFORMANCE_EVIDENCE_PRODUCERS.get(kind)
    return EVIDENCE_PRODUCERS.get(kind)


def _expected_input_contract(
    kind: str, job_id: str, phase: str = "P1"
) -> dict[str, tuple[str, str | None]]:
    if kind == "checker-exit-nonzero":
        return {
            "injected-probe": ("artifact", None),
            "probe-registry": (
                "repository",
                f"ci/utiki-{phase.lower()}-negative-probes.json",
            ),
        }
    contract = EVIDENCE_INPUT_CONTRACTS.get((job_id, kind))
    if contract is None:
        fail(
            "INVALID_EVIDENCE_REPORT",
            f"no reviewed input contract for {job_id}/{kind}",
        )
    if job_id == "architecture-types" and kind == "typecheck-report" and phase == "P2":
        return {
            "typed-manifest": ("repository", "ci/utiki-typed-scope.txt"),
            "mypy-config": ("repository", "ci/mypy-utiki.ini"),
        }
    if (
        job_id == "browser-contract-isolated"
        and kind == "browser-isolation-report"
        and phase in {"P2", "P3a"}
    ):
        return {
            name: (
                scope,
                f"tests/fixtures/utiki/manifest-{phase.lower()}.json"
                if name == "execution-manifest" else path,
            )
            for name, (scope, path) in contract.items()
        }
    if job_id == "performance-contract" and phase == "P2":
        phase_inputs = {
            "performance-contract": "ci/utiki-p2-performance-isolation.json",
            "execution-manifest": "tests/fixtures/utiki/manifest-p2.json",
        }
        return {
            name: (scope, phase_inputs.get(name, path))
            for name, (scope, path) in contract.items()
        }
    return contract


def _expected_output_contract(
    kind: str,
    job_id: str,
    evidence_path: Path,
    artifact_root: Path,
) -> dict[str, tuple[str, str]]:
    try:
        relative = evidence_path.relative_to(artifact_root).as_posix()
    except ValueError:
        fail("UNSAFE_PATH", f"{kind} evidence is outside its artifact root")
    pure = PurePosixPath(relative)
    parent = pure.parent

    def sibling(name: str) -> str:
        return (parent / name).as_posix()

    stdout_path = sibling(f"{pure.stem}.stdout")
    stderr_path = sibling(f"{pure.stem}.stderr")
    producer_reports = {
        ("architecture-types", "architecture-report"): stdout_path,
        ("architecture-types", "typecheck-report"): sibling(
            "typecheck-raw.json"
        ),
        ("modified-python-syntax", "python-syntax-report"): sibling(
            "python-syntax-raw.json"
        ),
        ("browser-contract-isolated", "browser-isolation-report"): sibling(
            "browser-isolation-raw.json"
        ),
        ("browser-contract-isolated", "production-origin-report"): sibling(
            "production-origin-verified.json"
        ),
        ("performance-contract", "browser-isolation-report"): sibling(
            "performance-isolation-raw.json"
        ),
        ("performance-contract", "benchmark-report"): sibling(
            "performance-verification-raw.json"
        ),
    }
    if kind == "checker-exit-nonzero":
        producer_report = sibling(f"{pure.stem}.producer.json")
    else:
        producer_report = producer_reports.get((job_id, kind))
        if producer_report is None:
            fail(
                "INVALID_EVIDENCE_REPORT",
                f"no reviewed output contract for {job_id}/{kind}",
            )
    contract = {
        "stdout": ("artifact", stdout_path),
        "stderr": ("artifact", stderr_path),
        "producer-report": ("artifact", producer_report),
    }
    if kind == "typecheck-report":
        contract.update(
            {
                "typecheck-stdout": (
                    "artifact",
                    sibling("typecheck-mypy.stdout"),
                ),
                "typecheck-stderr": (
                    "artifact",
                    sibling("typecheck-mypy.stderr"),
                ),
            }
        )
    return contract


def _repository_file(
    repository_root: Path,
    value: Any,
    expected_digest: Any,
    *,
    label: str,
) -> Path:
    path = safe_relative_path(repository_root, value, label=f"{label}.path")
    digest = require_sha256(expected_digest, label=f"{label}.sha256")
    if digest_file(path, label=label) != digest:
        fail("EVIDENCE_DIGEST_MISMATCH", f"{label} digest mismatch")
    return path


def _validate_evidence_references(
    values: Any,
    *,
    artifact_root: Path,
    repository_root: Path,
    label: str,
) -> dict[str, Path]:
    references: dict[str, Path] = {}
    for index, value in enumerate(require_list(values, label=label)):
        item_label = f"{label}[{index}]"
        item = require_dict(value, label=item_label)
        require_exact_keys(item, {"kind", "scope", "path", "sha256"}, label=item_label)
        kind = require_safe_id(item["kind"], label=f"{item_label}.kind")
        if kind in references:
            fail("DUPLICATE_EVIDENCE", f"{label} duplicates kind {kind!r}")
        scope = require_str(item["scope"], label=f"{item_label}.scope")
        if scope == "repository":
            root = repository_root
        elif scope == "artifact":
            root = artifact_root
        else:
            fail("INVALID_EVIDENCE_REPORT", f"{item_label}.scope is not trusted")
        path = safe_relative_path(root, item["path"], label=f"{item_label}.path")
        expected = require_sha256(item["sha256"], label=f"{item_label}.sha256")
        if digest_file(path, label=item_label) != expected:
            fail("EVIDENCE_DIGEST_MISMATCH", f"{item_label} digest mismatch")
        references[kind] = path
    return references


def _json_output(path: Path, *, label: str) -> dict[str, Any]:
    value, _ = read_json(path, label=label)
    return value


ARCHITECTURE_TARGET_ROOTS = (
    "src/platforms/utiki/domain/",
    "src/platforms/utiki/application/",
    "src/platforms/utiki/adapters/",
)
ARCHITECTURE_TARGET_SINGLE_FILES = {
    "src/platforms/utiki/bootstrap.py",
    "src/utiki_config.py",
    "src/keyword_rules.py",
    "src/utiki_core_services.py",
}
ARCHITECTURE_CURRENT_BOUNDARY_FILES = {
    "src/nodriver_common.py",
    "src/settings.py",
}


def _canonical_json_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _architecture_paths(repository_root: Path) -> list[str]:
    paths: set[str] = set()
    utiki = repository_root / "src" / "platforms" / "utiki"
    if utiki.is_dir():
        for path in utiki.rglob("*.py"):
            if path.is_symlink() or not path.is_file():
                fail("INVALID_EVIDENCE_CONTENT", "architecture source is not a regular file")
            paths.add(path.relative_to(repository_root).as_posix())
    for relative in ARCHITECTURE_TARGET_SINGLE_FILES | ARCHITECTURE_CURRENT_BOUNDARY_FILES:
        path = repository_root / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                fail("INVALID_EVIDENCE_CONTENT", "architecture source is not a regular file")
            paths.add(relative)
    return sorted(paths)


def _is_architecture_target(path: str) -> bool:
    return path in ARCHITECTURE_TARGET_SINGLE_FILES or path.startswith(
        ARCHITECTURE_TARGET_ROOTS
    )


def _validate_architecture_payload(
    payload: dict[str, Any], metadata: dict[str, Any], repository_root: Path
) -> None:
    require_exact_keys(
        payload,
        {
            "schema_version",
            "status",
            "baseline_sha",
            "candidate_sha",
            "repository_root",
            "checked_files",
            "checked_files_sha256",
            "target_files",
            "source_manifest",
            "source_manifest_sha256",
            "findings",
            "failure_reasons",
        },
        label="architecture report",
    )
    if payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        fail("INVALID_EVIDENCE_CONTENT", "architecture producer did not report PASS")
    require_str(
        payload.get("repository_root"),
        label="architecture.repository_root diagnostic",
    )
    canonical_root = repository_root.resolve()
    if payload.get("candidate_sha") != metadata["candidate_sha"]:
        fail("INVALID_EVIDENCE_CONTENT", "architecture report candidate does not match")
    require_git_sha(payload.get("baseline_sha"), label="architecture.baseline_sha")
    expected_paths = _architecture_paths(canonical_root)
    checked = require_list(payload.get("checked_files"), label="architecture.checked_files")
    if checked != expected_paths or not checked:
        fail("INVALID_EVIDENCE_CONTENT", "architecture checked-file set is not exhaustive")
    if payload.get("checked_files_sha256") != _canonical_json_digest(expected_paths):
        fail("INVALID_EVIDENCE_CONTENT", "architecture checked-file digest is invalid")
    expected_targets = [path for path in expected_paths if _is_architecture_target(path)]
    if payload.get("target_files") != expected_targets:
        fail("INVALID_EVIDENCE_CONTENT", "architecture target-file set is not exhaustive")
    expected_manifest = [
        {"path": relative, "sha256": digest_file(canonical_root / relative, label=relative)}
        for relative in expected_paths
    ]
    manifest = require_list(payload.get("source_manifest"), label="architecture.source_manifest")
    if manifest != expected_manifest:
        fail("INVALID_EVIDENCE_CONTENT", "architecture source manifest differs from candidate")
    if payload.get("source_manifest_sha256") != _canonical_json_digest(expected_manifest):
        fail("INVALID_EVIDENCE_CONTENT", "architecture source manifest digest is invalid")
    if require_list(payload.get("findings"), label="architecture.findings"):
        fail("INVALID_EVIDENCE_CONTENT", "architecture report contains findings")
    if require_list(payload.get("failure_reasons"), label="architecture.failure_reasons"):
        fail("INVALID_EVIDENCE_CONTENT", "architecture report contains failure reasons")


def _validate_typecheck_payload(
    payload: dict[str, Any],
    inputs: dict[str, Path],
    outputs: dict[str, Path],
    metadata: dict[str, Any],
    repository_root: Path,
) -> None:
    phase = metadata["phase"]
    if phase not in {"P1", "P2"}:
        fail("INVALID_EVIDENCE_CONTENT", "typecheck phase is not supported")
    base_keys = {
        "schema_version", "status", "files", "required_files",
        "effective_flags", "mypy_exit_code", "failure_reasons",
        "config_sha256", "manifest_sha256", "stdout_sha256", "stderr_sha256",
    }
    require_exact_keys(
        payload,
        base_keys | ({
            "coverage_kind",
            "production_scope_status",
            "legacy_seed_scope_status",
            "coverage_context_path",
            "coverage_context_sha256",
        } if phase == "P1" else {"phase"}),
        label="typecheck report",
    )
    if payload.get("schema_version") != 1 or payload.get("status") != "PASS":
        fail("INVALID_EVIDENCE_CONTENT", "typecheck producer did not report PASS")
    if payload.get("mypy_exit_code") != 0:
        fail("INVALID_EVIDENCE_CONTENT", "typecheck report did not observe mypy exit 0")
    if phase == "P2" and payload.get("phase") != "P2":
        fail("INVALID_EVIDENCE_CONTENT", "production typecheck phase is invalid")
    files = require_list(payload.get("files"), label="typecheck.files")
    required = require_list(payload.get("required_files"), label="typecheck.required_files")
    if any(type(item) is not str for item in files + required):
        fail("INVALID_EVIDENCE_CONTENT", "typecheck report has invalid source paths")
    if not files or not set(required).issubset(files):
        fail("INVALID_EVIDENCE_CONTENT", "typecheck scope is empty or incomplete")
    flags = require_list(payload.get("effective_flags"), label="typecheck.effective_flags")
    if "--strict" not in flags or "--no-incremental" not in flags:
        fail("INVALID_EVIDENCE_CONTENT", "typecheck strict flags are absent")
    manifest_path = inputs["typed-manifest"]
    config_path = inputs["mypy-config"]
    if phase == "P1":
        if (
            payload.get("coverage_kind") != "controlled-runner-contract"
            or payload.get("production_scope_status") != "NOT_CLAIMED"
            or payload.get("legacy_seed_scope_status") != "KNOWN_FAILING_DEBT"
        ):
            fail(
                "INVALID_EVIDENCE_CONTENT",
                "typecheck report misrepresents the controlled fixture as production coverage",
            )
        context_path = inputs["coverage-context"]
        if digest_file(context_path, label="typecheck coverage context") != require_sha256(
            payload.get("coverage_context_sha256"), label="typecheck.coverage_context_sha256"
        ):
            fail("EVIDENCE_DIGEST_MISMATCH", "typecheck coverage context digest mismatch")
        require_str(payload.get("coverage_context_path"), label="typecheck.coverage_context_path")
    else:
        if len(files) != len(set(files)) or len(required) != len(set(required)):
            fail("INVALID_EVIDENCE_CONTENT", "production typed report has invalid paths")
        try:
            manifest_files = [
                line.strip() for line in manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        except (OSError, UnicodeError):
            fail("INVALID_EVIDENCE_CONTENT", "production typed manifest is unreadable")
        if not manifest_files or len(manifest_files) != len(set(manifest_files)):
            fail("INVALID_EVIDENCE_CONTENT", "production typed manifest is empty or duplicated")
        required_paths: set[str] = set()
        for relative_root in (
            "src/platforms/utiki/domain",
            "src/platforms/utiki/application",
            "src/platforms/utiki/adapters",
        ):
            directory = repository_root / relative_root
            if directory.is_symlink():
                fail("INVALID_EVIDENCE_CONTENT", "production typed root is a symlink")
            if directory.is_dir():
                required_paths.update(
                    path.relative_to(repository_root).as_posix()
                    for path in directory.rglob("*.py")
                )
        for relative in (
            "src/platforms/utiki/auth.py",
            "src/platforms/utiki/profiles.py",
            "src/platforms/utiki/selection.py",
            "src/platforms/utiki/bootstrap.py",
            "src/utiki_config.py",
            "src/keyword_rules.py",
            "src/utiki_core_services.py",
            "tools/migrations/convert_utiki_config.py",
        ):
            if (repository_root / relative).exists() or (repository_root / relative).is_symlink():
                required_paths.add(relative)
        for relative in set(manifest_files) | required_paths:
            path = safe_relative_path(
                repository_root, relative, label="production typed source"
            )
            if path.suffix != ".py" or not path.is_file():
                fail("INVALID_EVIDENCE_CONTENT", f"production typed source is missing: {relative}")
        if files != manifest_files or set(required) != required_paths:
            fail("INVALID_EVIDENCE_CONTENT", "production typed scope differs from repository sources")
    if digest_file(manifest_path, label="typecheck typed manifest") != require_sha256(
        payload.get("manifest_sha256"), label="typecheck.manifest_sha256"
    ):
        fail("EVIDENCE_DIGEST_MISMATCH", "typecheck manifest digest mismatch")
    if digest_file(config_path, label="typecheck mypy config") != require_sha256(
        payload.get("config_sha256"), label="typecheck.config_sha256"
    ):
        fail("EVIDENCE_DIGEST_MISMATCH", "typecheck config digest mismatch")
    for kind, field in (
        ("typecheck-stdout", "stdout_sha256"),
        ("typecheck-stderr", "stderr_sha256"),
    ):
        if digest_file(outputs[kind], label=f"typecheck {kind}") != require_sha256(
            payload.get(field), label=f"typecheck.{field}"
        ):
            fail("EVIDENCE_DIGEST_MISMATCH", f"typecheck {kind} digest mismatch")
    if require_list(payload.get("failure_reasons"), label="typecheck.failure_reasons"):
        fail("INVALID_EVIDENCE_CONTENT", "typecheck report contains failure reasons")


def _validate_syntax_payload(payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1 or payload.get("overall_status") != "PASS":
        fail("INVALID_EVIDENCE_CONTENT", "syntax producer did not report PASS")
    if payload.get("candidate_sha") != metadata["candidate_sha"] or payload.get("exit_code") != 0:
        fail("INVALID_EVIDENCE_CONTENT", "syntax report context/exit is invalid")
    compiled = require_int(payload.get("compile_count"), label="syntax.compile_count", minimum=1)
    files = require_list(payload.get("files"), label="syntax.files")
    passed = [item for item in files if isinstance(item, dict) and item.get("status") == "PASS"]
    if len(passed) != compiled or len(passed) != len(files):
        fail("INVALID_EVIDENCE_CONTENT", "syntax report compile results are incomplete")
    if require_list(payload.get("failure_reasons"), label="syntax.failure_reasons"):
        fail("INVALID_EVIDENCE_CONTENT", "syntax report contains failure reasons")


def _validate_payload_execution_results(
    payload: dict[str, Any], expected_keys: set[ExecutionKey], *, label: str
) -> None:
    actual: set[ExecutionKey] = set()
    for index, value in enumerate(
        require_list(payload.get("execution_results"), label=f"{label}.execution_results")
    ):
        result = require_dict(value, label=f"{label}.execution_results[{index}]")
        require_exact_keys(
            result,
            {"case_id", "obligation_id", "variant_id", "job_id", "target", "status"},
            label=f"{label}.execution_results[{index}]",
        )
        if result["status"] != "PASS":
            fail("INVALID_EVIDENCE_CONTENT", f"{label} execution result is not PASS")
        key = ExecutionKey(
            require_safe_id(result["case_id"], label=f"{label}.case_id"),
            require_safe_id(result["obligation_id"], label=f"{label}.obligation_id"),
            require_safe_id(result["variant_id"], label=f"{label}.variant_id"),
            require_safe_id(result["job_id"], label=f"{label}.job_id"),
            require_safe_id(result["target"], label=f"{label}.target"),
        )
        if key in actual:
            fail("DUPLICATE_EVIDENCE", f"{label} execution result is duplicated")
        actual.add(key)
    if actual != expected_keys:
        fail("EVIDENCE_SET_MISMATCH", f"{label} execution results do not cover the exact manifest keys")


def _validate_performance_isolation_payload(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    expected_keys: set[ExecutionKey],
    producer_report: Path,
    inputs: dict[str, Path],
    input_scopes: dict[str, str],
    repository_root: Path,
) -> None:
    require_exact_keys(
        payload,
        {
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
        },
        label="performance isolation report",
    )
    if (
        payload["schema_version"] != 1
        or payload["report_kind"] != "utiki-browser-isolation-launcher"
        or payload["status"] != "PASS"
        or require_list(payload["reasons"], label="performance isolation reasons")
    ):
        fail("INVALID_EVIDENCE_CONTENT", "performance isolation launcher did not pass cleanly")
    for field in (
        "run_id",
        "attempt",
        "phase",
        "job_id",
        "target",
        "candidate_sha",
        "catalog_sha256",
        "execution_manifest_sha256",
    ):
        if payload[field] != metadata[field]:
            fail("STALE_EVIDENCE", f"performance isolation top-level {field} mismatch")
    context = require_dict(payload["context"], label="performance isolation context")
    require_exact_keys(
        context,
        {"run_id", "attempt", "phase", "job_id", "target", "candidate_sha", "catalog_sha256"},
        label="performance isolation context",
    )
    for field in ("run_id", "attempt", "phase", "job_id", "target", "candidate_sha", "catalog_sha256"):
        if context[field] != metadata[field]:
            fail("STALE_EVIDENCE", f"performance isolation context {field} mismatch")
    _validate_payload_execution_results(payload, expected_keys, label="performance isolation")
    runtime = require_dict(payload["runtime_evidence"], label="performance runtime")
    container = require_dict(runtime.get("isolated_container"), label="performance isolated container")
    if (
        container.get("network_mode") != "none"
        or container.get("privileged") is not False
        or container.get("readonly_rootfs") is not True
        or "ALL" not in require_list(container.get("cap_drop"), label="performance cap_drop")
    ):
        fail("INVALID_EVIDENCE_CONTENT", "performance container isolation is invalid")
    peer = require_dict(payload["peer_audit"], label="performance peer audit")
    if (
        peer.get("outbound_count") != 0
        or peer.get("forbidden_paths_observed") != []
        or peer.get("forbidden_events_observed") != []
        or peer.get("positive_control_counts")
        != {"tcp_ipv4": 1, "tcp_ipv6": 1, "udp_dns": 1}
    ):
        fail("INVALID_EVIDENCE_CONTENT", "performance peer audit is invalid")
    manifest = require_dict(payload["execution_manifest"], label="performance manifest")
    if manifest.get("sha256") != metadata["execution_manifest_sha256"]:
        fail("EVIDENCE_DIGEST_MISMATCH", "performance manifest digest mismatch")
    contract = require_dict(payload["contract"], label="performance contract")
    require_sha256(contract.get("sha256"), label="performance contract digest")
    image = require_dict(payload["image"], label="performance image")
    image_id = require_str(image.get("id"), label="performance image id")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        fail("INVALID_EVIDENCE_CONTENT", "performance image is not content addressed")
    build = require_dict(payload["image_build_evidence"], label="performance image build")
    require_exact_keys(
        build,
        {"base_image", "image_id", "inputs", "path", "sha256"},
        label="performance image build reference",
    )
    envelope_build_path = inputs.get("image-build")
    if envelope_build_path is None or input_scopes.get("image-build") != "artifact":
        fail(
            "INVALID_EVIDENCE_CONTENT",
            "performance image build is not an artifact-scoped envelope input",
        )
    require_str(build.get("path"), label="performance image build diagnostic path")
    build_digest = digest_file(envelope_build_path, label="performance image build")
    if build_digest != require_sha256(
        build.get("sha256"), label="performance image build digest"
    ):
        fail("EVIDENCE_DIGEST_MISMATCH", "performance image build evidence mismatch")
    build_payload = _json_output(envelope_build_path, label="performance image build")
    require_exact_keys(
        build_payload,
        {
            "schema_version",
            "evidence_kind",
            "base_image",
            "image_id",
            "inputs",
            "build_completed_before_isolation",
            "isolation_phase_pulled_or_built",
        },
        label="performance image build evidence",
    )
    if (
        build_payload["schema_version"] != 1
        or build_payload["evidence_kind"] != "utiki-isolation-image-build"
        or build_payload["build_completed_before_isolation"] is not True
        or build_payload["isolation_phase_pulled_or_built"] is not False
    ):
        fail("INVALID_EVIDENCE_CONTENT", "performance image build lifecycle is invalid")
    base_image = require_str(
        build_payload["base_image"], label="performance image build base image"
    )
    if BASE_IMAGE_RE.fullmatch(base_image) is None:
        fail(
            "INVALID_EVIDENCE_CONTENT",
            "performance image build base image is not repository-digest pinned",
        )
    evidence_image_id = require_str(
        build_payload["image_id"], label="performance image build image id"
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_image_id) is None:
        fail(
            "INVALID_EVIDENCE_CONTENT",
            "performance image build image id is not content-addressed",
        )
    build_inputs = require_list(build_payload["inputs"], label="performance image build inputs")
    required_build_inputs = {
        "ci/utiki-isolation/Dockerfile",
        "requirement.txt",
        "requirements-dev.txt",
    }
    seen_build_inputs: set[str] = set()
    for index, value in enumerate(build_inputs):
        item = require_dict(value, label=f"performance image build inputs[{index}]")
        require_exact_keys(
            item,
            {"path", "sha256"},
            label=f"performance image build inputs[{index}]",
        )
        relative = require_str(
            item["path"], label=f"performance image build inputs[{index}].path"
        )
        if relative in seen_build_inputs:
            fail("DUPLICATE_EVIDENCE", "performance image build input is duplicated")
        expected_input_digest = require_sha256(
            item["sha256"], label=f"performance image build inputs[{index}].sha256"
        )
        input_path = safe_relative_path(
            repository_root,
            relative,
            label=f"performance image build inputs[{index}].path",
        )
        if not input_path.is_file() or digest_file(
            input_path, label=f"performance image build input {relative}"
        ) != expected_input_digest:
            fail(
                "EVIDENCE_DIGEST_MISMATCH",
                f"performance image build input digest mismatch: {relative}",
            )
        seen_build_inputs.add(relative)
    if seen_build_inputs != required_build_inputs:
        fail(
            "EVIDENCE_SET_MISMATCH",
            "performance image build inputs do not equal the required repository files",
        )
    if (
        build["base_image"] != build_payload["base_image"]
        or build["image_id"] != build_payload["image_id"]
        or build["inputs"] != build_inputs
        or build_payload["image_id"] != image_id
    ):
        fail(
            "INVALID_EVIDENCE_CONTENT",
            "performance image build reference differs from envelope evidence",
        )
    sibling_digests = {
        "inner_report_sha256": "inner-performance-report.json",
        "benchmark_report_sha256": "raw-benchmark-report.json",
        "junit_sha256": "performance.xml",
        "reporter_result_sha256": "performance-reporter-result.json",
    }
    for field, filename in sibling_digests.items():
        sibling = producer_report.parent / filename
        expected_digest = require_sha256(payload[field], label=f"performance {field}")
        if not sibling.is_file() or digest_file(sibling, label=f"performance {filename}") != expected_digest:
            fail("EVIDENCE_DIGEST_MISMATCH", f"performance output digest mismatch: {filename}")


def _validate_browser_payload(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    expected_keys: set[ExecutionKey],
    producer_report: Path,
    inputs: dict[str, Path],
    input_scopes: dict[str, str],
    repository_root: Path,
) -> None:
    if metadata["job_id"] == "performance-contract":
        _validate_performance_isolation_payload(
            payload,
            metadata,
            expected_keys,
            producer_report,
            inputs,
            input_scopes,
            repository_root,
        )
        return
    if (
        payload.get("schema_version") != 1
        or payload.get("report_kind") != "utiki-browser-isolation-launcher"
        or payload.get("status") != "PASS"
        or payload.get("run_id") != metadata["run_id"]
        or payload.get("candidate_sha") != metadata["candidate_sha"]
        or payload.get("job_id") != metadata["job_id"]
        or payload.get("target") != metadata["target"]
        or payload.get("phase") != metadata["phase"]
        or payload.get("attempt") != metadata["attempt"]
        or payload.get("catalog_sha256") != metadata["catalog_sha256"]
        or payload.get("execution_manifest_sha256") != metadata["execution_manifest_sha256"]
    ):
        fail("INVALID_EVIDENCE_CONTENT", "browser isolation report context/status is invalid")
    runtime = require_dict(payload.get("runtime_evidence"), label="browser.runtime_evidence")
    peer = require_dict(payload.get("peer_audit"), label="browser.peer_audit")
    if not runtime or not peer:
        fail("INVALID_EVIDENCE_CONTENT", "browser isolation lacks runtime/process evidence")
    require_sha256(payload.get("inner_report_sha256"), label="browser.inner_report_sha256")
    require_sha256(payload.get("junit_sha256"), label="browser.junit_sha256")


def _validate_origin_payload(payload: dict[str, Any], metadata: dict[str, Any]) -> None:
    if (
        payload.get("schema_version") != 1
        or payload.get("status") != "PASS"
        or payload.get("run_id") != metadata["run_id"]
        or payload.get("candidate_sha") != metadata["candidate_sha"]
        or payload.get("interception_installed") is not True
        or payload.get("classifier_patched") is not False
        or payload.get("live_authorization_used") is not False
    ):
        fail("INVALID_EVIDENCE_CONTENT", "production-origin contract is invalid")
    if require_int(payload.get("fulfilled_requests"), label="origin.fulfilled_requests", minimum=1) < 1:
        fail("INVALID_EVIDENCE_CONTENT", "production-origin report observed no fulfilled request")
    require_str(payload.get("origin"), label="origin.origin")
    require_str(payload.get("path"), label="origin.path")


def _validate_benchmark_payload(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    expected_keys: set[ExecutionKey],
    inputs: dict[str, Path],
) -> None:
    if metadata["job_id"] == "performance-contract":
        phase = metadata["phase"]
        require_exact_keys(
            payload,
            {
                "schema_version",
                "report_kind",
                "status",
                "context",
                "execution_results",
                "method",
                "hard_egress_isolation",
                "digests",
                "source",
                "browser",
                "threshold_policy",
            } | ({"hot_path"} if phase == "P2" else set()),
            label="verified performance report",
        )
        if (
            payload["schema_version"] != 1
            or payload["report_kind"] != f"utiki-{phase.lower()}-performance-verification"
            or payload["status"] != "PASS"
        ):
            fail("INVALID_EVIDENCE_CONTENT", "verified performance report did not pass")
        context = require_dict(payload["context"], label="performance verification context")
        for field in ("run_id", "attempt", "phase", "job_id", "target", "candidate_sha", "catalog_sha256"):
            if context.get(field) != metadata[field]:
                fail("STALE_EVIDENCE", f"performance verification context {field} mismatch")
        _validate_payload_execution_results(payload, expected_keys, label="performance verification")
        isolation = require_dict(payload["hard_egress_isolation"], label="performance hard isolation")
        if (
            isolation.get("verified") is not True
            or isolation.get("mode") != "linux-container-network-none"
            or isolation.get("peer_outbound_count") != 0
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                require_str(isolation.get("image_id"), label="performance image id"),
            )
            is None
        ):
            fail("INVALID_EVIDENCE_CONTENT", "performance verification lacks hard isolation")
        require_sha256(
            isolation.get("image_build_evidence_sha256"),
            label="performance image build evidence digest",
        )
        digests = require_dict(payload["digests"], label="performance digests")
        required_input_digests = {
            "performance-contract": "contract_sha256",
            "performance-isolation-report": None,
            "raw-benchmark-report": "raw_benchmark_report_sha256",
            "managed-junit": "junit_sha256",
            "reporter-result": "reporter_result_sha256",
        }
        for input_kind, digest_name in required_input_digests.items():
            input_path = inputs.get(input_kind)
            if input_path is None:
                fail("INVALID_EVIDENCE_CONTENT", f"performance verifier lacks input {input_kind}")
            actual_digest = digest_file(input_path, label=f"performance input {input_kind}")
            expected_digest = (
                isolation.get("isolation_report_sha256")
                if digest_name is None
                else digests.get(digest_name)
            )
            if actual_digest != expected_digest:
                fail("EVIDENCE_DIGEST_MISMATCH", f"performance verifier input mismatch: {input_kind}")
        if digests.get("execution_manifest_sha256") != metadata["execution_manifest_sha256"]:
            fail("EVIDENCE_DIGEST_MISMATCH", "performance execution manifest digest mismatch")
        source = require_dict(payload["source"], label="performance source")
        require_exact_keys(
            source,
            {"run", "parameters", "case_results", "metrics"},
            label="performance source",
        )
        run = require_dict(source.get("run"), label="performance source run")
        git = require_dict(run.get("git"), label="performance source git")
        if git.get("candidate_sha") != metadata["candidate_sha"] or git.get("dirty") is not False:
            fail("INVALID_EVIDENCE_CONTENT", "performance source git context is invalid")
        if run.get("hard_egress_isolation") is not True:
            fail("INVALID_EVIDENCE_CONTENT", "performance source lacks active hard isolation")
        os_value = require_dict(run.get("os"), label="performance source os")
        if os_value.get("system") != "Linux" or os_value.get("machine") not in {
            "x86_64",
            "amd64",
        }:
            fail("INVALID_EVIDENCE_CONTENT", "performance source ran on the wrong platform")
        parameters = require_dict(source.get("parameters"), label="performance parameters")
        if (
            parameters.get("mode") != "all"
            or parameters.get("seed") != 1701
            or parameters.get("warmup_per_batch") != 20
            or parameters.get("warm_samples_total") != 300
            or parameters.get("batches") != 3
            or parameters.get("cold_samples_per_case") != 3
            or parameters.get("event_loop_samples_total") != 300
        ):
            fail("INVALID_EVIDENCE_CONTENT", "performance sample counts are invalid")
        expected_case_results = {
            "area": "b19b4557991c69cc677e4860efdc87dcb7bba4c989df70ab0db24deedf91041d",
            "date": "dc7d892478f42f861c82097ea07a7db7ffe31b48fce9387c309826a2968eecff",
            "quantity": "8f2cdf62b1266ab3025343419058332e6b1c2c72bb3e3a1c01c45b18f45be8b3",
            "seat": "99f3513d1481a78f1c47b890cade7f212257b92be19ea9432b568de65d14f342",
        }
        if source.get("case_results") != expected_case_results:
            fail("INVALID_EVIDENCE_CONTENT", "performance semantic outputs differ from P0")
        metrics = require_dict(source.get("metrics"), label="performance source metrics")
        source_cases = ("date", "area", "seat", "quantity")
        expected_metrics = {"event_loop.scheduling_lag"}
        for case in source_cases:
            expected_metrics.update(
                {
                    f"{case}.cold_import",
                    f"{case}.cold_first_operation",
                    f"{case}.warm_parse_select_plan",
                }
            )
        if set(metrics) != expected_metrics:
            fail("INVALID_EVIDENCE_CONTENT", "performance source metrics are incomplete")
        for name, metric_value in metrics.items():
            metric = require_dict(metric_value, label=f"performance metric {name}")
            require_exact_keys(
                metric,
                {
                    "unit",
                    "sample_count",
                    "samples_ns",
                    "failure_count",
                    "failures",
                    "batch_sizes",
                    "median_ns",
                    "p95_ns",
                    "p99_ns",
                    "min_ns",
                    "max_ns",
                },
                label=f"performance metric {name}",
            )
            if metric.get("failure_count") != 0 or metric.get("failures") != []:
                fail("INVALID_EVIDENCE_CONTENT", f"performance metric {name} contains failures")
            samples = require_list(
                metric.get("samples_ns"), label=f"performance metric {name} samples"
            )
            expected_count = (
                300
                if name == "event_loop.scheduling_lag"
                or name.endswith(".warm_parse_select_plan")
                else 3
            )
            if (
                metric.get("unit") != "ns"
                or metric.get("sample_count") != expected_count
                or len(samples) != expected_count
                or any(type(sample) is not int or sample < 0 for sample in samples)
                or metric.get("min_ns") != min(samples)
                or metric.get("max_ns") != max(samples)
            ):
                fail("INVALID_EVIDENCE_CONTENT", f"performance metric {name} samples are incomplete")
        browser = require_dict(payload["browser"], label="performance browser")
        if (
            browser.get("origin") != "https://tix.ctbcsports.com"
            or browser.get("path") != "/BROTHERS/UTK0201_"
            or browser.get("classifier") != "DATE"
            or browser.get("interception_stage") != "Request"
            or browser.get("interception_installed") is not True
            or browser.get("classifier_patched") is not False
            or browser.get("allowlist_entries") != 1
            or browser.get("navigation_fulfilled_requests") != 1
            or browser.get("rejected_unknown_requests") != 0
            or browser.get("live_authorization_used") is not False
        ):
            fail("INVALID_EVIDENCE_CONTENT", "performance browser contract is invalid")
        require_sha256(
            browser.get("semantic_output_sha256"),
            label="performance browser semantic output digest",
        )
        command_labels = require_dict(
            browser.get("command_labels"), label="performance browser command labels"
        )
        if not command_labels:
            fail("INVALID_EVIDENCE_CONTENT", "performance browser command labels are absent")
        browser_metrics = require_dict(
            browser.get("metrics"), label="performance browser metrics"
        )
        if set(browser_metrics) != {
            "observation_latency",
            "cdp_commands_per_observation",
            "ticketing_requests_per_observation",
        }:
            fail("INVALID_EVIDENCE_CONTENT", "performance browser metrics are incomplete")
        for name, metric_value in browser_metrics.items():
            metric = require_dict(metric_value, label=f"performance browser metric {name}")
            require_exact_keys(
                metric,
                {
                    "unit",
                    "sample_count",
                    "samples",
                    "failure_count",
                    "failures",
                    "median",
                    "p95",
                    "p99",
                    "min",
                    "max",
                },
                label=f"performance browser metric {name}",
            )
            samples = require_list(
                metric.get("samples"), label=f"performance browser metric {name} samples"
            )
            if (
                metric.get("sample_count") != 30
                or len(samples) != 30
                or metric.get("failure_count") != 0
                or metric.get("failures") != []
                or any(type(sample) is not int or sample < 0 for sample in samples)
            ):
                fail("INVALID_EVIDENCE_CONTENT", f"performance browser metric {name} is invalid")
        if any(
            sample <= 0
            for sample in browser_metrics["cdp_commands_per_observation"]["samples"]
        ) or any(
            sample != 0
            for sample in browser_metrics["ticketing_requests_per_observation"]["samples"]
        ):
            fail("INVALID_EVIDENCE_CONTENT", "performance browser observations are invalid")
        if phase == "P2":
            hot_path = require_dict(payload["hot_path"], label="P2 hot-path")
            require_exact_keys(
                hot_path,
                {"schema_version", "phase", "method", "v07", "v08"},
                label="P2 hot-path",
            )
            if (
                hot_path["schema_version"] != 1
                or hot_path["phase"] != "P2"
                or hot_path["method"] != {"samples": 30, "warmup": 5, "seat_count": 2000}
            ):
                fail("INVALID_EVIDENCE_CONTENT", "P2 hot-path method is invalid")
            v07 = require_dict(hot_path["v07"], label="V07")
            v08 = require_dict(hot_path["v08"], label="V08")
            if (
                v07.get("latency_includes_call_tracing") is not True
                or v07.get("forbidden_calls") != {
                    "parse_profile_json": 0,
                    "load_current_policy": 0,
                    "inspect_upgrade_input": 0,
                    "apply_current_policy_reload": 0,
                    "convert_profile_mapping": 0,
                    "json.loads": 0,
                    "hash": 0,
                    "hashlib.new": 0,
                    "sha256": 0,
                    "sha1": 0,
                    "md5": 0,
                    "blake2b": 0,
                }
                or v08.get("seat_count") != 2000
                or v08.get("latency_includes_call_tracing") is not True
                or v08.get("forbidden_calls") != {
                    "deepcopy": 0, "copy": 0, "asdict": 0,
                    "SeatRow.__init__": 0,
                    "SeatSnapshot.__init__": 0,
                    "TicketTypeRow.__init__": 0,
                }
                or type(v08.get("tracked_objects")) is not int
                or v08["tracked_objects"] < 2000
                or type(v08.get("max_reference_delta")) is not int
                or v08["max_reference_delta"] != 0
                or type(v08.get("pending_tasks_before")) is not int
                or v08["pending_tasks_before"] < 1
                or v08.get("pending_tasks_after") != v08["pending_tasks_before"]
            ):
                fail("INVALID_EVIDENCE_CONTENT", "P2 hot-path invariants failed")
            for metric_name, metric in (
                ("V07 latency", v07.get("latency")),
                ("V08 valid latency", v08.get("valid_latency")),
                ("V08 invalid latency", v08.get("invalid_latency")),
            ):
                item = require_dict(metric, label=metric_name)
                samples = require_list(item.get("samples"), label=f"{metric_name} samples")
                if (
                    item.get("unit") != "ns"
                    or item.get("sample_count") != 30
                    or len(samples) != 30
                    or item.get("failure_count") != 0
                    or item.get("failures") != []
                    or any(type(sample) is not int or sample < 0 for sample in samples)
                ):
                    fail("INVALID_EVIDENCE_CONTENT", f"{metric_name} samples are invalid")
        return
    if (
        payload.get("schema_version") != 1
        or payload.get("comparison") != "utiki-source"
        or payload.get("comparable") is not True
    ):
        fail("INVALID_EVIDENCE_CONTENT", "benchmark comparison is not comparable")
    metrics = require_dict(payload.get("metrics"), label="benchmark.metrics")
    if not metrics:
        fail("INVALID_EVIDENCE_CONTENT", "benchmark comparison has no metrics")
    for name, value in metrics.items():
        metric = require_dict(value, label=f"benchmark.metrics.{name}")
        samples = require_dict(metric.get("sample_count"), label=f"benchmark.metrics.{name}.sample_count")
        failures = require_dict(metric.get("failures"), label=f"benchmark.metrics.{name}.failures")
        if require_int(samples.get("baseline"), label="benchmark baseline samples", minimum=1) < 1:
            fail("INVALID_EVIDENCE_CONTENT", "benchmark baseline has no samples")
        if require_int(samples.get("candidate"), label="benchmark candidate samples", minimum=1) < 1:
            fail("INVALID_EVIDENCE_CONTENT", "benchmark candidate has no samples")
        if failures.get("baseline") != 0 or failures.get("candidate") != 0:
            fail("INVALID_EVIDENCE_CONTENT", "benchmark contains failed samples")


def _validate_positive_payload(
    kind: str,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    expected_keys: set[ExecutionKey],
    producer_report: Path,
    inputs: dict[str, Path],
    outputs: dict[str, Path],
    repository_root: Path,
    input_scopes: dict[str, str],
) -> None:
    validators = {
        "architecture-report": lambda: _validate_architecture_payload(
            payload, metadata, repository_root
        ),
        "typecheck-report": lambda: _validate_typecheck_payload(
            payload, inputs, outputs, metadata, repository_root
        ),
        "python-syntax-report": lambda: _validate_syntax_payload(payload, metadata),
        "browser-isolation-report": lambda: _validate_browser_payload(
            payload,
            metadata,
            expected_keys,
            producer_report,
            inputs,
            input_scopes,
            repository_root,
        ),
        "production-origin-report": lambda: _validate_origin_payload(payload, metadata),
        "benchmark-report": lambda: _validate_benchmark_payload(
            payload, metadata, expected_keys, inputs
        ),
    }
    validator = validators.get(kind)
    if validator is None:
        fail("UNKNOWN_EVIDENCE_KIND", f"unsupported evidence kind: {kind}")
    validator()


def _validate_negative_payload(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    expected_key: ExecutionKey,
    registry_entry: dict[str, Any],
    *,
    envelope_observed_exit: int,
    probe_request: dict[str, Any],
    probe_input_path: Path,
    registry_path: Path,
    envelope_outputs: dict[str, Path],
    repository_root: Path,
) -> None:
    require_exact_keys(
        payload,
        {
            "schema_version",
            "report_kind",
            "key",
            "candidate_sha",
            "environment",
            "mutation",
            "request_sha256",
            "registry_sha256",
            "child",
            "stdout_sha256",
            "stderr_sha256",
            "semantic_output",
            "observed_exit_code",
            "rejection_token",
            "isolation_proof",
            "status",
        },
        label="negative producer report",
    )
    if (
        payload["schema_version"] != NEGATIVE_TRANSCRIPT_SCHEMA_VERSION
        or payload["report_kind"] != "utiki-negative-probe-transcript"
        or payload["status"] != "EXPECTED_REJECTION"
        or payload["candidate_sha"] != metadata["candidate_sha"]
    ):
        fail("INVALID_NEGATIVE_PROBE", "negative producer context/status is invalid")
    key = require_dict(payload["key"], label="negative.key")
    require_exact_keys(
        key,
        {"case_id", "obligation_id", "variant_id", "job_id", "target"},
        label="negative.key",
    )
    if tuple(key[name] for name in ("case_id", "obligation_id", "variant_id", "job_id", "target")) != (
        expected_key.case_id,
        expected_key.obligation_id,
        expected_key.variant_id,
        expected_key.job_id,
        expected_key.target,
    ):
        fail("INVALID_NEGATIVE_PROBE", "negative producer key does not match its envelope")
    require_str(payload["mutation"], label="negative.mutation")
    expected_environment = (
        "isolated" if metadata["job_id"] == "browser-contract-isolated" else "host"
    )
    if payload["environment"] != expected_environment:
        fail("INVALID_NEGATIVE_PROBE", "negative probe ran in the wrong environment")
    if (
        payload["environment"] != registry_entry["environment"]
        or payload["mutation"] != registry_entry["mutation"]
        or payload["rejection_token"] != registry_entry["rejection_token"]
    ):
        fail("INVALID_NEGATIVE_PROBE", "negative transcript differs from reviewed registry")

    child = require_dict(payload["child"], label="negative.child")
    require_exact_keys(
        child,
        {"kind", "producer", "producer_sha256"},
        label="negative.child",
    )
    child_kind = child["kind"]
    child_producer = require_str(child["producer"], label="negative.child.producer")
    if (
        child_kind != registry_entry["child_kind"]
        or child_producer != registry_entry["child_producer"]
    ):
        fail("INVALID_NEGATIVE_PROBE", "negative child differs from reviewed registry")
    if child_kind == "repository-python":
        _repository_file(
            repository_root,
            child_producer,
            child["producer_sha256"],
            label="negative.child.producer",
        )
    elif child_kind == "pytest-module":
        expected_digest = hashlib.sha256(b"python-isolated-module:pytest").hexdigest()
        if child_producer != "pytest" or child["producer_sha256"] != expected_digest:
            fail("INVALID_NEGATIVE_PROBE", "negative pytest child identity is invalid")
    else:
        fail("INVALID_NEGATIVE_PROBE", "negative child kind is untrusted")
    if digest_file(probe_input_path, label="negative request") != require_sha256(
        payload["request_sha256"], label="negative.request_sha256"
    ):
        fail("EVIDENCE_DIGEST_MISMATCH", "negative request digest mismatch")
    if digest_file(registry_path, label="negative registry") != require_sha256(
        payload["registry_sha256"], label="negative.registry_sha256"
    ):
        fail("EVIDENCE_DIGEST_MISMATCH", "negative registry digest mismatch")
    try:
        validate_request_contract(
            registry_entry,
            probe_request,
            probe_root=probe_input_path.parent.resolve(),
            repository_root=repository_root,
            phase=metadata["phase"],
        )
    except NegativeContractError as error:
        fail("INVALID_NEGATIVE_PROBE", str(error))

    try:
        expected_semantic = probe_semantic_output(
            registry_entry,
            probe_request,
            probe_root=probe_input_path.parent.resolve(),
        )
    except NegativeContractError as error:
        fail("INVALID_NEGATIVE_PROBE", str(error))
    semantic_value = payload["semantic_output"]
    if expected_semantic is None:
        if semantic_value is not None:
            fail("INVALID_NEGATIVE_PROBE", "unexpected negative semantic output")
    else:
        semantic = require_dict(semantic_value, label="negative.semantic_output")
        require_exact_keys(
            semantic, {"path", "sha256"}, label="negative.semantic_output"
        )
        expected_relative, semantic_path = expected_semantic
        if semantic.get("path") != expected_relative:
            fail("INVALID_NEGATIVE_PROBE", "negative semantic output path differs")
        if digest_file(semantic_path, label="negative semantic output") != require_sha256(
            semantic.get("sha256"), label="negative.semantic_output.sha256"
        ):
            fail("EVIDENCE_DIGEST_MISMATCH", "negative semantic output digest mismatch")
    try:
        validate_probe_output_contract(
            registry_entry,
            probe_request,
            probe_request_path=probe_input_path,
            probe_root=probe_input_path.parent.resolve(),
            require_outputs=True,
        )
    except NegativeContractError as error:
        fail("INVALID_NEGATIVE_PROBE", str(error))

    if set(envelope_outputs) < {"stdout", "stderr"}:
        fail("INVALID_NEGATIVE_PROBE", "negative envelope lacks stdout/stderr")
    for kind in ("stdout", "stderr"):
        expected_digest = require_sha256(
            payload[f"{kind}_sha256"], label=f"negative.{kind}_sha256"
        )
        if digest_file(envelope_outputs[kind], label=f"negative {kind}") != expected_digest:
            fail("EVIDENCE_DIGEST_MISMATCH", f"negative {kind} digest mismatch")
    observed = require_int(payload["observed_exit_code"], label="negative.observed_exit_code")
    if observed == 0 or observed != envelope_observed_exit:
        fail("INVALID_NEGATIVE_PROBE", "negative child/envelope exit codes do not match")
    token = require_str(payload["rejection_token"], label="negative.rejection_token")
    child_text = envelope_outputs["stdout"].read_text(encoding="utf-8", errors="replace")
    child_text += envelope_outputs["stderr"].read_text(encoding="utf-8", errors="replace")
    if token not in child_text:
        fail("INVALID_NEGATIVE_PROBE", "negative child rejection token is absent")
    try:
        validate_probe_execution_contract(
            registry_entry,
            probe_request,
            stdout=envelope_outputs["stdout"].read_bytes(),
            stderr=envelope_outputs["stderr"].read_bytes(),
            exit_code=observed,
        )
    except NegativeContractError as error:
        fail("INVALID_NEGATIVE_PROBE", str(error))

    proof = payload["isolation_proof"]
    if expected_environment == "host":
        if proof is not None:
            fail("INVALID_NEGATIVE_PROBE", "host negative probe claims isolation")
    else:
        proof_value = require_dict(proof, label="negative.isolation_proof")
        require_exact_keys(proof_value, {"path", "sha256"}, label="negative.isolation_proof")
        proof_path = safe_relative_path(
            probe_input_path.parent,
            proof_value["path"],
            label="negative.isolation_proof.path",
        )
        if not proof_path.is_file():
            fail("INVALID_NEGATIVE_PROBE", "negative isolation proof is missing")
        if digest_file(proof_path, label="negative isolation proof") != require_sha256(
            proof_value["sha256"], label="negative.isolation_proof.sha256"
        ):
            fail("EVIDENCE_DIGEST_MISMATCH", "negative isolation proof digest mismatch")
        proof_payload = _json_output(proof_path, label="negative isolation proof")
        if (
            proof_payload.get("status") != "PASS"
            or proof_payload.get("network_mode") != "none"
            or proof_payload.get("candidate_sha") != metadata["candidate_sha"]
        ):
            fail("INVALID_NEGATIVE_PROBE", "negative isolation proof is invalid")


def _validate_evidence_report(
    path: Path,
    kind: str,
    metadata: dict[str, Any],
    expected_keys: set[ExecutionKey],
    catalog: CatalogModel,
    artifact_root: Path,
) -> None:
    report, _ = read_json(path, label=f"{kind} report")
    require_exact_keys(
        report,
        {
            "schema_version",
            "kind",
            "context",
            "producer",
            "launcher",
            "inputs",
            "outputs",
            "expectation",
            "observed_exit_code",
            "status",
            "results",
            "rejection_token",
        },
        label=f"{kind} report",
    )
    if report["schema_version"] != EVIDENCE_SCHEMA_VERSION or report["kind"] != kind:
        fail("INVALID_EVIDENCE_REPORT", f"{kind} report has the wrong schema/kind")
    context = require_dict(report["context"], label=f"{kind}.context")
    require_exact_keys(
        context,
        {
            "run_id",
            "attempt",
            "job_id",
            "phase",
            "target",
            "candidate_sha",
            "catalog_sha256",
            "execution_manifest_sha256",
        },
        label=f"{kind}.context",
    )
    for field in (
        "run_id",
        "attempt",
        "job_id",
        "phase",
        "target",
        "candidate_sha",
        "catalog_sha256",
        "execution_manifest_sha256",
    ):
        if context[field] != metadata[field]:
            fail("STALE_EVIDENCE", f"{kind} context {field} does not match the run")

    producer = require_dict(report["producer"], label=f"{kind}.producer")
    require_exact_keys(producer, {"path", "sha256"}, label=f"{kind}.producer")
    producer_path = require_str(producer["path"], label=f"{kind}.producer.path")
    expected_producer_path = _expected_evidence_producer(kind, metadata["job_id"])
    if expected_producer_path is None or producer_path != expected_producer_path:
        fail("UNTRUSTED_EVIDENCE_PRODUCER", f"{kind} used {producer_path!r}")
    _repository_file(
        catalog.repository_root,
        producer_path,
        producer["sha256"],
        label=f"{kind}.producer",
    )
    launcher = require_dict(report["launcher"], label=f"{kind}.launcher")
    require_exact_keys(launcher, {"path", "sha256"}, label=f"{kind}.launcher")
    if launcher["path"] != EVIDENCE_LAUNCHER:
        fail("UNTRUSTED_EVIDENCE_LAUNCHER", f"{kind} report was not emitted by the formal launcher")
    _repository_file(
        catalog.repository_root,
        launcher["path"],
        launcher["sha256"],
        label=f"{kind}.launcher",
    )
    inputs = _validate_evidence_references(
        report["inputs"],
        artifact_root=artifact_root,
        repository_root=catalog.repository_root,
        label=f"{kind}.inputs",
    )
    outputs = _validate_evidence_references(
        report["outputs"],
        artifact_root=artifact_root,
        repository_root=catalog.repository_root,
        label=f"{kind}.outputs",
    )
    if not inputs:
        fail("INVALID_EVIDENCE_REPORT", f"{kind} report has no bound input")
    if not {"stdout", "stderr"}.issubset(outputs):
        fail("INVALID_EVIDENCE_REPORT", f"{kind} report lacks captured stdout/stderr")
    input_scopes = {
        require_str(item["kind"], label=f"{kind}.inputs.kind"): item["scope"]
        for item in require_list(report["inputs"], label=f"{kind}.inputs")
    }
    raw_inputs = {
        require_str(item["kind"], label=f"{kind}.inputs.kind"): (
            item["scope"],
            item["path"],
        )
        for item in require_list(report["inputs"], label=f"{kind}.inputs")
    }
    expected_inputs = _expected_input_contract(
        kind, metadata["job_id"], metadata["phase"]
    )
    if set(raw_inputs) != set(expected_inputs):
        fail(
            "EVIDENCE_SET_MISMATCH",
            f"{kind} input kinds differ from the reviewed contract",
        )
    for input_kind, (expected_scope, expected_path) in expected_inputs.items():
        actual_scope, actual_path = raw_inputs[input_kind]
        if actual_scope != expected_scope or (
            expected_path is not None and actual_path != expected_path
        ):
            fail(
                "INVALID_EVIDENCE_REPORT",
                f"{kind} input {input_kind!r} differs from the reviewed contract",
            )
    raw_outputs = {
        require_str(item["kind"], label=f"{kind}.outputs.kind"): (
            item["scope"],
            item["path"],
        )
        for item in require_list(report["outputs"], label=f"{kind}.outputs")
    }
    expected_outputs = _expected_output_contract(
        kind,
        metadata["job_id"],
        path,
        artifact_root,
    )
    if raw_outputs != expected_outputs:
        fail(
            "INVALID_EVIDENCE_REPORT",
            f"{kind} outputs differ from the reviewed artifact contract",
        )
    actual_keys: set[ExecutionKey] = set()
    for index, value in enumerate(require_list(report["results"], label=f"{kind}.results")):
        result = require_dict(value, label=f"{kind}.results[{index}]")
        require_exact_keys(
            result,
            {"case_id", "obligation_id", "variant_id", "job_id", "target", "status"},
            label=f"{kind}.results[{index}]",
        )
        if result["status"] != "PASS":
            fail("INVALID_EVIDENCE_CONTENT", f"{kind} result is not PASS")
        key = ExecutionKey(
            require_safe_id(result["case_id"], label=f"{kind}.case_id"),
            require_safe_id(result["obligation_id"], label=f"{kind}.obligation_id"),
            require_safe_id(result["variant_id"], label=f"{kind}.variant_id"),
            require_safe_id(result["job_id"], label=f"{kind}.job_id"),
            require_safe_id(result["target"], label=f"{kind}.target"),
        )
        if key in actual_keys:
            fail("DUPLICATE_EVIDENCE", f"{kind} duplicates execution key: {key}")
        actual_keys.add(key)
    if actual_keys != expected_keys:
        fail(
            "EVIDENCE_SET_MISMATCH",
            f"{kind} key set mismatch: missing={sorted(expected_keys - actual_keys)!r}, extra={sorted(actual_keys - expected_keys)!r}",
        )

    observed_exit = require_int(report["observed_exit_code"], label=f"{kind}.observed_exit_code")
    if report["status"] != "PASS":
        fail("INVALID_EVIDENCE_REPORT", f"{kind} report status is not PASS")
    if kind == "checker-exit-nonzero":
        if len(expected_keys) != 1 or len(actual_keys) != 1:
            fail(
                "NEGATIVE_EVIDENCE_NOT_INDEPENDENT",
                "each negative execution key requires its own command/input/output/exit transcript",
            )
        if report["expectation"] != "NONZERO" or observed_exit == 0:
            fail("INVALID_NEGATIVE_PROBE", "negative probe did not observe a nonzero process exit")
        token = require_str(report["rejection_token"], label="negative probe rejection_token")
        combined = outputs["stdout"].read_text(encoding="utf-8", errors="replace") + outputs[
            "stderr"
        ].read_text(encoding="utf-8", errors="replace")
        if token not in combined:
            fail("INVALID_NEGATIVE_PROBE", "negative probe rejection token is absent from captured output")
        if not any(name.startswith("injected-") for name in inputs):
            fail("INVALID_NEGATIVE_PROBE", "negative probe has no digest-bound injected input")
        registry_path = inputs.get("probe-registry")
        probe_input_path = inputs.get("injected-probe")
        if registry_path is None or probe_input_path is None:
            fail("INVALID_NEGATIVE_PROBE", "negative probe lacks registry/request inputs")
        if (
            input_scopes.get("probe-registry") != "repository"
            or input_scopes.get("injected-probe") != "artifact"
        ):
            fail(
                "INVALID_NEGATIVE_PROBE",
                "negative registry/request inputs use untrusted scopes",
            )
        registry_payload = _json_output(registry_path, label="negative probe registry")
        require_exact_keys(
            registry_payload,
            {
                "schema_version",
                "registry_id",
                "phase",
                "catalog_id",
                "catalog_revision",
                "catalog_sha256",
                "probes",
            },
            label="negative probe registry",
        )
        if (
            registry_payload["schema_version"] != 1
            or registry_payload["registry_id"] != f"utiki-{metadata['phase'].lower()}-negative-probes"
            or registry_payload["phase"] != metadata["phase"]
            or registry_payload["catalog_sha256"] != metadata["catalog_sha256"]
        ):
            fail("INVALID_NEGATIVE_PROBE", "negative registry context is invalid")
        registry_matches: list[dict[str, Any]] = []
        registry_seen: set[ExecutionKey] = set()
        for index, value in enumerate(
            require_list(registry_payload["probes"], label="negative registry probes")
        ):
            entry = require_dict(value, label=f"negative registry probes[{index}]")
            require_exact_keys(
                entry,
                {
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
                },
                label=f"negative registry probes[{index}]",
            )
            entry_key = ExecutionKey(
                require_safe_id(entry["case_id"], label="negative registry case_id"),
                require_safe_id(entry["obligation_id"], label="negative registry obligation_id"),
                require_safe_id(entry["variant_id"], label="negative registry variant_id"),
                require_safe_id(entry["job_id"], label="negative registry job_id"),
                require_safe_id(entry["target"], label="negative registry target"),
            )
            if entry_key in registry_seen:
                fail("DUPLICATE_EVIDENCE", "negative registry contains a duplicate key")
            try:
                validate_registry_contract(entry)
            except NegativeContractError as error:
                fail("INVALID_NEGATIVE_PROBE", str(error))
            registry_seen.add(entry_key)
            if entry_key in expected_keys:
                registry_matches.append(entry)
        if len(registry_matches) != 1:
            fail("INVALID_NEGATIVE_PROBE", "negative key is not uniquely reviewed")
        probe_request = _json_output(probe_input_path, label="negative probe request")
        require_exact_keys(
            probe_request,
            {
                "schema_version",
                "case_id",
                "obligation_id",
                "variant_id",
                "job_id",
                "target",
                "candidate_sha",
                "environment",
                "mutation",
                "rejection_token",
                "child",
                "inputs",
            },
            label="negative probe request",
        )
        if probe_request["schema_version"] != SCHEMA_VERSION:
            fail("INVALID_NEGATIVE_PROBE", "negative probe request schema is invalid")
        expected = next(iter(expected_keys))
        if tuple(
            probe_request[field]
            for field in ("case_id", "obligation_id", "variant_id", "job_id", "target")
        ) != (
            expected.case_id,
            expected.obligation_id,
            expected.variant_id,
            expected.job_id,
            expected.target,
        ) or probe_request["candidate_sha"] != metadata["candidate_sha"]:
            fail("INVALID_NEGATIVE_PROBE", "negative probe request context is invalid")
        producer_report = outputs.get("producer-report")
        if producer_report is None:
            fail("INVALID_NEGATIVE_PROBE", "negative probe lacks its child transcript report")
        raw_negative = _json_output(producer_report, label="negative producer output")
        if raw_negative.get("rejection_token") != token:
            fail("INVALID_NEGATIVE_PROBE", "negative envelope/raw rejection tokens differ")
        _validate_negative_payload(
            raw_negative,
            metadata,
            next(iter(expected_keys)),
            registry_matches[0],
            envelope_observed_exit=observed_exit,
            probe_request=probe_request,
            probe_input_path=probe_input_path,
            registry_path=registry_path,
            envelope_outputs=outputs,
            repository_root=catalog.repository_root,
        )
    else:
        if report["expectation"] != "ZERO" or observed_exit != 0:
            fail("INVALID_EVIDENCE_CONTENT", f"{kind} producer exit was not zero")
        if report["rejection_token"] is not None:
            fail("INVALID_EVIDENCE_REPORT", f"{kind} may not declare a rejection token")
        producer_report = outputs.get("producer-report")
        if producer_report is None:
            fail("INVALID_EVIDENCE_REPORT", f"{kind} lacks the raw producer report")
        _validate_positive_payload(
            kind,
            _json_output(producer_report, label=f"{kind} producer output"),
            metadata,
            expected_keys,
            producer_report,
            inputs,
            outputs,
            catalog.repository_root,
            input_scopes,
        )


def validate_run_metadata(
    path: Path,
    catalog: CatalogModel,
    manifest_digest: str,
    mappings: dict[ExecutionKey, Mapping],
    *,
    expected_phase: str,
    expected_job: str | None,
    expected_target: str | None,
    expected_run_id: str | None,
    expected_candidate_sha: str | None,
    require_browser: bool,
) -> dict[str, Any]:
    metadata, _ = read_json(path, label="run metadata")
    required_keys = {
        "schema_version",
        "run_id",
        "attempt",
        "job_id",
        "phase",
        "target",
        "candidate_sha",
        "working_tree_digest",
        "working_tree_clean",
        "os",
        "architecture",
        "layers",
        "catalog_sha256",
        "execution_manifest_sha256",
        "junit_sha256",
        "fixture_digest",
        "script_digest",
        "resources",
        "artifacts",
        "effective_command",
        "effective_flags",
        "exit_codes",
        "collected_count",
        "selected_count",
        "started_at",
        "finished_at",
        "browser_enabled",
        "isolation",
        "tool_versions",
    }
    require_exact_keys(metadata, required_keys, label="run metadata")
    if metadata["schema_version"] != SCHEMA_VERSION:
        fail("UNKNOWN_SCHEMA_VERSION", "unsupported run metadata schema_version")
    run_id = require_str(metadata["run_id"], label="run metadata.run_id")
    attempt = require_int(metadata["attempt"], label="run metadata.attempt", minimum=1)
    if not RUN_ID_RE.fullmatch(run_id) or not run_id.endswith(f"-attempt-{attempt}"):
        fail("INVALID_RUN_ID", "run_id must end with the exact positive attempt")
    job_id = require_safe_id(metadata["job_id"], label="run metadata.job_id")
    phase = require_safe_id(metadata["phase"], label="run metadata.phase")
    target = require_safe_id(metadata["target"], label="run metadata.target")
    candidate_sha = require_git_sha(metadata["candidate_sha"], label="run metadata.candidate_sha")
    if phase != expected_phase:
        fail("RUN_CONTEXT_MISMATCH", f"run phase {phase} does not equal expected {expected_phase}")
    if expected_job is not None and job_id != expected_job:
        fail("RUN_CONTEXT_MISMATCH", f"run job {job_id} does not equal expected {expected_job}")
    if expected_target is not None and target != expected_target:
        fail("RUN_CONTEXT_MISMATCH", f"run target {target} does not equal expected {expected_target}")
    if expected_run_id is not None and run_id != expected_run_id:
        fail("RUN_CONTEXT_MISMATCH", "run_id does not equal trusted expected run_id")
    if expected_candidate_sha is not None and candidate_sha != expected_candidate_sha:
        fail("RUN_CONTEXT_MISMATCH", "candidate SHA does not equal trusted expected candidate SHA")
    profile = catalog.profiles.get(phase, {})
    if job_id not in profile or target not in profile[job_id][0]:
        fail("RUN_CONTEXT_MISMATCH", f"{job_id}/{target} is not required by the {phase} gate profile")
    layers = tuple(unique_strings(metadata["layers"], label="run metadata.layers"))
    if set(layers) != set(profile[job_id][1]):
        fail("WRONG_LAYER", "run metadata layers do not equal the gate profile")
    if metadata["catalog_sha256"] != catalog.digest:
        fail("RUN_CONTEXT_MISMATCH", "run metadata catalog digest mismatch")
    if metadata["execution_manifest_sha256"] != manifest_digest:
        fail("RUN_CONTEXT_MISMATCH", "run metadata manifest digest mismatch")
    require_sha256(metadata["junit_sha256"], label="run metadata.junit_sha256")
    require_sha256(metadata["working_tree_digest"], label="run metadata.working_tree_digest")
    if not require_bool(metadata["working_tree_clean"], label="run metadata.working_tree_clean"):
        fail("DIRTY_CANDIDATE", "formal CI evidence must come from a clean working tree")
    actual_os = require_str(metadata["os"], label="run metadata.os")
    actual_architecture = require_str(metadata["architecture"], label="run metadata.architecture")
    if target in TARGET_PLATFORM and (actual_os, actual_architecture) != TARGET_PLATFORM[target]:
        fail("RUN_CONTEXT_MISMATCH", f"run OS/architecture does not match target {target}")
    tools = require_dict(metadata["tool_versions"], label="run metadata.tool_versions")
    if not tools or any(not isinstance(key, str) or not isinstance(value, str) or not value for key, value in tools.items()):
        fail("INVALID_SCHEMA", "run metadata.tool_versions must be a non-empty string map")

    commands = require_list(metadata["effective_command"], label="run metadata.effective_command")
    if not commands or any(not isinstance(item, str) or not item for item in commands):
        fail("INVALID_SCHEMA", "effective_command must be a non-empty string array")
    flags = require_dict(metadata["effective_flags"], label="run metadata.effective_flags")
    require_exact_keys(flags, {"strict_config", "strict_markers", "marker_expression"}, label="effective_flags")
    pytest_style = any(
        mapping.status == "MAPPED"
        and mapping.key.job_id == job_id
        and mapping.key.target == target
        and mapping.node_id is not None
        for mapping in mappings.values()
    )
    if pytest_style:
        if not require_bool(flags["strict_config"], label="effective_flags.strict_config") or "--strict-config" not in commands:
            fail("STRICT_FLAGS_MISSING", "effective command did not preserve --strict-config")
        if not require_bool(flags["strict_markers"], label="effective_flags.strict_markers") or "--strict-markers" not in commands:
            fail("STRICT_FLAGS_MISSING", "effective command did not preserve --strict-markers")
        require_str(flags["marker_expression"], label="effective_flags.marker_expression")
    elif any(value is not None for value in flags.values()):
        fail("INVALID_NOT_APPLICABLE", "non-pytest run must mark pytest flags null")

    exits = require_dict(metadata["exit_codes"], label="run metadata.exit_codes")
    require_exact_keys(exits, {"test", "collector", "reporter"}, label="run metadata.exit_codes")
    for name in ("test", "collector", "reporter"):
        code = require_int(exits[name], label=f"exit_codes.{name}")
        if code != 0:
            fail("UPSTREAM_PROCESS_FAILED", f"original {name} exit code was {code}; checker cannot clear it")
    collected = require_int(metadata["collected_count"], label="run metadata.collected_count")
    selected = require_int(metadata["selected_count"], label="run metadata.selected_count")
    if selected > collected:
        fail("INVALID_SCHEMA", "selected_count cannot exceed collected_count")
    started = parse_timestamp(metadata["started_at"], label="run metadata.started_at")
    finished = parse_timestamp(metadata["finished_at"], label="run metadata.finished_at")
    if finished < started:
        fail("STALE_EVIDENCE", "run finished before it started")

    evidence_owners: dict[tuple[str, str], set[ExecutionKey]] = {}
    for mapping in mappings.values():
        if mapping.status != "MAPPED" or mapping.key.job_id != job_id or mapping.key.target != target:
            continue
        for item in mapping.evidence:
            evidence_owners.setdefault(item, set()).add(mapping.key)
    expected_evidence = set(evidence_owners)
    artifacts = _validate_digest_entries(
        path.parent, metadata["artifacts"], label="run metadata.artifacts", required_keys=expected_evidence
    )
    for evidence_key, artifact_path in artifacts.items():
        kind, _ = evidence_key
        _validate_evidence_report(
            artifact_path,
            kind,
            metadata,
            evidence_owners[evidence_key],
            catalog,
            path.parent.resolve(),
        )

    resources = _validate_digest_entries(path.parent, metadata["resources"], label="run metadata.resources")
    resource_required = bool(set(profile[job_id][1]) & RESOURCE_LAYERS)
    if resource_required:
        resource_kinds = {kind: resource_path for (kind, _), resource_path in resources.items()}
        if len(resources) != 2 or set(resource_kinds) != {"fixture", "script"}:
            fail(
                "RESOURCE_SET_MISMATCH",
                "browser/performance layers require exactly one fixture and one script",
            )
        fixture_digest = require_sha256(
            metadata["fixture_digest"], label="run metadata.fixture_digest"
        )
        script_digest = require_sha256(
            metadata["script_digest"], label="run metadata.script_digest"
        )
        digest_by_kind = {
            kind: digest_file(resource_path, label=f"{kind} resource")
            for kind, resource_path in resource_kinds.items()
        }
        if (
            digest_by_kind["fixture"] != fixture_digest
            or digest_by_kind["script"] != script_digest
        ):
            fail(
                "RESOURCE_DIGEST_MISMATCH",
                "fixture/script top-level digest does not match actual resource",
            )
    else:
        if resources:
            fail(
                "INVALID_NOT_APPLICABLE",
                "unit/architecture/syntax layers may not manufacture fixture/script resources",
            )
        if (
            metadata["fixture_digest"] != "NOT_APPLICABLE"
            or metadata["script_digest"] != "NOT_APPLICABLE"
        ):
            fail(
                "INVALID_NOT_APPLICABLE",
                "unit/architecture/syntax resources must be explicitly NOT_APPLICABLE",
            )

    browser_enabled = require_bool(metadata["browser_enabled"], label="run metadata.browser_enabled")
    isolation = require_dict(metadata["isolation"], label="run metadata.isolation")
    require_exact_keys(isolation, {"required", "verified", "report_sha256"}, label="run metadata.isolation")
    isolation_required = require_bool(isolation["required"], label="isolation.required")
    isolation_verified = require_bool(isolation["verified"], label="isolation.verified")
    isolation_digest = isolation["report_sha256"]
    isolation_job = bool(
        {"browser-isolation", "performance"}.intersection(profile[job_id][1])
    )
    if require_browser or isolation_job:
        if not browser_enabled or not isolation_required or not isolation_verified:
            fail("BROWSER_ISOLATION_MISSING", "browser job lacks enabled hard-egress isolation")
        require_sha256(isolation_digest, label="isolation.report_sha256")
        matching_isolation = [
            artifact_path
            for (kind, _), artifact_path in artifacts.items()
            if kind == "browser-isolation-report"
        ]
        if not matching_isolation or all(
            digest_file(item, label="browser isolation report") != isolation_digest for item in matching_isolation
        ):
            fail("BROWSER_ISOLATION_MISSING", "isolation digest does not identify a required isolation report")
    else:
        if isolation_required or isolation_verified or isolation_digest is not None:
            fail("INVALID_NOT_APPLICABLE", "non-browser run must explicitly leave isolation inactive/null")
    return metadata


def validate_single_run(
    *,
    junit_path: Path,
    metadata_path: Path,
    catalog: CatalogModel,
    manifest_digest: str,
    mappings: dict[ExecutionKey, Mapping],
    phase: str,
    expected_job: str | None = None,
    expected_target: str | None = None,
    expected_run_id: str | None = None,
    expected_candidate_sha: str | None = None,
    require_browser: bool = False,
) -> dict[str, Any]:
    metadata = validate_run_metadata(
        metadata_path,
        catalog,
        manifest_digest,
        mappings,
        expected_phase=phase,
        expected_job=expected_job,
        expected_target=expected_target,
        expected_run_id=expected_run_id,
        expected_candidate_sha=expected_candidate_sha,
        require_browser=require_browser,
    )
    required, total = validate_junit(junit_path, metadata, mappings, manifest_digest)
    required_executions = sum(
        1
        for mapping in mappings.values()
        if mapping.status == "MAPPED"
        and mapping.key.job_id == metadata["job_id"]
        and mapping.key.target == metadata["target"]
    )
    if metadata["collected_count"] != total:
        fail("COLLECTION_COUNT_MISMATCH", "metadata collected_count does not equal JUnit testcase count")
    if metadata["selected_count"] < required:
        fail("COLLECTION_COUNT_MISMATCH", "metadata selected_count is lower than required JUnit results")
    if required and total == 0:
        fail("EMPTY_REQUIRED_RESULT", "required pytest job collected no result")
    return {
        "run_id": metadata["run_id"],
        "attempt": metadata["attempt"],
        "job_id": metadata["job_id"],
        "target": metadata["target"],
        "candidate_sha": metadata["candidate_sha"],
        "required": required_executions,
        "required_junit": required,
        "junit_testcases": total,
    }


def validate_run_index(
    path: Path,
    catalog: CatalogModel,
    manifest_digest: str,
    mappings: dict[ExecutionKey, Mapping],
    phase: str,
    expected_run_id: str | None,
    expected_candidate_sha: str | None,
) -> list[dict[str, Any]]:
    index, _ = read_json(path, label="run index")
    require_exact_keys(
        index,
        {
            "schema_version",
            "run_id",
            "attempt",
            "phase",
            "candidate_sha",
            "catalog_sha256",
            "execution_manifest_sha256",
            "runs",
        },
        label="run index",
    )
    if index["schema_version"] != SCHEMA_VERSION:
        fail("UNKNOWN_SCHEMA_VERSION", "unsupported run index schema_version")
    run_id = require_str(index["run_id"], label="run index.run_id")
    attempt = require_int(index["attempt"], label="run index.attempt", minimum=1)
    candidate_sha = require_git_sha(index["candidate_sha"], label="run index.candidate_sha")
    if not RUN_ID_RE.fullmatch(run_id) or not run_id.endswith(f"-attempt-{attempt}"):
        fail("INVALID_RUN_ID", "run index run_id/attempt mismatch")
    if index["phase"] != phase or index["catalog_sha256"] != catalog.digest or index["execution_manifest_sha256"] != manifest_digest:
        fail("RUN_INDEX_CONTEXT_MISMATCH", "run index phase/catalog/manifest context mismatch")
    if expected_run_id is not None and run_id != expected_run_id:
        fail("RUN_INDEX_CONTEXT_MISMATCH", "run index run_id does not equal trusted expectation")
    if expected_candidate_sha is not None and candidate_sha != expected_candidate_sha:
        fail("RUN_INDEX_CONTEXT_MISMATCH", "run index candidate SHA does not equal trusted expectation")

    expected_pairs = {
        (job_id, target)
        for job_id, (targets, _) in catalog.profiles[phase].items()
        for target in targets
    }
    actual_entries: dict[tuple[str, str], tuple[Path, Path]] = {}
    for entry_index, value in enumerate(require_list(index["runs"], label="run index.runs")):
        label = f"run index.runs[{entry_index}]"
        entry = require_dict(value, label=label)
        require_exact_keys(
            entry,
            {"job_id", "target", "junit_path", "junit_sha256", "run_metadata_path", "run_metadata_sha256"},
            label=label,
        )
        pair = (
            require_safe_id(entry["job_id"], label=f"{label}.job_id"),
            require_safe_id(entry["target"], label=f"{label}.target"),
        )
        if pair in actual_entries:
            fail("DUPLICATE_RUN_INDEX_ENTRY", f"run index duplicates job/target {pair}")
        junit_path = safe_relative_path(path.parent, entry["junit_path"], label=f"{label}.junit_path")
        metadata_path = safe_relative_path(path.parent, entry["run_metadata_path"], label=f"{label}.run_metadata_path")
        if digest_file(junit_path, label=f"{label} JUnit") != require_sha256(entry["junit_sha256"], label=f"{label}.junit_sha256"):
            fail("RUN_INDEX_DIGEST_MISMATCH", f"{label} JUnit digest mismatch")
        if digest_file(metadata_path, label=f"{label} metadata") != require_sha256(entry["run_metadata_sha256"], label=f"{label}.run_metadata_sha256"):
            fail("RUN_INDEX_DIGEST_MISMATCH", f"{label} metadata digest mismatch")
        actual_entries[pair] = (junit_path, metadata_path)
    if set(actual_entries) != expected_pairs:
        fail(
            "RUN_INDEX_SET_MISMATCH",
            f"run index job/target mismatch: missing={sorted(expected_pairs - set(actual_entries))!r}, extra={sorted(set(actual_entries) - expected_pairs)!r}",
        )
    summaries = []
    for (job_id, target), (junit_path, metadata_path) in sorted(actual_entries.items()):
        summary = validate_single_run(
            junit_path=junit_path,
            metadata_path=metadata_path,
            catalog=catalog,
            manifest_digest=manifest_digest,
            mappings=mappings,
            phase=phase,
            expected_job=job_id,
            expected_target=target,
            expected_run_id=run_id,
            expected_candidate_sha=candidate_sha,
            require_browser=False,
        )
        if summary["attempt"] != attempt:
            fail("RUN_INDEX_CONTEXT_MISMATCH", "run entry attempt differs from run index")
        summaries.append(summary)
    return summaries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required-catalog", type=Path, required=True)
    parser.add_argument("--approved-catalog-sha256", required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-index", type=Path)
    source.add_argument("--junit", type=Path)
    parser.add_argument("--run-metadata", type=Path)
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--run-id")
    parser.add_argument("--job-id")
    parser.add_argument("--target")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--require-browser", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def write_report(path: Path | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path is None:
        print(rendered, end="")
        return
    try:
        path.write_text(rendered, encoding="utf-8")
    except OSError as error:
        fail("REPORT_WRITE_ERROR", f"cannot write report {path}: {error}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.run_index is not None:
            if arguments.run_metadata is not None or arguments.junit is not None:
                parser.error("--run-index cannot be combined with --junit/--run-metadata")
            if arguments.phase is None:
                parser.error("--phase is required with --run-index")
            if arguments.job_id is not None or arguments.target is not None or arguments.require_browser:
                parser.error("job/target/browser expectations apply only to a single run")
            phase = arguments.phase
        else:
            if arguments.run_metadata is None:
                parser.error("--run-metadata is required with --junit")
            if arguments.phase is None:
                # The documented single-job invocation binds the manifest and
                # metadata together without a separate phase flag.  A launcher
                # can still provide --phase as an external expectation.
                manifest_header, _ = read_json(arguments.execution_manifest, label="execution manifest")
                phase = require_str(manifest_header.get("phase"), label="execution manifest.phase")
                if phase not in PHASES:
                    fail("INVALID_PHASE", f"unknown manifest phase: {phase!r}")
            else:
                phase = arguments.phase
        if arguments.candidate_sha is not None:
            require_git_sha(arguments.candidate_sha, label="--candidate-sha")

        catalog = validate_catalog(arguments.required_catalog, arguments.approved_catalog_sha256)
        _, manifest_digest, mappings = validate_execution_manifest(arguments.execution_manifest, catalog, phase)
        if arguments.run_index is not None:
            runs = validate_run_index(
                arguments.run_index,
                catalog,
                manifest_digest,
                mappings,
                phase,
                arguments.run_id,
                arguments.candidate_sha,
            )
        else:
            runs = [
                validate_single_run(
                    junit_path=arguments.junit,
                    metadata_path=arguments.run_metadata,
                    catalog=catalog,
                    manifest_digest=manifest_digest,
                    mappings=mappings,
                    phase=phase,
                    expected_job=arguments.job_id,
                    expected_target=arguments.target,
                    expected_run_id=arguments.run_id,
                    expected_candidate_sha=arguments.candidate_sha,
                    require_browser=arguments.require_browser,
                )
            ]
        phase_required = sum(1 for mapping in mappings.values() if mapping.status == "MAPPED")
        checked = sum(run["required"] for run in runs)
        not_due = sum(1 for mapping in mappings.values() if mapping.status == "NOT_DUE")
        inapplicable = sum(1 for mapping in mappings.values() if mapping.status == "NOT_APPLICABLE")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "phase": phase,
            "catalog_sha256": catalog.digest,
            "execution_manifest_sha256": manifest_digest,
            "scope": "phase" if arguments.run_index is not None else "job-target",
            "checked": checked,
            "required": checked,
            "phase_required": phase_required,
            "missing": 0,
            "failed": 0,
            "inapplicable": inapplicable,
            "not_due": not_due,
            "runs": runs,
        }
        write_report(arguments.report, payload)
        return 0
    except GateError as error:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "error_code": error.code,
            "message": error.message,
        }
        try:
            write_report(arguments.report, payload)
        except GateError:
            print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
