from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "typecheck-p1"
RUNNER = PROJECT_ROOT / "tools" / "run_utiki_typecheck.py"


def test_controlled_p1_typecheck_fixture_is_explicitly_not_production_scope(tmp_path):
    context = json.loads((FIXTURE / "context.json").read_text(encoding="utf-8"))
    report = tmp_path / "typecheck.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--root",
            str(FIXTURE),
            "--manifest",
            str(FIXTURE / "manifest.txt"),
            "--config",
            str(FIXTURE / "mypy.ini"),
            "--coverage-context",
            str(FIXTURE / "context.json"),
            "--report",
            str(report),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["mypy_exit_code"] == 0
    assert payload["required_files"]
    assert set(payload["required_files"]).issubset(payload["files"])
    assert payload["coverage_kind"] == context["coverage_kind"] == "controlled-runner-contract"
    assert payload["production_scope_status"] == context["production_scope_status"] == "NOT_CLAIMED"
    assert payload["legacy_seed_scope_status"] == context["legacy_seed_scope_status"] == "KNOWN_FAILING_DEBT"
    assert payload["coverage_context_path"] == "context.json"
    assert len(payload["coverage_context_sha256"]) == 64
