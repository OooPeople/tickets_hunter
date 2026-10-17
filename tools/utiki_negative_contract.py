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
from typing import Any, Mapping
from xml.etree import ElementTree


PROBE_ROOT = "{PROBE_ROOT}"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


class NegativeContractError(Exception):
    """The concrete negative probe does not match its reviewed contract."""


def probe_directory_name(entry: Mapping[str, Any]) -> str:
    """Return a short, deterministic physical directory for one execution key."""

    fields = ("case_id", "obligation_id", "variant_id", "job_id", "target")
    values = [entry.get(field) for field in fields]
    if not all(isinstance(value, str) and value for value in values):
        raise NegativeContractError("negative probe directory key is invalid")
    identity = "\0".join(str(value) for value in values).encode("utf-8")
    return f"p-{hashlib.sha256(identity).hexdigest()[:16]}"


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


def _input_paths(
    request: dict[str, Any], probe_root: Path
) -> dict[str, tuple[str, Path]]:
    inputs = request.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise NegativeContractError("canonical negative probe requires concrete inputs")
    result: dict[str, tuple[str, Path]] = {}
    seen_paths: set[Path] = set()
    root = probe_root.resolve(strict=True)
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or set(item) != {"kind", "path", "sha256"}:
            raise NegativeContractError("canonical negative input schema is invalid")
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind or kind in result:
            raise NegativeContractError("canonical negative input kind is invalid")
        relative = _safe_relative(item["path"], f"negative input {index} path")
        current = root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise NegativeContractError("negative input path traverses a symlink")
        path = current.resolve()
        if root != path and root not in path.parents:
            raise NegativeContractError("negative input escapes its probe directory")
        if path.is_symlink() or not path.is_file() or path in seen_paths:
            raise NegativeContractError("negative input is not a unique regular file")
        digest = item.get("sha256")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise NegativeContractError("negative input digest is invalid")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise NegativeContractError("negative input digest does not match")
        result[kind] = (relative, path)
        seen_paths.add(path)
    return result


def probe_semantic_output(
    entry: dict[str, Any], request: dict[str, Any], *, probe_root: Path
) -> tuple[str, Path] | None:
    """Return the reviewed semantic child report, when this producer has one."""

    producer = entry.get("child_producer")
    if producer not in {"tools/run_utiki_typecheck.py", "tools/verify_modified_python.py"}:
        return None
    _, arguments = _child(request)
    values = _option_map(
        arguments,
        flags={"--include-worktree"} if producer == "tools/verify_modified_python.py" else None,
    )
    if producer == "tools/verify_modified_python.py" and "--report" not in values:
        return None
    raw_report = values.get("--report")
    if not isinstance(raw_report, str) or not raw_report.startswith(PROBE_ROOT + "/"):
        raise NegativeContractError("negative result path is not canonical")
    relative = _safe_relative(
        raw_report.removeprefix(PROBE_ROOT + "/"), "negative result path"
    )
    root = probe_root.resolve(strict=True)
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink():
            raise NegativeContractError("negative result path traverses a symlink")
    if path.exists() and not path.is_file():
        raise NegativeContractError("negative result path is not a regular file")
    return relative, path


def validate_probe_output_contract(
    entry: dict[str, Any],
    request: dict[str, Any],
    *,
    probe_request_path: Path,
    probe_root: Path,
    require_outputs: bool = False,
) -> None:
    """Validate exact child outputs for producers with semantic reports."""

    del probe_request_path
    semantic_output = probe_semantic_output(entry, request, probe_root=probe_root)
    if semantic_output is None:
        return
    _, report_path = semantic_output
    if not require_outputs:
        if report_path.exists() or report_path.is_symlink():
            raise NegativeContractError("negative probe contains a stale child result")
        return
    result = _json(report_path)
    if entry.get("child_producer") == "tools/verify_modified_python.py":
        if (
            result.get("schema_version") != 1
            or result.get("overall_status") != "FAIL"
            or result.get("failure_reasons") != [entry["rejection_token"]]
        ):
            raise NegativeContractError("syntax child rejected for the wrong reason")
        if entry["rejection_token"] == "SYNTAX_ERROR":
            subject = request["inputs"][0]["path"].removeprefix("repo/")
            if not any(
                item.get("path") == subject and item.get("reason") == "SYNTAX_ERROR"
                for item in result.get("files", [])
                if isinstance(item, dict)
            ):
                raise NegativeContractError("syntax child rejected an unrelated path")
        elif entry["rejection_token"] == "MANIFEST_MISMATCH":
            manifest = probe_root / "modified-python.json"
            if result.get("manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
                raise NegativeContractError("syntax child rejected an unrelated manifest")
        return
    variant = entry.get("variant_id")
    if result.get("schema_version") != 1 or result.get("status") != "FAIL":
        raise NegativeContractError("typecheck child result did not fail")
    expected_reason = (
        "TYPED_SCOPE_MISSING" if variant in TYPECHECK_PATHS else "UNSAFE_MYPY_CONFIG"
    )
    if result.get("failure_reasons") != [expected_reason]:
        raise NegativeContractError("typecheck child rejected for the wrong reason")
    if result.get("mypy_exit_code") is not None:
        raise NegativeContractError("typecheck mutation unexpectedly reached mypy")
    if variant in TYPECHECK_PATHS:
        expected_affected = [TYPECHECK_PATHS[variant].removeprefix("repo/")]
        if result.get("affected_paths") != expected_affected:
            raise NegativeContractError("typecheck child rejected an unrelated path")
    elif result.get("affected_paths") not in (None, []):
        raise NegativeContractError("typecheck child reported unrelated paths")


def validate_probe_execution_contract(
    entry: dict[str, Any],
    request: dict[str, Any],
    *,
    stdout: bytes,
    stderr: bytes,
    exit_code: int,
) -> None:
    """Validate variant-specific process rejection semantics."""

    producer = entry.get("child_producer")
    if producer == "tools/verify_utiki_ci_results.py":
        variant = entry["variant_id"]
        expected_code = (
            "RUN_INDEX_SET_MISMATCH"
            if variant == "missing-job-or-target"
            else entry["rejection_token"]
        )
        if exit_code != 1 or stderr:
            raise NegativeContractError("CI checker child rejected at the wrong stage")
        try:
            result = json.loads(
                stdout.decode("utf-8"), object_pairs_hook=_strict_object
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise NegativeContractError("CI checker child did not return JSON") from exc
        if (
            not isinstance(result, dict)
            or result.get("status") != "FAIL"
            or result.get("error_code") != expected_code
            or not isinstance(result.get("message"), str)
            or not result["message"]
        ):
            raise NegativeContractError("CI checker child rejected for the wrong reason")
        subject = (
            entry["obligation_id"]
            if variant in {
                "test-and-mapping-removed",
                "removed-test-and-mapping",
                "duplicate-or-unknown-id",
                "missing-obligation-variant",
                "forged-not-applicable",
            }
            else {
                "zero-collected": "JUnit lacks",
                "empty-or-missing-result": "JUnit lacks",
                "catalog-unapproved-change": "approval record",
                "stale-run-or-sha": "candidate SHA",
                "digest-mismatch": "JUnit digest",
                "duplicate-result-or-wrong-layer": "duplicate JUnit execution result",
                "missing-job-or-target": "run index job/target",
                "failure-exit-overwritten": "original test exit code",
            }.get(variant, "testcase[0]")
        )
        if subject not in result["message"]:
            raise NegativeContractError("CI checker child rejected an unrelated object")
        return
    if producer == "tools/verify_modified_python.py":
        expected_exit = (
            2 if entry["variant_id"] in {"ignored-build-source", "wrong-base-or-sha"} else 1
        )
        if (
            exit_code != expected_exit
            or stdout
            or stderr.decode("utf-8", errors="replace").strip()
            != f"verify_modified_python: {entry['rejection_token']}"
        ):
            raise NegativeContractError("syntax child rejected at the wrong stage")
        return
    if producer != "pytest":
        return
    expected = {
        "unknown-marker": (
            2,
            "'utiki_unknown_marker' not found in `markers` configuration option",
        ),
        "empty-addopts": (
            2,
            "'utiki_unknown_marker' not found in `markers` configuration option",
        ),
        "invalid-pytest-config": (
            4,
            "Unknown config option: utiki_unknown_config",
        ),
    }.get(entry.get("variant_id"))
    if expected is None:
        raise NegativeContractError("unknown pytest negative execution contract")
    expected_exit, expected_reason = expected
    combined = stdout.decode("utf-8", errors="replace") + stderr.decode(
        "utf-8", errors="replace"
    )
    if exit_code != expected_exit:
        raise NegativeContractError("pytest child rejected at the wrong stage")
    if expected_reason not in combined:
        raise NegativeContractError("pytest child rejected for the wrong reason")


TYPECHECK_SEED_FILES = (
    "src/platforms/utiki/auth.py",
    "src/platforms/utiki/profiles.py",
    "src/platforms/utiki/selection.py",
)
TYPECHECK_SEED_CONTENT = "VALUE: int = 1\n"
TYPECHECK_MANIFEST_CONTENT = "\n".join(TYPECHECK_SEED_FILES) + "\n"
TYPECHECK_CONFIG_CONTENT = (
    "[mypy]\n"
    "python_version = 3.11\n"
    "strict = True\n"
    "warn_unused_configs = True\n"
    "mypy_path = src\n"
)
TYPECHECK_FAKE_MYPY_CONTENT = "#!/usr/bin/env python3\nraise SystemExit(0)\n"
PYTEST_CONFIG_CONTENT = "[pytest]\n"


def _canonical_text_file(path: Path, expected: str, label: str) -> None:
    try:
        actual = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise NegativeContractError(f"{label} is unreadable") from exc
    if actual != expected:
        raise NegativeContractError(f"{label} differs from its reviewed content")


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
    variant: str,
    arguments: list[str],
    inputs: dict[str, tuple[str, Path]],
    child: dict[str, Any],
) -> None:
    marker_source = "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n"
    source_ref = f"{PROBE_ROOT}/test_probe.py"
    config_ref = f"{PROBE_ROOT}/pytest.ini"
    if child.get("working_directory") != PROBE_ROOT:
        raise NegativeContractError("pytest negative probe cwd is not canonical")
    if variant in {"unknown-marker", "empty-addopts"}:
        if set(inputs) != {"pytest-probe-source", "pytest-config"}:
            raise NegativeContractError("pytest source input contract differs from review")
        relative, source = inputs["pytest-probe-source"]
        if relative != "test_probe.py" or source.read_text(encoding="utf-8") != marker_source:
            raise NegativeContractError("pytest marker source is not canonical")
        config_relative, config = inputs["pytest-config"]
        if config_relative != "pytest.ini":
            raise NegativeContractError("pytest config path is not canonical")
        _canonical_text_file(config, PYTEST_CONFIG_CONTENT, "pytest config")
    if variant == "unknown-marker":
        if arguments != [
            "-c",
            config_ref,
            "--strict-config",
            "--strict-markers",
            source_ref,
        ]:
            raise NegativeContractError("unknown-marker arguments are not canonical")
    elif variant == "empty-addopts":
        if arguments != [
            "-o",
            "addopts=",
            "-c",
            config_ref,
            "--strict-config",
            "--strict-markers",
            source_ref,
        ]:
            raise NegativeContractError("empty-addopts arguments are not canonical")
    elif variant == "invalid-pytest-config":
        if arguments != [
            "-c",
            f"{PROBE_ROOT}/pytest.ini",
            "--strict-config",
            "--strict-markers",
            source_ref,
        ]:
            raise NegativeContractError("invalid-config arguments are not canonical")
        if set(inputs) != {"pytest-config", "pytest-probe-source"}:
            raise NegativeContractError("invalid-config input contract differs from review")
        config_relative, config = inputs["pytest-config"]
        source_relative, source = inputs["pytest-probe-source"]
        if config_relative != "pytest.ini" or config.read_text(encoding="utf-8") != (
            "[pytest]\nutiki_unknown_config = true\n"
        ):
            raise NegativeContractError("invalid-config payload is not canonical")
        if source_relative != "test_probe.py" or source.read_text(
            encoding="utf-8"
        ) != "def test_probe():\n    pass\n":
            raise NegativeContractError("invalid-config test source is not canonical")
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
TYPECHECK_REQUIRED_ROOTS = (
    "src/platforms/utiki/domain",
    "src/platforms/utiki/application",
    "src/platforms/utiki/adapters",
)
TYPECHECK_SUPPORT_PATHS = {
    "typecheck-manifest": "repo/ci/utiki-typed-scope.txt",
    "typecheck-mypy": "fake-mypy.py",
    "typecheck-seed-auth": "repo/src/platforms/utiki/auth.py",
    "typecheck-seed-profiles": "repo/src/platforms/utiki/profiles.py",
    "typecheck-seed-selection": "repo/src/platforms/utiki/selection.py",
}


def _typecheck_relevant_sources(probe_root: Path) -> set[str]:
    repo = probe_root / "repo"
    paths: set[str] = set()
    for relative_root in TYPECHECK_REQUIRED_ROOTS:
        directory = repo / relative_root
        if directory.is_dir() and not directory.is_symlink():
            paths.update(
                path.relative_to(repo).as_posix()
                for path in directory.rglob("*.py")
                if path.is_file() and not path.is_symlink()
            )
    candidate_singletons = {
        *TYPECHECK_SEED_FILES,
        *(value.removeprefix("repo/") for value in TYPECHECK_PATHS.values()),
    }
    paths.update(
        relative
        for relative in candidate_singletons
        if (repo / relative).is_file() and not (repo / relative).is_symlink()
    )
    return paths


def _validate_typecheck(
    variant: str,
    arguments: list[str],
    inputs: dict[str, tuple[str, Path]],
    probe_root: Path,
    phase: str,
) -> None:
    if phase not in {"P1", "P2"}:
        raise NegativeContractError("typecheck phase is untrusted")
    flags = {
        "--root",
        "--manifest",
        "--config",
        "--report",
    }
    if phase == "P1":
        flags.add("--mypy")
    else:
        flags.add("--phase")
    if variant in TYPECHECK_PATHS:
        flags.add("--probe-subject")
    values = _require_exact_options(arguments, flags)
    expected_static = {
        "--root": f"{PROBE_ROOT}/repo",
        "--manifest": f"{PROBE_ROOT}/repo/ci/utiki-typed-scope.txt",
        "--config": f"{PROBE_ROOT}/repo/ci/mypy-utiki.ini",
        "--report": f"{PROBE_ROOT}/typecheck-report.json",
    }
    if phase == "P1":
        expected_static["--mypy"] = f"{PROBE_ROOT}/fake-mypy.py"
    else:
        expected_static["--phase"] = "P2"
    if any(values.get(key) != value for key, value in expected_static.items()):
        raise NegativeContractError("typecheck arguments are not canonical")
    expected_inputs = dict(TYPECHECK_SUPPORT_PATHS)
    if phase == "P2":
        expected_inputs.pop("typecheck-mypy")
    expected_inputs["mutated-typecheck-input"] = (
        TYPECHECK_PATHS.get(variant, "repo/ci/mypy-utiki.ini")
    )
    if variant in TYPECHECK_PATHS:
        expected_inputs["typecheck-config"] = "repo/ci/mypy-utiki.ini"
    if {kind: relative for kind, (relative, _) in inputs.items()} != expected_inputs:
        raise NegativeContractError("typecheck input set differs from review")
    for kind, relative in TYPECHECK_SUPPORT_PATHS.items():
        if phase == "P2" and kind == "typecheck-mypy":
            continue
        _, path = inputs[kind]
        if kind == "typecheck-manifest":
            _canonical_text_file(path, TYPECHECK_MANIFEST_CONTENT, kind)
        elif kind == "typecheck-mypy":
            _canonical_text_file(path, TYPECHECK_FAKE_MYPY_CONTENT, kind)
        else:
            _canonical_text_file(path, TYPECHECK_SEED_CONTENT, kind)
    config = inputs.get("typecheck-config", inputs["mutated-typecheck-input"])[1]
    expected_config = TYPECHECK_CONFIG_CONTENT
    if variant == "broad-ignore-any":
        expected_config += "ignore_errors = True\n"
    _canonical_text_file(config, expected_config, "typecheck config")
    mutation_relative, mutation_path = inputs["mutated-typecheck-input"]
    if variant in TYPECHECK_PATHS:
        if values.get("--probe-subject") != (
            PROBE_ROOT + "/" + mutation_relative
        ):
            raise NegativeContractError("typecheck subject does not match its execution key")
        _canonical_text_file(
            mutation_path, TYPECHECK_SEED_CONTENT, "typecheck mutation subject"
        )
    elif variant == "broad-ignore-any":
        if "--probe-subject" in values:
            raise NegativeContractError("unsafe-config probe has an unexpected subject")
    else:
        raise NegativeContractError("unknown typecheck negative contract")
    expected_sources = set(TYPECHECK_SEED_FILES)
    if variant in TYPECHECK_PATHS:
        expected_sources.add(TYPECHECK_PATHS[variant].removeprefix("repo/"))
    if _typecheck_relevant_sources(probe_root) != expected_sources:
        raise NegativeContractError("typecheck relevant source set differs from review")


def _validate_syntax(
    variant: str,
    arguments: list[str],
    input_path: Path,
    request: dict[str, Any],
    probe_root: Path,
    phase: str,
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
    context = _json(probe_root / "run-context.json")
    if context.get("phase") != phase:
        raise NegativeContractError("syntax run phase differs from the reviewed registry")
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
            settings = repo / "src/settings.py"
            untracked = repo / "src/new_untracked.py"
            if (
                len(files) != 1
                or not isinstance(files[0], dict)
                or files[0].get("path") != "src/settings.py"
                or files[0].get("required_action") != "compile"
                or values.get("--include-worktree") is not True
                or settings.is_symlink()
                or not settings.is_file()
                or settings.read_bytes() != b"VALUE = 2\n"
                or files[0].get("sha256") != hashlib.sha256(settings.read_bytes()).hexdigest()
                or untracked.is_symlink()
                or not untracked.is_file()
                or untracked.read_bytes() != b"VALUE = 1\n"
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
    entry: dict[str, Any], arguments: list[str], input_path: Path,
    request: dict[str, Any], phase: str,
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
        values.get("--phase") != phase
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
    phase: str = "P1",
) -> None:
    """Validate one concrete request against its exact reviewed mutation."""

    del repository_root  # Reserved for future repository-bound contracts.
    validate_registry_contract(entry)
    child, arguments = _child(request)
    inputs = _input_paths(request, probe_root)
    mutation_kinds = entry.get("input_kinds", [])
    if (
        not isinstance(mutation_kinds, list)
        or len(mutation_kinds) != 1
        or mutation_kinds[0] not in inputs
    ):
        raise NegativeContractError("negative mutation input differs from review")
    input_path = inputs[mutation_kinds[0]][1]
    if request.get("mutation") != entry.get("mutation"):
        raise NegativeContractError("negative request mutation differs from review")
    producer = entry.get("child_producer")
    variant = entry.get("variant_id")
    if child.get("producer") != producer or child.get("kind") != entry.get("child_kind"):
        raise NegativeContractError("negative child identity differs from review")
    if producer == "pytest":
        _validate_pytest(variant, arguments, inputs, child)
    elif producer in {"tools/utiki_egress_canary.py", "tools/run_utiki_browser_isolation.py"}:
        if set(inputs) != set(mutation_kinds):
            raise NegativeContractError("negative input set differs from review")
        _validate_egress(variant, arguments, input_path)
    elif producer == "tools/run_utiki_typecheck.py":
        _validate_typecheck(variant, arguments, inputs, probe_root, phase)
    elif producer == "tools/verify_modified_python.py":
        if set(inputs) != set(mutation_kinds):
            raise NegativeContractError("negative input set differs from review")
        _validate_syntax(variant, arguments, input_path, request, probe_root, phase)
    elif producer == "tools/verify_utiki_ci_results.py":
        if set(inputs) != set(mutation_kinds):
            raise NegativeContractError("negative input set differs from review")
        _validate_ci(entry, arguments, input_path, request, phase)
    else:
        raise NegativeContractError("negative producer has no canonical contract")
