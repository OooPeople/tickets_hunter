import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import utiki_egress_canary as canary
from tools import build_utiki_negative_inputs as negative_builder
from tools import run_utiki_negative_job as negative_job
from tools.utiki_negative_contract import (
    NegativeContractError,
    probe_directory_name,
    validate_request_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = PROJECT_ROOT / "ci" / "utiki-required-cases.json"
MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "utiki" / "manifest.json"
REGISTRY = PROJECT_ROOT / "ci" / "utiki-p1-negative-probes.json"
RUNNER = PROJECT_ROOT / "tools" / "run_utiki_negative_job.py"
BUILDER = PROJECT_ROOT / "tools" / "build_utiki_negative_inputs.py"
CATALOG_SHA256 = "d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _command(tmp_path: Path, registry: Path, job_id: str, target: str) -> list[str]:
    input_root = tmp_path / "inputs"
    input_root.mkdir(exist_ok=True)
    return [
        sys.executable,
        str(RUNNER),
        "--repository-root",
        str(PROJECT_ROOT),
        "--required-catalog",
        str(CATALOG),
        "--approved-catalog-sha256",
        CATALOG_SHA256,
        "--execution-manifest",
        str(MANIFEST),
        "--probe-registry",
        str(registry),
        "--probe-input-root",
        str(input_root),
        "--run-dir",
        str(tmp_path / "run"),
        "--run-id",
        "negative-registry-test-attempt-1",
        "--attempt",
        "1",
        "--job-id",
        job_id,
        "--phase",
        "P1",
        "--target",
        target,
        "--candidate-sha",
        "1" * 40,
        "--catalog-sha256",
        CATALOG_SHA256,
    ]


def test_negative_registry_exactly_covers_manifest_and_catalog() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    registry_keys = {
        tuple(
            item[name]
            for name in ("case_id", "obligation_id", "variant_id", "job_id", "target")
        )
        for item in registry["probes"]
    }
    manifest_keys = {
        (
            case["id"],
            execution["obligation_id"],
            execution["variant_id"],
            execution["job_id"],
            execution["target"],
        )
        for case in manifest["cases"]
        for execution in case["executions"]
        if any(
            item["kind"] == "checker-exit-nonzero"
            for item in execution.get("evidence", [])
        )
    }

    assert len(registry_keys) == len(registry["probes"]) == 57
    assert registry_keys == manifest_keys
    assert sum(item["environment"] == "isolated" for item in registry["probes"]) == 12


def test_negative_job_rejects_registry_missing_one_key(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(registry)
    mutated["probes"].pop()
    path = tmp_path / "registry.json"
    _write_json(path, mutated)

    completed = subprocess.run(
        _command(tmp_path, path, "unit-contract", "source-linux-x64"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "key sets differ" in completed.stderr


def test_isolated_negative_job_cannot_run_on_host(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("TICKETS_HUNTER_HARD_EGRESS_ACTIVE", None)

    completed = subprocess.run(
        _command(
            tmp_path,
            REGISTRY,
            "browser-contract-isolated",
            "isolated-linux-x64",
        ),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "cannot run on the host" in completed.stderr


def test_builder_and_host_negative_job_execute_real_per_key_children(tmp_path: Path) -> None:
    input_root = tmp_path / "inputs"
    built = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--output-root",
            str(input_root),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "host",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    requests = list(input_root.glob("*/probe.json"))
    assert len(requests) == 45
    simulated_windows_root = (
        "C:/Users/runneradmin/AppData/Local/Temp/pytest-of-runneradmin/"
        "pytest-999/test_negative_fixture_path_budget0/inputs/"
    )
    longest_projected_path = max(
        len(simulated_windows_root + path.relative_to(input_root).as_posix())
        for path in input_root.rglob("*")
    )
    assert longest_projected_path < 240
    entries = json.loads(REGISTRY.read_text(encoding="utf-8"))["probes"]

    def request_path(obligation_id: str, variant_id: str) -> Path:
        entry = next(
            item
            for item in entries
            if item["obligation_id"] == obligation_id
            and item["variant_id"] == variant_id
        )
        return input_root / probe_directory_name(entry) / "probe.json"

    unknown = json.loads(
        request_path("H05.arch.unknown-marker", "unknown-marker").read_text(
            encoding="utf-8"
        )
    )
    assert unknown["child"]["working_directory"] == "{PROBE_ROOT}"
    typed = json.loads(
        request_path("H08.arch.new-domain-file", "new-domain-file").read_text(
            encoding="utf-8"
        )
    )
    assert typed["inputs"][0]["path"].endswith("/domain/state.py")
    assert "{PROBE_ROOT}/" + typed["inputs"][0]["path"] in typed["child"]["arguments"]
    ignored = json.loads(
        request_path(
            "H11.syntax.ignored-build-source", "ignored-build-source"
        ).read_text(encoding="utf-8")
    )
    assert ignored["inputs"][0]["path"] == "repo/generated_source.py"

    completed = subprocess.run(
        _command(tmp_path, REGISTRY, "unit-contract", "source-linux-x64"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    envelopes = []
    for path in (tmp_path / "run").glob("evidence/negative/H05*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "checker-exit-nonzero":
            envelopes.append(payload)
    assert len(envelopes) == 3
    assert all(
        payload["status"] == "PASS" and payload["observed_exit_code"] != 0
        for payload in envelopes
    )


def test_ci_checker_probe_keeps_absolute_evidence_bindings_in_place(
    tmp_path: Path,
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in registry["probes"]
        if item["obligation_id"] == "A05.arch.zero-collected"
    )
    report_parent = tmp_path / "run" / "evidence" / "negative"
    name = probe_directory_name(entry)
    probe_dir = report_parent / name
    probe_dir.mkdir(parents=True)
    negative_builder._ci_probe(PROJECT_ROOT, probe_dir, entry, "1" * 40)

    staged = negative_job._stage_probe_input(probe_dir, report_parent, name)
    assert staged == probe_dir.resolve()

    producer_report = report_parent / "producer.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_utiki_negative_probe.py"),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--probe-input",
            str(staged / "probe.json"),
            "--producer-report",
            str(producer_report),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert entry["rejection_token"] in completed.stdout + completed.stderr
    transcript = json.loads(producer_report.read_text(encoding="utf-8"))
    assert transcript["status"] == "EXPECTED_REJECTION"


def test_builder_emits_every_isolated_request_without_host_execution(tmp_path: Path) -> None:
    input_root = tmp_path / "isolated-inputs"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--output-root",
            str(input_root),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "isolated",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    requests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in input_root.glob("*/probe.json")
    ]
    assert len(requests) == 12
    assert all(item["environment"] == "isolated" for item in requests)
    assert {item["variant_id"] for item in requests} == {
        "interception-disabled",
        "subresource",
        "redirect",
        "websocket",
        "worker",
        "child-process",
        "dns",
        "direct-ipv4",
        "direct-ipv6",
        "udp-quic",
        "insufficient-isolation-permission",
        "missing-isolation-proof",
    }


def test_inner_canary_wires_all_isolated_negative_outputs_to_formal_job(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    job_run_dir = tmp_path / "job"
    job_run_dir.mkdir()

    def fake_run(command, **kwargs):
        del kwargs
        commands.append(list(command))
        if str(RUNNER) in command:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            for case in manifest["cases"]:
                for execution in case["executions"]:
                    if execution.get("job_id") != "browser-contract-isolated":
                        continue
                    for item in execution.get("evidence", []):
                        if item["kind"] == "checker-exit-nonzero":
                            _write_json(
                                job_run_dir / item["path"],
                                {
                                    "schema_version": 1,
                                    "kind": "checker-exit-nonzero",
                                    "status": "PASS",
                                },
                            )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(canary.subprocess, "run", fake_run)
    evidence = canary._run_negative_evidence(
        repository=PROJECT_ROOT,
        catalog=CATALOG,
        manifest=MANIFEST,
        registry=REGISTRY,
        output_root=tmp_path / "isolated-inputs",
        job_run_dir=job_run_dir,
        isolation_proof=tmp_path / "isolation-proof.json",
        run_id="p1-negative-attempt-1",
        attempt=1,
        phase="P1",
        catalog_sha256=CATALOG_SHA256,
        candidate_sha="1" * 40,
        netns="net:[123]",
    )

    assert len(evidence) == 12
    builder_command, runner_command = commands
    assert "--environment" in builder_command
    assert builder_command[builder_command.index("--environment") + 1] == "isolated"
    assert "--isolation-proof" in runner_command
    assert runner_command[runner_command.index("--run-dir") + 1] == str(job_run_dir)


def test_inner_canary_loads_project_config_and_preserves_pytest_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    context = tmp_path / "browser-pytest-context.json"
    _write_json(context, {"mappings": [{"node_id": "tests/example.py::test_one"}]})
    junit = tmp_path / "browser.xml"
    reporter_result = tmp_path / "browser-reporter-result.json"
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 4, b"pytest stdout\n", b"pytest stderr\n")

    monkeypatch.setattr(canary.subprocess, "run", fake_run)
    exit_code, command = canary._run_pytest(
        junit,
        context,
        reporter_result,
        PROJECT_ROOT,
    )

    assert exit_code == 4
    assert command == commands[0]
    assert command[command.index("-c") + 1] == str(PROJECT_ROOT / "pytest.ini")
    assert (tmp_path / "browser-pytest.stdout").read_bytes() == b"pytest stdout\n"
    assert (tmp_path / "browser-pytest.stderr").read_bytes() == b"pytest stderr\n"


@pytest.mark.parametrize("mutation", ["top-level-field", "entry-field", "duplicate-key"])
def test_builder_rejects_registry_schema_or_duplicate_keys(
    tmp_path: Path, mutation: str
) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if mutation == "top-level-field":
        registry["unreviewed"] = True
    elif mutation == "entry-field":
        registry["probes"][0]["unreviewed"] = True
    else:
        registry["probes"][1] = copy.deepcopy(registry["probes"][0])
    registry_path = tmp_path / "registry.json"
    _write_json(registry_path, registry)

    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(registry_path),
            "--output-root",
            str(tmp_path / "inputs"),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "host",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "registry" in completed.stderr


def test_builder_rejects_stale_output_root(tmp_path: Path) -> None:
    output = tmp_path / "inputs"
    output.mkdir()
    (output / "stale.txt").write_text("stale\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--output-root",
            str(output),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "host",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "output root must be empty" in completed.stderr


def test_same_token_requests_cannot_be_relabelled_across_execution_keys(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "all-inputs"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--output-root",
            str(input_root),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "all",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["probes"]
    entries = {
        (entry["obligation_id"], entry["variant_id"]): entry for entry in registry
    }
    accepted: list[tuple[str, str]] = []
    identity_fields = (
        "case_id",
        "obligation_id",
        "variant_id",
        "job_id",
        "target",
        "environment",
        "mutation",
        "rejection_token",
    )
    for source_path in sorted(input_root.glob("*/probe.json")):
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source_name = (
            f"{source['obligation_id']}--{source['variant_id']}"
        )
        for target in registry:
            target_name = f"{target['obligation_id']}--{target['variant_id']}"
            if (
                target_name == source_name
                or target["rejection_token"] != source["rejection_token"]
            ):
                continue
            relabelled = copy.deepcopy(source)
            for field in identity_fields:
                relabelled[field] = target[field]
            try:
                validate_request_contract(
                    entries[(target["obligation_id"], target["variant_id"])],
                    relabelled,
                    probe_root=source_path.parent.resolve(),
                    repository_root=PROJECT_ROOT,
                )
            except NegativeContractError:
                continue
            accepted.append((source_name, target_name))
    assert accepted == []


def test_empty_addopts_cannot_reuse_unknown_marker_arguments(tmp_path: Path) -> None:
    input_root = tmp_path / "host-inputs"
    completed = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--output-root",
            str(input_root),
            "--candidate-sha",
            "1" * 40,
            "--environment",
            "host",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["probes"]
    empty_entry = next(
        item
        for item in registry
        if item["obligation_id"] == "H05.arch.empty-addopts"
    )
    unknown_entry = next(
        item
        for item in registry
        if item["obligation_id"] == "H05.arch.unknown-marker"
    )
    empty_path = input_root / probe_directory_name(empty_entry) / "probe.json"
    unknown_path = input_root / probe_directory_name(unknown_entry) / "probe.json"
    empty = json.loads(empty_path.read_text(encoding="utf-8"))
    unknown = json.loads(unknown_path.read_text(encoding="utf-8"))
    empty["child"]["arguments"] = unknown["child"]["arguments"]
    _write_json(empty_path, empty)

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_utiki_negative_probe.py"),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--probe-input",
            str(empty_path),
            "--producer-report",
            str(tmp_path / "transcript.json"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "empty-addopts arguments are not canonical" in result.stderr
