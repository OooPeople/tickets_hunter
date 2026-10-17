#!/usr/bin/env python3
"""Build deterministic concrete inputs for every reviewed P1 negative probe.

The generated requests execute the real checker, syntax verifier, typecheck
launcher, pytest, or isolated egress producer.  They contain no precomputed
exit status or synthetic producer report; those are captured later by
``run_utiki_negative_probe.py``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from xml.etree import ElementTree

try:
    from tools.utiki_negative_contract import (
        NegativeContractError,
        probe_directory_name,
        validate_probe_output_contract,
        validate_registry_contract,
        validate_request_contract,
    )
except ModuleNotFoundError:  # Direct execution from tools/.
    from utiki_negative_contract import (  # type: ignore[no-redef]
        NegativeContractError,
        probe_directory_name,
        validate_probe_output_contract,
        validate_registry_contract,
        validate_request_contract,
    )


SCHEMA_VERSION = 1
PROBE_ROOT = "{PROBE_ROOT}"


class BuildError(Exception):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BuildError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise BuildError(f"JSON root is not an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _name(entry: dict[str, Any]) -> str:
    return f"{entry['obligation_id']}--{entry['variant_id']}"


def _placeholder(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.resolve())
    if not relative.parts:
        return PROBE_ROOT
    return f"{PROBE_ROOT}/{relative.as_posix()}"


def _request(
    root: Path,
    entry: dict[str, Any],
    candidate_sha: str,
    *,
    arguments: list[str | Path],
    input_path: Path,
    support_inputs: list[tuple[str, Path]] | None = None,
    working_directory: Path | None = None,
) -> None:
    input_kinds = entry["input_kinds"]
    if not isinstance(input_kinds, list) or len(input_kinds) != 1:
        raise BuildError(f"probe must declare exactly one concrete input: {_name(entry)}")
    if root not in input_path.resolve().parents or not input_path.is_file():
        raise BuildError(f"probe input is outside its directory: {_name(entry)}")
    normalized_arguments = [
        _placeholder(value, root) if isinstance(value, Path) else value
        for value in arguments
    ]
    concrete = _placeholder(input_path, root)
    if concrete not in normalized_arguments:
        raise BuildError(f"child command does not consume probe input: {_name(entry)}")
    child_cwd = _placeholder(working_directory, root) if working_directory else None
    concrete_inputs = [(input_kinds[0], input_path), *(support_inputs or [])]
    if len({kind for kind, _ in concrete_inputs}) != len(concrete_inputs):
        raise BuildError(f"probe input kind is duplicated: {_name(entry)}")
    if len({path.resolve() for _, path in concrete_inputs}) != len(concrete_inputs):
        raise BuildError(f"probe input path is duplicated: {_name(entry)}")
    for kind, path in concrete_inputs:
        if not kind or root not in path.resolve().parents or not path.is_file():
            raise BuildError(f"probe support input is invalid: {_name(entry)}")
    request = {
        "schema_version": SCHEMA_VERSION,
        **{
            field: entry[field]
            for field in (
                "case_id",
                "obligation_id",
                "variant_id",
                "job_id",
                "target",
            )
        },
        "candidate_sha": candidate_sha,
        "environment": entry["environment"],
        "mutation": entry["mutation"],
        "rejection_token": entry["rejection_token"],
        "child": {
            "kind": entry["child_kind"],
            "producer": entry["child_producer"],
            "arguments": normalized_arguments,
            "working_directory": child_cwd,
        },
        "inputs": [
            {
                "kind": kind,
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
            }
            for kind, path in concrete_inputs
        ],
    }
    _write_json(root / "probe.json", request)


def _pytest_probe(root: Path, entry: dict[str, Any], candidate_sha: str) -> None:
    source = root / "test_probe.py"
    config = root / "pytest.ini"
    variant = entry["variant_id"]
    if variant == "invalid-pytest-config":
        source.write_text("def test_probe():\n    pass\n", encoding="utf-8")
        config.write_text("[pytest]\nutiki_unknown_config = true\n", encoding="utf-8")
        arguments: list[str | Path] = [
            "-c",
            config,
            "--strict-config",
            "--strict-markers",
            source,
        ]
        input_path = config
        support_inputs = [("pytest-probe-source", source)]
    else:
        config.write_text("[pytest]\n", encoding="utf-8")
        source.write_text(
            "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n",
            encoding="utf-8",
        )
        arguments = []
        if variant == "empty-addopts":
            arguments.extend(["-o", "addopts="])
        arguments.extend(["-c", config, "--strict-config", "--strict-markers", source])
        input_path = source
        support_inputs = [("pytest-config", config)]
    _request(
        root,
        entry,
        candidate_sha,
        arguments=arguments,
        input_path=input_path,
        support_inputs=support_inputs,
        working_directory=root,
    )


def _typecheck_probe(
    root: Path, entry: dict[str, Any], candidate_sha: str, *, phase: str = "P1"
) -> None:
    if phase not in {"P1", "P2"}:
        raise BuildError("unsupported typecheck probe phase")
    fixture = root / "repo"
    seed_files = (
        "src/platforms/utiki/auth.py",
        "src/platforms/utiki/profiles.py",
        "src/platforms/utiki/selection.py",
    )
    for relative in seed_files:
        path = fixture / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE: int = 1\n", encoding="utf-8")
    manifest = fixture / "ci/utiki-typed-scope.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(seed_files) + "\n", encoding="utf-8")
    config = fixture / "ci/mypy-utiki.ini"
    config.write_text(
        "[mypy]\npython_version = 3.11\nstrict = True\nwarn_unused_configs = True\nmypy_path = src\n",
        encoding="utf-8",
    )
    fake_mypy = root / "fake-mypy.py"
    if phase == "P1":
        fake_mypy.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
    new_paths = {
        "new-domain-file": "src/platforms/utiki/domain/state.py",
        "new-application-file": "src/platforms/utiki/application/engine.py",
        "new-port-file": "src/platforms/utiki/application/ports.py",
        "new-adapter-file": "src/platforms/utiki/adapters/browser.py",
        "new-bootstrap-file": "src/platforms/utiki/bootstrap.py",
        "new-current-config-file": "src/utiki_config.py",
        "new-keyword-file": "src/keyword_rules.py",
        "new-core-bridge-file": "src/utiki_core_services.py",
        "new-upgrade-tool-file": "tools/migrations/convert_utiki_config.py",
    }
    variant = entry["variant_id"]
    if variant in new_paths:
        new_file = fixture / new_paths[variant]
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("VALUE: int = 1\n", encoding="utf-8")
        input_path = new_file
    elif variant == "broad-ignore-any":
        config.write_text(
            config.read_text(encoding="utf-8") + "ignore_errors = True\n",
            encoding="utf-8",
        )
        input_path = config
    else:
        raise BuildError(f"unsupported typecheck probe: {variant}")
    report = root / "typecheck-report.json"
    _request(
        root,
        entry,
        candidate_sha,
        arguments=[
            *(["--phase", "P2"] if phase == "P2" else []),
            "--root",
            fixture,
            "--manifest",
            manifest,
            "--config",
            config,
            *(["--mypy", fake_mypy] if phase == "P1" else []),
            "--report",
            report,
            *(["--probe-subject", input_path] if variant in new_paths else []),
        ],
        input_path=input_path,
        support_inputs=[
            ("typecheck-manifest", manifest),
            *([("typecheck-config", config)] if input_path != config else []),
            *([("typecheck-mypy", fake_mypy)] if phase == "P1" else []),
            ("typecheck-seed-auth", fixture / seed_files[0]),
            ("typecheck-seed-profiles", fixture / seed_files[1]),
            ("typecheck-seed-selection", fixture / seed_files[2]),
        ],
    )


def _first_mapped(manifest: dict[str, Any], job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for case in manifest["cases"]:
        for execution in case["executions"]:
            if execution["status"] == "MAPPED" and execution["job_id"] == job_id:
                return case, execution
    raise BuildError("synthetic CI fixture has no mapped execution")


def _own_mapped(
    manifest: dict[str, Any], entry: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    for case in manifest["cases"]:
        if case.get("id") != entry["case_id"]:
            continue
        for execution in case["executions"]:
            if (
                execution.get("status") == "MAPPED"
                and execution.get("job_id") == entry["job_id"]
                and execution.get("target") == entry["target"]
                and execution.get("obligation_id") == entry["obligation_id"]
                and execution.get("variant_id") == entry["variant_id"]
            ):
                return case, execution
    raise BuildError(f"synthetic CI fixture lacks own mapping: {_name(entry)}")


def _set_testcase_property(testcase: ElementTree.Element, name: str, value: str) -> None:
    properties = testcase.find("properties")
    if properties is None:
        raise BuildError("synthetic JUnit lacks properties")
    for item in properties.findall("property"):
        if item.get("name") == name:
            item.set("value", value)
            return
    raise BuildError(f"synthetic JUnit lacks property: {name}")


def _ci_probe(
    repository: Path, root: Path, entry: dict[str, Any], candidate_sha: str,
    *, phase: str = "P1",
) -> None:
    if phase not in {"P1", "P2"}:
        raise BuildError("unsupported CI probe phase")
    from tests.architecture import test_utiki_ci_results_gate as gate

    previous_candidate = gate.CANDIDATE_SHA
    previous_run = gate.RUN_ID
    previous_phase = gate.PHASE
    previous_registry = gate.NEGATIVE_REGISTRY
    gate.CANDIDATE_SHA = candidate_sha
    gate.RUN_ID = "utiki-negative-builder-attempt-1"
    gate.PHASE = phase
    gate.NEGATIVE_REGISTRY = repository / f"ci/utiki-{phase.lower()}-negative-probes.json"
    previous_internal = os.environ.get("UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL")
    os.environ["UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL"] = "1"
    try:
        # ``case/run-index.json`` is part of the reviewed aggregate-probe
        # contract.  Keep that logical path stable while shortening only the
        # opaque outer probe directory and non-contract support artifacts.
        case_root = root / "case"
        manifest_path, manifest, catalog, profiles = gate._make_manifest(
            case_root, compact=True
        )
        run = gate._make_run(
            case_root,
            manifest_path,
            manifest,
            profiles,
            "architecture-types",
            "source-linux-x64",
            run_dir_name="r",
            compact_support=True,
        )
        variant = entry["variant_id"]
        primary: Path
        if variant == "missing-job-or-target":
            case = {
                "root": case_root,
                "manifest_path": manifest_path,
                "manifest": manifest,
                "catalog": catalog,
                "profiles": profiles,
            }
            index_path, index, _ = gate._make_run_index(case, compact=True)
            index["runs"].pop()
            gate._write_json(index_path, index)
            arguments = gate._index_arguments(manifest_path, index_path)
            primary = index_path
        else:
            arguments = gate._single_arguments(
                manifest_path, run, "architecture-types", "source-linux-x64"
            )
            if variant == "catalog-unapproved-change":
                changed = copy.deepcopy(catalog)
                changed["cases"][0]["scenario"] += " mutated"
                primary = case_root / "changed-catalog.json"
                gate._write_json(primary, changed)
                arguments[1] = primary
                arguments[3] = gate._sha256(primary)
            elif variant in {"test-and-mapping-removed", "removed-test-and-mapping"}:
                changed = copy.deepcopy(manifest)
                case, mapped = _own_mapped(changed, entry)
                case["executions"].remove(mapped)
                gate._write_json(manifest_path, changed)
                primary = manifest_path
            elif variant in {"duplicate-or-unknown-id", "missing-obligation-variant"}:
                changed = copy.deepcopy(manifest)
                case, mapped = _own_mapped(changed, entry)
                if variant == "duplicate-or-unknown-id":
                    case["executions"].append(copy.deepcopy(mapped))
                else:
                    case["executions"].remove(mapped)
                gate._write_json(manifest_path, changed)
                primary = manifest_path
            elif variant == "forged-not-applicable":
                changed = copy.deepcopy(manifest)
                _, mapped = _own_mapped(changed, entry)
                mapped.update(status="NOT_APPLICABLE", node_id=None, evidence=[])
                gate._write_json(manifest_path, changed)
                primary = manifest_path
            elif variant in {"zero-collected", "empty-or-missing-result"}:
                tree = ElementTree.parse(run["junit"])
                suite = tree.getroot()
                for testcase in list(suite.findall("testcase")):
                    suite.remove(testcase)
                tree.write(run["junit"], encoding="utf-8", xml_declaration=True)
                metadata = _load(run["metadata"])
                if variant == "zero-collected":
                    metadata["collected_count"] = 0
                    metadata["selected_count"] = 0
                    primary = run["metadata"]
                else:
                    primary = run["junit"]
                gate._write_json(run["metadata"], metadata)
                gate._refresh_junit_digest(run)
            elif variant == "stale-run-or-sha":
                metadata = _load(run["metadata"])
                metadata["candidate_sha"] = "2" * 40
                gate._write_json(run["metadata"], metadata)
                primary = run["metadata"]
            elif variant == "digest-mismatch":
                run["junit"].write_bytes(run["junit"].read_bytes() + b"\nmutated\n")
                primary = run["junit"]
            elif variant == "duplicate-result-or-wrong-layer":
                tree = ElementTree.parse(run["junit"])
                suite = tree.getroot()
                suite.append(copy.deepcopy(suite.find("testcase")))
                tree.write(run["junit"], encoding="utf-8", xml_declaration=True)
                gate._refresh_junit_digest(run)
                primary = run["junit"]
            elif variant == "failure-exit-overwritten":
                metadata = _load(run["metadata"])
                metadata["exit_codes"]["test"] = 7
                gate._write_json(run["metadata"], metadata)
                primary = run["metadata"]
            elif variant in {
                "all-skipped",
                "required-outcome-skip",
                "required-outcome-xfail",
                "required-outcome-strict-xpass",
                "required-outcome-non-strict-xpass",
                "required-outcome-setup-error",
                "required-outcome-teardown-error",
            }:
                tree = ElementTree.parse(run["junit"])
                testcases = tree.getroot().findall("testcase")
                selected = testcases if variant == "all-skipped" else testcases[:1]
                property_name, property_value, child = {
                    "all-skipped": ("outcome", "skipped", "skipped"),
                    "required-outcome-skip": ("outcome", "skipped", "skipped"),
                    "required-outcome-xfail": ("wasxfail", "true", "skipped"),
                    "required-outcome-strict-xpass": ("wasxfail", "true", "failure"),
                    "required-outcome-non-strict-xpass": ("wasxfail", "true", None),
                    "required-outcome-setup-error": ("setup_outcome", "error", "error"),
                    "required-outcome-teardown-error": ("teardown_outcome", "error", "error"),
                }[variant]
                for testcase in selected:
                    _set_testcase_property(testcase, property_name, property_value)
                    if child:
                        ElementTree.SubElement(testcase, child, message=variant)
                tree.write(run["junit"], encoding="utf-8", xml_declaration=True)
                gate._refresh_junit_digest(run)
                primary = run["junit"]
            else:
                raise BuildError(f"unsupported CI probe: {variant}")
        normalized = [
            _placeholder(value, root) if isinstance(value, Path) else str(value)
            for value in arguments
        ]
        _request(
            root,
            entry,
            candidate_sha,
            arguments=normalized,
            input_path=primary,
        )
    finally:
        if previous_internal is None:
            os.environ.pop("UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL", None)
        else:
            os.environ["UTIKI_TEST_NEGATIVE_BUILDER_INTERNAL"] = previous_internal
        gate.CANDIDATE_SHA = previous_candidate
        gate.RUN_ID = previous_run
        gate.PHASE = previous_phase
        gate.NEGATIVE_REGISTRY = previous_registry


def _git(repo: Path, *arguments: str) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
            "LC_ALL": "C",
        }
    )
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _syntax_context(path: Path, base: str, candidate: str, phase: str = "P1") -> None:
    _write_json(
        path,
        {
            "schema_version": 1,
            "run_id": "utiki-negative-syntax",
            "job_id": "modified-python-syntax",
            "phase": phase,
            "base_sha": base,
            "candidate_sha": candidate,
        },
    )


def _syntax_probe(
    repository: Path, root: Path, entry: dict[str, Any], candidate_sha: str,
    *, phase: str = "P1",
) -> None:
    if phase not in {"P1", "P2"}:
        raise BuildError("unsupported syntax probe phase")
    repo = root / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "negative-builder@example.invalid")
    _git(repo, "config", "user.name", "Negative Builder")
    baseline = repo / "src/settings.py"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    base = _git(repo, "rev-parse", "HEAD")
    variant = entry["variant_id"]
    include_worktree = False
    write_mode = False
    probe_subject: Path | None = None
    if variant in {"outside-fixed-roots", "not-imported-by-pytest"}:
        if variant == "not-imported-by-pytest":
            relative_broken = "tests/helpers/broken_helper.py"
        elif entry["case_id"] == "A06":
            relative_broken = "misc/broken.py"
        else:
            relative_broken = "external/broken.py"
        changed = repo / relative_broken
        changed.parent.mkdir(parents=True, exist_ok=True)
        changed.write_text("def broken(:\n", encoding="utf-8")
        probe_subject = changed
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "broken outside fixed roots")
    elif variant == "staged-and-working":
        baseline.write_text("def broken(:\n", encoding="utf-8")
        probe_subject = baseline
        _git(repo, "add", str(baseline.relative_to(repo)))
        include_worktree = True
    elif variant == "ignored-build-source":
        (repo / ".gitignore").write_text("generated_source.py\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "ignore source")
        probe_subject = repo / "generated_source.py"
        probe_subject.write_text("VALUE = 1\n", encoding="utf-8")
        write_mode = True
    elif variant == "explicit-deletion-rename":
        renamed = repo / "src/renamed.py"
        baseline.rename(renamed)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "rename source")
    elif variant == "conditional-bridge":
        bridge = repo / "src/utiki_core_services.py"
        bridge.write_text("VALUE = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add bridge")
    elif variant == "new-or-untracked-omitted":
        baseline.write_text("VALUE = 2\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "change source")
        untracked = repo / "src/new_untracked.py"
        untracked.write_text("VALUE = 1\n", encoding="utf-8")
        include_worktree = True
    else:
        baseline.write_text("VALUE = 2\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "change source")
    candidate = _git(repo, "rev-parse", "HEAD")
    context = root / "run-context.json"
    _syntax_context(context, base, candidate, phase)
    manifest = root / "modified-python.json"
    report = root / "syntax-report.json"
    producer = repository / "tools/verify_modified_python.py"
    generation = [
        sys.executable,
        str(producer),
        "--base-ref",
        base,
        "--candidate-ref",
        candidate,
        "--run-metadata",
        str(context),
        "--write-manifest",
        str(manifest),
    ]
    if include_worktree:
        generation.append("--include-worktree")
    if write_mode:
        arguments: list[str | Path] = [
            "--base-ref",
            base,
            "--candidate-ref",
            candidate,
            "--run-metadata",
            context,
            "--write-manifest",
            manifest,
            "--probe-subject",
            probe_subject,
        ]
        if probe_subject is None:
            raise BuildError("ignored-source probe subject is absent")
        primary = probe_subject
    elif variant == "wrong-base-or-sha":
        arguments = [
            "--base-ref",
            candidate,
            "--candidate-ref",
            candidate,
            "--run-metadata",
            context,
            "--write-manifest",
            manifest,
        ]
        primary = context
    else:
        generated = subprocess.run(
            generation,
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if generated.returncode != 0:
            raise BuildError(f"syntax manifest generation failed: {generated.stderr}")
        if variant == "missing-file":
            baseline.unlink()
            include_worktree = True
        if variant not in {"outside-fixed-roots", "not-imported-by-pytest", "staged-and-working"}:
            value = _load(manifest)
            if variant == "duplicate-or-digest-race" and value["files"]:
                value["files"].append(copy.deepcopy(value["files"][0]))
            elif variant == "new-or-untracked-omitted":
                paths = [item.get("path") for item in value["files"]]
                if paths != ["src/new_untracked.py", "src/settings.py"]:
                    raise BuildError("syntax control does not contain both changed files")
                value["files"] = [value["files"][1]]
            elif variant != "missing-file":
                value["files"] = []
            _write_json(manifest, value)
        arguments = [
            "--base-ref",
            base,
            "--candidate-ref",
            candidate,
            "--run-metadata",
            context,
            "--manifest",
            manifest,
            "--report",
            report,
        ]
        if include_worktree:
            arguments.append("--include-worktree")
        if probe_subject is not None:
            arguments.extend(["--probe-subject", probe_subject])
            primary = probe_subject
        else:
            primary = manifest
    _request(
        root,
        entry,
        candidate_sha,
        arguments=arguments,
        input_path=primary,
        working_directory=repo,
    )


def _isolated_probe(
    repository: Path, root: Path, entry: dict[str, Any], candidate_sha: str
) -> None:
    control = root / "isolated-probe.json"
    _write_json(control, {"schema_version": 1, "mode": entry["variant_id"]})
    if entry["child_producer"] == "tools/utiki_egress_canary.py":
        arguments: list[str | Path] = ["--negative-probe", control]
        input_path = control
    elif entry["child_producer"] == "tools/run_utiki_browser_isolation.py":
        runtime = root / "unavailable-container-runtime"
        runtime.write_text("not an executable runtime\n", encoding="utf-8")
        arguments = [
            "--runtime-preflight-negative",
            "--runtime",
            runtime,
        ]
        input_path = runtime
    else:
        raise BuildError(f"unsupported isolated producer: {entry['child_producer']}")
    _request(
        root,
        entry,
        candidate_sha,
        arguments=arguments,
        input_path=input_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--probe-registry", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument(
        "--environment", choices=("host", "isolated", "all"), default="all"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        repository = arguments.repository_root.resolve(strict=True)
        if str(repository) not in sys.path:
            sys.path.insert(0, str(repository))
        if len(arguments.candidate_sha) != 40 or any(
            character not in "0123456789abcdef" for character in arguments.candidate_sha
        ):
            raise BuildError("candidate SHA must be 40 lowercase hexadecimal characters")
        registry = _load(arguments.probe_registry.resolve(strict=True))
        if set(registry) != {
            "schema_version",
            "registry_id",
            "phase",
            "catalog_id",
            "catalog_revision",
            "catalog_sha256",
            "probes",
        } or registry.get("schema_version") != 1 or registry.get("phase") not in {"P1", "P2"}:
            raise BuildError("negative registry identity/schema is invalid")
        phase = registry["phase"]
        if registry.get("registry_id") != f"utiki-{phase.lower()}-negative-probes":
            raise BuildError("negative registry identity/schema is invalid")
        probes = registry.get("probes")
        if not isinstance(probes, list) or len(probes) != 57:
            raise BuildError("reviewed registry must contain exactly 57 probes")
        entry_fields = {
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
        keys: set[tuple[str, str, str, str, str]] = set()
        for item in probes:
            if not isinstance(item, dict) or set(item) != entry_fields:
                raise BuildError("negative registry entry schema is invalid")
            key = tuple(
                item[field]
                for field in (
                    "case_id",
                    "obligation_id",
                    "variant_id",
                    "job_id",
                    "target",
                )
            )
            if key in keys:
                raise BuildError("negative registry contains a duplicate key")
            validate_registry_contract(item)
            keys.add(key)
        if sum(item["environment"] == "isolated" for item in probes) != 12:
            raise BuildError("negative registry must contain exactly 12 isolated probes")
        output = arguments.output_root.resolve()
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise BuildError("output root must be empty")
        selected = [
            item
            for item in probes
            if isinstance(item, dict)
            and arguments.environment in {"all", item.get("environment")}
        ]
        physical_names = [probe_directory_name(entry) for entry in selected]
        if len(set(physical_names)) != len(physical_names):
            raise BuildError("negative probe directory hash collision")
        for entry, physical_name in zip(selected, physical_names):
            root = output / physical_name
            root.mkdir()
            job_id = entry["job_id"]
            if entry["environment"] == "isolated":
                _isolated_probe(repository, root, entry, arguments.candidate_sha)
            elif job_id == "unit-contract":
                _pytest_probe(root, entry, arguments.candidate_sha)
            elif job_id == "architecture-types" and entry["child_producer"].endswith(
                "verify_utiki_ci_results.py"
            ):
                _ci_probe(repository, root, entry, arguments.candidate_sha, phase=phase)
            elif job_id == "architecture-types":
                _typecheck_probe(root, entry, arguments.candidate_sha, phase=phase)
            elif job_id == "modified-python-syntax":
                _syntax_probe(repository, root, entry, arguments.candidate_sha, phase=phase)
            else:
                raise BuildError(f"unsupported negative probe family: {_name(entry)}")
            request = _load(root / "probe.json")
            validate_request_contract(
                entry,
                request,
                probe_root=root.resolve(),
                repository_root=repository,
                phase=phase,
            )
            validate_probe_output_contract(
                entry,
                request,
                probe_request_path=(root / "probe.json").resolve(),
                probe_root=root.resolve(),
            )
        index = {
            "schema_version": 1,
            "registry_sha256": _sha256(arguments.probe_registry),
            "candidate_sha": arguments.candidate_sha,
            "environment": arguments.environment,
            "probe_count": len(selected),
            "probes": sorted(_name(item) for item in selected),
        }
        _write_json(output / "index.json", index)
        return 0
    except (BuildError, NegativeContractError, OSError, ValueError) as exc:
        sys.stderr.write(f"build_utiki_negative_inputs: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
