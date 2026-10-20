"""Trust-boundary tests for the deterministic P1 execution manifest."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "tools" / "generate_utiki_execution_manifest.py"
CATALOG = PROJECT_ROOT / "ci" / "utiki-required-cases.json"
REGISTRY = PROJECT_ROOT / "ci" / "utiki-p1-execution-bindings.json"
PERSISTED = PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "manifest.json"
P2_REGISTRY = PROJECT_ROOT / "ci" / "utiki-p2-execution-bindings.json"
P2_PERSISTED = PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "manifest-p2.json"
P2_NEGATIVE = PROJECT_ROOT / "ci" / "utiki-p2-negative-probes.json"
APPROVED_CATALOG_SHA256 = "73455dc332d181b1350ec1bc8f3318a45a098ac9b23b88c1c7e712e59c7361cf"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _command(
    registry: Path, output: Path, *, check: bool = False, phase: str | None = None
) -> list[str]:
    command = [
        sys.executable,
        str(GENERATOR),
        "--root",
        str(PROJECT_ROOT),
        "--required-catalog",
        str(CATALOG),
        "--approved-catalog-sha256",
        APPROVED_CATALOG_SHA256,
        "--binding-registry",
        str(registry),
        "--output",
        str(output),
    ]
    if phase is not None:
        command.extend(("--phase", phase))
    if check:
        command.append("--check")
    return command


def _run(
    registry: Path, output: Path, *, check: bool = False, phase: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(registry, output, check=check, phase=phase),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _registry() -> dict[str, object]:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_persisted_manifest_is_byte_stable_and_has_fixed_counts(tmp_path):
    generated = tmp_path / "manifest.json"

    first = _run(REGISTRY, generated)
    second = _run(REGISTRY, generated, check=True)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert generated.read_bytes() == PERSISTED.read_bytes()
    report = json.loads(first.stdout)
    assert report == {
        "case_count": 143,
        "execution_count": 457,
        "mapped_count": 62,
        "output_sha256": hashlib.sha256(PERSISTED.read_bytes()).hexdigest(),
        "status": "PASS",
    }
    manifest = json.loads(generated.read_text(encoding="utf-8"))
    executions = [
        execution
        for case in manifest["cases"]
        for execution in case["executions"]
    ]
    assert len(manifest["cases"]) == 143
    assert len(executions) == 457
    assert sum(item["status"] == "MAPPED" for item in executions) == 62
    assert all(
        item["node_id"] is None and item["evidence"] == []
        for item in executions
        if item["status"] != "MAPPED"
    )
    negative_paths = [
        evidence["path"]
        for item in executions
        for evidence in item["evidence"]
        if evidence["kind"] == "checker-exit-nonzero"
    ]
    assert len(negative_paths) == len(set(negative_paths))
    assert all(path.startswith("evidence/negative/") for path in negative_paths)

    registry = _registry()
    blocked = [
        binding
        for binding in registry["bindings"]
        if binding["formal_readiness"] == "BLOCKED_ENVIRONMENT"
    ]
    assert len(blocked) == 18
    assert {binding["job_id"] for binding in blocked} == {
        "browser-contract-isolated",
        "performance-contract",
    }


def test_p2_phase_requires_all_new_bindings_instead_of_reusing_p1_result(tmp_path):
    registry = _registry()
    registry["registry_id"] = "utiki-p2-execution-bindings"
    registry["phase"] = "P2"
    registry_path = tmp_path / "p2-incomplete.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "p2-manifest.json", phase="P2")

    assert result.returncode != 0
    failure = json.loads(result.stderr)
    assert failure["reason"] == "BINDING_SET_MISMATCH"
    assert "(42)" in failure["detail"]
    assert not (tmp_path / "p2-manifest.json").exists()


def test_p3a_phase_rejects_relabelled_p2_bindings(tmp_path):
    registry = json.loads(P2_REGISTRY.read_text(encoding="utf-8"))
    registry["registry_id"] = "utiki-p3a-execution-bindings"
    registry["phase"] = "P3a"
    registry_path = tmp_path / "p3a-incomplete.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "p3a-manifest.json", phase="P3a")

    assert result.returncode != 0
    failure = json.loads(result.stderr)
    assert failure["reason"] == "BINDING_SET_MISMATCH"
    assert "(55)" in failure["detail"]
    assert "(0)" in failure["detail"]
    assert not (tmp_path / "p3a-manifest.json").exists()


def test_p2_persisted_manifest_and_negative_registry_bind_exact_due_keys(tmp_path):
    generated = tmp_path / "manifest-p2.json"
    first = _run(P2_REGISTRY, generated, phase="P2")
    second = _run(P2_REGISTRY, generated, check=True, phase="P2")

    assert first.returncode == second.returncode == 0, first.stderr or second.stderr
    assert generated.read_bytes() == P2_PERSISTED.read_bytes()
    manifest = json.loads(P2_PERSISTED.read_text(encoding="utf-8"))
    assert manifest["phase"] == "P2"
    assert json.loads(first.stdout)["mapped_count"] == 104
    mapped = {
        (case["id"], execution["obligation_id"], execution["variant_id"],
         execution["job_id"], execution["target"])
        for case in manifest["cases"]
        for execution in case["executions"]
        if execution["status"] == "MAPPED"
    }
    assert len(mapped) == 104
    negative = {
        (case["id"], execution["obligation_id"], execution["variant_id"],
         execution["job_id"], execution["target"])
        for case in manifest["cases"]
        for execution in case["executions"]
        if execution["status"] == "MAPPED"
        and any(item["kind"] == "checker-exit-nonzero" for item in execution["evidence"])
    }
    registry = json.loads(P2_NEGATIVE.read_text(encoding="utf-8"))
    assert registry["registry_id"] == "utiki-p2-negative-probes"
    assert registry["phase"] == "P2"
    assert registry["catalog_sha256"] == APPROVED_CATALOG_SHA256
    registry_keys = {
        (entry["case_id"], entry["obligation_id"], entry["variant_id"],
         entry["job_id"], entry["target"])
        for entry in registry["probes"]
    }
    assert len(negative) == len(registry_keys) == len(registry["probes"]) == 56
    assert negative == registry_keys


def test_byte_pinned_json_uses_lf_checkout() -> None:
    paths = (
        "ci/utiki-required-cases.json",
        "ci/utiki-p1-negative-probes.json",
        "ci/utiki-p2-negative-probes.json",
        "tests/fixtures/utiki/manifest.json",
        "tests/fixtures/utiki/manifest-p2.json",
    )
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert set(result.stdout.splitlines()) == {
        f"{path}: eol: lf" for path in paths
    }


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_binding_set_mutations_fail_closed(tmp_path, mutation):
    registry = _registry()
    bindings = registry["bindings"]
    if mutation == "missing":
        bindings.pop()
    elif mutation == "extra":
        extra = copy.deepcopy(bindings[0])
        extra["case_id"] = "UNKNOWN"
        extra["obligation_id"] = "UNKNOWN.arch.extra"
        extra["variant_id"] = "extra"
        extra["node_id"] = (
            "tests/architecture/test_utiki_architecture_gate.py::"
            "test_strict_mode_is_required"
        )
        extra["producer"] = "tests/architecture/test_utiki_architecture_gate.py"
        bindings.append(extra)
    else:
        bindings.append(copy.deepcopy(bindings[0]))
    registry_path = tmp_path / f"{mutation}.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    expected = {
        "missing": "BINDING_SET_MISMATCH",
        "extra": "BINDING_SET_MISMATCH",
        "duplicate": "DUPLICATE_BINDING",
    }[mutation]
    assert expected in result.stderr


def test_duplicate_node_binding_fails_closed(tmp_path):
    registry = _registry()
    bindings = registry["bindings"]
    first = next(
        item
        for item in bindings
        if item["job_id"] == "architecture-types" and item["node_id"] is not None
    )
    second = next(
        item
        for item in bindings
        if item is not first
        and item["job_id"] == first["job_id"]
        and item["target"] == first["target"]
        and item["node_id"] is not None
    )
    second["node_id"] = first["node_id"]
    registry_path = tmp_path / "duplicate-node.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    assert "DUPLICATE_NODE_ID" in result.stderr


def test_unknown_obligation_binding_fails_closed(tmp_path):
    registry = _registry()
    registry["bindings"][0]["obligation_id"] = "A05.arch.unknown"
    registry_path = tmp_path / "unknown-obligation.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    assert "BINDING_SET_MISMATCH" in result.stderr


def test_checker_exit_path_cannot_be_overridden_per_binding(tmp_path):
    registry = _registry()
    binding = next(
        item
        for item in registry["bindings"]
        if any(
            evidence["kind"] == "checker-exit-nonzero"
            for evidence in item["evidence"]
        )
    )
    evidence = next(
        item
        for item in binding["evidence"]
        if item["kind"] == "checker-exit-nonzero"
    )
    evidence["path"] = "evidence/negative/manual-override.json"
    registry_path = tmp_path / "manual-override.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    assert "INVALID_NEGATIVE_EVIDENCE_PATH" in result.stderr


def test_unknown_checker_exit_template_placeholder_fails_closed(tmp_path):
    registry = _registry()
    registry["checker_exit_path_template"] = (
        "evidence/negative/{unknown_placeholder}.json"
    )
    registry_path = tmp_path / "unknown-placeholder.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    assert "BINDING_CONTEXT_MISMATCH" in result.stderr


def test_sanitized_checker_exit_path_collision_fails_closed(tmp_path):
    registry = _registry()
    checker_bindings = [
        binding
        for binding in registry["bindings"]
        if any(
            evidence["kind"] == "checker-exit-nonzero"
            for evidence in binding["evidence"]
        )
    ]
    checker_bindings[0]["obligation_id"] = "collision:key"
    checker_bindings[1]["obligation_id"] = "collision_key"
    checker_bindings[0]["variant_id"] = "same"
    checker_bindings[1]["variant_id"] = "same"
    registry_path = tmp_path / "collision.json"
    _write_json(registry_path, registry)

    result = _run(registry_path, tmp_path / "manifest.json")

    assert result.returncode != 0
    assert "NEGATIVE_EVIDENCE_PATH_COLLISION" in result.stderr


def test_persisted_manifest_drift_fails_check_mode(tmp_path):
    drifted = tmp_path / "manifest.json"
    drifted.write_bytes(PERSISTED.read_bytes() + b"\n")

    result = _run(REGISTRY, drifted, check=True)

    assert result.returncode != 0
    assert "MANIFEST_NOT_REPRODUCIBLE" in result.stderr


def test_every_junit_binding_is_an_exact_collectable_pytest_node():
    registry = _registry()
    nodes = [
        binding["node_id"]
        for binding in registry["bindings"]
        if binding["node_id"] is not None
    ]
    assert len(nodes) == 51
    assert len(nodes) == len(set(nodes))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--strict-config",
            "--strict-markers",
            "--collect-only",
            "-q",
            *nodes,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    collected = {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("tests/")
    }
    assert collected == set(nodes)
