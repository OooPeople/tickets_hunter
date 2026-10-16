import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTER_MODULE = "tools.utiki_pytest_reporter"
PYTEST_LAUNCHER = PROJECT_ROOT / "tools" / "run_utiki_pytest_job.py"
EVIDENCE_LAUNCHER = PROJECT_ROOT / "tools" / "run_utiki_evidence.py"
NON_PYTEST_ASSEMBLER = PROJECT_ROOT / "tools" / "assemble_utiki_non_pytest_job.py"


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reporter_context(node_id):
    return {
        "schema_version": 1,
        "run_id": "reporter-test-attempt-1",
        "attempt": 1,
        "job_id": "unit-contract",
        "phase": "P1",
        "target": "source-linux-x64",
        "candidate_sha": "1" * 40,
        "catalog_sha256": "2" * 64,
        "execution_manifest_sha256": "3" * 64,
        "fixture_digest": "NOT_APPLICABLE",
        "script_digest": "NOT_APPLICABLE",
        "mappings": [
            {
                "case_id": "H05",
                "obligation_id": "H05.strict-pytest-flags",
                "variant_id": "default",
                "node_id": node_id,
                "layer": "unit",
            }
        ],
    }


def _run_reporter(tmp_path, source, mapped_node="test_sample.py::test_case"):
    test_path = tmp_path / "test_sample.py"
    test_path.write_text(source, encoding="utf-8")
    context = tmp_path / "context.json"
    junit = tmp_path / "managed.xml"
    result = tmp_path / "reporter.json"
    _write_json(context, _reporter_context(mapped_node))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--strict-config",
            "--strict-markers",
            "-p",
            REPORTER_MODULE,
            "--utiki-reporter-context",
            str(context),
            "--utiki-managed-junit",
            str(junit),
            "--utiki-reporter-result",
            str(result),
            str(test_path),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, junit, result


def _properties(element):
    return {
        item.get("name"): item.get("value")
        for item in element.find("properties").findall("property")
    }


def test_reporter_writes_managed_junit_from_real_phase_reports(tmp_path):
    completed, junit, result = _run_reporter(
        tmp_path, "def test_case():\n    assert True\n"
    )

    assert completed.returncode == 0, completed.stderr
    testcase = ElementTree.parse(junit).getroot().find("testcase")
    properties = _properties(testcase)
    assert properties["case_id"] == "H05"
    assert properties["setup_outcome"] == "passed"
    assert properties["call_outcome"] == "passed"
    assert properties["teardown_outcome"] == "passed"
    assert properties["outcome"] == "passed"
    assert json.loads(result.read_text(encoding="utf-8"))["status"] == "PASS"


def test_reporter_never_synthesizes_a_missing_manifest_node(tmp_path):
    completed, junit, result = _run_reporter(
        tmp_path,
        "def test_case():\n    assert True\n",
        mapped_node="test_sample.py::test_not_present",
    )

    assert completed.returncode != 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["missing_node_ids"] == ["test_sample.py::test_not_present"]
    testcase = ElementTree.parse(junit).getroot().find("testcase")
    assert testcase.find("properties") is None


def test_reporter_preserves_runtime_xfail_as_required_failure(tmp_path):
    completed, junit, result = _run_reporter(
        tmp_path,
        "import pytest\n@pytest.mark.xfail(reason='controlled')\ndef test_case():\n    assert False\n",
    )

    assert completed.returncode != 0
    testcase = ElementTree.parse(junit).getroot().find("testcase")
    properties = _properties(testcase)
    assert properties["wasxfail"] == "true"
    assert properties["outcome"] == "failed"
    assert json.loads(result.read_text(encoding="utf-8"))["status"] == "FAIL"


def _git(repository, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _make_git_pytest_repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    _git(repository, "add", "test_sample.py")
    _git(
        repository,
        "-c",
        "user.name=Codex Test",
        "-c",
        "user.email=codex@example.invalid",
        "commit",
        "-qm",
        "test fixture",
    )
    return repository, _git(repository, "rev-parse", "HEAD")


def _minimal_manifest(path, catalog_digest):
    value = {
        "schema_version": 1,
        "catalog_id": "utiki-required-cases",
        "catalog_revision": "1.5.0",
        "catalog_sha256": catalog_digest,
        "phase": "P1",
        "cases": [
            {
                "id": "H05",
                "executions": [
                    {
                        "obligation_id": "H05.strict-pytest-flags",
                        "variant_id": "default",
                        "job_id": "unit-contract",
                        "target": "source-linux-x64",
                        "layer": "unit",
                        "status": "MAPPED",
                        "node_id": "test_sample.py::test_ok",
                        "evidence": [],
                    }
                ],
            }
        ],
    }
    _write_json(path, value)


def test_pytest_launcher_derives_nodes_and_marks_resources_not_applicable(tmp_path):
    repository, candidate = _make_git_pytest_repository(tmp_path)
    catalog_digest = "a" * 64
    manifest = tmp_path / "manifest.json"
    run_dir = tmp_path / "run"
    _minimal_manifest(manifest, catalog_digest)

    completed = subprocess.run(
        [
            sys.executable,
            str(PYTEST_LAUNCHER),
            "--repository-root",
            str(repository),
            "--execution-manifest",
            str(manifest),
            "--run-dir",
            str(run_dir),
            "--run-id",
            "formal-test-attempt-1",
            "--attempt",
            "1",
            "--job-id",
            "unit-contract",
            "--phase",
            "P1",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            candidate,
            "--catalog-sha256",
            catalog_digest,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
    assert metadata["fixture_digest"] == "NOT_APPLICABLE"
    assert metadata["script_digest"] == "NOT_APPLICABLE"
    assert metadata["resources"] == []
    assert metadata["exit_codes"] == {"collector": 0, "reporter": 0, "test": 0}


def test_pytest_launcher_refuses_dirty_candidate(tmp_path):
    repository, candidate = _make_git_pytest_repository(tmp_path)
    catalog_digest = "a" * 64
    manifest = tmp_path / "manifest.json"
    _minimal_manifest(manifest, catalog_digest)
    (repository / "untracked.py").write_text("value = 1\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(PYTEST_LAUNCHER),
            "--repository-root",
            str(repository),
            "--execution-manifest",
            str(manifest),
            "--run-dir",
            str(tmp_path / "run"),
            "--run-id",
            "formal-test-attempt-1",
            "--attempt",
            "1",
            "--job-id",
            "unit-contract",
            "--phase",
            "P1",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            candidate,
            "--catalog-sha256",
            catalog_digest,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "clean working tree" in completed.stderr


def test_non_pytest_assembler_writes_empty_harness_junit_without_fake_case(tmp_path):
    repository, candidate = _make_git_pytest_repository(tmp_path)
    catalog_digest = "a" * 64
    manifest = tmp_path / "syntax-manifest.json"
    run_dir = tmp_path / "syntax-run"
    artifact = run_dir / "evidence" / "python-syntax-report.json"
    manifest_value = {
        "schema_version": 1,
        "catalog_id": "utiki-required-cases",
        "catalog_revision": "1.5.0",
        "catalog_sha256": catalog_digest,
        "phase": "P1",
        "cases": [
            {
                "id": "H11",
                "executions": [
                    {
                        "obligation_id": "H11.syntax.outside-fixed-roots",
                        "variant_id": "outside-fixed-roots",
                        "job_id": "modified-python-syntax",
                        "target": "source-linux-x64",
                        "layer": "syntax",
                        "status": "MAPPED",
                        "node_id": None,
                        "evidence": [
                            {
                                "kind": "python-syntax-report",
                                "path": "evidence/python-syntax-report.json",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    _write_json(manifest, manifest_value)
    artifact_context = {
        "run_id": "syntax-test-attempt-1",
        "attempt": 1,
        "job_id": "modified-python-syntax",
        "phase": "P1",
        "target": "source-linux-x64",
        "candidate_sha": candidate,
        "catalog_sha256": catalog_digest,
        "execution_manifest_sha256": _sha256(manifest),
    }
    _write_json(
        artifact,
        {
            "schema_version": 1,
            "kind": "python-syntax-report",
            "context": artifact_context,
            "status": "PASS",
        },
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(NON_PYTEST_ASSEMBLER),
            "--repository-root",
            str(repository),
            "--execution-manifest",
            str(manifest),
            "--run-dir",
            str(run_dir),
            "--run-id",
            "syntax-test-attempt-1",
            "--attempt",
            "1",
            "--job-id",
            "modified-python-syntax",
            "--phase",
            "P1",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            candidate,
            "--catalog-sha256",
            catalog_digest,
            "--artifact",
            "python-syntax-report",
            str(artifact),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    junit = ElementTree.parse(run_dir / "junit.xml").getroot()
    assert junit.findall("testcase") == []
    metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
    assert metadata["collected_count"] == 0
    assert metadata["selected_count"] == 0
    assert metadata["effective_flags"] == {
        "marker_expression": None,
        "strict_config": None,
        "strict_markers": None,
    }


def test_evidence_launcher_records_actual_negative_process_transcript(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    injected = evidence_dir / "test_probe.py"
    injected.write_text(
        "import pytest\n\n@pytest.mark.utiki_unknown_marker\ndef test_probe():\n    pass\n",
        encoding="utf-8",
    )
    manifest = evidence_dir / "manifest.json"
    manifest_value = {
        "cases": [
            {
                "id": "H05",
                "executions": [
                    {
                        "status": "MAPPED",
                        "job_id": "unit-contract",
                        "target": "source-linux-x64",
                        "obligation_id": "H05.arch.unknown-marker",
                        "variant_id": "unknown-marker",
                        "evidence": [
                            {
                                "kind": "checker-exit-nonzero",
                                "path": "evidence/checker-exit-nonzero.json",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    _write_json(manifest, manifest_value)
    report = evidence_dir / "checker-exit-nonzero.json"
    transcript = evidence_dir / "negative-transcript.json"
    probe_input = evidence_dir / "probe-input.json"
    registry = evidence_dir / "negative-registry.json"
    _write_json(
        registry,
        {
            "schema_version": 1,
            "registry_id": "utiki-p1-negative-probes",
            "phase": "P1",
            "catalog_id": "utiki-required-cases",
            "catalog_revision": "1.5.0",
            "catalog_sha256": "2" * 64,
            "probes": [
                {
                    "argument_contract": "H05.arch.unknown-marker:unknown-marker:argv-v1",
                    "case_id": "H05",
                    "obligation_id": "H05.arch.unknown-marker",
                    "variant_id": "unknown-marker",
                    "job_id": "unit-contract",
                    "target": "source-linux-x64",
                    "environment": "host",
                    "mutation": "unknown-marker",
                    "payload_contract": "H05.arch.unknown-marker:unknown-marker:payload-v1",
                    "rejection_token": "utiki_unknown_marker",
                    "child_kind": "pytest-module",
                    "child_producer": "pytest",
                    "input_kinds": ["pytest-probe-source"],
                }
            ],
        },
    )
    _write_json(
        probe_input,
        {
            "schema_version": 1,
            "case_id": "H05",
            "obligation_id": "H05.arch.unknown-marker",
            "variant_id": "unknown-marker",
            "job_id": "unit-contract",
            "target": "source-linux-x64",
            "candidate_sha": "1" * 40,
            "environment": "host",
            "mutation": "unknown-marker",
            "rejection_token": "utiki_unknown_marker",
            "child": {
                "kind": "pytest-module",
                "producer": "pytest",
                "arguments": [
                    "--strict-config",
                    "--strict-markers",
                    "{PROBE_ROOT}/test_probe.py",
                ],
                "working_directory": "{PROBE_ROOT}",
            },
            "inputs": [
                {
                    "kind": "pytest-probe-source",
                    "path": injected.name,
                    "sha256": _sha256(injected),
                }
            ],
        },
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_LAUNCHER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--execution-manifest",
            str(manifest),
            "--report",
            str(report),
            "--kind",
            "checker-exit-nonzero",
            "--run-id",
            "evidence-test-attempt-1",
            "--attempt",
            "1",
            "--job-id",
            "unit-contract",
            "--phase",
            "P1",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            "1" * 40,
            "--catalog-sha256",
            "2" * 64,
            "--producer",
            "tools/run_utiki_negative_probe.py",
            "--input",
            "injected-probe",
            "evidence",
            probe_input.name,
            "--input",
            "probe-registry",
            "evidence",
            registry.name,
            "--producer-report",
            transcript.name,
            "--rejection-token",
            "utiki_unknown_marker",
            "--case-id",
            "H05",
            "--obligation-id",
            "H05.arch.unknown-marker",
            "--variant-id",
            "unknown-marker",
            "--",
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(registry),
            "--probe-input",
            str(probe_input),
            "--producer-report",
            str(transcript),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["observed_exit_code"] != 0
    assert payload["producer"]["sha256"] == _sha256(
        PROJECT_ROOT / "tools" / "run_utiki_negative_probe.py"
    )
    outputs = {item["kind"]: item for item in payload["outputs"]}
    assert {"stdout", "stderr", "producer-report"}.issubset(outputs)
    captured = "".join(
        (evidence_dir / outputs[kind]["path"]).read_text(encoding="utf-8")
        for kind in ("stdout", "stderr")
    )
    assert "utiki_unknown_marker" in captured
    raw = json.loads(transcript.read_text(encoding="utf-8"))
    assert raw["observed_exit_code"] == payload["observed_exit_code"]
    assert raw["status"] == "EXPECTED_REJECTION"


def test_evidence_launcher_captures_actual_positive_producer_stdout(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    manifest = evidence_dir / "manifest.json"
    _write_json(
        manifest,
        {
            "cases": [
                {
                    "id": "A05",
                    "executions": [
                        {
                            "status": "MAPPED",
                            "job_id": "architecture-types",
                            "target": "source-linux-x64",
                            "obligation_id": "A05.arch.check",
                            "variant_id": "default",
                            "evidence": [
                                {
                                    "kind": "architecture-report",
                                    "path": "evidence/architecture-report.json",
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    report = evidence_dir / "architecture-report.json"
    inventory = (
        PROJECT_ROOT
        / "docs"
        / "02-development"
        / "utiki-refactor"
        / "core-integration-inventory.md"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_LAUNCHER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--execution-manifest",
            str(manifest),
            "--report",
            str(report),
            "--kind",
            "architecture-report",
            "--run-id",
            "architecture-test-attempt-1",
            "--attempt",
            "1",
            "--job-id",
            "architecture-types",
            "--phase",
            "P1",
            "--target",
            "source-linux-x64",
            "--candidate-sha",
            "1" * 40,
            "--catalog-sha256",
            "2" * 64,
            "--producer",
            "tools/check_utiki_architecture.py",
            "--input",
            "core-inventory",
            "repository",
            inventory.relative_to(PROJECT_ROOT).as_posix(),
            "--producer-report",
            "stdout",
            "--",
            "--root",
            str(PROJECT_ROOT),
            "--core-inventory",
            str(inventory),
            "--candidate-sha",
            "1" * 40,
            "--strict",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(report.read_text(encoding="utf-8"))
    assert envelope["observed_exit_code"] == 0
    outputs = {item["kind"]: item for item in envelope["outputs"]}
    assert outputs["producer-report"]["path"] == outputs["stdout"]["path"]
    payload = json.loads(
        (evidence_dir / outputs["stdout"]["path"]).read_text(encoding="utf-8")
    )
    assert payload["status"] == "PASS"
    assert payload["checked_files"]
