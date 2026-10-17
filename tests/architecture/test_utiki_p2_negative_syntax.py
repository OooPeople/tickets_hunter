"""P2 syntax probes reach the Git-derived modified-Python gate."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import build_utiki_negative_inputs as builder
from tools.utiki_negative_contract import validate_request_contract


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "ci/utiki-p2-negative-probes.json"
PRODUCER = ROOT / "tools/verify_modified_python.py"
PROBE_RUNNER = ROOT / "tools/run_utiki_negative_probe.py"
EXPECTED_REJECTIONS = {
    "A06.syntax.outside-fixed-roots": "SYNTAX_ERROR",
    "A06.syntax.not-imported-by-pytest": "SYNTAX_ERROR",
    "H11.syntax.outside-fixed-roots": "SYNTAX_ERROR",
    "H11.syntax.new-or-untracked-omitted": "MANIFEST_MISMATCH",
    "H11.syntax.staged-and-working": "SYNTAX_ERROR",
    "H11.syntax.missing-file": "MANIFEST_MISMATCH",
    "H11.syntax.explicit-deletion-rename": "MANIFEST_MISMATCH",
    "H11.syntax.wrong-base-or-sha": "RUN_METADATA_BASE_MISMATCH",
    "H11.syntax.duplicate-or-digest-race": "MANIFEST_MISMATCH",
    "H11.syntax.ignored-build-source": "IGNORED_SOURCE_WITHOUT_INVENTORY",
    "H11.syntax.self-or-zero-compile": "MANIFEST_MISMATCH",
}


def test_p2_h11_conditional_bridge_is_not_omitted_from_syntax_scope(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == "H11.syntax.conditional-bridge"
    )
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._syntax_probe(ROOT, probe, entry, "1" * 40, phase="P2")
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    context = json.loads((probe / "run-context.json").read_text(encoding="utf-8"))
    assert context["phase"] == "P2"

    arguments = [value.replace("{PROBE_ROOT}", str(probe)) for value in request["child"]["arguments"]]
    manifest = probe / "modified-python.json"
    mutated_bytes = manifest.read_bytes()
    base = arguments[arguments.index("--base-ref") + 1]
    candidate = arguments[arguments.index("--candidate-ref") + 1]
    generated = subprocess.run(
        [
            sys.executable, str(PRODUCER), "--base-ref", base,
            "--candidate-ref", candidate, "--run-metadata", str(probe / "run-context.json"),
            "--write-manifest", str(probe / "legal-manifest.json"),
        ],
        cwd=probe / "repo", capture_output=True, text=True, check=False,
    )
    assert generated.returncode == 0, generated.stderr
    legal_bytes = (probe / "legal-manifest.json").read_bytes()
    legal_manifest = json.loads(legal_bytes)
    assert [item["path"] for item in legal_manifest["files"]] == [
        "src/utiki_core_services.py"
    ]
    assert json.loads(mutated_bytes)["files"] == []

    manifest.write_bytes(legal_bytes)
    positive = subprocess.run(
        [sys.executable, str(PRODUCER), *arguments],
        cwd=probe / "repo", capture_output=True, text=True, check=False,
    )
    positive_report = json.loads((probe / "syntax-report.json").read_text(encoding="utf-8"))
    assert positive.returncode == 0, positive.stderr
    assert positive_report["phase"] == "P2"
    assert positive_report["overall_status"] == "PASS"
    assert positive_report["compile_count"] == 1
    assert [item["path"] for item in positive_report["files"]] == [
        "src/utiki_core_services.py"
    ]

    manifest.write_bytes(mutated_bytes)
    (probe / "syntax-report.json").unlink()
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    negative = subprocess.run(
        [sys.executable, str(PRODUCER), *arguments],
        cwd=probe / "repo", capture_output=True, text=True, check=False,
    )
    negative_report = json.loads((probe / "syntax-report.json").read_text(encoding="utf-8"))
    assert negative.returncode == 1
    assert negative_report["phase"] == "P2"
    assert negative_report["failure_reasons"] == ["MANIFEST_MISMATCH"]
    assert negative_report["overall_status"] == "FAIL"

    (probe / "syntax-report.json").unlink()
    transcript_path = probe / "transcript.json"
    launched = subprocess.run(
        [
            sys.executable, str(PROBE_RUNNER), "--repository-root", str(ROOT),
            "--probe-registry", str(REGISTRY), "--probe-input", str(probe / "probe.json"),
            "--producer-report", str(transcript_path),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert launched.returncode == 1
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["key"]["obligation_id"] == entry["obligation_id"]
    assert transcript["rejection_token"] == "MANIFEST_MISMATCH"
    assert transcript["observed_exit_code"] == 1


@pytest.mark.parametrize("obligation_id", EXPECTED_REJECTIONS)
def test_p2_syntax_probe_runs_declared_rejection(
    tmp_path: Path, obligation_id: str
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert {
        entry["obligation_id"] for entry in registry["probes"]
        if entry["job_id"] == "modified-python-syntax"
    } == {*EXPECTED_REJECTIONS, "H11.syntax.conditional-bridge"}
    entry = next(
        item for item in registry["probes"]
        if item["obligation_id"] == obligation_id
    )
    assert entry["rejection_token"] == EXPECTED_REJECTIONS[obligation_id]
    probe = tmp_path / "probe"
    probe.mkdir()
    builder._syntax_probe(ROOT, probe, entry, "1" * 40, phase="P2")
    request = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
    validate_request_contract(
        entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
    )
    if obligation_id == "H11.syntax.new-or-untracked-omitted":
        arguments = [
            value.replace("{PROBE_ROOT}", str(probe))
            for value in request["child"]["arguments"]
        ]
        manifest = probe / "modified-python.json"
        mutated_bytes = manifest.read_bytes()
        assert [item["path"] for item in json.loads(mutated_bytes)["files"]] == [
            "src/settings.py"
        ]
        base = arguments[arguments.index("--base-ref") + 1]
        candidate = arguments[arguments.index("--candidate-ref") + 1]
        generated = subprocess.run(
            [
                sys.executable, str(PRODUCER), "--base-ref", base,
                "--candidate-ref", candidate,
                "--run-metadata", str(probe / "run-context.json"),
                "--write-manifest", str(probe / "legal-manifest.json"),
                "--include-worktree",
            ],
            cwd=probe / "repo", capture_output=True, text=True, check=False,
        )
        assert generated.returncode == 0, generated.stderr
        legal_bytes = (probe / "legal-manifest.json").read_bytes()
        assert [item["path"] for item in json.loads(legal_bytes)["files"]] == [
            "src/new_untracked.py", "src/settings.py"
        ]
        manifest.write_bytes(legal_bytes)
        positive = subprocess.run(
            [sys.executable, str(PRODUCER), *arguments],
            cwd=probe / "repo", capture_output=True, text=True, check=False,
        )
        positive_report = json.loads(
            (probe / "syntax-report.json").read_text(encoding="utf-8")
        )
        assert positive.returncode == 0, positive.stderr
        assert positive_report["overall_status"] == "PASS"
        assert positive_report["compile_count"] == 2
        manifest.write_bytes(mutated_bytes)
        (probe / "syntax-report.json").unlink()
        validate_request_contract(
            entry, request, probe_root=probe, repository_root=ROOT, phase="P2"
        )

    transcript_path = probe / "transcript.json"
    launched = subprocess.run(
        [
            sys.executable, str(PROBE_RUNNER), "--repository-root", str(ROOT),
            "--probe-registry", str(REGISTRY), "--probe-input", str(probe / "probe.json"),
            "--producer-report", str(transcript_path),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    expected_exit = 2 if obligation_id in {
        "H11.syntax.wrong-base-or-sha", "H11.syntax.ignored-build-source"
    } else 1
    assert launched.returncode == expected_exit
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["key"]["obligation_id"] == obligation_id
    assert transcript["rejection_token"] == EXPECTED_REJECTIONS[obligation_id]
    assert transcript["observed_exit_code"] == expected_exit
