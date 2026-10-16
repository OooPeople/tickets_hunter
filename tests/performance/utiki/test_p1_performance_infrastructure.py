import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from tools import run_utiki_performance_isolation as isolation
from tools.benchmarks import bench_utiki
from tools.benchmarks import run_utiki_p1_performance as inner_runner
from tools.benchmarks import verify_utiki_p1_performance as verifier
from tools.benchmarks.utiki_p1_performance import (
    NODE_IDS,
    RESULT_KEYS,
    PerformanceReportError,
    execution_results,
    load_contract,
    sha256_file,
    sha256_json,
    summarize_samples,
    validate_raw_report,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "ci" / "utiki-performance-isolation.json"
MANIFEST = ROOT / "tests" / "fixtures" / "utiki" / "manifest.json"
CANDIDATE = "a" * 40
CATALOG = "b" * 64


def _source_metric(samples):
    ordered = sorted(samples)
    count = len(samples)
    return {
        "unit": "ns",
        "sample_count": count,
        "samples_ns": samples,
        "failure_count": 0,
        "failures": [],
        "batch_sizes": [count],
        "median_ns": ordered[(count - 1) // 2]
        if count % 2
        else int((ordered[count // 2 - 1] + ordered[count // 2]) / 2),
        "p95_ns": ordered[max(0, math.ceil(0.95 * count) - 1)],
        "p99_ns": ordered[max(0, math.ceil(0.99 * count) - 1)],
        "min_ns": ordered[0],
        "max_ns": ordered[-1],
    }


def _source_report():
    metrics = {}
    for index, case in enumerate(("date", "area", "seat", "quantity"), 1):
        metrics[f"{case}.cold_import"] = _source_metric(
            [index * 100 + value for value in range(3)]
        )
        metrics[f"{case}.cold_first_operation"] = _source_metric(
            [index * 10 + value for value in range(3)]
        )
        metrics[f"{case}.warm_parse_select_plan"] = _source_metric(
            [index * 1000 + value for value in range(300)]
        )
    metrics["event_loop.scheduling_lag"] = _source_metric(
        [2000 + value for value in range(300)]
    )
    return {
        "schema_version": 1,
        "benchmark": "utiki-source",
        "run": {
            "git": {"candidate_sha": CANDIDATE, "dirty": False},
            "os": {"system": "Linux", "machine": "x86_64"},
            "hard_egress_isolation": True,
        },
        "parameters": {
            "mode": "all",
            "seed": 1701,
            "warmup_per_batch": 20,
            "warm_samples_total": 300,
            "batches": 3,
            "cold_samples_per_case": 3,
            "event_loop_samples_total": 300,
        },
        "metrics": metrics,
        "case_results": {
            "area": "b19b4557991c69cc677e4860efdc87dcb7bba4c989df70ab0db24deedf91041d",
            "date": "dc7d892478f42f861c82097ea07a7db7ffe31b48fce9387c309826a2968eecff",
            "quantity": "8f2cdf62b1266ab3025343419058332e6b1c2c72bb3e3a1c01c45b18f45be8b3",
            "seat": "99f3513d1481a78f1c47b890cade7f212257b92be19ea9432b568de65d14f342",
        },
        "digests": {"subject_sha256": "9" * 64},
        "subject_files": [
            "src/platforms/utiki/profiles.py",
            "src/platforms/utiki/selection.py",
        ],
    }


def _raw_report():
    contract = load_contract(CONTRACT)
    semantic = sha256_json({"snapshot": "date"})
    subjects = [
        {"path": "src/platforms/utiki/dom.py", "sha256": "4" * 64},
        {"path": "src/platforms/utiki/profiles.py", "sha256": "5" * 64},
        {"path": "src/platforms/utiki/selection.py", "sha256": "6" * 64},
        {"path": "src/util.py", "sha256": "7" * 64},
    ]
    return {
        "schema_version": 1,
        "report_kind": "utiki-p1-performance",
        "status": "PENDING_OUTER_AUDIT",
        "context": {
            "run_id": "p1-performance-test",
            "attempt": 1,
            "phase": "P1",
            "job_id": "performance-contract",
            "target": "isolated-linux-x64",
            "candidate_sha": CANDIDATE,
        },
        "environment": {
            "os": "linux",
            "architecture": "x86_64",
            "python": "3.11.9",
            "browser": "Chromium test",
        },
        "method": contract["measurement"],
        "digests": {
            "contract_sha256": sha256_file(CONTRACT),
            "execution_manifest_sha256": "c" * 64,
            "fixture_sha256": "d" * 64,
            "production_script_sha256": "e" * 64,
            "source_benchmark_script_sha256": "f" * 64,
            "inner_harness_sha256": "1" * 64,
            "semantic_output_sha256": semantic,
            "production_subject_manifest_sha256": sha256_json(subjects),
        },
        "production_subjects": subjects,
        "isolation": {
            "mode": "linux-container-network-none",
            "only_loopback_interface": True,
            "no_default_ipv4_route": True,
            "no_default_ipv6_route": True,
            "inner_probe_status": "PASS",
            "outer_peer_audit_required": True,
        },
        "source": _source_report(),
        "browser": {
            "origin": "https://tix.ctbcsports.com",
            "path": "/BROTHERS/UTK0201_",
            "classifier": "DATE",
            "interception_stage": "Request",
            "interception_installed": True,
            "classifier_patched": False,
            "allowlist_entries": 1,
            "navigation_fulfilled_requests": 1,
            "rejected_unknown_requests": 0,
            "live_authorization_used": False,
            "semantic_output_sha256": semantic,
            "command_labels": {"evaluate": 30},
            "metrics": {
                "observation_latency": summarize_samples(
                    [1000 + value for value in range(30)], "ns"
                ),
                "cdp_commands_per_observation": summarize_samples(
                    [1] * 30, "count"
                ),
                "ticketing_requests_per_observation": summarize_samples(
                    [0] * 30, "count"
                ),
            },
        },
        "execution_results": execution_results(),
    }


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _junit(path, raw, manifest_sha):
    suite = ElementTree.Element("testsuite", tests="5")
    suite_properties = ElementTree.SubElement(suite, "properties")
    suite_values = {
        **raw["context"],
        "catalog_sha256": CATALOG,
        "execution_manifest_sha256": manifest_sha,
        "fixture_digest": raw["digests"]["fixture_sha256"],
        "script_digest": raw["digests"]["production_script_sha256"],
    }
    for name, value in suite_values.items():
        ElementTree.SubElement(
            suite_properties, "property", name=name, value=str(value)
        )
    for key, node_id in zip(RESULT_KEYS, NODE_IDS):
        testcase = ElementTree.SubElement(
            suite,
            "testcase",
            name=node_id.rsplit("::", 1)[-1],
            classname=node_id.split("::", 1)[0],
        )
        properties = ElementTree.SubElement(testcase, "properties")
        values = {
            "case_id": key[0],
            "obligation_id": key[1],
            "variant_id": key[2],
            "outcome": "passed",
            "wasxfail": "false",
            "setup_outcome": "passed",
            "call_outcome": "passed",
            "teardown_outcome": "passed",
        }
        for name, value in values.items():
            ElementTree.SubElement(
                properties, "property", name=name, value=value
            )
    ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _outer(raw_path, junit_path, reporter_path):
    return {
        "schema_version": 1,
        "report_kind": "utiki-browser-isolation-launcher",
        "status": "PASS",
        "run_id": "p1-performance-test",
        "attempt": 1,
        "phase": "P1",
        "job_id": "performance-contract",
        "target": "isolated-linux-x64",
        "candidate_sha": CANDIDATE,
        "catalog_sha256": CATALOG,
        "execution_manifest_sha256": "c" * 64,
        "context": {
            **_raw_report()["context"],
            "catalog_sha256": CATALOG,
        },
        "execution_results": execution_results(),
        "contract": {
            "path": "ci/utiki-performance-isolation.json",
            "sha256": sha256_file(CONTRACT),
            "contract_id": "utiki-p1-performance-hard-egress-v1",
        },
        "peer_audit": {
            "outbound_count": 0,
            "forbidden_paths_observed": [],
            "forbidden_events_observed": [],
            "positive_control_counts": {
                "tcp_ipv4": 1,
                "tcp_ipv6": 1,
                "udp_dns": 1,
            },
        },
        "runtime_evidence": {
            "isolated_container": {
                "network_mode": "none",
                "privileged": False,
                "readonly_rootfs": True,
                "cap_drop": ["ALL"],
            }
        },
        "benchmark_report_sha256": sha256_file(raw_path),
        "inner_report_sha256": "4" * 64,
        "junit_sha256": sha256_file(junit_path),
        "reporter_result_sha256": sha256_file(reporter_path),
        "execution_manifest": {"sha256": "c" * 64},
        "image": {"id": "sha256:" + "2" * 64},
        "image_build_evidence": {"sha256": "3" * 64},
        "started_at_unix": 1.0,
        "finished_at_unix": 2.0,
        "reasons": [],
    }


def test_contract_pins_p0_sample_policy_and_five_results():
    contract = load_contract(CONTRACT)

    assert contract["measurement"]["warm_samples"] == 300
    assert contract["measurement"]["cold_samples_per_case"] == 3
    assert contract["measurement"]["event_loop_samples"] == 300
    assert contract["measurement"]["browser_samples"] == 30
    assert len(contract["required_execution_results"]) == 5


def test_complete_raw_report_is_accepted():
    validate_raw_report(
        _raw_report(),
        contract=load_contract(CONTRACT),
        expected_candidate_sha=CANDIDATE,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(status="PASS"), "outer isolation audit"),
        (
            lambda value: value["source"]["run"]["git"].update(dirty=True),
            "hard-isolated candidate",
        ),
        (
            lambda value: value["source"]["run"].update(
                hard_egress_isolation=False
            ),
            "hard-isolated candidate",
        ),
        (
            lambda value: value["source"]["case_results"].update(date="0" * 64),
            "differ from P0",
        ),
        (
            lambda value: value["browser"]["metrics"][
                "cdp_commands_per_observation"
            ].update(samples=[0] * 30, median=0, p95=0, p99=0, min=0, max=0),
            "no CDP command",
        ),
        (
            lambda value: value["browser"].update(rejected_unknown_requests=1),
            "browser contract",
        ),
        (
            lambda value: value["execution_results"].pop(),
            "results are incomplete",
        ),
    ],
)
def test_raw_report_corruption_is_rejected(mutation, message):
    report = _raw_report()
    mutation(report)

    with pytest.raises(PerformanceReportError, match=message):
        validate_raw_report(report, contract=load_contract(CONTRACT))


def test_isolated_command_enforces_hard_egress_and_exact_inputs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        isolation.browser_isolation,
        "_user_options",
        lambda: ["--user", "65532:65532"],
    )
    command = isolation._isolated_command(
        runtime="docker",
        image="sha256:" + "a" * 64,
        name="isolated",
        workspace=ROOT,
        run_dir=tmp_path,
        contract_relative=CONTRACT.relative_to(ROOT),
        execution_manifest_relative=MANIFEST.relative_to(ROOT),
        peer_ip="172.20.0.2",
        peer_ipv6="fd42::2",
        run_id="p1-performance-test",
        attempt=1,
        phase="P1",
        catalog_sha256=CATALOG,
        candidate_sha=CANDIDATE,
        candidate_attestation_path="/artifacts/candidate-workspace-attestation.json",
    )

    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert command[command.index("--user") + 1] == "65532:65532"
    assert "tools/benchmarks/run_utiki_p1_performance.py" in command
    assert "/artifacts/raw-benchmark-report.json" in command
    assert "/artifacts/candidate-workspace-attestation.json" in command


def test_no_git_workspace_uses_outer_candidate_attestation(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source = workspace / "src" / "platforms" / "utiki" / "selection.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    data = source.read_bytes()
    files = [
        {
            "path": "src/platforms/utiki/selection.py",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "executable": False,
        }
    ]
    tree_digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    attestation = tmp_path / "candidate-attestation.json"
    _write(
        attestation,
        {
            "schema_version": 1,
            "attestation_kind": "utiki-sanitized-candidate-workspace",
            "candidate_sha": CANDIDATE,
            "archive_sha256": "d" * 64,
            "tree_sha256": tree_digest,
            "tracked_file_count": 1,
            "git_directory_present": False,
            "files": files,
        },
    )
    monkeypatch.setattr(inner_runner, "REPOSITORY", workspace)

    payload, by_path = inner_runner._verify_candidate_attestation(
        attestation, CANDIDATE
    )
    monkeypatch.setenv("TICKETS_HUNTER_CANDIDATE_ATTESTATION", str(attestation))
    metadata = bench_utiki._git_metadata(workspace)

    assert not (workspace / ".git").exists()
    assert payload["tree_sha256"] == tree_digest
    assert by_path[files[0]["path"]] == files[0]["sha256"]
    assert metadata == {
        "candidate_sha": CANDIDATE,
        "dirty": False,
        "working_tree_digest": tree_digest,
    }
    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(
        inner_runner.PerformanceRunError, match="file digest mismatch"
    ):
        inner_runner._verify_candidate_attestation(attestation, CANDIDATE)


def test_browser_launch_failure_is_recorded_as_performance_blocker(monkeypatch):
    async def fail_measurement(measurement):
        assert measurement == {"browser_samples": 30}
        raise RuntimeError("synthetic browser boundary failure")

    monkeypatch.setattr(inner_runner, "_measure_browser", fail_measurement)

    with pytest.raises(
        inner_runner.PerformanceRunError,
        match="isolated browser boundary failed: RuntimeError",
    ):
        inner_runner._run_browser_measurement({"browser_samples": 30})


def test_untrusted_candidate_is_blocked_before_container_runtime(tmp_path):
    run_dir = tmp_path / "run"
    report = run_dir / "performance-isolation-raw.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "run_utiki_performance_isolation.py"),
            "--image",
            "example/performance@sha256:" + "a" * 64,
            "--image-build-evidence",
            str(tmp_path / "not-reached.json"),
            "--execution-manifest",
            str(MANIFEST),
            "--run-dir",
            str(run_dir),
            "--report",
            str(report),
            "--run-id",
            "p1-performance-runtime-missing",
            "--attempt",
            "1",
            "--phase",
            "P1",
            "--catalog-sha256",
            "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1",
            "--candidate-sha",
            CANDIDATE,
            "--runtime",
            str(tmp_path / "missing-runtime"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["execution_results"] == []
    assert any(
        token in payload["reasons"][0]
        for token in ("candidate SHA does not equal", "clean working tree")
    )


def test_verifier_binds_outer_raw_junit_and_reporter(tmp_path):
    raw = _raw_report()
    raw_path = tmp_path / "raw.json"
    junit_path = tmp_path / "performance.xml"
    reporter_path = tmp_path / "reporter.json"
    isolation_path = tmp_path / "isolation.json"
    _write(raw_path, raw)
    _junit(junit_path, raw, raw["digests"]["execution_manifest_sha256"])
    _write(
        reporter_path,
        {"status": "PASS", "required_count": 5, "selected_count": 5},
    )
    _write(isolation_path, _outer(raw_path, junit_path, reporter_path))

    result = verifier.verify(
        contract_path=CONTRACT,
        isolation_path=isolation_path,
        raw_report_path=raw_path,
        junit_path=junit_path,
        reporter_result_path=reporter_path,
    )

    assert result["status"] == "PASS"
    assert result["execution_results"] == execution_results()
    assert result["hard_egress_isolation"]["verified"] is True


def test_outer_audit_binds_inner_raw_junit_and_reporter(tmp_path):
    contract = load_contract(CONTRACT)
    raw = _raw_report()
    raw_path = tmp_path / "raw-benchmark-report.json"
    junit_path = tmp_path / "performance.xml"
    reporter_path = tmp_path / "performance-reporter-result.json"
    inner_path = tmp_path / "inner-performance-report.json"
    _write(raw_path, raw)
    _junit(junit_path, raw, raw["digests"]["execution_manifest_sha256"])
    _write(
        reporter_path,
        {
            "schema_version": 1,
            "status": "PASS",
            "initial_exit_code": 0,
            "final_exit_code": 0,
            "collected_count": 5,
            "selected_count": 5,
            "required_count": 5,
            "missing_node_ids": [],
            "invalid_node_ids": [],
        },
    )
    _write(
        inner_path,
        {
            "schema_version": 1,
            "report_kind": "utiki-p1-performance-inner",
            "status": "PENDING_OUTER_AUDIT",
            "context": raw["context"],
            "probes": {
                name: {"status": "PASS"}
                for name in contract["canary"]["required_inner_probes"]
            },
            "benchmark_report_sha256": sha256_file(raw_path),
            "junit_sha256": sha256_file(junit_path),
            "pytest": {
                "exit_code": 0,
                "command": ["--strict-config", "--strict-markers", *NODE_IDS],
            },
            "reasons": [],
            "netns": "net:[123]",
        },
    )

    digests = isolation._validate_inner_outputs(
        run_dir=tmp_path,
        contract=contract,
        candidate_sha=CANDIDATE,
        expected_context={**raw["context"], "catalog_sha256": CATALOG},
    )

    assert digests["inner"] == sha256_file(inner_path)
    changed = json.loads(reporter_path.read_text(encoding="utf-8"))
    changed["selected_count"] = 4
    _write(reporter_path, changed)
    with pytest.raises(isolation.PerformanceIsolationFailed, match="reporter"):
        isolation._validate_inner_outputs(
            run_dir=tmp_path,
            contract=contract,
            candidate_sha=CANDIDATE,
            expected_context={**raw["context"], "catalog_sha256": CATALOG},
        )


def test_verifier_rejects_changed_raw_after_outer_audit(tmp_path):
    raw = _raw_report()
    raw_path = tmp_path / "raw.json"
    junit_path = tmp_path / "performance.xml"
    reporter_path = tmp_path / "reporter.json"
    isolation_path = tmp_path / "isolation.json"
    _write(raw_path, raw)
    _junit(junit_path, raw, raw["digests"]["execution_manifest_sha256"])
    _write(
        reporter_path,
        {"status": "PASS", "required_count": 5, "selected_count": 5},
    )
    _write(isolation_path, _outer(raw_path, junit_path, reporter_path))
    changed = copy.deepcopy(raw)
    changed["browser"]["metrics"]["cdp_commands_per_observation"]["samples"][0] = 2
    _write(raw_path, changed)

    with pytest.raises(PerformanceReportError, match="digest mismatch"):
        verifier.verify(
            contract_path=CONTRACT,
            isolation_path=isolation_path,
            raw_report_path=raw_path,
            junit_path=junit_path,
            reporter_result_path=reporter_path,
        )


def test_managed_performance_nodes_collect_exactly_five():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--strict-config",
            "--strict-markers",
            "--collect-only",
            "-q",
            *NODE_IDS,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    collected = [line for line in completed.stdout.splitlines() if "::test_v" in line]
    assert collected == list(NODE_IDS)
