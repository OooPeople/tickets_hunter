import copy
import hashlib
import json
import os
from pathlib import Path
import re
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

H08_AFFECTED_PATHS = {
    "new-domain-file": "src/platforms/utiki/domain/state.py",
    "new-application-file": "src/platforms/utiki/application/engine.py",
    "new-port-file": "src/platforms/utiki/application/ports.py",
    "new-adapter-file": "src/platforms/utiki/adapters/browser.py",
    "new-bootstrap-file": "src/platforms/utiki/bootstrap.py",
    "new-current-config-file": "src/utiki_config.py",
    "new-keyword-file": "src/keyword_rules.py",
    "new-core-bridge-file": "src/utiki_core_services.py",
    "new-upgrade-tool-file": "tools/migrations/convert_utiki_config.py",
}


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


def _registry_entry(obligation_id: str) -> dict[str, object]:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return next(
        item
        for item in registry["probes"]
        if item["obligation_id"] == obligation_id
    )


SLICE2_ENTRIES = [
    item
    for item in json.loads(REGISTRY.read_text(encoding="utf-8"))["probes"]
    if item["case_id"] in {"A05", "A06", "H06", "H11"}
]


def _read_probe_transcript(
    path: Path, completed: subprocess.CompletedProcess[str], root: Path
) -> dict[str, object]:
    if not path.is_file():
        def redact_path(value: str) -> str:
            value = value.replace(str(root), "<probe-root>")
            value = value.replace(str(PROJECT_ROOT), "<repository>")
            value = value.replace(sys.executable, "<python>")
            value = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "<path>", value)
            return re.sub(r"/(?:Users|home|tmp|private)/[^\s'\"]+", "<path>", value)

        sensitive = re.compile(
            r"(?i)\b(password|cookie|token|authorization|webhook|secret|api[_-]?key)\b"
        )
        stderr_lines = [
            "<redacted sensitive stderr line>" if sensitive.search(line) else redact_path(line)
            for line in completed.stderr.splitlines()[:8]
        ]
        safe_stderr = "\n".join(stderr_lines)[:1200]
        command = [redact_path(str(argument)) for argument in completed.args]

        raise AssertionError(
            "negative probe report absent: "
            f"exit_code={completed.returncode}; "
            f"command={command!r}; "
            "cwd=<repository>; "
            f"stderr={safe_stderr!r}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _run_probe(root: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    producer_report = root / "producer.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_utiki_negative_probe.py"),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--probe-input",
            str(root / "probe.json"),
            "--producer-report",
            str(producer_report),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    transcript = _read_probe_transcript(producer_report, completed, root)
    return completed, transcript


def test_missing_probe_report_preserves_redacted_child_failure(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(
        args=[
            sys.executable,
            str(PROJECT_ROOT / "tools/run_utiki_negative_probe.py"),
            r"C:\Users\runneradmin\Temp\probe.json",
        ],
        returncode=2,
        stdout="",
        stderr=(
            f"fixture failed at {tmp_path}\n"
            "fatal: Invalid path 'C:\\Users\\runneradmin\\Temp\\probe.json'\n"
            "Authorization: Bearer sample-credential\n"
            'token="sample part-two"\n'
        ),
    )
    with pytest.raises(AssertionError, match="exit_code=2") as error:
        _read_probe_transcript(tmp_path / "producer.json", completed, tmp_path)
    message = str(error.value)
    assert "command=" in message and "cwd=<repository>" in message
    assert "fixture failed at <probe-root>" in message
    assert "sample-credential" not in message
    assert "part-two" not in message
    assert "runneradmin" not in message
    assert "<redacted sensitive stderr line>" in message


@pytest.mark.parametrize("entry", SLICE2_ENTRIES, ids=lambda item: item["obligation_id"])
def test_slice2_negative_probe_reaches_its_real_checker_and_reason(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    probe_root = tmp_path / "p"
    probe_root.mkdir()
    if entry["child_producer"] == "tools/verify_utiki_ci_results.py":
        negative_builder._ci_probe(PROJECT_ROOT, probe_root, entry, "1" * 40)
    else:
        negative_builder._syntax_probe(PROJECT_ROOT, probe_root, entry, "1" * 40)

    completed, transcript = _run_probe(probe_root)

    assert transcript["status"] == "EXPECTED_REJECTION", completed.stderr
    assert transcript["key"]["obligation_id"] == entry["obligation_id"]
    assert transcript["child"]["producer"] == entry["child_producer"]
    assert completed.returncode == transcript["observed_exit_code"] != 0
    if entry["child_producer"] == "tools/verify_utiki_ci_results.py":
        result = json.loads(completed.stdout)
        assert result["status"] == "FAIL"
        expected_code = (
            "RUN_INDEX_SET_MISMATCH"
            if entry["variant_id"] == "missing-job-or-target"
            else entry["rejection_token"]
        )
        assert result["error_code"] == expected_code
        variant = entry["variant_id"]
        subject = (
            entry["obligation_id"]
            if variant in {
                "test-and-mapping-removed",
                "removed-test-and-mapping",
                "duplicate-or-unknown-id",
                "missing-obligation-variant",
                "forged-not-applicable",
            }
            else {
                "zero-collected": "JUnit lacks",
                "empty-or-missing-result": "JUnit lacks",
                "catalog-unapproved-change": "approval record",
                "stale-run-or-sha": "candidate SHA",
                "digest-mismatch": "JUnit digest",
                "duplicate-result-or-wrong-layer": "duplicate JUnit execution result",
                "missing-job-or-target": "run index job/target",
                "failure-exit-overwritten": "original test exit code",
            }.get(variant, "testcase[0]")
        )
        assert subject in result["message"]
    elif transcript["semantic_output"] is not None:
        semantic = transcript["semantic_output"]
        result = json.loads((probe_root / semantic["path"]).read_text(encoding="utf-8"))
        assert result["overall_status"] == "FAIL"
        assert result["failure_reasons"] == [entry["rejection_token"]]
        if entry["rejection_token"] == "SYNTAX_ERROR":
            request = json.loads((probe_root / "probe.json").read_text(encoding="utf-8"))
            subject = request["inputs"][0]["path"].removeprefix("repo/")
            assert any(
                item["path"] == subject and item["reason"] == "SYNTAX_ERROR"
                for item in result["files"]
            )
        elif entry["rejection_token"] == "MANIFEST_MISMATCH":
            manifest = probe_root / "modified-python.json"
            assert result["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    else:
        assert completed.stderr.strip() == (
            f"verify_modified_python: {entry['rejection_token']}"
        )


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
    git_directories = list(input_root.rglob(".git"))
    assert git_directories
    for git_directory in git_directories:
        projected_object = (
            simulated_windows_root
            + git_directory.relative_to(input_root).as_posix()
            + "/objects/ff/"
            + "a" * 38
        )
        assert len(projected_object) < 240, projected_object
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


def test_pytest_negative_probe_ignores_unreviewed_conftest(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in registry["probes"]
        if item["obligation_id"] == "H05.arch.unknown-marker"
        and item["variant_id"] == "unknown-marker"
    )
    negative_builder._pytest_probe(tmp_path, entry, "1" * 40)
    (tmp_path / "conftest.py").write_text(
        "raise RuntimeError('unreviewed conftest was loaded')\n", encoding="utf-8"
    )
    producer_report = tmp_path / "producer.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_utiki_negative_probe.py"),
            "--repository-root",
            str(PROJECT_ROOT),
            "--probe-registry",
            str(REGISTRY),
            "--probe-input",
            str(tmp_path / "probe.json"),
            "--producer-report",
            str(producer_report),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    transcript = _read_probe_transcript(producer_report, completed, tmp_path)
    assert completed.returncode != 0
    assert entry["rejection_token"] in completed.stdout + completed.stderr
    assert "unreviewed conftest was loaded" not in completed.stdout + completed.stderr
    assert transcript["status"] == "EXPECTED_REJECTION"


def test_strict_pytest_accepts_valid_control(tmp_path: Path) -> None:
    source = tmp_path / "test_control.py"
    config = tmp_path / "pytest.ini"
    source.write_text("def test_control():\n    pass\n", encoding="utf-8")
    config.write_text("[pytest]\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--noconftest",
            "-c",
            str(config),
            "--strict-config",
            "--strict-markers",
            str(source),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_pytest_negative_probe_ignores_external_plugin_and_addopts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _registry_entry("H05.arch.unknown-marker")
    negative_builder._pytest_probe(tmp_path, entry, "1" * 40)
    monkeypatch.setenv("PYTEST_PLUGINS", "utiki_unknown_marker")
    monkeypatch.setenv(
        "PYTEST_ADDOPTS",
        "--override-ini markers=utiki_unknown_marker "
        "--override-ini utiki_unknown_marker=true",
    )

    completed, transcript = _run_probe(tmp_path)

    output = completed.stdout + completed.stderr
    assert completed.returncode == 2
    assert "'utiki_unknown_marker' not found in `markers` configuration option" in output
    assert "ModuleNotFoundError" not in output
    assert transcript["status"] == "EXPECTED_REJECTION"


def test_pytest_negative_probe_rejects_symlinked_payload(tmp_path: Path) -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in registry["probes"]
        if item["obligation_id"] == "H05.arch.unknown-marker"
        and item["variant_id"] == "unknown-marker"
    )
    negative_builder._pytest_probe(tmp_path, entry, "1" * 40)
    request_path = tmp_path / "probe.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    payload = tmp_path / "test_probe.py"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-test-probe.py"
    outside.write_bytes(payload.read_bytes())
    payload.unlink()
    try:
        payload.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this platform")

    with pytest.raises(NegativeContractError, match="symlink"):
        validate_request_contract(
            entry,
            request,
            probe_root=tmp_path,
            repository_root=PROJECT_ROOT,
        )


@pytest.mark.parametrize(
    ("variant", "affected_path"),
    [*H08_AFFECTED_PATHS.items(), ("broad-ignore-any", None)],
)
def test_h08_probe_reaches_exact_reason_and_subject(
    tmp_path: Path, variant: str, affected_path: str | None
) -> None:
    entry = _registry_entry(f"H08.arch.{variant}")
    negative_builder._typecheck_probe(tmp_path, entry, "1" * 40)
    completed, transcript = _run_probe(tmp_path)

    assert completed.returncode == 1
    result = json.loads((tmp_path / "typecheck-report.json").read_text(encoding="utf-8"))
    assert result["status"] == "FAIL"
    expected_reason = "TYPED_SCOPE_MISSING" if affected_path else "UNSAFE_MYPY_CONFIG"
    assert result["failure_reasons"] == [expected_reason]
    assert result.get("affected_paths", []) == ([affected_path] if affected_path else [])
    assert result["mypy_exit_code"] is None
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["observed_exit_code"] == 1
    assert transcript["rejection_token"] == expected_reason
    semantic = transcript["semantic_output"]
    semantic_path = tmp_path / semantic["path"]
    assert semantic["path"] == "typecheck-report.json"
    assert semantic["sha256"] == hashlib.sha256(semantic_path.read_bytes()).hexdigest()


def test_h08_request_binds_only_its_actual_decision_inputs(tmp_path: Path) -> None:
    entry = _registry_entry("H08.arch.new-domain-file")
    negative_builder._typecheck_probe(tmp_path, entry, "1" * 40)

    request = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    actual = {item["kind"]: item["path"] for item in request["inputs"]}

    assert actual == {
        "mutated-typecheck-input": "repo/src/platforms/utiki/domain/state.py",
        "typecheck-manifest": "repo/ci/utiki-typed-scope.txt",
        "typecheck-config": "repo/ci/mypy-utiki.ini",
        "typecheck-mypy": "fake-mypy.py",
        "typecheck-seed-auth": "repo/src/platforms/utiki/auth.py",
        "typecheck-seed-profiles": "repo/src/platforms/utiki/profiles.py",
        "typecheck-seed-selection": "repo/src/platforms/utiki/selection.py",
    }


def test_h08_rejects_an_unbound_relevant_source_before_execution(
    tmp_path: Path,
) -> None:
    entry = _registry_entry("H08.arch.new-domain-file")
    negative_builder._typecheck_probe(tmp_path, entry, "1" * 40)
    unbound = tmp_path / "repo/src/platforms/utiki/domain/unbound.py"
    unbound.write_text("VALUE: int = 1\n", encoding="utf-8")
    request = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))

    with pytest.raises(
        NegativeContractError, match="typecheck relevant source set differs"
    ):
        validate_request_contract(
            entry,
            request,
            probe_root=tmp_path,
            repository_root=PROJECT_ROOT,
        )


def test_h08_unrelated_probe_file_does_not_replace_semantic_rejection(
    tmp_path: Path,
) -> None:
    entry = _registry_entry("H08.arch.new-domain-file")
    negative_builder._typecheck_probe(tmp_path, entry, "1" * 40)
    (tmp_path / "review-notes.txt").write_text("not a producer input\n", encoding="utf-8")

    completed, transcript = _run_probe(tmp_path)

    result = json.loads((tmp_path / "typecheck-report.json").read_text(encoding="utf-8"))
    assert completed.returncode == 1
    assert result["failure_reasons"] == ["TYPED_SCOPE_MISSING"]
    assert result["affected_paths"] == ["src/platforms/utiki/domain/state.py"]
    assert transcript["status"] == "EXPECTED_REJECTION"


@pytest.mark.parametrize(
    ("variant", "expected_exit", "expected_reason"),
    [
        (
            "unknown-marker",
            2,
            "'utiki_unknown_marker' not found in `markers` configuration option",
        ),
        (
            "invalid-pytest-config",
            4,
            "Unknown config option: utiki_unknown_config",
        ),
        (
            "empty-addopts",
            2,
            "'utiki_unknown_marker' not found in `markers` configuration option",
        ),
    ],
)
def test_h05_probe_reaches_strict_pytest_rejection(
    tmp_path: Path, variant: str, expected_exit: int, expected_reason: str
) -> None:
    entry = _registry_entry(f"H05.arch.{variant}")
    negative_builder._pytest_probe(tmp_path, entry, "1" * 40)
    request = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    actual_inputs = {item["kind"]: item["path"] for item in request["inputs"]}
    if variant == "invalid-pytest-config":
        assert actual_inputs == {
            "pytest-config": "pytest.ini",
            "pytest-probe-source": "test_probe.py",
        }
    else:
        assert actual_inputs == {
            "pytest-probe-source": "test_probe.py",
            "pytest-config": "pytest.ini",
        }

    completed, transcript = _run_probe(tmp_path)

    output = completed.stdout + completed.stderr
    assert completed.returncode == expected_exit
    assert entry["rejection_token"] in output
    assert expected_reason in output
    assert transcript["status"] == "EXPECTED_REJECTION"
    assert transcript["observed_exit_code"] == completed.returncode
    assert transcript["semantic_output"] is None
    assert transcript["child"]["kind"] == "pytest-module"
    assert transcript["child"]["producer"] == "pytest"
    assert len(transcript["child"]["producer_sha256"]) == 64


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

    transcript = _read_probe_transcript(producer_report, completed, tmp_path)
    assert completed.returncode != 0
    assert entry["rejection_token"] in completed.stdout + completed.stderr
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
