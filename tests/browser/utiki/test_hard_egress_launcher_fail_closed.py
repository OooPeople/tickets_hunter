import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.browser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER_PATH = PROJECT_ROOT / "tools" / "run_utiki_browser_isolation.py"
CANARY_PATH = PROJECT_ROOT / "tools" / "utiki_egress_canary.py"


def test_h04_missing_isolation_proof_is_blocked(tmp_path):
    probe = tmp_path / "missing-isolation-proof.json"
    probe.write_text(
        json.dumps({"schema_version": 1, "mode": "missing-isolation-proof"}),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("TICKETS_HUNTER_HARD_EGRESS_ACTIVE", None)
    environment.pop("TICKETS_HUNTER_EGRESS_CANARY_IPV4", None)
    environment.pop("TICKETS_HUNTER_EGRESS_CANARY_IPV6", None)
    environment.pop("TICKETS_HUNTER_EGRESS_CANARY_PORT", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(CANARY_PATH),
            "--negative-probe",
            str(probe),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 1
    assert "attestation is absent" in completed.stdout


def test_h04_insufficient_isolation_permission_is_blocked(tmp_path):
    runtime = tmp_path / "docker-denied"
    runtime.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(LAUNCHER_PATH),
            "--runtime-preflight-negative",
            "--runtime",
            str(runtime),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "container runtime unavailable" in completed.stderr
