from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSEMBLER = PROJECT_ROOT / "tools" / "assemble_utiki_isolated_job.py"
RUN_ID = "p1-404-3-attempt-3"
ATTEMPT = 3
JOB_ID = "browser-contract-isolated"
PHASE = "P1"
TARGET = "isolated-linux-x64"
CANDIDATE_SHA = "a" * 40
CATALOG_SHA256 = "b" * 64
NODE_ID = "tests/browser/utiki/test_contract.py::test_required_contract"


def _load_assembler():
    spec = importlib.util.spec_from_file_location("utiki_isolated_assembler", ASSEMBLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _properties(parent: ElementTree.Element, values: dict[str, object]) -> None:
    properties = ElementTree.SubElement(parent, "properties")
    for name, value in values.items():
        ElementTree.SubElement(properties, "property", name=name, value=str(value))


def _fixture(tmp_path: Path) -> dict[str, object]:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    run_dir = tmp_path / "run"
    evidence = run_dir / "evidence"
    manifest = tmp_path / "manifest.json"
    mapping = {
        "case_id": "H01",
        "obligation_id": "H01.egress.required",
        "variant_id": "required",
        "node_id": NODE_ID,
        "layer": "browser-isolation",
    }
    isolation_relative = "evidence/browser-isolation-report.json"
    negative_relative = "evidence/negative/H01.egress.required--required.json"
    _write_json(
        manifest,
        {
            "schema_version": 1,
            "phase": PHASE,
            "catalog_sha256": CATALOG_SHA256,
            "cases": [
                {
                    "id": "H01",
                    "executions": [
                        {
                            "status": "MAPPED",
                            "job_id": JOB_ID,
                            "target": TARGET,
                            **{key: mapping[key] for key in mapping if key != "case_id"},
                            "evidence": [
                                {
                                    "kind": "browser-isolation-report",
                                    "path": isolation_relative,
                                },
                                {
                                    "kind": "checker-exit-nonzero",
                                    "path": negative_relative,
                                },
                            ],
                        }
                    ],
                }
            ],
        },
    )
    manifest_digest = _digest(manifest)
    context = {
        "run_id": RUN_ID,
        "attempt": ATTEMPT,
        "job_id": JOB_ID,
        "phase": PHASE,
        "target": TARGET,
        "candidate_sha": CANDIDATE_SHA,
        "catalog_sha256": CATALOG_SHA256,
        "execution_manifest_sha256": manifest_digest,
    }

    fixture_path = run_dir / "resources" / "fixture.html"
    script_path = run_dir / "resources" / "script.py"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text("<html></html>\n", encoding="utf-8")
    script_path.write_text("raise SystemExit(0)\n", encoding="utf-8")

    junit_path = run_dir / "raw" / "managed.xml"
    junit_path.parent.mkdir(parents=True)
    suite = ElementTree.Element("testsuite", name="isolated", tests="1")
    suite_context = {
        **context,
        "fixture_digest": _digest(fixture_path),
        "script_digest": _digest(script_path),
    }
    _properties(suite, suite_context)
    testcase = ElementTree.SubElement(
        suite,
        "testcase",
        name="test_required_contract",
        classname="tests.browser.utiki.test_contract.py",
    )
    _properties(
        testcase,
        {
            **mapping,
            "outcome": "passed",
            "wasxfail": "false",
            "setup_outcome": "passed",
            "call_outcome": "passed",
            "teardown_outcome": "passed",
        },
    )
    ElementTree.ElementTree(suite).write(
        junit_path, encoding="utf-8", xml_declaration=True
    )

    reporter_path = run_dir / "raw" / "reporter-result.json"
    _write_json(
        reporter_path,
        {
            "schema_version": 1,
            "status": "PASS",
            "initial_exit_code": 0,
            "final_exit_code": 0,
            "collected_count": 1,
            "selected_count": 1,
            "required_count": 1,
            "missing_node_ids": [],
            "invalid_node_ids": [],
        },
    )
    inner_path = run_dir / "raw" / "inner.json"
    command = [
        "/usr/bin/python3",
        "-m",
        "pytest",
        "--strict-config",
        "--strict-markers",
        "-m",
        "browser and not live",
        NODE_ID,
    ]
    negative_path = run_dir / negative_relative
    _write_json(
        negative_path,
        {
            "schema_version": 1,
            "kind": "checker-exit-nonzero",
            "status": "PASS",
            "context": context,
        },
    )
    negative_evidence = [
        {"path": negative_relative, "sha256": _digest(negative_path)}
    ]
    _write_json(
        inner_path,
        {
            "schema_version": 1,
            "status": "PENDING_OUTER_AUDIT",
            "pytest": {"command": command, "exit_code": 0},
            "negative_evidence": negative_evidence,
        },
    )
    raw_path = evidence / "raw-isolation-report.json"
    _write_json(
        raw_path,
        {
            "schema_version": 1,
            "report_kind": "utiki-browser-isolation-launcher",
            "status": "PASS",
            **context,
            "inner_report_sha256": _digest(inner_path),
            "junit_sha256": _digest(junit_path),
            "reporter_result_sha256": _digest(reporter_path),
            "negative_evidence_sha256": negative_evidence,
            "runtime_evidence": {
                "network_mode": "none",
                "candidate_workspace": {
                    "source": "git-archive",
                    "candidate_sha": CANDIDATE_SHA,
                    "archive_sha256": "c" * 64,
                    "tree_sha256": "d" * 64,
                    "tracked_file_count": 10,
                    "source_checkout_mounted": False,
                    "ignored_or_untracked_files_included": False,
                },
            },
            "peer_audit": {"network_mode": "none"},
            "started_at_unix": 100.0,
            "finished_at_unix": 101.0,
        },
    )
    isolation_path = run_dir / isolation_relative
    _write_json(
        isolation_path,
        {
            "schema_version": 1,
            "kind": "browser-isolation-report",
            "status": "PASS",
            "context": context,
            "outputs": [
                {
                    "kind": "producer-report",
                    "scope": "evidence",
                    "path": raw_path.name,
                    "sha256": _digest(raw_path),
                }
            ],
        },
    )
    return {
        "repository": repository,
        "run_dir": run_dir,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "fixture": fixture_path,
        "script": script_path,
        "junit": junit_path,
        "reporter": reporter_path,
        "inner": inner_path,
        "raw": raw_path,
        "isolation": isolation_path,
        "negative": negative_path,
        "command": command,
    }


def _arguments(fixture: dict[str, object]) -> list[str]:
    return [
        "--repository-root",
        str(fixture["repository"]),
        "--execution-manifest",
        str(fixture["manifest"]),
        "--run-dir",
        str(fixture["run_dir"]),
        "--run-id",
        RUN_ID,
        "--attempt",
        str(ATTEMPT),
        "--job-id",
        JOB_ID,
        "--phase",
        PHASE,
        "--target",
        TARGET,
        "--candidate-sha",
        CANDIDATE_SHA,
        "--catalog-sha256",
        CATALOG_SHA256,
        "--junit",
        str(fixture["junit"]),
        "--reporter-result",
        str(fixture["reporter"]),
        "--inner-report",
        str(fixture["inner"]),
        "--resource",
        "fixture",
        str(fixture["fixture"]),
        "--resource",
        "script",
        str(fixture["script"]),
        "--artifact",
        "browser-isolation-report",
        str(fixture["isolation"]),
        "--artifact",
        "checker-exit-nonzero",
        str(fixture["negative"]),
    ]


def _prepare(tmp_path: Path, monkeypatch):
    assembler = _load_assembler()
    fixture = _fixture(tmp_path)

    def fake_git(repository: Path, *arguments: str) -> bytes:
        del repository
        if arguments == ("rev-parse", "HEAD"):
            return (CANDIDATE_SHA + "\n").encode("ascii")
        if arguments == ("status", "--porcelain=v1", "-z"):
            return b""
        raise AssertionError(arguments)

    monkeypatch.setattr(assembler, "_git", fake_git)
    return assembler, fixture


def test_assembler_packages_byte_identical_managed_junit_and_bound_metadata(
    tmp_path, monkeypatch
):
    assembler, fixture = _prepare(tmp_path, monkeypatch)
    original_junit = fixture["junit"].read_bytes()

    exit_code = assembler.main(_arguments(fixture))

    assert exit_code == 0
    run_dir = fixture["run_dir"]
    assert (run_dir / "junit.xml").read_bytes() == original_junit
    metadata = json.loads((run_dir / "run-metadata.json").read_text(encoding="utf-8"))
    assert metadata["execution_manifest_sha256"] == fixture["manifest_digest"]
    assert metadata["effective_command"] == fixture["command"]
    assert metadata["effective_flags"]["marker_expression"] == "browser and not live"
    assert metadata["exit_codes"] == {"test": 0, "collector": 0, "reporter": 0}
    assert metadata["selected_count"] == 1
    assert metadata["isolation"]["verified"] is True
    assert {(item["kind"], item["path"]) for item in metadata["artifacts"]} == {
        ("browser-isolation-report", "evidence/browser-isolation-report.json"),
        (
            "checker-exit-nonzero",
            "evidence/negative/H01.egress.required--required.json",
        ),
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-artifact",
        "junit-failure",
        "outer-junit-digest",
        "reporter-exit",
        "inner-pytest-exit",
        "stale-envelope",
    ],
)
def test_assembler_rejects_incomplete_or_invented_isolated_results(
    tmp_path, monkeypatch, mutation
):
    assembler, fixture = _prepare(tmp_path, monkeypatch)
    arguments = _arguments(fixture)
    if mutation == "missing-artifact":
        index = arguments.index("checker-exit-nonzero")
        del arguments[index - 1 : index + 2]
    elif mutation == "junit-failure":
        root = ElementTree.parse(fixture["junit"]).getroot()
        testcase = root.find("testcase")
        assert testcase is not None
        properties = testcase.find("properties")
        assert properties is not None
        outcome = next(item for item in properties if item.get("name") == "outcome")
        outcome.set("value", "failed")
        ElementTree.ElementTree(root).write(
            fixture["junit"], encoding="utf-8", xml_declaration=True
        )
    elif mutation == "outer-junit-digest":
        raw = json.loads(fixture["raw"].read_text(encoding="utf-8"))
        raw["junit_sha256"] = "0" * 64
        _write_json(fixture["raw"], raw)
        envelope = json.loads(fixture["isolation"].read_text(encoding="utf-8"))
        envelope["outputs"][0]["sha256"] = _digest(fixture["raw"])
        _write_json(fixture["isolation"], envelope)
    elif mutation == "reporter-exit":
        reporter = json.loads(fixture["reporter"].read_text(encoding="utf-8"))
        reporter["final_exit_code"] = 1
        _write_json(fixture["reporter"], reporter)
    elif mutation == "inner-pytest-exit":
        inner = json.loads(fixture["inner"].read_text(encoding="utf-8"))
        inner["pytest"]["exit_code"] = 1
        _write_json(fixture["inner"], inner)
        raw = json.loads(fixture["raw"].read_text(encoding="utf-8"))
        raw["inner_report_sha256"] = _digest(fixture["inner"])
        _write_json(fixture["raw"], raw)
        envelope = json.loads(fixture["isolation"].read_text(encoding="utf-8"))
        envelope["outputs"][0]["sha256"] = _digest(fixture["raw"])
        _write_json(fixture["isolation"], envelope)
    elif mutation == "stale-envelope":
        envelope = json.loads(fixture["negative"].read_text(encoding="utf-8"))
        envelope["context"]["run_id"] = "p1-stale-attempt-1"
        _write_json(fixture["negative"], envelope)

    exit_code = assembler.main(arguments)

    assert exit_code != 0
    assert not (fixture["run_dir"] / "run-metadata.json").exists()


def test_assembler_refuses_stale_metadata_destination(tmp_path, monkeypatch):
    assembler, fixture = _prepare(tmp_path, monkeypatch)
    metadata = fixture["run_dir"] / "run-metadata.json"
    metadata.write_text("{}\n", encoding="utf-8")

    exit_code = assembler.main(_arguments(fixture))

    assert exit_code != 0
    assert metadata.read_text(encoding="utf-8") == "{}\n"
