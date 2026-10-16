import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL_SOURCE = PROJECT_ROOT / "tools" / "verify_modified_python.py"


def _run(command, *, cwd, check=True):
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {command!r}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _git(repo, *arguments, check=True):
    return _run(["git", *arguments], cwd=repo, check=check)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def gate_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "Gate Test")

    tool_target = repo / "tools" / "verify_modified_python.py"
    tool_target.parent.mkdir(parents=True)
    shutil.copy2(TOOL_SOURCE, tool_target)
    _write(repo / "src" / "settings.py", "VALUE = 1\n")
    _write(repo / "src" / "platforms" / "utiki" / "old.py", "VALUE = 2\n")
    _write(repo / "build_scripts" / "sample.spec", "VALUE = 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return {"repo": repo, "run_dir": run_dir, "base": base}


def _context(case, base, candidate):
    path = case["run_dir"] / "run-context.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "modified-python-test",
                "job_id": "pytest",
                "phase": "P1",
                "base_sha": base,
                "candidate_sha": candidate,
            }
        ),
        encoding="utf-8",
    )
    return path


def _tool_command(case, base, candidate, context):
    return [
        sys.executable,
        str(case["repo"] / "tools" / "verify_modified_python.py"),
        "--base-ref",
        base,
        "--candidate-ref",
        candidate,
        "--run-metadata",
        str(context),
    ]


def _generate(case, base, candidate, *, include_worktree=False, check=True):
    context = _context(case, base, candidate)
    manifest = case["run_dir"] / "modified-python.json"
    command = _tool_command(case, base, candidate, context)
    command.extend(["--write-manifest", str(manifest)])
    if include_worktree:
        command.append("--include-worktree")
    result = _run(command, cwd=case["repo"], check=check)
    return result, manifest, context


def _verify(
    case,
    base,
    candidate,
    manifest,
    context,
    *,
    include_worktree=False,
    check=False,
):
    report = case["run_dir"] / "python-syntax.json"
    command = _tool_command(case, base, candidate, context)
    command.extend(["--manifest", str(manifest), "--report", str(report)])
    if include_worktree:
        command.append("--include-worktree")
    result = _run(command, cwd=case["repo"], check=check)
    return result, report


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_clean_empty_scope_reports_no_python_changes(gate_repo):
    case = gate_repo
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()

    _, manifest, context = _generate(case, candidate, candidate)
    result, report_path = _verify(
        case, candidate, candidate, manifest, context, check=True
    )

    assert result.returncode == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "NO_PYTHON_CHANGES"
    assert report["compile_count"] == 0
    assert report["files"] == []


def test_valid_change_compiles_only_into_artifact_cache(gate_repo):
    case = gate_repo
    _write(case["repo"] / "src" / "settings.py", "VALUE = 20\n")
    candidate = _commit(case["repo"], "valid settings change")

    _, manifest, context = _generate(case, case["base"], candidate)
    result, report_path = _verify(
        case, case["base"], candidate, manifest, context, check=True
    )

    assert result.returncode == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "PASS"
    assert report["compile_count"] == 1
    assert list((case["run_dir"] / "python-bytecode-cache").glob("*.pyc"))
    assert not list(case["repo"].rglob("__pycache__"))


def test_python_change_outside_new_utiki_roots_is_compiled(gate_repo):
    case = gate_repo
    _write(case["repo"] / "src" / "settings.py", "def broken(:\n")
    candidate = _commit(case["repo"], "break settings syntax")

    _, manifest, context = _generate(case, case["base"], candidate)
    result, report_path = _verify(
        case, case["base"], candidate, manifest, context
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["overall_status"] == "FAIL"
    assert any(
        item["path"] == "src/settings.py"
        and item["reason"] == "SYNTAX_ERROR"
        for item in report["files"]
    )


def test_dirty_worktree_requires_explicit_include_flag(gate_repo):
    case = gate_repo
    _write(case["repo"] / "src" / "settings.py", "VALUE = 4\n")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()

    result, _, _ = _generate(
        case, case["base"], candidate, include_worktree=False, check=False
    )

    assert result.returncode != 0
    assert "DIRTY_WORKTREE_REQUIRES_INCLUDE" in result.stderr


def test_base_ref_must_match_run_context(gate_repo):
    case = gate_repo
    _write(case["repo"] / "src" / "settings.py", "VALUE = 21\n")
    candidate = _commit(case["repo"], "advance candidate")
    context = _context(case, case["base"], candidate)
    manifest = case["run_dir"] / "modified-python.json"
    command = _tool_command(case, candidate, candidate, context)
    command.extend(["--write-manifest", str(manifest)])

    result = _run(command, cwd=case["repo"], check=False)

    assert result.returncode != 0
    assert "RUN_METADATA_BASE_MISMATCH" in result.stderr


def test_untracked_file_omitted_from_manifest_is_rejected(gate_repo):
    case = gate_repo
    _write(case["repo"] / "tools" / "new_gate.py", "VALUE = 5\n")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()
    _, manifest_path, context = _generate(
        case, case["base"], candidate, include_worktree=True
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        item for item in manifest["files"] if item["path"] != "tools/new_gate.py"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result, report_path = _verify(
        case,
        case["base"],
        candidate,
        manifest_path,
        context,
        include_worktree=True,
    )
    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "MANIFEST_MISMATCH" in report["failure_reasons"]


def test_staged_then_broken_working_copy_compiles_actual_bytes(gate_repo):
    case = gate_repo
    target = case["repo"] / "src" / "settings.py"
    _write(target, "VALUE = 6\n")
    _git(case["repo"], "add", str(target.relative_to(case["repo"])))
    _write(target, "def broken(:\n")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()

    _, manifest, context = _generate(
        case, case["base"], candidate, include_worktree=True
    )
    result, report_path = _verify(
        case,
        case["base"],
        candidate,
        manifest,
        context,
        include_worktree=True,
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    record = next(item for item in report["files"] if item["path"] == "src/settings.py")
    assert record["reason"] == "SYNTAX_ERROR"


def test_rename_delete_and_spec_actions_are_recorded(gate_repo):
    case = gate_repo
    repo = case["repo"]
    _git(
        repo,
        "mv",
        "src/platforms/utiki/old.py",
        "src/platforms/utiki/new.py",
    )
    (repo / "build_scripts" / "sample.spec").unlink()
    _write(repo / "build_scripts" / "new.spec", "VALUE = 7\n")
    candidate = _commit(repo, "rename delete and add spec")

    _, manifest_path, _ = _generate(case, case["base"], candidate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {item["path"]: item for item in manifest["files"]}

    assert records["src/platforms/utiki/new.py"]["required_action"] == "compile"
    assert records["src/platforms/utiki/new.py"]["old_path"] == (
        "src/platforms/utiki/old.py"
    )
    assert records["build_scripts/sample.spec"]["required_action"] == "verify-delete"
    assert records["build_scripts/new.spec"]["required_action"] == "compile"


def test_copy_is_classified_and_compiled(gate_repo):
    case = gate_repo
    repo = case["repo"]
    shutil.copy2(
        repo / "src" / "platforms" / "utiki" / "old.py",
        repo / "src" / "platforms" / "utiki" / "copied.py",
    )
    candidate = _commit(repo, "copy Python source")

    _, manifest_path, _ = _generate(case, case["base"], candidate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    copied = next(
        item
        for item in manifest["files"]
        if item["path"] == "src/platforms/utiki/copied.py"
    )

    assert copied["required_action"] == "compile"
    assert copied["old_path"] == "src/platforms/utiki/old.py"
    assert any(status.startswith("C") for status in copied["git_statuses"])


def test_candidate_then_staged_rename_retires_intermediate_path(gate_repo):
    case = gate_repo
    repo = case["repo"]
    _git(
        repo,
        "mv",
        "src/platforms/utiki/old.py",
        "src/platforms/utiki/middle.py",
    )
    candidate = _commit(repo, "first rename")
    _git(
        repo,
        "mv",
        "src/platforms/utiki/middle.py",
        "src/platforms/utiki/final.py",
    )

    _, manifest_path, _ = _generate(
        case, case["base"], candidate, include_worktree=True
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {item["path"]: item for item in manifest["files"]}

    assert records["src/platforms/utiki/middle.py"]["required_action"] == (
        "verify-delete"
    )
    assert records["src/platforms/utiki/final.py"]["required_action"] == "compile"


def test_checker_self_change_is_in_scope(gate_repo):
    case = gate_repo
    checker = case["repo"] / "tools" / "verify_modified_python.py"
    checker.write_text(
        checker.read_text(encoding="utf-8") + "\n# changed by self-scope test\n",
        encoding="utf-8",
    )
    candidate = _commit(case["repo"], "modify checker")

    _, manifest_path, _ = _generate(case, case["base"], candidate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checker_record = next(
        item
        for item in manifest["files"]
        if item["path"] == "tools/verify_modified_python.py"
    )
    assert checker_record["required_action"] == "compile"
    assert checker_record["role"] == "tool"


def test_file_removed_after_manifest_generation_is_rejected(gate_repo):
    case = gate_repo
    target = case["repo"] / "src" / "new_untracked.py"
    _write(target, "VALUE = 8\n")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()
    _, manifest, context = _generate(
        case, case["base"], candidate, include_worktree=True
    )
    target.unlink()

    result, report_path = _verify(
        case,
        case["base"],
        candidate,
        manifest,
        context,
        include_worktree=True,
    )
    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "MANIFEST_MISMATCH" in report["failure_reasons"]


def test_manifest_content_digest_tampering_is_rejected(gate_repo):
    case = gate_repo
    _write(case["repo"] / "src" / "settings.py", "VALUE = 10\n")
    candidate = _commit(case["repo"], "change settings")
    _, manifest_path, context = _generate(case, case["base"], candidate)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result, report_path = _verify(
        case, case["base"], candidate, manifest_path, context
    )
    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "MANIFEST_MISMATCH" in report["failure_reasons"]


def test_python_symlink_is_rejected(gate_repo):
    case = gate_repo
    source = case["repo"] / "outside.py"
    _write(source, "VALUE = 9\n")
    link = case["repo"] / "src" / "linked.py"
    try:
        os.symlink(source, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()

    result, _, _ = _generate(
        case, case["base"], candidate, include_worktree=True, check=False
    )
    assert result.returncode != 0
    assert "UNSAFE_SYMLINK" in result.stderr


def test_case_colliding_untracked_python_is_rejected(gate_repo):
    case = gate_repo
    upper = case["repo"] / "src" / "CaseCollision.py"
    lower = case["repo"] / "src" / "casecollision.py"
    _write(upper, "VALUE = 11\n")
    _write(lower, "VALUE = 12\n")
    try:
        if os.path.samefile(upper, lower):
            pytest.skip("case-insensitive filesystem")
    except OSError:
        pytest.skip("filesystem cannot represent the case collision")
    candidate = _git(case["repo"], "rev-parse", "HEAD").stdout.strip()

    result, _, _ = _generate(
        case, case["base"], candidate, include_worktree=True, check=False
    )

    assert result.returncode != 0
    assert "CASE_COLLISION" in result.stderr


def test_ignored_python_requires_approved_build_source_inventory(gate_repo):
    case = gate_repo
    _write(case["repo"] / ".gitignore", "generated_source.py\n")
    candidate = _commit(case["repo"], "ignore generated source path")
    _write(case["repo"] / "generated_source.py", "VALUE = 13\n")

    result, _, _ = _generate(
        case, case["base"], candidate, include_worktree=False, check=False
    )

    assert result.returncode != 0
    assert "IGNORED_SOURCE_WITHOUT_INVENTORY" in result.stderr


def test_generated_output_roots_are_not_treated_as_project_source(gate_repo):
    case = gate_repo
    _write(case["repo"] / ".gitignore", "build/\ndist/\nartifacts/\n.venv/\n")
    candidate = _commit(case["repo"], "ignore generated output roots")
    for path in (
        "build/vendor.py",
        "dist/app/vendor.py",
        "artifacts/cache/generated.py",
        ".venv/lib/dependency.py",
    ):
        _write(case["repo"] / path, "VALUE = 1\n")

    result, manifest, context = _generate(
        case, case["base"], candidate, include_worktree=False, check=True
    )
    report_result, report_path = _verify(
        case, case["base"], candidate, manifest, context, check=True
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report_result.returncode == 0
    assert report["overall_status"] == "NO_PYTHON_CHANGES"


def test_digest_pinned_ignored_build_source_is_compiled(gate_repo):
    case = gate_repo
    repo = case["repo"]
    _write(repo / ".gitignore", "generated_source.py\n")
    candidate = _commit(repo, "ignore generated source path")
    generated = repo / "generated_source.py"
    _write(generated, "VALUE = 14\n")
    digest = hashlib.sha256(generated.read_bytes()).hexdigest()
    context = _context(case, case["base"], candidate)
    metadata = json.loads(context.read_text(encoding="utf-8"))
    metadata["build_sources"] = [
        {
            "path": "generated_source.py",
            "sha256": digest,
            "source": "pytest-fixture",
        }
    ]
    context.write_text(json.dumps(metadata), encoding="utf-8")
    manifest = case["run_dir"] / "modified-python.json"
    command = _tool_command(case, case["base"], candidate, context)
    command.extend(["--write-manifest", str(manifest)])
    _run(command, cwd=repo)

    result, report_path = _verify(
        case, case["base"], candidate, manifest, context, check=True
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["overall_status"] == "PASS"
    assert any(
        item["path"] == "generated_source.py" and item["reason"] == "COMPILED"
        for item in report["files"]
    )


def test_file_digest_preserves_crlf_bytes(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_modified_python_for_binary_digest", TOOL_SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        source = tmp_path / "source.py"
        source.write_bytes(b"VALUE = 1\r\n")
        digest, _ = module._file_digest(tmp_path, "source.py")
    finally:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous

    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()


def test_unmerged_index_is_rejected(gate_repo):
    case = gate_repo
    repo = case["repo"]
    _git(repo, "checkout", "-b", "side")
    _write(repo / "src" / "settings.py", "VALUE = 'side'\n")
    _commit(repo, "side change")
    _git(repo, "checkout", "main")
    _write(repo / "src" / "settings.py", "VALUE = 'main'\n")
    _commit(repo, "main change")
    merge = _git(repo, "merge", "side", check=False)
    assert merge.returncode != 0
    candidate = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result, _, _ = _generate(
        case, case["base"], candidate, include_worktree=True, check=False
    )
    assert result.returncode != 0
    assert "UNMERGED_INDEX" in result.stderr


def test_spec_is_compile_checked(gate_repo):
    case = gate_repo
    _write(case["repo"] / "build_scripts" / "sample.spec", "def broken(:\n")
    candidate = _commit(case["repo"], "break spec syntax")

    _, manifest, context = _generate(case, case["base"], candidate)
    result, report_path = _verify(
        case, case["base"], candidate, manifest, context
    )

    assert result.returncode != 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    record = next(
        item for item in report["files"] if item["path"] == "build_scripts/sample.spec"
    )
    assert record["reason"] == "SYNTAX_ERROR"
