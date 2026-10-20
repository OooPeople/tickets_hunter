"""Direct P1 negative probes bound by the reviewed execution registry."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from tests.architecture import test_modified_python_gate as syntax_gate
from tests.architecture import test_utiki_ci_results_gate as result_gate
from tests.architecture import test_utiki_typecheck_gate as type_gate


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_pytest(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments, str(path)],
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=False,
    )


def test_h05_unknown_marker_runs_pytest_and_fails_nonzero(tmp_path):
    test_file = tmp_path / "test_unknown_marker.py"
    test_file.write_text(
        "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n",
        encoding="utf-8",
    )

    result = _run_pytest(test_file, "--strict-config", "--strict-markers")

    assert result.returncode != 0
    assert "utiki_unknown_marker" in result.stdout + result.stderr


def test_h05_invalid_config_runs_pytest_and_fails_nonzero(tmp_path):
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nutiki_unknown_config = true\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "test_valid.py"
    test_file.write_text("def test_probe():\n    pass\n", encoding="utf-8")

    result = _run_pytest(test_file, "--strict-config", "--strict-markers")

    assert result.returncode != 0
    assert "utiki_unknown_config" in result.stdout + result.stderr


def test_h05_empty_addopts_keeps_explicit_strict_flags_and_fails_nonzero(tmp_path):
    test_file = tmp_path / "test_unknown_marker.py"
    test_file.write_text(
        "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n",
        encoding="utf-8",
    )

    result = _run_pytest(
        test_file,
        "-o",
        "addopts=",
        "--strict-config",
        "--strict-markers",
    )

    assert result.returncode != 0
    assert "utiki_unknown_marker" in result.stdout + result.stderr


def _ci_case(root: Path, job_id: str = "architecture-types") -> dict[str, object]:
    manifest_path, manifest, catalog, profiles = result_gate._make_manifest(root)
    target = profiles[result_gate.PHASE][job_id]["targets"][0]
    run = result_gate._make_run(
        root,
        manifest_path,
        manifest,
        profiles,
        job_id,
        target,
    )
    return {
        "root": root,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "catalog": catalog,
        "profiles": profiles,
        "run": run,
        "job_id": job_id,
        "target": target,
    }


def _run_ci_case(case: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return result_gate._run_checker(
        *result_gate._single_arguments(
            case["manifest_path"],
            case["run"],
            case["job_id"],
            case["target"],
        )
    )


def _write_manifest(case: dict[str, object], manifest: dict[str, object]) -> None:
    result_gate._write_json(case["manifest_path"], manifest)


def test_a05_zero_collected_is_rejected_by_real_checker(tmp_path):
    case = _ci_case(tmp_path)
    positive = _run_ci_case(case)
    assert positive.returncode == 0, positive.stderr or positive.stdout
    assert json.loads(positive.stdout)["status"] == "PASS"

    tree = ElementTree.parse(case["run"]["junit"])
    suite = tree.getroot()
    for testcase in list(suite.findall("testcase")):
        suite.remove(testcase)
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    metadata = json.loads(case["run"]["metadata"].read_text(encoding="utf-8"))
    metadata["collected_count"] = 0
    metadata["selected_count"] = 0
    result_gate._write_json(case["run"]["metadata"], metadata)
    result_gate._refresh_junit_digest(case["run"])

    result = _run_ci_case(case)

    assert result.returncode != 0
    rejection = json.loads(result.stdout)
    assert rejection["status"] == "FAIL"
    assert rejection["error_code"] == "EMPTY_OR_MISSING_RESULT"
    assert "architecture-types" in rejection["message"]


def test_a05_all_skipped_is_rejected_by_real_checker(tmp_path):
    case = _ci_case(tmp_path)
    tree = ElementTree.parse(case["run"]["junit"])
    for testcase in tree.getroot().findall("testcase"):
        for prop in testcase.find("properties").findall("property"):
            if prop.get("name") == "outcome":
                prop.set("value", "skipped")
        ElementTree.SubElement(testcase, "skipped", message="injected all-skip")
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    result_gate._refresh_junit_digest(case["run"])

    result = _run_ci_case(case)

    assert result.returncode != 0
    assert "REQUIRED_OUTCOME_NOT_PASS" in result.stdout


def test_a05_test_and_mapping_removal_is_rejected_by_real_checker(tmp_path):
    case = _ci_case(tmp_path)
    manifest = copy.deepcopy(case["manifest"])
    removed = next(
        execution
        for item in manifest["cases"]
        for execution in item["executions"]
        if execution["status"] == "MAPPED"
        and execution["job_id"] == "architecture-types"
    )
    for item in manifest["cases"]:
        item["executions"] = [
            execution
            for execution in item["executions"]
            if execution is not removed
        ]
    _write_manifest(case, manifest)
    tree = ElementTree.parse(case["run"]["junit"])
    suite = tree.getroot()
    for testcase in suite.findall("testcase"):
        properties = {
            prop.get("name"): prop.get("value")
            for prop in testcase.find("properties").findall("property")
        }
        if properties["node_id"] == removed["node_id"]:
            suite.remove(testcase)
            break
    tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
    result_gate._refresh_junit_digest(case["run"])

    result = _run_ci_case(case)

    assert result.returncode != 0
    assert "EXECUTION_SET_MISMATCH" in result.stdout


def test_h06_duplicate_and_unknown_ids_are_rejected_by_real_checker(tmp_path):
    outcomes: dict[str, str] = {}
    for mutation in (
        "duplicate",
        "unknown-case",
        "unknown-obligation",
        "unknown-result",
    ):
        case = _ci_case(tmp_path / mutation)
        manifest = copy.deepcopy(case["manifest"])
        if mutation == "duplicate":
            first = manifest["cases"][0]
            first["executions"].append(copy.deepcopy(first["executions"][0]))
            _write_manifest(case, manifest)
        elif mutation == "unknown-case":
            manifest["cases"][0]["id"] = "UNKNOWN"
            _write_manifest(case, manifest)
        elif mutation == "unknown-obligation":
            manifest["cases"][0]["executions"][0]["obligation_id"] = (
                "UNKNOWN.obligation"
            )
            _write_manifest(case, manifest)
        else:
            tree = ElementTree.parse(case["run"]["junit"])
            testcase = tree.getroot().find("testcase")
            for prop in testcase.find("properties").findall("property"):
                if prop.get("name") == "obligation_id":
                    prop.set("value", "UNKNOWN.result")
                    break
            tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
            result_gate._refresh_junit_digest(case["run"])
        result = _run_ci_case(case)
        assert result.returncode != 0
        outcomes[mutation] = result.stdout

    assert "DUPLICATE_EXECUTION_KEY" in outcomes["duplicate"]
    assert "MANIFEST_CASE_SET_MISMATCH" in outcomes["unknown-case"]
    assert "EXECUTION_SET_MISMATCH" in outcomes["unknown-obligation"]
    assert "UNKNOWN_JUNIT_RESULT" in outcomes["unknown-result"]


def test_h06_missing_obligation_variant_is_rejected_by_real_checker(tmp_path):
    case = _ci_case(tmp_path)
    manifest = copy.deepcopy(case["manifest"])
    for item in manifest["cases"]:
        if item["executions"]:
            item["executions"].pop()
            break
    _write_manifest(case, manifest)

    result = _run_ci_case(case)

    assert result.returncode != 0
    assert "EXECUTION_SET_MISMATCH" in result.stdout


def test_h06_empty_and_missing_results_are_rejected_by_real_checker(tmp_path):
    reasons: list[str] = []
    for mutation in ("empty", "missing-one"):
        case = _ci_case(tmp_path / mutation)
        tree = ElementTree.parse(case["run"]["junit"])
        suite = tree.getroot()
        tests = suite.findall("testcase")
        for testcase in tests if mutation == "empty" else tests[:1]:
            suite.remove(testcase)
        tree.write(case["run"]["junit"], encoding="utf-8", xml_declaration=True)
        result_gate._refresh_junit_digest(case["run"])
        result = _run_ci_case(case)
        assert result.returncode != 0
        reasons.append(result.stdout)

    assert all("EMPTY_OR_MISSING_RESULT" in reason for reason in reasons)


def test_h06_digest_mismatches_are_rejected_by_real_checker(tmp_path):
    reasons: list[str] = []
    for mutation in ("junit", "fixture", "script"):
        job_id = (
            "architecture-types"
            if mutation == "junit"
            else "browser-contract-isolated"
        )
        case = _ci_case(tmp_path / mutation, job_id=job_id)
        target = {
            "junit": case["run"]["junit"],
            "fixture": case["run"]["dir"] / "fixture.html",
            "script": case["run"]["dir"] / "script.js",
        }[mutation]
        target.write_bytes(target.read_bytes() + b"\nmutated\n")
        result = _run_ci_case(case)
        assert result.returncode != 0
        reasons.append(result.stdout)

    assert "JUNIT_DIGEST_MISMATCH" in reasons[0]
    assert all("EVIDENCE_DIGEST_MISMATCH" in reason for reason in reasons[1:])


def test_h06_duplicate_result_and_wrong_layer_are_rejected_by_real_checker(tmp_path):
    duplicate_case = _ci_case(tmp_path / "duplicate")
    duplicate_tree = ElementTree.parse(duplicate_case["run"]["junit"])
    suite = duplicate_tree.getroot()
    suite.append(copy.deepcopy(suite.find("testcase")))
    duplicate_tree.write(
        duplicate_case["run"]["junit"], encoding="utf-8", xml_declaration=True
    )
    result_gate._refresh_junit_digest(duplicate_case["run"])
    duplicate_result = _run_ci_case(duplicate_case)

    layer_case = _ci_case(tmp_path / "layer")
    manifest = copy.deepcopy(layer_case["manifest"])
    mapped = next(
        execution
        for item in manifest["cases"]
        for execution in item["executions"]
        if execution["status"] == "MAPPED"
    )
    mapped["layer"] = "wrong-layer"
    _write_manifest(layer_case, manifest)
    layer_result = _run_ci_case(layer_case)

    assert duplicate_result.returncode != 0
    assert "DUPLICATE_RESULT" in duplicate_result.stdout
    assert layer_result.returncode != 0
    assert "WRONG_LAYER" in layer_result.stdout


def test_h06_missing_and_unknown_job_targets_are_rejected_by_real_aggregate(tmp_path):
    reasons: list[str] = []
    for mutation in ("missing", "unknown"):
        case = _ci_case(tmp_path / mutation)
        index_path, index, _ = result_gate._make_run_index(case)
        if mutation == "missing":
            index["runs"].pop()
        else:
            index["runs"][0]["target"] = "unknown-target"
        result_gate._write_json(index_path, index)
        result = result_gate._run_checker(
            *result_gate._index_arguments(case["manifest_path"], index_path)
        )
        assert result.returncode != 0
        reasons.append(result.stdout)

    assert all("RUN_INDEX" in reason for reason in reasons)


@pytest.mark.parametrize(
    ("path", "variant"),
    [
        ("src/platforms/utiki/domain/state.py", "new-domain-file"),
        ("src/platforms/utiki/application/engine.py", "new-application-file"),
        ("src/platforms/utiki/application/ports.py", "new-port-file"),
        ("src/platforms/utiki/adapters/browser.py", "new-adapter-file"),
        ("src/platforms/utiki/bootstrap.py", "new-bootstrap-file"),
        ("src/utiki_config.py", "new-current-config-file"),
        ("src/keyword_rules.py", "new-keyword-file"),
        ("src/utiki_core_services.py", "new-core-bridge-file"),
    ],
    ids=lambda value: value if isinstance(value, str) and "/" not in value else None,
)
def test_h08_each_new_typed_file_omitted_from_scope_fails(
    tmp_path: Path, path: str, variant: str
) -> None:
    root, manifest, config, fake, _ = type_gate._fixture(tmp_path)
    type_gate._write(root / path, "VALUE: int = 1\n")

    result, report_path, log = type_gate._run(root, manifest, config, fake, tmp_path)

    assert result.returncode != 0, variant
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "TYPED_SCOPE_MISSING" in report["failure_reasons"]
    assert path in report["affected_paths"]
    assert not log.exists()


def _syntax_case(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    syntax_gate._git(repo, "init", "-b", "main")
    syntax_gate._git(repo, "config", "user.email", "gate@example.invalid")
    syntax_gate._git(repo, "config", "user.name", "Gate Test")
    target = repo / "tools" / "verify_modified_python.py"
    target.parent.mkdir(parents=True)
    shutil.copy2(syntax_gate.TOOL_SOURCE, target)
    syntax_gate._write(repo / "src" / "settings.py", "VALUE = 1\n")
    syntax_gate._git(repo, "add", ".")
    syntax_gate._git(repo, "commit", "-m", "baseline")
    base = syntax_gate._git(repo, "rev-parse", "HEAD").stdout.strip()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return {"repo": repo, "run_dir": run_dir, "base": base}


def test_h11_conditional_bridge_is_git_derived_and_cannot_be_omitted(tmp_path):
    case = _syntax_case(tmp_path)
    bridge = "src/utiki_core_services.py"
    syntax_gate._write(case["repo"] / bridge, "VALUE = 1\n")
    candidate = syntax_gate._commit(case["repo"], "add conditional bridge")
    _, manifest_path, context = syntax_gate._generate(case, case["base"], candidate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert any(item["path"] == bridge for item in manifest["files"])
    manifest["files"] = [item for item in manifest["files"] if item["path"] != bridge]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result, report_path = syntax_gate._verify(
        case, case["base"], candidate, manifest_path, context
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "MANIFEST_MISMATCH" in report["failure_reasons"]


def test_h11_candidate_sha_mismatch_is_rejected_before_generation(tmp_path):
    case = _syntax_case(tmp_path)
    syntax_gate._write(case["repo"] / "src" / "settings.py", "VALUE = 2\n")
    candidate = syntax_gate._commit(case["repo"], "advance candidate")
    context = syntax_gate._context(case, case["base"], candidate)
    metadata = json.loads(context.read_text(encoding="utf-8"))
    metadata["candidate_sha"] = case["base"]
    context.write_text(json.dumps(metadata), encoding="utf-8")
    command = syntax_gate._tool_command(case, case["base"], candidate, context)
    command.extend(["--write-manifest", str(case["run_dir"] / "manifest.json")])

    result = syntax_gate._run(command, cwd=case["repo"], check=False)

    assert result.returncode != 0
    assert "RUN_METADATA_CANDIDATE_MISMATCH" in result.stderr


def test_h11_python_change_cannot_be_rewritten_as_zero_compile(tmp_path):
    case = _syntax_case(tmp_path)
    syntax_gate._write(case["repo"] / "src" / "settings.py", "VALUE = 3\n")
    candidate = syntax_gate._commit(case["repo"], "change Python")
    _, manifest_path, context = syntax_gate._generate(case, case["base"], candidate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files"]
    manifest["files"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result, report_path = syntax_gate._verify(
        case, case["base"], candidate, manifest_path, context
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "FAIL"
    assert "MANIFEST_MISMATCH" in report["failure_reasons"]
