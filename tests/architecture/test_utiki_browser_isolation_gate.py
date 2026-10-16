import ast
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "tools" / "run_utiki_browser_isolation.py"
CONTRACT_PATH = PROJECT_ROOT / "ci" / "utiki-browser-isolation.json"


def _zendriver_start_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uc"
        and node.func.attr == "start"
    ]


def test_isolated_browser_launches_delegate_sandboxing_to_the_container():
    launchers = (
        PROJECT_ROOT / "tests/browser/utiki/test_hard_egress_canary.py",
        PROJECT_ROOT / "tests/browser/utiki/test_production_origin_contract.py",
        PROJECT_ROOT / "tools/utiki_egress_canary.py",
        PROJECT_ROOT / "tools/benchmarks/run_utiki_p1_performance.py",
    )

    for launcher in launchers:
        calls = _zendriver_start_calls(launcher)
        assert len(calls) == 1, launcher
        sandbox = next(
            (keyword.value for keyword in calls[0].keywords if keyword.arg == "sandbox"),
            None,
        )
        assert isinstance(sandbox, ast.Constant), launcher
        assert sandbox.value is False, launcher


def _load_launcher():
    spec = importlib.util.spec_from_file_location("utiki_isolation_launcher", LAUNCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_isolation_contract_requires_independent_os_boundary():
    launcher = _load_launcher()
    contract = launcher._load_json(CONTRACT_PATH)

    launcher._require_contract(contract)

    assert contract["runtime"]["network_mode"] == "none"
    assert contract["runtime"]["docker_socket_mount"] is False
    assert contract["canary"]["interception_state"] == "deliberately-not-installed"
    assert contract["production_origin"]["status_without_hard_isolation_report"] == "BLOCKED"
    assert contract["production_origin"]["production_connection_as_canary_forbidden"] is True


def test_contract_cannot_downgrade_network_none(tmp_path):
    launcher = _load_launcher()
    payload = launcher._load_json(CONTRACT_PATH)
    payload["runtime"]["network_mode"] = "bridge"

    with pytest.raises(launcher.IsolationBlocked, match="network_mode"):
        launcher._require_contract(payload)


def test_image_reference_must_be_digest_pinned(monkeypatch):
    launcher = _load_launcher()

    def unexpected_run(*args, **kwargs):
        raise AssertionError("runtime must not execute for an unpinned image")

    monkeypatch.setattr(launcher, "_run", unexpected_run)
    with pytest.raises(launcher.IsolationBlocked, match="repository digest"):
        launcher._validate_image("docker", "example/isolation:latest")


def test_exact_content_image_id_is_accepted_without_registry(monkeypatch):
    launcher = _load_launcher()
    image_id = "sha256:" + "a" * 64
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, '{"Os":"linux"}', ""),
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps({"Id": image_id, "RepoDigests": []}),
                "",
            ),
        ]
    )
    monkeypatch.setattr(launcher, "_run", lambda *args, **kwargs: next(outputs))

    result = launcher._validate_image("docker", image_id)

    assert result["id"] == image_id
    assert result["reference_kind"] == "content-image-id"


def test_exact_content_image_id_must_match_inspected_id(monkeypatch):
    launcher = _load_launcher()
    requested = "sha256:" + "a" * 64
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, '{"Os":"linux"}', ""),
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {"Id": "sha256:" + "b" * 64, "RepoDigests": []}
                ),
                "",
            ),
        ]
    )
    monkeypatch.setattr(launcher, "_run", lambda *args, **kwargs: next(outputs))

    with pytest.raises(launcher.IsolationBlocked, match="does not match"):
        launcher._validate_image("docker", requested)


def test_launcher_refuses_to_overwrite_stale_outer_report(tmp_path):
    launcher = _load_launcher()
    report = tmp_path / "browser-isolation-raw.json"
    original = b'{"stale":true}\n'
    report.write_bytes(original)

    exit_code = launcher._main(
        [
            "--image",
            "sha256:" + "a" * 64,
            "--image-build-evidence",
            str(tmp_path / "image-build.json"),
            "--run-dir",
            str(tmp_path),
            "--report",
            str(report),
            "--run-id",
            "p1-stale-attempt-1",
            "--candidate-sha",
            "b" * 40,
        ]
    )

    assert exit_code == 2
    assert report.read_bytes() == original


def test_image_build_evidence_binds_recipe_base_and_image(tmp_path):
    launcher = _load_launcher()
    repository = tmp_path / "repo"
    paths = {
        "ci/utiki-isolation/Dockerfile": "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n",
        "requirement.txt": "runtime==1.0\n",
        "requirements-dev.txt": "-r requirement.txt\n",
    }
    inputs = []
    for relative, content in paths.items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        inputs.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    image_id = "sha256:" + "b" * 64
    evidence = tmp_path / "image-build.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_kind": "utiki-isolation-image-build",
                "base_image": "example/python@sha256:" + "c" * 64,
                "image_id": image_id,
                "build_completed_before_isolation": True,
                "isolation_phase_pulled_or_built": False,
                "inputs": inputs,
            }
        ),
        encoding="utf-8",
    )

    result = launcher._validate_image_build_evidence(
        evidence,
        repository.resolve(),
        {"id": image_id},
    )

    assert result["image_id"] == image_id
    assert result["base_image"].startswith("example/python@sha256:")
    assert {item["path"] for item in result["inputs"]} == set(paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"unreviewed": True}), "fields mismatch"),
        (
            lambda payload: payload["inputs"][0].update({"mode": "mutable"}),
            "input fields mismatch",
        ),
        (
            lambda payload: payload.update({"base_image": "python:latest"}),
            "repository digest",
        ),
        (
            lambda payload: payload.update({"image_id": "sha256:" + "d" * 64}),
            "does not match",
        ),
    ],
    ids=("unknown-field", "unknown-input-field", "mutable-base", "wrong-image-id"),
)
def test_image_build_evidence_rejects_unbound_or_unknown_fields(
    tmp_path, mutation, message
):
    launcher = _load_launcher()
    repository = tmp_path / "repo"
    inputs = []
    for relative in (
        "ci/utiki-isolation/Dockerfile",
        "requirement.txt",
        "requirements-dev.txt",
    ):
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")
        inputs.append(
            {"path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        )
    image_id = "sha256:" + "b" * 64
    payload = {
        "schema_version": 1,
        "evidence_kind": "utiki-isolation-image-build",
        "base_image": "example/python@sha256:" + "c" * 64,
        "image_id": image_id,
        "build_completed_before_isolation": True,
        "isolation_phase_pulled_or_built": False,
        "inputs": inputs,
    }
    mutation(payload)
    evidence = tmp_path / "image-build.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(launcher.IsolationBlocked, match=message):
        launcher._validate_image_build_evidence(
            evidence,
            repository.resolve(),
            {"id": image_id},
        )


def test_isolated_command_cannot_restore_network_or_privilege(
    tmp_path, monkeypatch
):
    launcher = _load_launcher()
    monkeypatch.setattr(
        launcher, "_user_options", lambda: ["--user", "65532:65532"]
    )
    image = "example/isolation@sha256:" + "a" * 64
    command = launcher._isolated_command(
        "docker",
        image,
        "utiki-test-isolated",
        PROJECT_ROOT,
        PROJECT_ROOT,
        tmp_path,
        "172.31.0.2",
        "fd42:1:2::2",
        "p1-test",
        1,
        "P1",
        "c" * 64,
        "b" * 40,
    )

    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert "--privileged" not in command
    assert "--pid" not in command
    assert "/var/run/docker.sock" not in " ".join(command)
    repository_mount = command[command.index("--mount") + 1]
    assert repository_mount.endswith(f"dst={PROJECT_ROOT},readonly")
    assert any(
        item.endswith(f"dst={tmp_path.parent}")
        for item in command
        if item.startswith("type=bind")
    )
    assert "tools/build_utiki_negative_inputs.py" not in command
    assert "--negative-output-root" in command
    assert command[command.index("--entrypoint") + 1] == "python"


def test_controlled_peer_audit_fails_when_isolated_path_arrives():
    launcher = _load_launcher()
    logs = (
        "PEER_EVENT tcp path=/positive-control\n"
        "PEER_EVENT tcp path=/positive-control-ipv6\n"
        "PEER_EVENT udp kind=dns-positive\n"
        "PEER_EVENT tcp path=/browser-worker\n"
    )

    with pytest.raises(launcher.IsolationFailed, match="controlled peer"):
        launcher._audit_peer_logs(logs, ["/browser-worker"], ["kind=dns-query"])


def test_controlled_peer_audit_requires_every_positive_control():
    launcher = _load_launcher()
    logs = (
        "PEER_EVENT tcp path=/positive-control\n"
        "PEER_EVENT tcp path=/positive-control-ipv6\n"
    )

    with pytest.raises(launcher.IsolationFailed, match="positive controls"):
        launcher._audit_peer_logs(
            logs,
            ["/browser-worker"],
            ["PEER_EVENT udp kind=dns-query", "PEER_EVENT udp kind=quic"],
        )


@pytest.mark.parametrize(
    "forbidden_event",
    ["PEER_EVENT udp kind=dns-query", "PEER_EVENT udp kind=quic"],
    ids=("dns-query", "udp-quic"),
)
def test_controlled_peer_audit_rejects_forbidden_udp_events(forbidden_event):
    launcher = _load_launcher()
    logs = (
        "PEER_EVENT tcp path=/positive-control\n"
        "PEER_EVENT tcp path=/positive-control-ipv6\n"
        "PEER_EVENT udp kind=dns-positive\n"
        f"{forbidden_event}\n"
    )

    with pytest.raises(launcher.IsolationFailed, match="controlled peer"):
        launcher._audit_peer_logs(
            logs,
            ["/browser-worker"],
            ["PEER_EVENT udp kind=dns-query", "PEER_EVENT udp kind=quic"],
        )


def test_controlled_peer_audit_accepts_only_positive_controls():
    launcher = _load_launcher()
    logs = (
        "PEER_EVENT tcp path=/positive-control\n"
        "PEER_EVENT tcp path=/positive-control-ipv6\n"
        "PEER_EVENT udp kind=dns-positive\n"
    )

    result = launcher._audit_peer_logs(
        logs,
        ["/browser-worker"],
        ["PEER_EVENT udp kind=dns-query", "PEER_EVENT udp kind=quic"],
    )

    assert result["positive_control_counts"] == {
        "tcp_ipv4": 1,
        "tcp_ipv6": 1,
        "udp_dns": 1,
    }
    assert result["forbidden_paths_observed"] == []
    assert result["forbidden_events_observed"] == []


def test_contract_records_native_platforms_as_blocked():
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert payload["native_platforms"] == {
        "windows-x64": "BLOCKED_UNTIL_EQUIVALENT_PROCESS_TREE_PROOF",
        "macos-arm64": "BLOCKED_UNTIL_EQUIVALENT_PROCESS_TREE_PROOF",
    }
