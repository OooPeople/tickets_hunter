#!/usr/bin/env python3
"""Run the uTiki P1 performance baseline in a hard-egress container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools import run_utiki_browser_isolation as browser_isolation
from tools.utiki_isolated_workspace import (
    WorkspaceError,
    candidate_attestation,
    materialize_candidate,
    require_clean_candidate,
)
from tools.benchmarks.utiki_p1_performance import (
    JOB_ID,
    NODE_IDS,
    PHASE,
    TARGET,
    PerformanceReportError,
    execution_results,
    load_contract,
    load_json,
    node_ids,
    performance_phase,
    sha256_file,
    validate_raw_report,
    write_json,
)


SCHEMA_VERSION = 1
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_CONTRACT = (
    Path(__file__).resolve().parent.parent / "ci" / "utiki-performance-isolation.json"
)
PEER_PORT = browser_isolation.PEER_PORT


class PerformanceIsolationBlocked(RuntimeError):
    """The environment cannot supply trustworthy performance evidence."""


class PerformanceIsolationFailed(RuntimeError):
    """The performance process violated an isolation or evidence invariant."""


def _git(repository: Path, *arguments: str) -> bytes:
    completed = browser_isolation._run(
        ["git", *arguments], timeout=30, check=False
    )
    if completed.returncode != 0:
        raise PerformanceIsolationBlocked("git context cannot be verified")
    return completed.stdout.encode("utf-8")


def _require_clean_candidate(repository: Path, candidate_sha: str) -> None:
    try:
        require_clean_candidate(repository, candidate_sha)
    except WorkspaceError as exc:
        raise PerformanceIsolationBlocked(str(exc)) from exc


def _safe_name(run_id: str, suffix: str) -> str:
    compact = re.sub(r"[^a-z0-9_.-]", "-", run_id.lower()).strip("-.")[:48]
    if not compact:
        compact = "run"
    return f"utiki-perf-{compact}-{secrets.token_hex(4)}-{suffix}"


def _isolated_command(
    *,
    runtime: str,
    image: str,
    name: str,
    workspace: Path,
    run_dir: Path,
    contract_relative: Path,
    execution_manifest_relative: Path,
    peer_ip: str,
    peer_ipv6: str,
    run_id: str,
    attempt: int,
    phase: str,
    catalog_sha256: str,
    candidate_sha: str,
    candidate_attestation_path: str,
    owner: str,
) -> list[str]:
    return [
        runtime,
        "run",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        f"{browser_isolation.OWNER_LABEL}={owner}",
        "--network",
        "none",
        *browser_isolation._security_options(),
        *browser_isolation._user_options(),
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=2g",
        "--shm-size",
        "1g",
        "--workdir",
        "/workspace",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={run_dir},dst=/artifacts",
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
        "--entrypoint",
        "python",
        image,
        "tools/benchmarks/run_utiki_p1_performance.py",
        "--contract",
        f"/workspace/{contract_relative.as_posix()}",
        "--execution-manifest",
        f"/workspace/{execution_manifest_relative.as_posix()}",
        "--report",
        "/artifacts/raw-benchmark-report.json",
        "--inner-report",
        "/artifacts/inner-performance-report.json",
        "--junit",
        "/artifacts/performance.xml",
        "--reporter-result",
        "/artifacts/performance-reporter-result.json",
        "--run-id",
        run_id,
        "--attempt",
        str(attempt),
        "--phase",
        phase,
        "--catalog-sha256",
        catalog_sha256,
        "--candidate-sha",
        candidate_sha,
        "--candidate-attestation",
        candidate_attestation_path,
    ]


def _inspect_peer_address(runtime: str, server: str, network: str) -> tuple[str, str]:
    completed = browser_isolation._run(
        [
            runtime,
            "inspect",
            server,
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ],
        timeout=20,
    )
    try:
        networks = json.loads(completed.stdout)
        peer = networks[network]
        ipv4 = peer["IPAddress"]
        ipv6 = peer["GlobalIPv6Address"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PerformanceIsolationBlocked("controlled peer address is invalid") from exc
    if not ipv4 or not ipv6:
        raise PerformanceIsolationBlocked("controlled peer lacks IPv4 or IPv6")
    return ipv4, ipv6


def _wait_peer(runtime: str, server: str) -> None:
    detail = ""
    for _ in range(50):
        result = browser_isolation._run(
            [runtime, "logs", server], check=False, timeout=10
        )
        detail = result.stdout + result.stderr
        if result.returncode == 0 and "PEER_READY\n" in detail:
            return
        time.sleep(0.1)
    raise PerformanceIsolationBlocked(
        "controlled peer did not become ready: " + detail.strip()
    )


def _validate_inner_outputs(
    *,
    run_dir: Path,
    contract: dict[str, Any],
    candidate_sha: str,
    expected_context: dict[str, Any],
) -> dict[str, str]:
    paths = {
        "inner": run_dir / "inner-performance-report.json",
        "benchmark": run_dir / "raw-benchmark-report.json",
        "junit": run_dir / "performance.xml",
        "reporter": run_dir / "performance-reporter-result.json",
    }
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise PerformanceIsolationFailed("isolated performance outputs are incomplete")
    inner = load_json(paths["inner"])
    benchmark = load_json(paths["benchmark"])
    reporter = load_json(paths["reporter"])
    validate_raw_report(
        benchmark, contract=contract, expected_candidate_sha=candidate_sha
    )
    phase = expected_context["phase"]
    required_nodes = node_ids(phase)
    if set(inner) != {
        "schema_version",
        "report_kind",
        "status",
        "context",
        "probes",
        "benchmark_report_sha256",
        "junit_sha256",
        "pytest",
        "reasons",
        "netns",
    }:
        raise PerformanceIsolationFailed("inner performance report schema is invalid")
    pytest_result = inner.get("pytest")
    if (
        inner.get("schema_version") != 1
        or inner.get("report_kind") != f"utiki-{phase.lower()}-performance-inner"
        or inner.get("status") != "PENDING_OUTER_AUDIT"
        or inner.get("context") != {
            name: expected_context[name]
            for name in (
                "run_id",
                "attempt",
                "phase",
                "job_id",
                "target",
                "candidate_sha",
            )
        }
        or inner.get("reasons") != []
        or inner.get("benchmark_report_sha256") != sha256_file(paths["benchmark"])
        or inner.get("junit_sha256") != sha256_file(paths["junit"])
        or not isinstance(inner.get("netns"), str)
        or not inner["netns"]
        or not isinstance(pytest_result, dict)
        or set(pytest_result) != {"exit_code", "command"}
        or pytest_result.get("exit_code") != 0
        or not isinstance(pytest_result.get("command"), list)
        or "--strict-config" not in pytest_result["command"]
        or "--strict-markers" not in pytest_result["command"]
        or any(node_id not in pytest_result["command"] for node_id in required_nodes)
    ):
        raise PerformanceIsolationFailed("inner performance evidence is invalid")
    if set(reporter) != {
        "schema_version",
        "status",
        "initial_exit_code",
        "final_exit_code",
        "collected_count",
        "selected_count",
        "required_count",
        "missing_node_ids",
        "invalid_node_ids",
    } or (
        reporter.get("schema_version") != 1
        or reporter.get("status") != "PASS"
        or reporter.get("initial_exit_code") != 0
        or reporter.get("final_exit_code") != 0
        or reporter.get("required_count") != len(required_nodes)
        or reporter.get("selected_count") != len(required_nodes)
        or reporter.get("missing_node_ids") != []
        or reporter.get("invalid_node_ids") != []
    ):
        raise PerformanceIsolationFailed("managed performance reporter is invalid")
    required_probes = set(contract["canary"]["required_inner_probes"])
    probes = inner.get("probes")
    if not isinstance(probes, dict):
        raise PerformanceIsolationFailed("inner performance probes are absent")
    passed = {
        name
        for name, value in probes.items()
        if isinstance(value, dict) and value.get("status") == "PASS"
    }
    if not required_probes.issubset(passed):
        raise PerformanceIsolationFailed("inner performance probes are incomplete")
    return {name: sha256_file(path) for name, path in paths.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-build-evidence", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--runtime", default="docker")
    parser.add_argument("--plan-only", action="store_true")
    arguments = parser.parse_args(argv)

    repository = REPOSITORY_ROOT
    run_dir = arguments.run_dir.resolve()
    report_path = arguments.report.resolve()
    network = _safe_name(arguments.run_id, "network")
    server = _safe_name(arguments.run_id, "peer")
    isolated = _safe_name(arguments.run_id, "isolated")
    owner = secrets.token_hex(16)
    cleanup_needed = False
    created_containers: list[str] = []
    workspace_context = None
    exit_code = 2
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": "utiki-browser-isolation-launcher",
        "status": "BLOCKED",
        "run_id": arguments.run_id,
        "attempt": arguments.attempt,
        "phase": arguments.phase,
        "job_id": JOB_ID,
        "target": TARGET,
        "candidate_sha": arguments.candidate_sha,
        "catalog_sha256": arguments.catalog_sha256,
        "execution_manifest_sha256": None,
        "context": {
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "phase": arguments.phase,
            "job_id": JOB_ID,
            "target": TARGET,
            "candidate_sha": arguments.candidate_sha,
            "catalog_sha256": arguments.catalog_sha256,
        },
        "execution_results": [],
        "contract": {},
        "execution_manifest": {},
        "image": {},
        "image_build_evidence": {},
        "runtime_evidence": {},
        "peer_audit": {},
        "inner_report_sha256": None,
        "benchmark_report_sha256": None,
        "junit_sha256": None,
        "reporter_result_sha256": None,
        "started_at_unix": time.time(),
        "finished_at_unix": None,
        "reasons": [],
    }
    cleanup_errors: list[str] = []
    try:
        if not NAME_PATTERN.fullmatch(arguments.run_id):
            raise PerformanceIsolationBlocked("run-id has invalid characters")
        if arguments.attempt < 1 or arguments.phase not in {"P1", "P2"}:
            raise PerformanceIsolationBlocked("performance run context is invalid")
        if SHA_PATTERN.fullmatch(arguments.candidate_sha) is None:
            raise PerformanceIsolationBlocked("candidate SHA is invalid")
        if SHA256_PATTERN.fullmatch(arguments.catalog_sha256) is None:
            raise PerformanceIsolationBlocked("catalog digest is invalid")
        if arguments.run_dir.is_symlink() or arguments.report.is_symlink():
            raise PerformanceIsolationBlocked("performance output path is a symlink")
        run_dir.mkdir(parents=True, exist_ok=True)
        if report_path.parent != run_dir:
            raise PerformanceIsolationBlocked("performance report must be inside run-dir")
        if report_path.exists():
            raise PerformanceIsolationBlocked("performance isolation report already exists")
        raw_outputs = {
            run_dir / "raw-benchmark-report.json",
            run_dir / "inner-performance-report.json",
            run_dir / "performance.xml",
            run_dir / "performance-reporter-result.json",
            run_dir / "candidate-workspace-attestation.json",
        }
        if any(path.exists() for path in raw_outputs):
            raise PerformanceIsolationBlocked("stale performance output is present")
        if repository in run_dir.parents:
            relative = run_dir.relative_to(repository)
            if not relative.parts or relative.parts[0] != "artifacts":
                raise PerformanceIsolationBlocked(
                    "repository-local performance output must be below artifacts/"
                )
        contract_path = arguments.contract.resolve(strict=True)
        manifest_path = arguments.execution_manifest.resolve(strict=True)
        if repository not in contract_path.parents or repository not in manifest_path.parents:
            raise PerformanceIsolationBlocked("contract and manifest must be repository files")
        contract = load_contract(contract_path)
        if performance_phase(contract) != arguments.phase:
            raise PerformanceIsolationBlocked("performance contract phase is invalid")
        manifest = load_json(manifest_path)
        if (
            manifest.get("phase") != arguments.phase
            or manifest.get("catalog_sha256") != arguments.catalog_sha256
        ):
            raise PerformanceIsolationBlocked("execution manifest context is invalid")
        report["contract"] = {
            "path": contract_path.relative_to(repository).as_posix(),
            "sha256": sha256_file(contract_path),
            "contract_id": contract["contract_id"],
        }
        report["execution_manifest"] = {
            "path": manifest_path.relative_to(repository).as_posix(),
            "sha256": sha256_file(manifest_path),
        }
        report["execution_manifest_sha256"] = report["execution_manifest"]["sha256"]
        _require_clean_candidate(repository, arguments.candidate_sha)
        workspace_context = materialize_candidate(repository, arguments.candidate_sha)
        candidate_workspace, workspace_evidence = workspace_context.__enter__()
        attestation_path = run_dir / "candidate-workspace-attestation.json"
        write_json(
            attestation_path,
            candidate_attestation(candidate_workspace, workspace_evidence),
        )
        attestation_sha256 = sha256_file(attestation_path)
        workspace_evidence["attestation_sha256"] = attestation_sha256
        report["runtime_evidence"]["candidate_workspace"] = workspace_evidence
        report["image"] = browser_isolation._validate_image(
            arguments.runtime, arguments.image
        )
        evidence_path = arguments.image_build_evidence.resolve(strict=True)
        report["image_build_evidence"] = (
            browser_isolation._validate_image_build_evidence(
                evidence_path, repository, report["image"]
            )
        )
        if arguments.plan_only:
            report["reasons"].append("plan-only mode cannot produce performance evidence")
            return exit_code

        browser_isolation._require_unused_cleanup_names(
            arguments.runtime, [isolated, server], network
        )
        subnet_digest = hashlib.sha256(network.encode("ascii")).hexdigest()
        ipv6_subnet = f"fd42:{subnet_digest[:4]}:{subnet_digest[4:8]}::/64"
        cleanup_needed = True
        browser_isolation._run(
            [
                arguments.runtime,
                "network",
                "create",
                "--label",
                f"{browser_isolation.OWNER_LABEL}={owner}",
                "--internal",
                "--ipv6",
                "--subnet",
                ipv6_subnet,
                network,
            ],
            timeout=20,
        )
        report["runtime_evidence"]["controlled_peer_network"] = (
            browser_isolation._inspect_internal_network(arguments.runtime, network)
        )
        created_containers.append(server)
        browser_isolation._run(
            browser_isolation._peer_server_command(
                arguments.runtime,
                arguments.image,
                network,
                server,
                candidate_workspace,
                owner,
            ),
            timeout=30,
        )
        peer_ip, peer_ipv6 = _inspect_peer_address(
            arguments.runtime, server, network
        )
        _wait_peer(arguments.runtime, server)
        positive = browser_isolation._positive_control_command(
            arguments.runtime, arguments.image, network, peer_ip, peer_ipv6
        )
        positive_result = browser_isolation._run(
            positive, check=False, timeout=10
        )
        if positive_result.returncode != 0:
            raise PerformanceIsolationBlocked("controlled peer positive control failed")

        command = _isolated_command(
            runtime=arguments.runtime,
            image=arguments.image,
            name=isolated,
            workspace=candidate_workspace,
            run_dir=run_dir,
            contract_relative=contract_path.relative_to(repository),
            execution_manifest_relative=manifest_path.relative_to(repository),
            peer_ip=peer_ip,
            peer_ipv6=peer_ipv6,
            run_id=arguments.run_id,
            attempt=arguments.attempt,
            phase=arguments.phase,
            catalog_sha256=arguments.catalog_sha256,
            candidate_sha=arguments.candidate_sha,
            candidate_attestation_path="/artifacts/candidate-workspace-attestation.json",
            owner=owner,
        )
        created_containers.append(isolated)
        completed = browser_isolation._run(command, check=False, timeout=1200)
        report["runtime_evidence"]["isolated_container"] = (
            browser_isolation._inspect_isolated_container(arguments.runtime, isolated)
        )
        peer_logs_result = browser_isolation._run(
            [arguments.runtime, "logs", server], timeout=20
        )
        peer_logs = peer_logs_result.stdout + peer_logs_result.stderr
        report["peer_audit"] = browser_isolation._audit_peer_logs(
            peer_logs,
            contract["canary"]["forbidden_peer_paths"],
            contract["canary"]["forbidden_peer_events"],
        )
        if completed.returncode != 0:
            raise PerformanceIsolationFailed(
                f"isolated performance command returned {completed.returncode}"
            )
        if sha256_file(attestation_path) != attestation_sha256:
            raise PerformanceIsolationFailed(
                "candidate workspace attestation changed inside isolation"
            )
        output_digests = _validate_inner_outputs(
            run_dir=run_dir,
            contract=contract,
            candidate_sha=arguments.candidate_sha,
            expected_context=report["context"],
        )
        report["inner_report_sha256"] = output_digests["inner"]
        report["benchmark_report_sha256"] = output_digests["benchmark"]
        report["junit_sha256"] = output_digests["junit"]
        report["reporter_result_sha256"] = output_digests["reporter"]
        report["execution_results"] = execution_results(arguments.phase)
        report["status"] = "PASS"
        exit_code = 0
    except (
        PerformanceIsolationBlocked,
        browser_isolation.IsolationBlocked,
        PerformanceReportError,
        WorkspaceError,
        OSError,
        ValueError,
    ) as exc:
        report["status"] = "BLOCKED"
        report["reasons"].append(str(exc))
    except (
        PerformanceIsolationFailed,
        browser_isolation.IsolationFailed,
    ) as exc:
        report["status"] = "FAIL"
        report["reasons"].append(str(exc))
        exit_code = 1
    finally:
        if cleanup_needed:
            cleanup_errors = browser_isolation._cleanup(
                arguments.runtime, list(reversed(created_containers)), network, owner
            )
        if workspace_context is not None:
            workspace_context.__exit__(None, None, None)
        if cleanup_errors:
            report["status"] = "FAIL"
            report["reasons"].extend(cleanup_errors)
            exit_code = 1
        report["finished_at_unix"] = time.time()
        try:
            write_json(report_path, report)
        except OSError as exc:
            sys.stderr.write(f"cannot write performance isolation report: {exc}\n")
            return 2
        print(json.dumps({"status": report["status"], "report": str(report_path)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
