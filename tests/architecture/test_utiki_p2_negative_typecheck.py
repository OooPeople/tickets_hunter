"""P2 H08 probes reach production typed-scope checks before mypy starts."""

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
PRODUCER = ROOT / "tools/run_utiki_typecheck.py"
PROBE_RUNNER = ROOT / "tools/run_utiki_negative_probe.py"
H08_PATHS = {
    "new-domain-file": "src/platforms/utiki/domain/state.py",
    "new-application-file": "src/platforms/utiki/application/engine.py",
    "new-port-file": "src/platforms/utiki/application/ports.py",
    "new-adapter-file": "src/platforms/utiki/adapters/browser.py",
    "new-bootstrap-file": "src/platforms/utiki/bootstrap.py",
    "new-current-config-file": "src/utiki_config.py",
    "new-keyword-file": "src/keyword_rules.py",
    "new-core-bridge-file": "src/utiki_core_services.py",
}


def _run(root: Path, manifest: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(PRODUCER), "--phase", "P2",
            "--root", str(root), "--manifest", str(manifest),
            "--config", str(root / "ci/mypy-utiki.ini"),
            "--report", str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("variant", [*H08_PATHS, "broad-ignore-any"])
def test_p2_h08_reaches_exact_scope_or_config_check(tmp_path: Path, variant: str) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert registry["phase"] == "P2"
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == f"H08.arch.{variant}"
    )
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._typecheck_probe(probe, entry, "1" * 40, phase="P2")
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    assert not (probe / "fake-mypy.py").exists()
    assert "--mypy" not in request["child"]["arguments"]
    assert request["child"]["arguments"][:2] == ["--phase", "P2"]

    source_root = probe / "repo"
    manifest = source_root / "ci/utiki-typed-scope.txt"
    config = source_root / "ci/mypy-utiki.ini"
    affected = H08_PATHS.get(variant)
    mutated_file = manifest if affected else config
    mutated_content = mutated_file.read_text(encoding="utf-8")
    legal_content = (
        mutated_content + affected + "\n"
        if affected else mutated_content.removesuffix("ignore_errors = True\n")
    )
    assert legal_content != mutated_content
    mutated_file.write_text(legal_content, encoding="utf-8")
    positive = _run(source_root, manifest, probe / "positive.json")
    positive_report = json.loads((probe / "positive.json").read_text(encoding="utf-8"))
    assert positive.returncode == 0, positive.stderr
    assert positive_report["status"] == "PASS"
    assert positive_report["phase"] == "P2"
    assert positive_report["mypy_exit_code"] == 0
    if affected:
        assert affected in positive_report["required_files"]

    mutated_file.write_text(mutated_content, encoding="utf-8")
    negative = _run(source_root, manifest, probe / "typecheck-report.json")
    negative_report = json.loads((probe / "typecheck-report.json").read_text(encoding="utf-8"))
    assert negative.returncode == 1
    assert negative_report["status"] == "FAIL"
    assert negative_report["phase"] == "P2"
    assert negative_report["failure_reasons"] == [
        "TYPED_SCOPE_MISSING" if affected else "UNSAFE_MYPY_CONFIG"
    ]
    assert negative_report.get("affected_paths", []) == ([affected] if affected else [])
    assert negative_report["mypy_exit_code"] is None
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    (probe / "typecheck-report.json").unlink()
    transcript_path = probe / "transcript.json"
    launched = subprocess.run(
        [
            sys.executable, str(PROBE_RUNNER), "--repository-root", str(ROOT),
            "--probe-registry", str(REGISTRY),
            "--probe-input", str(probe / "probe.json"),
            "--producer-report", str(transcript_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert launched.returncode == 1
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["key"]["obligation_id"] == entry["obligation_id"]
    assert transcript["rejection_token"] == negative_report["failure_reasons"][0]
    assert transcript["observed_exit_code"] == 1


def test_p1_fake_typecheck_child_cannot_be_relabelled_as_p2(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == "H08.arch.new-application-file"
    )
    builder._typecheck_probe(tmp_path, entry, "1" * 40)
    request = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))

    assert "--mypy" in request["child"]["arguments"]
    assert "--phase" not in request["child"]["arguments"]
    with pytest.raises(NegativeContractError, match="negative child option contract differs from review"):
        validate_request_contract(
            entry, request, probe_root=tmp_path, repository_root=ROOT, phase="P2"
        )
