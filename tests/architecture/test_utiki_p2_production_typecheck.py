"""P2 production typed evidence must not inherit the controlled P1 fixture claim."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import verify_utiki_ci_results as checker


ROOT = Path(__file__).resolve().parents[2]
ENGINE = "src/platforms/utiki/application/engine.py"


def _produce(
    tmp_path: Path, manifest: Path, *, mypy: Path | None = None,
    config: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    report = tmp_path / "typecheck-raw.json"
    completed = subprocess.run(
        [
            sys.executable, "tools/run_utiki_typecheck.py",
            "--phase", "P2",
            "--root", str(ROOT),
            "--manifest", str(manifest),
            "--config", str(config or ROOT / "ci/mypy-utiki.ini"),
            "--report", str(report),
            "--stdout-report", str(tmp_path / "typecheck-mypy.stdout"),
            "--stderr-report", str(tmp_path / "typecheck-mypy.stderr"),
            *(["--mypy", str(mypy)] if mypy is not None else []),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(report.read_text(encoding="utf-8"))


def test_p2_true_production_typecheck_and_scope_tamper(tmp_path: Path) -> None:
    assert checker._expected_input_contract("typecheck-report", "architecture-types", "P2") == {
        "typed-manifest": ("repository", "ci/utiki-typed-scope.txt"),
        "mypy-config": ("repository", "ci/mypy-utiki.ini"),
    }
    completed, report = _produce(tmp_path, ROOT / "ci/utiki-typed-scope.txt")
    assert completed.returncode == 0, completed.stderr
    assert report["status"] == "PASS" and report["mypy_exit_code"] == 0
    assert report["phase"] == "P2"
    assert ENGINE in report["files"] and ENGINE in report["required_files"]
    assert "coverage_kind" not in report

    inputs = {
        "typed-manifest": ROOT / "ci/utiki-typed-scope.txt",
        "mypy-config": ROOT / "ci/mypy-utiki.ini",
    }
    outputs = {
        "typecheck-stdout": tmp_path / "typecheck-mypy.stdout",
        "typecheck-stderr": tmp_path / "typecheck-mypy.stderr",
    }
    checker._validate_typecheck_payload(report, inputs, outputs, {"phase": "P2"}, ROOT)

    omitted = copy.deepcopy(report)
    omitted["files"].remove(ENGINE)
    omitted["required_files"].remove(ENGINE)
    with pytest.raises(checker.GateError) as captured:
        checker._validate_typecheck_payload(omitted, inputs, outputs, {"phase": "P2"}, ROOT)
    assert captured.value.code == "INVALID_EVIDENCE_CONTENT"
    assert "scope differs" in captured.value.message

    fixture_claim = copy.deepcopy(report)
    fixture_claim["coverage_kind"] = "controlled-runner-contract"
    with pytest.raises(checker.GateError) as captured:
        checker._validate_typecheck_payload(fixture_claim, inputs, outputs, {"phase": "P2"}, ROOT)
    assert captured.value.code == "INVALID_SCHEMA"


def test_p2_producer_omitted_engine_never_starts_mypy(tmp_path: Path) -> None:
    lines = (ROOT / "ci/utiki-typed-scope.txt").read_text(encoding="utf-8").splitlines()
    manifest = tmp_path / "typed-scope.txt"
    manifest.write_text("\n".join(line for line in lines if line != ENGINE) + "\n", encoding="utf-8")
    completed, report = _produce(tmp_path, manifest)
    assert completed.returncode == 1
    assert report["status"] == "FAIL"
    assert report["failure_reasons"] == ["TYPED_SCOPE_MISSING"]
    assert report["affected_paths"] == [ENGINE]
    assert report["mypy_exit_code"] is None


def test_p2_rejects_controlled_mypy_override_before_execution(tmp_path: Path) -> None:
    fake = tmp_path / "fake-mypy.py"
    marker = tmp_path / "fake-ran"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    completed, report = _produce(
        tmp_path, ROOT / "ci/utiki-typed-scope.txt", mypy=fake
    )
    assert completed.returncode == 1
    assert report["status"] == "FAIL"
    assert report["failure_reasons"] == ["MYPY_OVERRIDE_FORBIDDEN"]
    assert report["mypy_exit_code"] is None
    assert not marker.exists()


def test_p2_ignores_candidate_root_mypy_shadow(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    source = root / "src/platforms/utiki/domain/state.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE: int = 1\n", encoding="utf-8")
    manifest = root / "ci/typed-scope.txt"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("src/platforms/utiki/domain/state.py\n", encoding="utf-8")
    config = root / "ci/mypy.ini"
    config.write_text(
        "[mypy]\npython_version = 3.11\nstrict = True\nmypy_path = src\n",
        encoding="utf-8",
    )
    marker = tmp_path / "shadow-ran"
    (root / "mypy.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "shadow-report.json"
    stdout_path = tmp_path / "shadow-mypy.stdout"
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "tools/run_utiki_typecheck.py"),
            "--phase", "P2", "--root", str(root),
            "--manifest", str(manifest), "--config", str(config),
            "--report", str(report_path),
            "--stdout-report", str(stdout_path),
            "--stderr-report", str(tmp_path / "shadow-mypy.stderr"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr
    assert report["status"] == "PASS" and report["mypy_exit_code"] == 0
    assert "Success: no issues found in 1 source file" in stdout_path.read_text(encoding="utf-8")
    assert not marker.exists()


def test_p2_rejects_executable_mypy_plugin_before_launch(tmp_path: Path) -> None:
    config = tmp_path / "mypy-plugin.ini"
    original_config = (ROOT / "ci/mypy-utiki.ini").read_text(encoding="utf-8")
    config.write_text(original_config, encoding="utf-8")
    legal, legal_report = _produce(
        tmp_path, ROOT / "ci/utiki-typed-scope.txt", config=config
    )
    assert legal.returncode == 0 and legal_report["status"] == "PASS"

    marker = tmp_path / "plugin-ran"
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    config.write_text(
        original_config.replace("[mypy]\n", f"[mypy]\nplugins = {plugin}\n", 1),
        encoding="utf-8",
    )
    blocked, report = _produce(
        tmp_path, ROOT / "ci/utiki-typed-scope.txt", config=config
    )
    assert blocked.returncode == 1
    assert report["status"] == "FAIL"
    assert report["failure_reasons"] == ["UNSAFE_MYPY_CONFIG"]
    assert report["mypy_exit_code"] is None
    assert not marker.exists()
