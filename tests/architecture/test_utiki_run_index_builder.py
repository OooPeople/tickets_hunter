from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILDER = PROJECT_ROOT / "tools" / "build_utiki_run_index.py"
PHASE = "P1"
RUN_ID = "p1-101-2-attempt-2"
ATTEMPT = 2
CANDIDATE_SHA = "a" * 40
PAIRS = [
    ("unit-contract", "source-linux-x64"),
    ("modified-python-syntax", "source-linux-x64"),
    ("architecture-types", "source-linux-x64"),
    ("browser-contract-isolated", "isolated-linux-x64"),
    ("performance-contract", "isolated-linux-x64"),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fixture(root: Path) -> dict[str, object]:
    catalog = root / "catalog.json"
    _write_json(
        catalog,
        {
            "schema_version": 1,
            "status": "APPROVED",
            "gate_profiles": {
                PHASE: {
                    "required_jobs": [
                        {"job_id": job, "targets": [target], "layers": ["unit"]}
                        for job, target in PAIRS
                    ]
                }
            },
        },
    )
    catalog_digest = _digest(catalog)
    manifest = root / "manifest.json"
    _write_json(
        manifest,
        {
            "schema_version": 1,
            "phase": PHASE,
            "catalog_sha256": catalog_digest,
            "cases": [],
        },
    )
    manifest_digest = _digest(manifest)
    aggregate = root / "aggregate"
    runs: list[tuple[str, str, Path]] = []
    for index, (job, target) in enumerate(PAIRS):
        run_dir = aggregate / f"job-{index}"
        junit = run_dir / "junit.xml"
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(f'<testsuite name="{job}" tests="0" />\n', encoding="utf-8")
        _write_json(
            run_dir / "run-metadata.json",
            {
                "schema_version": 1,
                "run_id": RUN_ID,
                "attempt": ATTEMPT,
                "job_id": job,
                "phase": PHASE,
                "target": target,
                "candidate_sha": CANDIDATE_SHA,
                "catalog_sha256": catalog_digest,
                "execution_manifest_sha256": manifest_digest,
                "junit_sha256": _digest(junit),
                "working_tree_clean": True,
                "exit_codes": {"test": 0, "collector": 0, "reporter": 0},
            },
        )
        runs.append((job, target, run_dir))
    return {
        "catalog": catalog,
        "catalog_digest": catalog_digest,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "aggregate": aggregate,
        "output": aggregate / "gate-runs.json",
        "runs": runs,
    }


def _command(fixture: dict[str, object], runs=None) -> list[str]:
    selected = fixture["runs"] if runs is None else runs
    command = [
        sys.executable,
        str(BUILDER),
        "--required-catalog",
        str(fixture["catalog"]),
        "--approved-catalog-sha256",
        str(fixture["catalog_digest"]),
        "--execution-manifest",
        str(fixture["manifest"]),
        "--phase",
        PHASE,
        "--run-id",
        RUN_ID,
        "--attempt",
        str(ATTEMPT),
        "--candidate-sha",
        CANDIDATE_SHA,
        "--output",
        str(fixture["output"]),
    ]
    for job, target, directory in selected:
        command.extend(["--run", job, target, str(directory)])
    return command


def _run(fixture: dict[str, object], runs=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(fixture, runs),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_builder_records_exact_job_set_and_actual_digests(tmp_path):
    fixture = _fixture(tmp_path)

    completed = _run(fixture)

    assert completed.returncode == 0, completed.stderr
    index = json.loads(fixture["output"].read_text(encoding="utf-8"))
    assert index["run_id"] == RUN_ID
    assert index["candidate_sha"] == CANDIDATE_SHA
    assert index["catalog_sha256"] == fixture["catalog_digest"]
    assert index["execution_manifest_sha256"] == fixture["manifest_digest"]
    assert {(item["job_id"], item["target"]) for item in index["runs"]} == set(PAIRS)
    for item in index["runs"]:
        assert (fixture["aggregate"] / item["junit_path"]).is_file()
        assert _digest(fixture["aggregate"] / item["junit_path"]) == item["junit_sha256"]
        metadata = fixture["aggregate"] / item["run_metadata_path"]
        assert _digest(metadata) == item["run_metadata_sha256"]


def test_builder_rejects_missing_or_unknown_job(tmp_path):
    fixture = _fixture(tmp_path)
    missing = fixture["runs"][:-1]

    completed = _run(fixture, missing)

    assert completed.returncode != 0
    assert "job/target set mismatch" in completed.stderr


def test_builder_rejects_failed_or_cross_run_metadata(tmp_path):
    fixture = _fixture(tmp_path)
    _, _, directory = fixture["runs"][0]
    metadata_path = directory / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["exit_codes"]["test"] = 1
    _write_json(metadata_path, metadata)

    completed = _run(fixture)

    assert completed.returncode != 0
    assert "required producer failed" in completed.stderr


def test_builder_rejects_junit_digest_mismatch(tmp_path):
    fixture = _fixture(tmp_path)
    _, _, directory = fixture["runs"][0]
    (directory / "junit.xml").write_text(
        '<testsuite name="changed" tests="0" />\n', encoding="utf-8"
    )

    completed = _run(fixture)

    assert completed.returncode != 0
    assert "junit_sha256" in completed.stderr


def test_builder_refuses_stale_output_reuse(tmp_path):
    fixture = _fixture(tmp_path)
    fixture["output"].write_text("{}\n", encoding="utf-8")

    completed = _run(fixture)

    assert completed.returncode != 0
    assert "output already exists" in completed.stderr


def test_builder_rejects_duplicate_catalog_keys(tmp_path):
    fixture = _fixture(tmp_path)
    fixture["catalog"].write_text(
        '{"schema_version":1,"schema_version":1,"status":"APPROVED"}\n',
        encoding="utf-8",
    )
    fixture["catalog_digest"] = _digest(fixture["catalog"])

    completed = _run(fixture)

    assert completed.returncode != 0
    assert "duplicate JSON key" in completed.stderr
