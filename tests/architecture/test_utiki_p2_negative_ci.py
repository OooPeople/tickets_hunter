"""P2 H06 checker probes retain a valid P2 control before one mutation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import build_utiki_negative_inputs as builder
from tools.utiki_negative_contract import NegativeContractError, validate_request_contract


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "ci/utiki-p2-negative-probes.json"
CATALOG = ROOT / "ci/utiki-required-cases.json"
APPROVED = "73455dc332d181b1350ec1bc8f3318a45a098ac9b23b88c1c7e712e59c7361cf"
EXPECTED_REJECTIONS = {
    "A05.arch.zero-collected": ("EMPTY_OR_MISSING_RESULT", "JUnit lacks"),
    "A05.arch.all-skipped": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "A05.arch.test-and-mapping-removed": ("EXECUTION_SET_MISMATCH", "A05.arch.test-and-mapping-removed"),
    "H06.arch.removed-test-and-mapping": ("EXECUTION_SET_MISMATCH", "H06.arch.removed-test-and-mapping"),
    "H06.arch.catalog-unapproved-change": ("REVIEW_REQUIRED", "approval record"),
    "H06.arch.duplicate-or-unknown-id": ("DUPLICATE_EXECUTION_KEY", "H06.arch.duplicate-or-unknown-id"),
    "H06.arch.missing-obligation-variant": ("EXECUTION_SET_MISMATCH", "H06.arch.missing-obligation-variant"),
    "H06.arch.forged-not-applicable": ("FORGED_APPLICABILITY", "H06.arch.forged-not-applicable"),
    "H06.arch.empty-or-missing-result": ("EMPTY_OR_MISSING_RESULT", "JUnit lacks"),
    "H06.arch.stale-run-or-sha": ("RUN_CONTEXT_MISMATCH", "candidate SHA"),
    "H06.arch.digest-mismatch": ("JUNIT_DIGEST_MISMATCH", "JUnit digest"),
    "H06.arch.duplicate-result-or-wrong-layer": ("DUPLICATE_RESULT", "duplicate JUnit execution result"),
    "H06.arch.missing-job-or-target": ("RUN_INDEX_SET_MISMATCH", "run index job/target"),
    "H06.arch.failure-exit-overwritten": ("UPSTREAM_PROCESS_FAILED", "original test exit code"),
    "H06.arch.required-outcome-skip": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "H06.arch.required-outcome-xfail": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "H06.arch.required-outcome-strict-xpass": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "H06.arch.required-outcome-non-strict-xpass": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "H06.arch.required-outcome-setup-error": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
    "H06.arch.required-outcome-teardown-error": ("REQUIRED_OUTCOME_NOT_PASS", "testcase[0]"),
}


def _expand(root: Path, arguments: list[str]) -> list[str]:
    return [value.replace("{PROBE_ROOT}", str(root)) for value in arguments]


def test_p2_h06_changed_catalog_reaches_approval_check(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == "H06.arch.catalog-unapproved-change"
    )
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._ci_probe(ROOT, probe, entry, "1" * 40, phase="P2")
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    arguments = _expand(probe, request["child"]["arguments"])
    assert arguments[arguments.index("--phase") + 1] == "P2"

    legal_arguments = arguments.copy()
    legal_arguments[legal_arguments.index("--required-catalog") + 1] = str(CATALOG)
    legal_arguments[legal_arguments.index("--approved-catalog-sha256") + 1] = APPROVED
    control = subprocess.run(
        [sys.executable, str(ROOT / "tools/verify_utiki_ci_results.py"), *legal_arguments],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert control.returncode == 0, control.stdout + control.stderr
    assert json.loads(control.stdout)["status"] == "PASS"

    transcript_path = probe / "transcript.json"
    negative = subprocess.run(
        [
            sys.executable, str(ROOT / "tools/run_utiki_negative_probe.py"),
            "--repository-root", str(ROOT), "--probe-registry", str(REGISTRY),
            "--probe-input", str(probe / "probe.json"),
            "--producer-report", str(transcript_path),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert negative.returncode == 1, negative.stderr
    result = json.loads(negative.stdout)
    assert result["status"] == "FAIL"
    assert result["error_code"] == entry["rejection_token"]
    assert "approval record" in result["message"]
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["key"]["obligation_id"] == entry["obligation_id"]


@pytest.mark.parametrize("obligation_id", EXPECTED_REJECTIONS)
def test_p2_ci_probe_reaches_declared_checker_reason(
    tmp_path: Path, obligation_id: str
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(item for item in registry["probes"] if item["obligation_id"] == obligation_id)
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._ci_probe(ROOT, probe, entry, "1" * 40, phase="P2")
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    validate_request_contract(entry, request, probe_root=probe, repository_root=ROOT, phase="P2")

    transcript_path = probe / "transcript.json"
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "tools/run_utiki_negative_probe.py"),
            "--repository-root", str(ROOT), "--probe-registry", str(REGISTRY),
            "--probe-input", str(probe / "probe.json"),
            "--producer-report", str(transcript_path),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 1, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "FAIL"
    expected_code, expected_subject = EXPECTED_REJECTIONS[obligation_id]
    assert result["error_code"] == expected_code
    assert expected_subject in result["message"]
    assert json.loads(transcript_path.read_text(encoding="utf-8"))["status"] == "EXPECTED_REJECTION"


def test_p1_ci_checker_child_cannot_be_relabelled_as_p2(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == "H06.arch.catalog-unapproved-change"
    )
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._ci_probe(ROOT, probe, entry, "1" * 40)
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))

    with pytest.raises(NegativeContractError, match="CI checker arguments are not canonical"):
        validate_request_contract(
            entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
        )
