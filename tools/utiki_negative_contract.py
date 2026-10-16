#!/usr/bin/env python3
"""Canonical, per-execution-key contracts for P1 negative probes.

The negative registry identifies the reviewed mutation, but an identity and a
rejection token are not sufficient: two mutations that happen to emit the same
token must not be interchangeable.  This module validates the concrete child
arguments and the mutation payload before execution and again while the final
evidence is verified.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree


PROBE_ROOT = "{PROBE_ROOT}"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class NegativeContractError(Exception):
    """The concrete negative probe does not match its reviewed contract."""


def argument_contract_id(entry: dict[str, Any]) -> str:
    return f"{entry.get('obligation_id')}:{entry.get('variant_id')}:argv-v1"


def payload_contract_id(entry: dict[str, Any]) -> str:
    return f"{entry.get('obligation_id')}:{entry.get('variant_id')}:payload-v1"


def validate_registry_contract(entry: dict[str, Any]) -> None:
    if entry.get("argument_contract") != argument_contract_id(entry):
        raise NegativeContractError("negative argument contract identity is invalid")
    if entry.get("payload_contract") != payload_contract_id(entry):
        raise NegativeContractError("negative payload contract identity is invalid")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise NegativeContractError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NegativeContractError(f"cannot read contract JSON: {path}") from exc
    if not isinstance(value, dict):
        raise NegativeContractError("contract JSON root must be an object")
    return value


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise NegativeContractError(f"{label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise NegativeContractError(f"{label} is unsafe")
    return value


def _input_path(request: dict[str, Any], probe_root: Path) -> Path:
    inputs = request.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise NegativeContractError("canonical negative probe requires exactly one input")
    item = inputs[0]
    if set(item) != {"kind", "path", "sha256"}:
        raise NegativeContractError("canonical negative input schema is invalid")
    relative = _safe_relative(item["path"], "negative input path")
    path = (probe_root / relative).resolve()
    if probe_root != path and probe_root not in path.parents:
        raise NegativeContractError("negative input escapes its probe directory")
    if path.is_symlink() or not path.is_file():
        raise NegativeContractError("negative input is not a regular file")
    digest = item.get("sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise NegativeContractError("negative input digest is invalid")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise NegativeContractError("negative input digest does not match")
    return path


def _expand(probe_root: Path, value: str) -> str:
    if value == PROBE_ROOT:
        return str(probe_root)
    prefix = PROBE_ROOT + "/"
    if not value.startswith(prefix):
        return value
    relative = _safe_relative(value[len(prefix) :], "negative argument path")
    path = (probe_root / relative).resolve()
    if probe_root not in path.parents:
        raise NegativeContractError("negative argument path escapes its probe directory")
    return str(path)


def _child(request: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    child = request.get("child")
    if not isinstance(child, dict) or set(child) != {
        "kind",
        "producer",
        "arguments",
        "working_directory",
    }:
        raise NegativeContractError("canonical negative child schema is invalid")
    arguments = child.get("arguments")
    if not isinstance(arguments, list) or any(
        not isinstance(item, str) or not item for item in arguments
    ):
        raise NegativeContractError("canonical negative child arguments are invalid")
    return child, arguments


def _option_map(arguments: list[str], *, flags: set[str] | None = None) -> dict[str, str | bool]:
    flags = flags or set()
    result: dict[str, str | bool] = {}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option in flags:
            if option in result:
                raise NegativeContractError(f"duplicate negative child flag: {option}")
            result[option] = True
            index += 1
            continue
        if not option.startswith("--") or index + 1 >= len(arguments):
            raise NegativeContractError("negative child option sequence is invalid")
        if option in result:
            raise NegativeContractError(f"duplicate negative child option: {option}")
        result[option] = arguments[index + 1]
        index += 2
    return result


def _require_exact_options(
    arguments: list[str], expected: set[str], *, flags: set[str] | None = None
) -> dict[str, str | bool]:
    values = _option_map(arguments, flags=flags)
    if set(values) != expected:
        raise NegativeContractError("negative child option contract differs from review")
    return values


def _properties(testcase: ElementTree.Element) -> dict[str, str]:
    properties = testcase.find("properties")
    if properties is None:
        return {}
    return {
        item.get("name", ""): item.get("value", "")
        for item in properties.findall("property")
    }


def _validate_pytest(
    variant: str, arguments: list[str], input_path: Path, child: dict[str, Any]
) -> None:
    marker_source = "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n"
    source_ref = f"{PROBE_ROOT}/test_probe.py"
    if child.get("working_directory") != PROBE_ROOT:
        raise NegativeContractError("pytest negative probe cwd is not canonical")
    if variant == "unknown-marker":
        if arguments != ["--strict-config", "--strict-markers", source_ref]:
            raise NegativeContractError("unknown-marker arguments are not canonical")
        if input_path.name != "test_probe.py" or input_path.read_text(encoding="utf-8") != marker_source:
            raise NegativeContractError("unknown-marker payload is not canonical")
    elif variant == "empty-addopts":
        if arguments != [
            "-o",
            "addopts=",
            "--strict-config",
            "--strict-markers",
            source_ref,
        ]:
            raise NegativeContractError("empty-addopts arguments are not canonical")
        if input_path.name != "test_probe.py" or input_path.read_text(encoding="utf-8") != marker_source:
            raise NegativeContractError("empty-addopts payload is not canonical")
    elif variant == "invalid-pytest-config":
        if arguments != [
            "-c",
            f"{PROBE_ROOT}/pytest.ini",
            "--strict-config",
            "--strict-markers",
            source_ref,
        ]:
            raise NegativeContractError("invalid-config arguments are not canonical")
        if input_path.name != "pytest.ini" or input_path.read_text(encoding="utf-8") != (
            "[pytest]\nutiki_unknown_config = true\n"
        ):
            raise NegativeContractError("invalid-config payload is not canonical")
    else:
        raise NegativeContractError("unknown pytest negative contract")


def _validate_egress(variant: str, arguments: list[str], input_path: Path) -> None:
    if variant == "insufficient-isolation-permission":
        if arguments != [
            "--runtime-preflight-negative",
            "--runtime",
            f"{PROBE_ROOT}/unavailable-container-runtime",
        ]:
            raise NegativeContractError("runtime-preflight arguments are not canonical")
        if input_path.name != "unavailable-container-runtime" or input_path.read_text(
            encoding="utf-8"
        ) != "not an executable runtime\n":
            raise NegativeContractError("runtime-preflight payload is not canonical")
        return
    if arguments != ["--negative-probe", f"{PROBE_ROOT}/isolated-probe.json"]:
        raise NegativeContractError("isolated probe arguments are not canonical")
    payload = _json(input_path)
    if payload != {"schema_version": 1, "mode": variant}:
        raise NegativeContractError("isolated probe mode does not match its execution key")


TYPECHECK_PATHS = {
    "new-domain-file": "repo/src/platforms/utiki/domain/state.py",
    "new-application-file": "repo/src/platforms/utiki/application/engine.py",
    "new-port-file": "repo/src/platforms/utiki/application/ports.py",
    "new-adapter-file": "repo/src/platforms/utiki/adapters/browser.py",
    "new-bootstrap-file": "repo/src/platforms/utiki/bootstrap.py",
    "new-current-config-file": "repo/src/utiki_config.py",
    "new-keyword-file": "repo/src/keyword_rules.py",
    "new-core-bridge-file": "repo/src/utiki_core_services.py",
    "new-upgrade-tool-file": "repo/tools/migrations/convert_utiki_config.py",
}


def _validate_typecheck(
    variant: str, arguments: list[str], input_path: Path, request: dict[str, Any]
) -> None:
    flags = {
        "--root",
        "--manifest",
        "--config",
        "--mypy",
        "--report",
    }
    if variant in TYPECHECK_PATHS:
        flags.add("--probe-subject")
    values = _require_exact_options(arguments, flags)
    expected_static = {
        "--root": f"{PROBE_ROOT}/repo",
        "--manifest": f"{PROBE_ROOT}/repo/ci/utiki-typed-scope.txt",
        "--config": f"{PROBE_ROOT}/repo/ci/mypy-utiki.ini",
        "--mypy": f"{PROBE_ROOT}/fake-mypy",
        "--report": f"{PROBE_ROOT}/typecheck-report.json",
    }
    if any(values.get(key) != value for key, value in expected_static.items()):
        raise NegativeContractError("typecheck arguments are not canonical")
    relative = request["inputs"][0]["path"]
    if variant in TYPECHECK_PATHS:
        if relative != TYPECHECK_PATHS[variant] or values.get("--probe-subject") != (
            PROBE_ROOT + "/" + relative
        ):
            raise NegativeContractError("typecheck subject does not match its execution key")
        if input_path.read_text(encoding="utf-8") != "VALUE: int = 1\n":
            raise NegativeContractError("typecheck new-file payload is not canonical")
    elif variant == "broad-ignore-any":
        if relative != "repo/ci/mypy-utiki.ini" or "ignore_errors = True" not in input_path.read_text(
            encoding="utf-8"
        ).splitlines():
            raise NegativeContractError("unsafe mypy-config payload is not canonical")
    else:
        raise NegativeContractError("unknown typecheck negative contract")


def _validate_syntax(
    variant: str,
    arguments: list[str],
    input_path: Path,
    request: dict[str, Any],
    probe_root: Path,
) -> None:
    flags: set[str] = set()
    option_names = {
        "--base-ref",
        "--candidate-ref",
        "--run-metadata",
    }
    if variant in {"ignored-build-source", "wrong-base-or-sha"}:
        option_names.add("--write-manifest")
    else:
        option_names.update({"--manifest", "--report"})
    if variant in {
        "outside-fixed-roots",
        "not-imported-by-pytest",
        "staged-and-working",
        "ignored-build-source",
    }:
        option_names.add("--probe-subject")
    if variant in {"staged-and-working", "new-or-untracked-omitted", "missing-file"}:
        option_names.add("--include-worktree")
        flags.add("--include-worktree")
    values = _require_exact_options(arguments, option_names, flags=flags)
    base = values.get("--base-ref")
    candidate = values.get("--candidate-ref")
    if not isinstance(base, str) or GIT_SHA_RE.fullmatch(base) is None:
        raise NegativeContractError("syntax base ref is invalid")
    if not isinstance(candidate, str) or GIT_SHA_RE.fullmatch(candidate) is None:
        raise NegativeContractError("syntax candidate ref is invalid")
    if values.get("--run-metadata") != f"{PROBE_ROOT}/run-context.json":
        raise NegativeContractError("syntax run metadata path is not canonical")
    relative = request["inputs"][0]["path"]
    repo = probe_root / "repo"
    if variant in {"outside-fixed-roots", "not-imported-by-pytest"}:
        expected_relative = {
            ("A06", "outside-fixed-roots"): "repo/misc/broken.py",
            ("A06", "not-imported-by-pytest"): "repo/tests/helpers/broken_helper.py",
            ("H11", "outside-fixed-roots"): "repo/external/broken.py",
        }.get((request.get("case_id"), variant))
        if expected_relative is None or relative != expected_relative or values.get("--probe-subject") != (
            PROBE_ROOT + "/" + relative
        ) or input_path.read_text(encoding="utf-8") != "def broken(:\n":
            raise NegativeContractError("outside-root syntax payload is not canonical")
    elif variant == "staged-and-working":
        if relative != "repo/src/settings.py" or values.get("--probe-subject") != (
            PROBE_ROOT + "/" + relative
        ) or values.get("--include-worktree") is not True:
            raise NegativeContractError("staged/worktree syntax payload is not canonical")
        if input_path.read_text(encoding="utf-8") != "def broken(:\n":
            raise NegativeContractError("staged/worktree syntax source is not canonical")
    elif variant == "ignored-build-source":
        if relative != "repo/generated_source.py" or values.get("--probe-subject") != (
            PROBE_ROOT + "/" + relative
        ) or input_path.read_text(encoding="utf-8") != "VALUE = 1\n":
            raise NegativeContractError("ignored-source payload is not canonical")
    elif variant == "wrong-base-or-sha":
        if relative != "run-context.json" or base != candidate:
            raise NegativeContractError("wrong-base payload is not canonical")
        context = _json(input_path)
        if context.get("base_sha") == base or context.get("candidate_sha") != candidate:
            raise NegativeContractError("wrong-base context does not demonstrate a mismatch")
    else:
        if relative != "modified-python.json" or values.get("--manifest") != (
            PROBE_ROOT + "/modified-python.json"
        ):
            raise NegativeContractError("syntax manifest input is not canonical")
        manifest = _json(input_path)
        files = manifest.get("files")
        if not isinstance(files, list):
            raise NegativeContractError("syntax manifest files are invalid")
        if variant == "duplicate-or-digest-race":
            paths = [item.get("path") for item in files if isinstance(item, dict)]
            if len(paths) < 2 or len(paths) == len(set(paths)):
                raise NegativeContractError("duplicate syntax manifest payload is absent")
        elif variant == "new-or-untracked-omitted":
            if (
                files
                or values.get("--include-worktree") is not True
                or not (repo / "src/new_untracked.py").is_file()
            ):
                raise NegativeContractError("untracked omitted payload is not canonical")
        elif variant == "missing-file":
            paths = [item.get("path") for item in files if isinstance(item, dict)]
            if (
                values.get("--include-worktree") is not True
                or "src/settings.py" not in paths
                or (repo / "src/settings.py").exists()
            ):
                raise NegativeContractError("missing-file payload is not canonical")
        elif variant == "explicit-deletion-rename":
            if files or not (repo / "src/renamed.py").is_file() or (repo / "src/settings.py").exists():
                raise NegativeContractError("rename/deletion payload is not canonical")
        elif variant == "conditional-bridge":
            if (
                files
                or not (repo / "src/utiki_core_services.py").is_file()
                or (repo / "src/renamed.py").exists()
                or (repo / "src/new_untracked.py").exists()
            ):
                raise NegativeContractError("conditional-bridge payload is not canonical")
        elif variant == "self-or-zero-compile":
            if (
                files
                or not (repo / "src/settings.py").is_file()
                or (repo / "src/utiki_core_services.py").exists()
                or (repo / "src/renamed.py").exists()
                or (repo / "src/new_untracked.py").exists()
            ):
                raise NegativeContractError("zero-compile payload is not canonical")
        else:
            raise NegativeContractError("unknown syntax manifest negative contract")


def _ci_execution_keys(manifest: dict[str, Any]) -> list[tuple[str, str, str]]:
    values: list[tuple[str, str, str]] = []
    for case in manifest.get("cases", []):
        if not isinstance(case, dict):
            continue
        for execution in case.get("executions", []):
            if isinstance(execution, dict):
                values.append(
                    (
                        case.get("id"),
                        execution.get("obligation_id"),
                        execution.get("variant_id"),
                    )
                )
    return values


def _validate_ci(
    entry: dict[str, Any], arguments: list[str], input_path: Path, request: dict[str, Any]
) -> None:
    variant = entry["variant_id"]
    aggregate = variant == "missing-job-or-target"
    options = {
        "--required-catalog",
        "--approved-catalog-sha256",
        "--execution-manifest",
        "--phase",
        "--run-id",
        "--candidate-sha",
    }
    if aggregate:
        options.add("--run-index")
    else:
        options.update({"--junit", "--run-metadata", "--job-id", "--target"})
    values = _require_exact_options(arguments, options)
    if (
        values.get("--phase") != "P1"
        or values.get("--candidate-sha") != request.get("candidate_sha")
        or (not aggregate and values.get("--job-id") != "architecture-types")
        or (not aggregate and values.get("--target") != "source-linux-x64")
    ):
        raise NegativeContractError("CI checker arguments are not canonical")
    if aggregate:
        if request["inputs"][0]["path"] != "case/run-index.json" or values.get(
            "--run-index"
        ) != f"{PROBE_ROOT}/case/run-index.json":
            raise NegativeContractError("run-index payload path is not canonical")
        index = _json(input_path)
        if len(index.get("runs", [])) == 0:
            raise NegativeContractError("missing-job run index is not a concrete partial run")
        return
    own_key = (entry["case_id"], entry["obligation_id"], entry["variant_id"])
    if variant in {
        "test-and-mapping-removed",
        "removed-test-and-mapping",
        "duplicate-or-unknown-id",
        "missing-obligation-variant",
        "forged-not-applicable",
    }:
        manifest = _json(input_path)
        keys = _ci_execution_keys(manifest)
        count = keys.count(own_key)
        if variant in {
            "test-and-mapping-removed",
            "removed-test-and-mapping",
            "missing-obligation-variant",
        } and count != 0:
            raise NegativeContractError("removed mapping does not match its execution key")
        if variant == "duplicate-or-unknown-id" and count != 2:
            raise NegativeContractError("duplicate mapping does not match its execution key")
        if variant == "forged-not-applicable":
            matching = [
                execution
                for case in manifest.get("cases", [])
                if isinstance(case, dict) and case.get("id") == own_key[0]
                for execution in case.get("executions", [])
                if isinstance(execution, dict)
                and execution.get("obligation_id") == own_key[1]
                and execution.get("variant_id") == own_key[2]
            ]
            if len(matching) != 1 or matching[0].get("status") != "NOT_APPLICABLE":
                raise NegativeContractError("forged applicability payload is not canonical")
        return
    if variant == "catalog-unapproved-change":
        catalog = _json(input_path)
        cases = catalog.get("cases")
        if not isinstance(cases, list) or not cases or not str(cases[0].get("scenario", "")).endswith(
            " mutated"
        ):
            raise NegativeContractError("unapproved catalog mutation is not canonical")
        return
    if variant in {"zero-collected", "stale-run-or-sha", "failure-exit-overwritten"}:
        metadata = _json(input_path)
        if variant == "zero-collected" and (
            metadata.get("collected_count") != 0 or metadata.get("selected_count") != 0
        ):
            raise NegativeContractError("zero-collected metadata is not canonical")
        if variant == "stale-run-or-sha" and metadata.get("candidate_sha") != "2" * 40:
            raise NegativeContractError("stale-run metadata is not canonical")
        if variant == "failure-exit-overwritten" and metadata.get("exit_codes", {}).get("test") != 7:
            raise NegativeContractError("failure-exit metadata is not canonical")
        return
    try:
        tree = ElementTree.parse(input_path)
    except (OSError, ElementTree.ParseError) as exc:
        if variant == "digest-mismatch" and input_path.read_bytes().endswith(b"\nmutated\n"):
            return
        raise NegativeContractError("negative JUnit payload is invalid") from exc
    testcases = tree.getroot().findall("testcase")
    if variant == "empty-or-missing-result":
        if testcases:
            raise NegativeContractError("empty-result JUnit still has testcases")
        return
    if variant == "duplicate-result-or-wrong-layer":
        names = [(item.get("classname"), item.get("name")) for item in testcases]
        if len(names) == len(set(names)):
            raise NegativeContractError("duplicate JUnit result is absent")
        return
    outcome = {
        "all-skipped": ("outcome", "skipped", "skipped", True),
        "required-outcome-skip": ("outcome", "skipped", "skipped", False),
        "required-outcome-xfail": ("wasxfail", "true", "skipped", False),
        "required-outcome-strict-xpass": ("wasxfail", "true", "failure", False),
        "required-outcome-non-strict-xpass": ("wasxfail", "true", None, False),
        "required-outcome-setup-error": ("setup_outcome", "error", "error", False),
        "required-outcome-teardown-error": ("teardown_outcome", "error", "error", False),
    }.get(variant)
    if outcome is not None:
        property_name, property_value, child_name, all_nodes = outcome
        matched = [
            node
            for node in testcases
            if _properties(node).get(property_name) == property_value
            and (child_name is None or node.find(child_name) is not None)
            and (
                child_name is not None
                or (node.find("failure") is None and node.find("skipped") is None and node.find("error") is None)
            )
        ]
        if (
            not matched
            or (all_nodes and len(matched) != len(testcases))
            or (not all_nodes and len(matched) != 1)
        ):
            raise NegativeContractError("JUnit outcome does not match its execution key")
        return
    if variant == "digest-mismatch" and input_path.read_bytes().endswith(b"\nmutated\n"):
        return
    raise NegativeContractError("unknown CI negative contract")


def validate_request_contract(
    entry: dict[str, Any],
    request: dict[str, Any],
    *,
    probe_root: Path,
    repository_root: Path,
) -> None:
    """Validate one concrete request against its exact reviewed mutation."""

    del repository_root  # Reserved for future repository-bound contracts.
    validate_registry_contract(entry)
    child, arguments = _child(request)
    input_path = _input_path(request, probe_root)
    actual_input_kinds = sorted(
        item.get("kind")
        for item in request["inputs"]
        if isinstance(item, dict)
    )
    if actual_input_kinds != sorted(entry.get("input_kinds", [])):
        raise NegativeContractError("negative input kinds differ from review")
    if request.get("mutation") != entry.get("mutation"):
        raise NegativeContractError("negative request mutation differs from review")
    producer = entry.get("child_producer")
    variant = entry.get("variant_id")
    if child.get("producer") != producer or child.get("kind") != entry.get("child_kind"):
        raise NegativeContractError("negative child identity differs from review")
    if producer == "pytest":
        _validate_pytest(variant, arguments, input_path, child)
    elif producer in {"tools/utiki_egress_canary.py", "tools/run_utiki_browser_isolation.py"}:
        _validate_egress(variant, arguments, input_path)
    elif producer == "tools/run_utiki_typecheck.py":
        _validate_typecheck(variant, arguments, input_path, request)
    elif producer == "tools/verify_modified_python.py":
        _validate_syntax(variant, arguments, input_path, request, probe_root)
    elif producer == "tools/verify_utiki_ci_results.py":
        _validate_ci(entry, arguments, input_path, request)
    else:
        raise NegativeContractError("negative producer has no canonical contract")


def validate_transcript_contract(
    entry: dict[str, Any],
    request: dict[str, Any],
    transcript_child: dict[str, Any],
    *,
    probe_root: Path,
    repository_root: Path,
) -> None:
    """Bind a captured command/cwd transcript back to the concrete request."""

    validate_request_contract(
        entry, request, probe_root=probe_root, repository_root=repository_root
    )
    child, arguments = _child(request)
    expanded = [_expand(probe_root, value) for value in arguments]
    command = transcript_child.get("command")
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
        raise NegativeContractError("negative transcript command is invalid")
    if child["kind"] == "repository-python":
        producer_path = str((repository_root / child["producer"]).resolve())
        if command[1:] != [producer_path, *expanded]:
            raise NegativeContractError("negative transcript command differs from request")
    elif child["kind"] == "pytest-module":
        if command[1:] != ["-m", "pytest", *expanded]:
            raise NegativeContractError("negative pytest transcript differs from request")
    else:
        raise NegativeContractError("negative transcript child kind is invalid")
    raw_cwd = child.get("working_directory")
    expected_cwd = repository_root if raw_cwd is None else Path(_expand(probe_root, raw_cwd))
    if Path(str(transcript_child.get("working_directory"))).resolve() != expected_cwd.resolve():
        raise NegativeContractError("negative transcript cwd differs from request")
