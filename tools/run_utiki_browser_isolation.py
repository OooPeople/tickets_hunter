#!/usr/bin/env python3
"""Run uTiki browser fixtures in a fail-closed hard-egress container.

The launcher never pulls or builds an image.  It requires an already-loaded,
digest-pinned Linux image and runs the browser/driver process tree with Docker
``--network none``.  A controlled peer on a separate internal-only network is
used as a positive control; any request from the isolated job reaching that
peer makes the report fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from typing import Any, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.utiki_isolated_workspace import (
    WorkspaceError,
    materialize_candidate,
    require_clean_candidate,
)


SCHEMA_VERSION = 1
REPOSITORY_DIGEST_PATTERN = re.compile(r"^.+@sha256:([0-9a-f]{64})$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_CONTRACT = Path(__file__).resolve().parent.parent / "ci" / "utiki-browser-isolation.json"
PEER_PORT = 8765
OWNER_LABEL = "org.tickets-hunter.utiki-cleanup-owner"
PHASE_INPUTS = {
    "P1": (
        "tests/fixtures/utiki/manifest.json",
        "ci/utiki-p1-negative-probes.json",
    ),
    "P2": (
        "tests/fixtures/utiki/manifest-p2.json",
        "ci/utiki-p2-negative-probes.json",
    ),
}


class IsolationBlocked(RuntimeError):
    """The environment cannot supply trustworthy hard-isolation evidence."""


class IsolationFailed(RuntimeError):
    """A configured isolation invariant was violated."""


def _require_clean_candidate(repository: Path, candidate_sha: str) -> None:
    try:
        require_clean_candidate(repository, candidate_sha)
    except WorkspaceError as exc:
        raise IsolationBlocked(str(exc)) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IsolationBlocked(f"duplicate JSON key in isolation contract: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IsolationBlocked(f"cannot read isolation contract: {exc}") from exc
    if not isinstance(payload, dict):
        raise IsolationBlocked("isolation contract root must be an object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_contract(payload: dict[str, Any]) -> None:
    runtime = payload.get("runtime")
    canary = payload.get("canary")
    production = payload.get("production_origin")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise IsolationBlocked("unknown isolation contract schema")
    if payload.get("job_id") != "browser-contract-isolated":
        raise IsolationBlocked("isolation contract has the wrong job_id")
    if payload.get("target") != "isolated-linux-x64":
        raise IsolationBlocked("isolation contract has the wrong target")
    if not isinstance(runtime, dict) or not isinstance(canary, dict):
        raise IsolationBlocked("isolation contract is incomplete")
    required_runtime = {
        "kind": "docker-linux-container",
        "image_must_be_digest_pinned": True,
        "build_evidence_required": True,
        "build_must_finish_before_isolation": True,
        "build_recipe_and_inputs_must_be_digested": True,
        "base_image_must_be_repository_digest": True,
        "image_pull_during_test": False,
        "network_mode": "none",
        "read_only_root": True,
        "no_new_privileges": True,
        "privileged": False,
        "docker_socket_mount": False,
        "host_network": False,
        "host_pid": False,
        "repository_mount": "read-only",
        "artifact_mount": "write-only-purpose",
    }
    for key, expected in required_runtime.items():
        if runtime.get(key) != expected:
            raise IsolationBlocked(f"unsafe isolation contract field: runtime.{key}")
    if runtime.get("cap_drop") != ["ALL"]:
        raise IsolationBlocked("isolation contract must drop every capability")
    if runtime.get("accepted_image_references") != [
        "repository-digest",
        "content-image-id",
    ]:
        raise IsolationBlocked("isolation contract has unsafe image reference types")
    if canary.get("interception_state") != "deliberately-not-installed":
        raise IsolationBlocked("broken-interception canary must disable interception")
    required_probes = canary.get("required_probes")
    forbidden_paths = canary.get("forbidden_peer_paths")
    forbidden_events = canary.get("forbidden_peer_events")
    if not isinstance(required_probes, list) or not required_probes:
        raise IsolationBlocked("isolation contract has no required probes")
    if not isinstance(forbidden_paths, list) or not forbidden_paths:
        raise IsolationBlocked("isolation contract has no forbidden peer paths")
    if not isinstance(forbidden_events, list) or not forbidden_events:
        raise IsolationBlocked("isolation contract has no forbidden peer events")
    if (
        not isinstance(production, dict)
        or production.get("status_without_hard_isolation_report") != "BLOCKED"
        or production.get("production_connection_as_canary_forbidden") is not True
        or production.get("classifier_or_allowlist_patch_forbidden") is not True
    ):
        raise IsolationBlocked("production-origin fail-closed contract is incomplete")


def _validate_image_build_evidence(
    path: Path,
    repository: Path,
    image_info: dict[str, Any],
) -> dict[str, Any]:
    payload = _load_json(path)
    required_evidence_keys = {
        "schema_version",
        "evidence_kind",
        "base_image",
        "image_id",
        "build_completed_before_isolation",
        "isolation_phase_pulled_or_built",
        "inputs",
    }
    if set(payload) != required_evidence_keys:
        raise IsolationBlocked(
            "image build evidence fields mismatch: "
            f"{sorted(set(payload) ^ required_evidence_keys)}"
        )
    if payload.get("schema_version") != 1:
        raise IsolationBlocked("unknown image build evidence schema")
    if payload.get("evidence_kind") != "utiki-isolation-image-build":
        raise IsolationBlocked("image build evidence has the wrong kind")
    if payload.get("build_completed_before_isolation") is not True:
        raise IsolationBlocked("image build was not completed before isolation")
    if payload.get("isolation_phase_pulled_or_built") is not False:
        raise IsolationBlocked("image evidence permits pull/build during isolation")
    base_image = payload.get("base_image")
    if not isinstance(base_image, str) or REPOSITORY_DIGEST_PATTERN.fullmatch(
        base_image
    ) is None:
        raise IsolationBlocked("base image must use a repository digest")
    evidence_image_id = payload.get("image_id")
    if not isinstance(evidence_image_id, str) or IMAGE_ID_PATTERN.fullmatch(
        evidence_image_id
    ) is None:
        raise IsolationBlocked("build evidence image ID is not content-addressed")
    if evidence_image_id != image_info.get("id"):
        raise IsolationBlocked("build evidence image ID does not match inspected image")

    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        raise IsolationBlocked("image build evidence inputs must be a list")
    required_paths = {
        "ci/utiki-isolation/Dockerfile",
        "requirement.txt",
        "requirements-dev.txt",
    }
    seen: set[str] = set()
    checked_inputs: list[dict[str, str]] = []
    for item in inputs:
        if not isinstance(item, dict):
            raise IsolationBlocked("image build evidence input must be an object")
        if set(item) != {"path", "sha256"}:
            raise IsolationBlocked(
                "image build evidence input fields mismatch: "
                f"{sorted(set(item) ^ {'path', 'sha256'})}"
            )
        relative = item.get("path")
        expected = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            raise IsolationBlocked("image build evidence has an invalid input entry")
        target = (repository / relative).resolve()
        if repository not in target.parents or not target.is_file():
            raise IsolationBlocked(f"image build input is outside repository: {relative}")
        actual = _sha256(target)
        if actual != expected:
            raise IsolationBlocked(f"image build input digest mismatch: {relative}")
        seen.add(relative)
        checked_inputs.append({"path": relative, "sha256": actual})
    if seen != required_paths:
        raise IsolationBlocked(
            f"image build evidence input set mismatch: {sorted(seen ^ required_paths)}"
        )
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "base_image": base_image,
        "image_id": image_info.get("id"),
        "inputs": checked_inputs,
    }


def _run(
    command: Sequence[str],
    *,
    timeout: float = 60.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IsolationBlocked(f"container runtime unavailable: {exc}") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise IsolationBlocked(
            f"container runtime command failed ({completed.returncode}): {detail}"
        )
    return completed


def _validate_image(runtime: str, image: str) -> dict[str, Any]:
    repository_digest = REPOSITORY_DIGEST_PATTERN.fullmatch(image)
    content_id = IMAGE_ID_PATTERN.fullmatch(image)
    if repository_digest is None and content_id is None:
        raise IsolationBlocked(
            "image must use an explicit repository digest or sha256 image ID"
        )
    server = _run(
        [runtime, "version", "--format", "{{json .Server}}"],
        timeout=20,
    )
    try:
        server_payload = json.loads(server.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationBlocked("cannot verify container runtime server") from exc
    if not isinstance(server_payload, dict) or server_payload.get("Os") != "linux":
        raise IsolationBlocked("hard-egress launcher requires a Linux container engine")

    inspected = _run(
        [runtime, "image", "inspect", image, "--format", "{{json .}}"],
        timeout=20,
    )
    try:
        image_payload = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationBlocked("container image inspection returned invalid JSON") from exc
    if not isinstance(image_payload, dict):
        raise IsolationBlocked("container image inspection did not return an object")
    repo_digests = image_payload.get("RepoDigests", [])
    actual_id = image_payload.get("Id")
    if not isinstance(repo_digests, list) or not all(
        isinstance(item, str) for item in repo_digests
    ):
        raise IsolationBlocked("container image repository digests are invalid")
    if not isinstance(actual_id, str) or IMAGE_ID_PATTERN.fullmatch(actual_id) is None:
        raise IsolationBlocked("container image ID is not content-addressed")
    if repository_digest is not None and image not in repo_digests:
        raise IsolationBlocked("loaded image does not attest the requested repository digest")
    if content_id is not None and actual_id != image:
        raise IsolationBlocked("loaded image ID does not match the requested content ID")
    return {
        "requested": image,
        "reference_kind": (
            "repository-digest" if repository_digest is not None else "content-image-id"
        ),
        "id": actual_id,
        "repo_digests": repo_digests,
        "runtime_server": server_payload,
    }


def _security_options() -> list[str]:
    return [
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "512",
    ]


def _user_options() -> list[str]:
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        raise IsolationBlocked("launcher cannot determine an unprivileged container user")
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0:
        return ["--user", "65532:65532"]
    return ["--user", f"{uid}:{gid}"]


def _peer_server_command(
    runtime: str,
    image: str,
    network: str,
    name: str,
    repository: Path,
    owner: str,
) -> list[str]:
    return [
        runtime,
        "run",
        "-d",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"{OWNER_LABEL}={owner}",
        "--network",
        network,
        *_security_options(),
        *_user_options(),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=32m",
        "--workdir",
        "/workspace",
        "--mount",
        f"type=bind,src={repository},dst=/workspace,readonly",
        "--entrypoint",
        "python",
        image,
        "-u",
        "tools/utiki_isolation_peer.py",
        "--port",
        str(PEER_PORT),
    ]


def _positive_control_command(
    runtime: str,
    image: str,
    network: str,
    peer_ip: str,
    peer_ipv6: str,
) -> list[str]:
    script = f"""import socket
s = socket.create_connection(({peer_ip!r}, {PEER_PORT}), 2)
s.sendall(b'GET /positive-control HTTP/1.1\\r\\nHost: peer\\r\\nConnection: close\\r\\n\\r\\n')
assert s.recv(1)
s.close()
s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
s6.settimeout(2)
s6.connect(({peer_ipv6!r}, {PEER_PORT}, 0, 0))
s6.sendall(b'GET /positive-control-ipv6 HTTP/1.1\\r\\nHost: peer\\r\\nConnection: close\\r\\n\\r\\n')
assert s6.recv(1)
s6.close()
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.settimeout(2)
u.sendto(b'UTIKI-DNS-POSITIVE', ({peer_ip!r}, {PEER_PORT}))
assert u.recv(64) == b'DNS-POSITIVE-ACK'
u.close()
"""
    return [
        runtime,
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        network,
        *_security_options(),
        *_user_options(),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--entrypoint",
        "python",
        image,
        "-c",
        script,
    ]


def _isolated_command(
    runtime: str,
    image: str,
    name: str,
    workspace: Path,
    container_repository: Path,
    run_dir: Path,
    peer_ip: str,
    peer_ipv6: str,
    run_id: str,
    attempt: int,
    phase: str,
    catalog_sha256: str,
    candidate_sha: str,
    owner: str,
) -> list[str]:
    paths = PHASE_INPUTS.get(phase)
    if paths is None:
        raise IsolationBlocked("browser execution manifest phase mismatch")
    manifest_relative, registry_relative = paths
    job_run_dir = run_dir.parent
    return [
        runtime,
        "run",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"{OWNER_LABEL}={owner}",
        "--network",
        "none",
        *_security_options(),
        *_user_options(),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=2g",
        "--shm-size",
        "1g",
        "--workdir",
        str(container_repository),
        "--mount",
        f"type=bind,src={workspace},dst={container_repository},readonly",
        "--mount",
        f"type=bind,src={job_run_dir},dst={job_run_dir}",
        "--env",
        "HOME=/tmp/home",
        "--env",
        "TICKETS_HUNTER_HARD_EGRESS_ACTIVE=1",
        "--env",
        f"TICKETS_HUNTER_EGRESS_CANARY_IPV4={peer_ip}",
        "--env",
        f"TICKETS_HUNTER_EGRESS_CANARY_IPV6={peer_ipv6}",
        "--env",
        f"TICKETS_HUNTER_EGRESS_CANARY_PORT={PEER_PORT}",
        "--env",
        f"TICKETS_HUNTER_ISOLATION_RUN_ID={run_id}",
        "--env",
        f"TICKETS_HUNTER_CANDIDATE_SHA={candidate_sha}",
        "--env",
        "TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1",
        "--env",
        f"TICKETS_HUNTER_BROWSER_PROBE_REPORT={run_dir / 'browser-network-probes.json'}",
        "--env",
        f"TICKETS_HUNTER_PRODUCTION_ORIGIN_REPORT={run_dir / 'production-origin-raw.json'}",
        "--entrypoint",
        "python",
        image,
        "tools/utiki_egress_canary.py",
        "--report",
        str(run_dir / "inner-isolation-report.json"),
        "--junit",
        str(run_dir / "browser.xml"),
        "--reporter-context",
        str(run_dir / "browser-pytest-context.json"),
        "--reporter-result",
        str(run_dir / "browser-reporter-result.json"),
        "--repository-root",
        str(container_repository),
        "--required-catalog",
        str(container_repository / "ci/utiki-required-cases.json"),
        "--execution-manifest",
        str(container_repository / manifest_relative),
        "--probe-registry",
        str(container_repository / registry_relative),
        "--negative-output-root",
        str(job_run_dir / "isolated-negative-inputs"),
        "--job-run-dir",
        str(job_run_dir),
        "--isolation-proof",
        str(run_dir / "negative-isolation-proof.json"),
        "--attempt",
        str(attempt),
        "--phase",
        phase,
        "--catalog-sha256",
        catalog_sha256,
    ]


def _inspect_isolated_container(
    runtime: str,
    name: str,
    *,
    workspace_destination: str = "/workspace",
    artifact_destination: str = "/artifacts",
) -> dict[str, Any]:
    completed = _run(
        [runtime, "inspect", name, "--format", "{{json .}}"],
        timeout=20,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationFailed("isolated container inspection is invalid") from exc
    host = payload.get("HostConfig", {})
    config = payload.get("Config", {})
    mounts = payload.get("Mounts", [])
    security = host.get("SecurityOpt") or []
    cap_drop = host.get("CapDrop") or []
    if host.get("NetworkMode") != "none":
        raise IsolationFailed("isolated container did not use network none")
    if host.get("Privileged") is not False:
        raise IsolationFailed("isolated container was privileged")
    if host.get("ReadonlyRootfs") is not True:
        raise IsolationFailed("isolated container root was writable")
    if host.get("PidMode") not in ("", None):
        raise IsolationFailed("isolated container used a host/shared PID namespace")
    if host.get("IpcMode") not in ("", "private", None):
        raise IsolationFailed("isolated container used a shared IPC namespace")
    if "ALL" not in cap_drop:
        raise IsolationFailed("isolated container did not drop all capabilities")
    if host.get("CapAdd"):
        raise IsolationFailed("isolated container added capabilities")
    if host.get("Devices"):
        raise IsolationFailed("isolated container received host devices")
    if not any(str(item).startswith("no-new-privileges") for item in security):
        raise IsolationFailed("isolated container did not enable no-new-privileges")
    if any(mount.get("Destination") == "/var/run/docker.sock" for mount in mounts):
        raise IsolationFailed("isolated container mounted the Docker socket")
    mount_by_destination = {item.get("Destination"): item for item in mounts}
    if mount_by_destination.get(workspace_destination, {}).get("RW") is not False:
        raise IsolationFailed("repository mount was not read-only")
    if mount_by_destination.get(artifact_destination, {}).get("RW") is not True:
        raise IsolationFailed("artifact mount was not writable")
    unexpected_bind_mounts = [
        item.get("Destination")
        for item in mounts
        if item.get("Type") == "bind"
        and item.get("Destination")
        not in {workspace_destination, artifact_destination}
    ]
    if unexpected_bind_mounts:
        raise IsolationFailed(f"unexpected bind mounts: {unexpected_bind_mounts}")
    user = str(config.get("User") or "")
    if not user or user.split(":", 1)[0] in {"0", "root"}:
        raise IsolationFailed("isolated container did not use an unprivileged user")
    return {
        "container_id": payload.get("Id"),
        "network_mode": host.get("NetworkMode"),
        "privileged": host.get("Privileged"),
        "readonly_rootfs": host.get("ReadonlyRootfs"),
        "pid_mode": host.get("PidMode"),
        "ipc_mode": host.get("IpcMode"),
        "cap_drop": cap_drop,
        "cap_add": host.get("CapAdd") or [],
        "security_opt": security,
        "image": config.get("Image"),
        "user": user,
        "mounts": [
            {
                "destination": item.get("Destination"),
                "rw": item.get("RW"),
                "type": item.get("Type"),
            }
            for item in mounts
        ],
    }


def _inspect_internal_network(runtime: str, name: str) -> dict[str, Any]:
    completed = _run(
        [runtime, "network", "inspect", name, "--format", "{{json .}}"],
        timeout=20,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IsolationFailed("controlled peer network inspection is invalid") from exc
    if payload.get("Internal") is not True:
        raise IsolationFailed("controlled peer network is not internal-only")
    if payload.get("Ingress") is True:
        raise IsolationFailed("controlled peer network unexpectedly enables ingress")
    if payload.get("EnableIPv6") is not True:
        raise IsolationFailed("controlled peer network has no IPv6 positive-control path")
    return {
        "id": payload.get("Id"),
        "driver": payload.get("Driver"),
        "internal": payload.get("Internal"),
        "ingress": payload.get("Ingress"),
        "enable_ipv6": payload.get("EnableIPv6"),
        "attachable": payload.get("Attachable"),
    }


def _audit_peer_logs(
    logs: str,
    forbidden_paths: list[str],
    forbidden_events: list[str],
) -> dict[str, Any]:
    positive_ipv4 = logs.count("PEER_EVENT tcp path=/positive-control\n")
    positive_ipv6 = logs.count("PEER_EVENT tcp path=/positive-control-ipv6\n")
    positive_dns = logs.count("PEER_EVENT udp kind=dns-positive\n")
    reached_paths = [
        path for path in forbidden_paths if f"PEER_EVENT tcp path={path}\n" in logs
    ]
    reached_events = [event for event in forbidden_events if event in logs]
    if (positive_ipv4, positive_ipv6, positive_dns) != (1, 1, 1):
        raise IsolationFailed(
            "controlled TCP/IPv6/UDP positive controls were not observed exactly once"
        )
    if reached_paths or reached_events:
        raise IsolationFailed(
            "isolated process reached controlled peer: "
            f"paths={reached_paths}, events={reached_events}"
        )
    return {
        "positive_control_counts": {
            "tcp_ipv4": positive_ipv4,
            "tcp_ipv6": positive_ipv6,
            "udp_dns": positive_dns,
        },
        "forbidden_paths_observed": reached_paths,
        "forbidden_events_observed": reached_events,
        "outbound_count": 0,
    }


def _safe_name(run_id: str, suffix: str) -> str:
    compact = re.sub(r"[^a-z0-9_.-]", "-", run_id.lower()).strip("-.")[:48]
    if not compact:
        compact = "run"
    return f"utiki-{compact}-{secrets.token_hex(4)}-{suffix}"


def _managed_mappings(
    manifest: dict[str, Any], job_id: str, target: str
) -> list[dict[str, str]]:
    mappings: list[dict[str, str]] = []
    for case in manifest.get("cases", []):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise IsolationBlocked("execution manifest case is invalid")
        executions = case.get("executions")
        if not isinstance(executions, list):
            raise IsolationBlocked("execution manifest executions are invalid")
        for execution in executions:
            if (
                not isinstance(execution, dict)
                or execution.get("status") != "MAPPED"
                or execution.get("job_id") != job_id
                or execution.get("target") != target
            ):
                continue
            values = {
                "case_id": case["id"],
                "obligation_id": execution.get("obligation_id"),
                "variant_id": execution.get("variant_id"),
                "node_id": execution.get("node_id"),
                "layer": execution.get("layer"),
            }
            if any(not isinstance(value, str) or not value for value in values.values()):
                raise IsolationBlocked("managed browser mapping is invalid")
            mappings.append(values)  # type: ignore[arg-type]
    if len(mappings) != 13 or len({item["node_id"] for item in mappings}) != 13:
        raise IsolationBlocked("browser contract must bind exactly 13 managed nodes")
    return sorted(mappings, key=lambda item: item["node_id"])


def _prepare_reporter_context(
    *,
    repository: Path,
    manifest_path: Path,
    run_dir: Path,
    run_id: str,
    attempt: int,
    phase: str,
    candidate_sha: str,
    catalog_sha256: str,
) -> tuple[str, str, str]:
    paths = PHASE_INPUTS.get(phase)
    manifest = _load_json(manifest_path)
    if (
        paths is None
        or manifest.get("phase") != phase
        or manifest_path.resolve() != (repository / paths[0]).resolve()
    ):
        raise IsolationBlocked("browser execution manifest phase mismatch")
    if manifest.get("catalog_sha256") != catalog_sha256:
        raise IsolationBlocked("browser execution manifest catalog mismatch")
    if attempt < 1 or not run_id.endswith(f"-attempt-{attempt}"):
        raise IsolationBlocked("browser run attempt context is invalid")
    fixture = repository / "tests" / "fixtures" / "ctbc" / "UTK0201_date.html"
    script = repository / "tools" / "utiki_egress_canary.py"
    if not fixture.is_file() or not script.is_file():
        raise IsolationBlocked("browser fixture/script resource is missing")
    fixture_digest = _sha256(fixture)
    script_digest = _sha256(script)
    context = {
        "schema_version": 1,
        "run_id": run_id,
        "attempt": attempt,
        "job_id": "browser-contract-isolated",
        "phase": phase,
        "target": "isolated-linux-x64",
        "candidate_sha": candidate_sha,
        "catalog_sha256": catalog_sha256,
        "execution_manifest_sha256": _sha256(manifest_path),
        "fixture_digest": fixture_digest,
        "script_digest": script_digest,
        "mappings": _managed_mappings(
            manifest, "browser-contract-isolated", "isolated-linux-x64"
        ),
    }
    _write_json(run_dir / "browser-pytest-context.json", context)
    return _sha256(manifest_path), fixture_digest, script_digest


def _verify_existing_origin(
    isolation_path: Path, origin_path: Path, output_path: Path
) -> int:
    isolation = _load_json(isolation_path.resolve(strict=True))
    origin = _load_json(origin_path.resolve(strict=True))
    if (
        isolation.get("schema_version") != 1
        or isolation.get("report_kind") != "utiki-browser-isolation-launcher"
        or isolation.get("status") != "PASS"
        or isolation.get("production_origin_report_sha256") != _sha256(origin_path)
    ):
        raise IsolationFailed("outer isolation report does not bind production origin")
    if (
        origin.get("schema_version") != 1
        or origin.get("status") != "PASS"
        or origin.get("run_id") != isolation.get("run_id")
        or origin.get("candidate_sha") != isolation.get("candidate_sha")
        or origin.get("interception_installed") is not True
        or origin.get("classifier_patched") is not False
        or origin.get("live_authorization_used") is not False
    ):
        raise IsolationFailed("production-origin report does not match isolated run")
    if output_path.exists() or output_path.is_symlink():
        raise IsolationBlocked("verified production-origin output already exists")
    _write_json(output_path, origin)
    print(json.dumps(origin, sort_keys=True))
    return 0


def _negative_evidence_digests(
    manifest_path: Path, job_run_dir: Path
) -> list[dict[str, str]]:
    manifest = _load_json(manifest_path)
    result: list[dict[str, str]] = []
    for case in manifest.get("cases", []):
        for execution in case.get("executions", []):
            if (
                not isinstance(execution, dict)
                or execution.get("status") != "MAPPED"
                or execution.get("job_id") != "browser-contract-isolated"
                or execution.get("target") != "isolated-linux-x64"
            ):
                continue
            for item in execution.get("evidence", []):
                if not isinstance(item, dict) or item.get("kind") != "checker-exit-nonzero":
                    continue
                relative = item.get("path")
                if not isinstance(relative, str):
                    raise IsolationFailed("negative evidence path is invalid")
                path = (job_run_dir / relative).resolve()
                if job_run_dir.resolve() not in path.parents or path.is_symlink() or not path.is_file():
                    raise IsolationFailed("negative evidence is absent or unsafe")
                envelope = _load_json(path)
                if envelope.get("kind") != "checker-exit-nonzero" or envelope.get("status") != "PASS":
                    raise IsolationFailed("negative evidence envelope did not pass")
                result.append({"path": relative, "sha256": _sha256(path)})
    if len(result) != 12 or len({item["path"] for item in result}) != 12:
        raise IsolationFailed("negative evidence set is incomplete")
    return sorted(result, key=lambda item: item["path"])


def _resource_names(runtime: str, kind: str) -> set[str]:
    command = (
        [runtime, "container", "ls", "--all", "--format", "{{.Names}}"]
        if kind == "container"
        else [runtime, "network", "ls", "--format", "{{.Name}}"]
    )
    return set(_run(command, timeout=20).stdout.splitlines())


def _require_unused_cleanup_names(
    runtime: str, containers: Sequence[str], network: str
) -> None:
    existing_containers = _resource_names(runtime, "container")
    existing_networks = _resource_names(runtime, "network")
    if any(name in existing_containers for name in containers) or network in existing_networks:
        raise IsolationBlocked("isolation resource name already exists")


def _is_only_missing_error(kind: str, identifier: str, detail: str) -> bool:
    prefix = r"(?:Error response from daemon: |Error: )?"
    escaped = re.escape(identifier)
    missing = (
        rf"(?:No such container: {escaped}|No such object: {escaped})"
        if kind == "container"
        else rf"(?:No such network: {escaped}|network {escaped} not found)"
    )
    return re.fullmatch(prefix + missing, detail.strip()) is not None


def _cleanup_resource(runtime: str, kind: str, name: str, owner: str) -> str | None:
    inspect_command = [
        runtime, kind, "inspect", name, "--format", "{{json .}}",
    ]
    try:
        inspected = _run(inspect_command, check=False, timeout=20)
    except IsolationBlocked as exc:
        return f"cannot inspect {kind} {name} before cleanup: {exc}"
    if inspected.returncode != 0:
        detail = inspected.stderr.strip() or inspected.stdout.strip()
        if inspected.returncode != 1 or not _is_only_missing_error(kind, name, detail):
            return f"cannot inspect {kind} {name} ({inspected.returncode}): {detail}"
        try:
            remaining = _resource_names(runtime, kind)
        except IsolationBlocked as exc:
            return f"cannot confirm {kind} {name} is absent after cleanup: {exc}"
        return None if name not in remaining else f"{kind} {name} still exists after cleanup"
    try:
        resource = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return f"cannot verify {kind} {name} ownership: invalid inspect response"
    if not isinstance(resource, dict):
        return f"cannot verify {kind} {name} ownership: invalid inspect response"
    resource_id = resource.get("Id")
    expected_name = f"/{name}" if kind == "container" else name
    labels = (
        resource.get("Config", {}).get("Labels")
        if kind == "container" and isinstance(resource.get("Config"), dict)
        else resource.get("Labels") if kind == "network" else None
    )
    if (
        not isinstance(resource_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", resource_id) is None
        or resource.get("Name") != expected_name
        or not isinstance(labels, dict)
        or labels.get(OWNER_LABEL) != owner
    ):
        return f"cannot verify {kind} {name} belongs to this run"
    remove_command = (
        [runtime, "rm", "-f", resource_id]
        if kind == "container"
        else [runtime, "network", "rm", resource_id]
    )
    try:
        removed = _run(remove_command, check=False, timeout=20)
    except IsolationBlocked as exc:
        return f"cannot remove {kind} {name}: {exc}"
    if removed.returncode == 0:
        return None
    detail = removed.stderr.strip() or removed.stdout.strip()
    if removed.returncode != 1 or not _is_only_missing_error(kind, resource_id, detail):
        return f"failed to remove {kind} {name} ({removed.returncode}): {detail}"
    try:
        remaining = _run(
            [runtime, kind, "inspect", resource_id, "--format", "{{json .}}"],
            check=False, timeout=20,
        )
    except IsolationBlocked as exc:
        return f"cannot confirm {kind} {name} is absent after cleanup: {exc}"
    remaining_detail = remaining.stderr.strip() or remaining.stdout.strip()
    if remaining.returncode != 1 or not _is_only_missing_error(
        kind, resource_id, remaining_detail
    ):
        return f"cannot confirm {kind} {name} is absent after cleanup"
    return None


def _cleanup(
    runtime: str, containers: Sequence[str], network: str, owner: str
) -> list[str]:
    errors: list[str] = []
    for name in containers:
        error = _cleanup_resource(runtime, "container", name, owner)
        if error is not None:
            errors.append(error)
    error = _cleanup_resource(runtime, "network", network, owner)
    if error is not None:
        errors.append(error)
    return errors


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--image")
    parser.add_argument("--image-build-evidence", type=Path)
    parser.add_argument("--execution-manifest", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--phase")
    parser.add_argument("--catalog-sha256")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verify-existing-origin", action="store_true")
    parser.add_argument("--runtime-preflight-negative", action="store_true")
    parser.add_argument("--isolation-report", type=Path)
    parser.add_argument("--origin-report", type=Path)
    args = parser.parse_args(argv)

    if args.runtime_preflight_negative:
        try:
            _run(
                [args.runtime, "version", "--format", "{{json .Server}}"],
                timeout=20,
            )
        except IsolationBlocked as exc:
            print(f"run_utiki_browser_isolation: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.verify_existing_origin:
        if (
            args.isolation_report is None
            or args.origin_report is None
            or args.report is None
        ):
            parser.error(
                "--verify-existing-origin requires --isolation-report, --origin-report, and --report"
            )
        try:
            return _verify_existing_origin(
                args.isolation_report, args.origin_report, args.report
            )
        except (IsolationBlocked, IsolationFailed, OSError, UnicodeError) as exc:
            print(f"run_utiki_browser_isolation: {exc}", file=sys.stderr)
            return 2

    required = {
        "image": args.image,
        "image-build-evidence": args.image_build_evidence,
        "run-dir": args.run_dir,
        "run-id": args.run_id,
        "candidate-sha": args.candidate_sha,
    }
    missing = sorted(name for name, value in required.items() if value is None)
    if missing:
        parser.error(f"regular isolation mode lacks required arguments: {missing}")

    repository = REPOSITORY_ROOT
    contract_path = args.contract.resolve()
    raw_run_dir = args.run_dir
    run_dir = raw_run_dir.resolve()
    report_path = (
        args.report.resolve()
        if args.report is not None
        else run_dir / "browser-isolation-report.json"
    )
    if report_path.exists() or report_path.is_symlink():
        print(
            "run_utiki_browser_isolation: isolation report already exists or is a symlink",
            file=sys.stderr,
        )
        return 2
    started = time.time()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": "utiki-browser-isolation-launcher",
        "status": "BLOCKED",
        "run_id": args.run_id,
        "attempt": args.attempt,
        "phase": args.phase,
        "candidate_sha": args.candidate_sha,
        "catalog_sha256": args.catalog_sha256,
        "execution_manifest_sha256": None,
        "job_id": "browser-contract-isolated",
        "target": "isolated-linux-x64",
        "started_at_unix": started,
        "finished_at_unix": None,
        "contract": {},
        "image": {},
        "image_build_evidence": {},
        "runtime_evidence": {},
        "peer_audit": {},
        "inner_report_sha256": None,
        "junit_sha256": None,
        "reporter_result_sha256": None,
        "production_origin_report_sha256": None,
        "negative_evidence_sha256": [],
        "reasons": [],
    }
    network = _safe_name(args.run_id, "network")
    server = _safe_name(args.run_id, "peer")
    isolated = _safe_name(args.run_id, "isolated")
    owner = secrets.token_hex(16)
    cleanup_errors: list[str] = []
    cleanup_needed = False
    created_containers: list[str] = []
    workspace_context = None
    exit_code = 2
    try:
        if not NAME_PATTERN.fullmatch(args.run_id):
            raise IsolationBlocked("run-id has invalid characters")
        if SHA_PATTERN.fullmatch(args.candidate_sha) is None:
            raise IsolationBlocked("candidate SHA must be an exact 40-character lowercase SHA")
        _require_clean_candidate(repository, args.candidate_sha)
        if not contract_path.is_file():
            raise IsolationBlocked("isolation contract does not exist")
        if raw_run_dir.is_symlink():
            raise IsolationBlocked("run directory must not be a symlink")
        workspace_context = materialize_candidate(repository, args.candidate_sha)
        candidate_workspace, workspace_evidence = workspace_context.__enter__()
        report["runtime_evidence"]["candidate_workspace"] = workspace_evidence
        run_dir.mkdir(parents=True, exist_ok=True)
        stale_outputs = {
            run_dir / "browser-pytest-context.json",
            run_dir / "inner-isolation-report.json",
            run_dir / "browser.xml",
            run_dir / "browser-reporter-result.json",
            run_dir / "production-origin-raw.json",
        }
        if any(path.exists() or path.is_symlink() for path in stale_outputs):
            raise IsolationBlocked("stale browser isolation output is present")
        if run_dir == repository or run_dir in repository.parents:
            raise IsolationBlocked("run directory cannot expose the repository as writable")
        if repository in run_dir.parents:
            relative_run_dir = run_dir.relative_to(repository)
            if not relative_run_dir.parts or relative_run_dir.parts[0] != "artifacts":
                raise IsolationBlocked(
                    "repository-local run directory must be below artifacts/"
                )
        contract = _load_json(contract_path)
        _require_contract(contract)
        report["contract"] = {
            "path": str(contract_path),
            "sha256": _sha256(contract_path),
            "contract_id": contract.get("contract_id"),
        }
        report["image"] = _validate_image(args.runtime, args.image)
        evidence_path = args.image_build_evidence.resolve()
        if not evidence_path.is_file() or args.image_build_evidence.is_symlink():
            raise IsolationBlocked("image build evidence file is absent or a symlink")
        report["image_build_evidence"] = _validate_image_build_evidence(
            evidence_path,
            repository,
            report["image"],
        )
        if (
            args.execution_manifest is None
            or args.attempt is None
            or args.phase is None
            or args.catalog_sha256 is None
            or SHA256_PATTERN.fullmatch(args.catalog_sha256) is None
        ):
            raise IsolationBlocked("formal browser run context is incomplete")
        manifest_path = args.execution_manifest.resolve(strict=True)
        manifest_digest, _, _ = _prepare_reporter_context(
            repository=repository,
            manifest_path=manifest_path,
            run_dir=run_dir,
            run_id=args.run_id,
            attempt=args.attempt,
            phase=args.phase,
            candidate_sha=args.candidate_sha,
            catalog_sha256=args.catalog_sha256,
        )
        report["execution_manifest_sha256"] = manifest_digest
        if args.plan_only:
            report["reasons"].append("plan-only mode cannot produce isolation evidence")
            return exit_code

        _require_unused_cleanup_names(args.runtime, [isolated, server], network)
        subnet_digest = hashlib.sha256(network.encode("ascii")).hexdigest()
        ipv6_subnet = f"fd42:{subnet_digest[:4]}:{subnet_digest[4:8]}::/64"
        cleanup_needed = True
        _run(
            [
                args.runtime,
                "network",
                "create",
                "--label",
                f"{OWNER_LABEL}={owner}",
                "--internal",
                "--ipv6",
                "--subnet",
                ipv6_subnet,
                network,
            ],
            timeout=20,
        )
        report["runtime_evidence"]["controlled_peer_network"] = (
            _inspect_internal_network(args.runtime, network)
        )
        created_containers.append(server)
        _run(
            _peer_server_command(
                args.runtime,
                args.image,
                network,
                server,
                candidate_workspace,
                owner,
            ),
            timeout=30,
        )
        peer_inspect = _run(
            [
                args.runtime,
                "inspect",
                server,
                "--format",
                "{{json .NetworkSettings.Networks}}",
            ],
            timeout=20,
        )
        try:
            peer_networks = json.loads(peer_inspect.stdout)
            peer_network = peer_networks[network]
            peer_ip = peer_network["IPAddress"]
            peer_ipv6 = peer_network["GlobalIPv6Address"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise IsolationBlocked("controlled peer address evidence is invalid") from exc
        if not peer_ip or not peer_ipv6:
            raise IsolationBlocked(
                "controlled peer did not receive both IPv4 and IPv6 addresses"
            )
        peer_ready_detail = ""
        for _ in range(50):
            peer_ready = _run(
                [args.runtime, "logs", server],
                check=False,
                timeout=10,
            )
            peer_ready_detail = peer_ready.stdout + peer_ready.stderr
            if peer_ready.returncode == 0 and "PEER_READY\n" in peer_ready_detail:
                break
            time.sleep(0.1)
        else:
            raise IsolationBlocked(
                "controlled peer did not become ready: " + peer_ready_detail.strip()
            )
        positive = _positive_control_command(
            args.runtime,
            args.image,
            network,
            peer_ip,
            peer_ipv6,
        )
        completed = _run(positive, check=False, timeout=10)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise IsolationBlocked(f"controlled peer positive control failed: {detail}")

        isolated_command = _isolated_command(
            args.runtime,
            args.image,
            isolated,
            candidate_workspace,
            repository,
            run_dir,
            peer_ip,
            peer_ipv6,
            args.run_id,
            args.attempt,
            args.phase,
            args.catalog_sha256,
            args.candidate_sha,
            owner,
        )
        created_containers.append(isolated)
        completed = _run(isolated_command, check=False, timeout=900)
        report["runtime_evidence"]["isolated_container"] = (
            _inspect_isolated_container(
                args.runtime,
                isolated,
                workspace_destination=str(repository),
                artifact_destination=str(run_dir.parent),
            )
        )
        peer_log_result = _run([args.runtime, "logs", server], timeout=20)
        peer_logs = peer_log_result.stdout + peer_log_result.stderr
        forbidden_paths = contract["canary"]["forbidden_peer_paths"]
        forbidden_events = contract["canary"]["forbidden_peer_events"]
        report["peer_audit"] = _audit_peer_logs(
            peer_logs,
            forbidden_paths,
            forbidden_events,
        )

        inner_path = run_dir / "inner-isolation-report.json"
        junit_path = run_dir / "browser.xml"
        reporter_path = run_dir / "browser-reporter-result.json"
        origin_path = run_dir / "production-origin-raw.json"
        if (
            not inner_path.is_file()
            or not junit_path.is_file()
            or not reporter_path.is_file()
            or not origin_path.is_file()
        ):
            raise IsolationFailed(
                "isolated job did not produce managed reporter, origin, and JUnit evidence"
            )
        inner = _load_json(inner_path)
        reporter_result = _load_json(reporter_path)
        origin_report = _load_json(origin_path)
        negative_evidence = _negative_evidence_digests(manifest_path, run_dir.parent)
        report["inner_report_sha256"] = _sha256(inner_path)
        report["junit_sha256"] = _sha256(junit_path)
        report["reporter_result_sha256"] = _sha256(reporter_path)
        report["production_origin_report_sha256"] = _sha256(origin_path)
        report["negative_evidence_sha256"] = negative_evidence
        if completed.returncode != 0:
            raise IsolationFailed(
                f"isolated browser command returned {completed.returncode}"
            )
        if inner.get("status") != "PENDING_OUTER_AUDIT":
            raise IsolationFailed("inner canary did not reach outer-audit state")
        if inner.get("negative_evidence") != negative_evidence:
            raise IsolationFailed("inner/outer negative evidence digest set differs")
        if (
            reporter_result.get("schema_version") != 1
            or reporter_result.get("status") != "PASS"
            or reporter_result.get("final_exit_code") != 0
            or reporter_result.get("required_count") != 13
            or reporter_result.get("selected_count") != 13
            or reporter_result.get("missing_node_ids") != []
            or reporter_result.get("invalid_node_ids") != []
        ):
            raise IsolationFailed("managed browser reporter result is invalid")
        if (
            origin_report.get("status") != "PASS"
            or origin_report.get("interception_installed") is not True
            or origin_report.get("classifier_patched") is not False
            or origin_report.get("live_authorization_used") is not False
        ):
            raise IsolationFailed("production-origin fixture report is invalid")
        required_probes = set(contract["canary"]["required_probes"])
        inner_probes = inner.get("probes", {})
        host_probes = {
            name
            for name, value in inner_probes.items()
            if isinstance(value, dict) and value.get("status") == "PASS"
        }
        missing = sorted(required_probes - host_probes)
        if missing:
            raise IsolationFailed(f"required isolation probes are missing: {missing}")
        report["status"] = "PASS"
        exit_code = 0
    except (IsolationBlocked, WorkspaceError) as exc:
        report["status"] = "BLOCKED"
        report["reasons"].append(str(exc))
    except IsolationFailed as exc:
        report["status"] = "FAIL"
        report["reasons"].append(str(exc))
        exit_code = 1
    finally:
        if cleanup_needed:
            cleanup_errors = _cleanup(
                args.runtime, list(reversed(created_containers)), network, owner
            )
        if workspace_context is not None:
            workspace_context.__exit__(None, None, None)
        if cleanup_errors:
            report["status"] = "FAIL"
            report["reasons"].extend(cleanup_errors)
            exit_code = 1
        report["finished_at_unix"] = time.time()
        try:
            _write_json(report_path, report)
        except OSError as exc:
            print(f"cannot write isolation report: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"status": report["status"], "report": str(report_path)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_main())
