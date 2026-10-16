#!/usr/bin/env python3
"""Prove that a browser-test process tree has no non-loopback egress.

This program is the in-container half of ``run_utiki_browser_isolation.py``.
It is intentionally Linux-specific.  It does not install CDP interception and
does not contact a production origin.  A PASS is possible only when the
process has a loopback-only network namespace and all controlled probes are
blocked.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import tempfile
from typing import Any, Callable


SCHEMA_VERSION = 1
ACTIVE_ENV = "TICKETS_HUNTER_HARD_EGRESS_ACTIVE"
PEER_IPV4_ENV = "TICKETS_HUNTER_EGRESS_CANARY_IPV4"
PEER_IPV6_ENV = "TICKETS_HUNTER_EGRESS_CANARY_IPV6"
PEER_PORT_ENV = "TICKETS_HUNTER_EGRESS_CANARY_PORT"
RUN_ID_ENV = "TICKETS_HUNTER_ISOLATION_RUN_ID"
CANDIDATE_SHA_ENV = "TICKETS_HUNTER_CANDIDATE_SHA"
NEGATIVE_CHILD_ENV = "TICKETS_HUNTER_NEGATIVE_CHILD"


class CanaryFailure(RuntimeError):
    """A hard-isolation precondition or probe failed."""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _netns_identity() -> str:
    return os.readlink("/proc/self/ns/net")


def _interfaces() -> list[str]:
    root = Path("/sys/class/net")
    if not root.is_dir():
        raise CanaryFailure("Linux interface inventory is unavailable")
    return sorted(entry.name for entry in root.iterdir())


def _ipv4_default_routes() -> list[str]:
    path = Path("/proc/net/route")
    if not path.is_file():
        raise CanaryFailure("IPv4 route inventory is unavailable")
    routes: list[str] = []
    for line in path.read_text(encoding="ascii").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000" and fields[2] != "00000000":
            routes.append(line)
    return routes


def _ipv6_default_routes() -> list[str]:
    path = Path("/proc/net/ipv6_route")
    if not path.is_file():
        raise CanaryFailure("IPv6 route inventory is unavailable")
    routes: list[str] = []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[0] == "0" * 32 and fields[1] == "00000000":
            if fields[-1] != "lo":
                routes.append(line)
    return routes


def _loopback_positive_control() -> dict[str, Any]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.settimeout(2.0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    received: list[bytes] = []

    def accept_once() -> None:
        connection, _ = listener.accept()
        with connection:
            received.append(connection.recv(16))

    worker = threading.Thread(target=accept_once, daemon=True)
    worker.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as client:
            client.sendall(b"loopback-control")
        worker.join(timeout=2.0)
    finally:
        listener.close()
    if received != [b"loopback-control"]:
        raise CanaryFailure("loopback positive control did not complete")
    return {"status": "PASS", "port": port}


def _expect_blocked(name: str, operation: Callable[[], None]) -> dict[str, Any]:
    try:
        operation()
    except (OSError, socket.gaierror) as exc:
        return {
            "status": "PASS",
            "result": "blocked",
            "error_type": type(exc).__name__,
            "errno": getattr(exc, "errno", None),
        }
    raise CanaryFailure(f"{name} unexpectedly completed")


def _http_probe(host: str, port: int, path: str) -> None:
    with socket.create_connection((host, port), timeout=1.0) as connection:
        request = (
            f"GET {path} HTTP/1.1\r\nHost: canary.invalid\r\n"
            "Connection: close\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        data = connection.recv(1)
        if data:
            return


def _ipv6_probe(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as connection:
        connection.settimeout(1.0)
        connection.connect((host, port, 0, 0))
        connection.sendall(
            b"GET /driver-ipv6 HTTP/1.1\r\nHost: canary.invalid\r\n"
            b"Connection: close\r\n\r\n"
        )
        connection.recv(1)


def _dns_query_bytes() -> bytes:
    return (
        b"\x54\x48\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        b"\x0cutiki-canary\x08internal\x00\x00\x01\x00\x01"
    )


def _dns_probe(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1.0)
        connection.connect((host, port))
        connection.send(_dns_query_bytes())
        if connection.recv(64) != b"DNS-QUERY-ACK":
            raise CanaryFailure("controlled DNS peer returned an invalid response")


def _udp_probe(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1.0)
        connection.connect((host, port))
        connection.send(b"UTIKI-QUIC-CANARY")
        if connection.recv(64) != b"QUIC-ACK":
            raise CanaryFailure("controlled UDP peer returned an invalid response")


def _run_child_probe(expected_netns: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child-probe",
            "--expected-netns",
            expected_netns,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise CanaryFailure(
            "child-process isolation probe failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CanaryFailure("child-process probe returned invalid JSON") from exc
    if payload.get("status") != "PASS":
        raise CanaryFailure("child-process probe did not report PASS")
    return payload


async def _browser_negative_probe(host: str, port: int, mode: str) -> str:
    try:
        import zendriver as uc
    except ImportError as exc:
        raise CanaryFailure("browser driver is unavailable for negative probe") from exc
    browser = None
    profile = tempfile.TemporaryDirectory(prefix="tickets-hunter-negative-egress-")
    target = f"http://{host}:{port}"
    scripts = {
        "interception-disabled": """bounded(fetch(target + '/driver-http')
            .then(() => 'unexpected-success').catch(() => 'blocked-error'))""",
        "subresource": """bounded(new Promise((resolve) => {
            const image = new Image();
            image.onload = () => resolve('unexpected-success');
            image.onerror = () => resolve('blocked-error');
            image.src = target + '/browser-subresource';
        }))""",
        "redirect": """bounded(fetch(target + '/browser-redirect', {redirect: 'follow'})
            .then(() => 'unexpected-success').catch(() => 'blocked-error'))""",
        "websocket": """bounded(new Promise((resolve) => {
            const socket = new WebSocket(
                target.replace('http://', 'ws://') + '/browser-websocket'
            );
            socket.onopen = () => { socket.close(); resolve('unexpected-success'); };
            socket.onerror = () => resolve('blocked-error');
            socket.onclose = () => resolve('blocked-close');
        }))""",
        "worker": """bounded(new Promise((resolve) => {
            const source = `onmessage = async (event) => {
                try {
                    await fetch(event.data + '/browser-worker');
                    postMessage('unexpected-success');
                } catch (error) { postMessage('blocked-error'); }
            }`;
            const worker = new Worker(URL.createObjectURL(
                new Blob([source], {type: 'text/javascript'})
            ));
            worker.onmessage = (event) => {
                worker.terminate(); resolve(event.data);
            };
            worker.onerror = () => {
                worker.terminate(); resolve('blocked-worker-error');
            };
            worker.postMessage(target);
        }))""",
    }
    expression = scripts.get(mode)
    if expression is None:
        raise CanaryFailure(f"unknown browser negative probe mode: {mode}")
    try:
        browser = await uc.start(
            user_data_dir=profile.name,
            headless=True,
            browser_args=[
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        )
        tab = await browser.get("data:text/html,<title>uTiki negative egress</title>")
        script = f"""(async () => {{
            const target = {json.dumps(target)};
            const bounded = (promise) => Promise.race([
                promise,
                new Promise((resolve) => setTimeout(
                    () => resolve('blocked-timeout'), 1500
                )),
            ]);
            return await {expression};
        }})()"""
        result = await tab.evaluate(script, await_promise=True, return_by_value=True)
        if not isinstance(result, str):
            raise CanaryFailure("browser negative probe returned an invalid result")
        return result
    finally:
        try:
            if browser is not None:
                await browser.stop()
        finally:
            profile.cleanup()


def _negative_probe(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryFailure(f"negative probe input is invalid: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "mode"}
        or payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("mode"), str)
    ):
        raise CanaryFailure("negative probe input schema is invalid")
    mode = payload["mode"]
    if mode == "missing-isolation-proof":
        print("attestation is absent")
        return 1
    if os.environ.get(ACTIVE_ENV) != "1":
        raise CanaryFailure("hard-egress launcher attestation is absent")
    if sys.platform != "linux":
        raise CanaryFailure("hard-egress negative probes require Linux")
    host = os.environ.get(PEER_IPV4_ENV, "")
    host_ipv6 = os.environ.get(PEER_IPV6_ENV, "")
    try:
        port = int(os.environ.get(PEER_PORT_ENV, ""))
    except ValueError as exc:
        raise CanaryFailure("controlled peer port is invalid") from exc
    if not host or not host_ipv6 or not (1 <= port <= 65535):
        raise CanaryFailure("controlled peer endpoint is absent")
    if mode == "child-process" and os.environ.get(NEGATIVE_CHILD_ENV) != "1":
        environment = os.environ.copy()
        environment[NEGATIVE_CHILD_ENV] = "1"
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--negative-probe", str(path)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode
    if mode in {
        "interception-disabled",
        "subresource",
        "redirect",
        "websocket",
        "worker",
    }:
        result = asyncio.run(_browser_negative_probe(host, port, mode))
        if result.startswith("blocked"):
            print(f"EGRESS_BLOCKED mode={mode} browser_result={result}")
            return 1
        print(f"EGRESS_UNEXPECTED mode={mode} browser_result={result}")
        return 2
    operations: dict[str, Callable[[], None]] = {
        "child-process": lambda: _http_probe(host, port, "/child-http"),
        "dns": lambda: _dns_probe(host, port),
        "direct-ipv4": lambda: _http_probe(host, port, "/driver-http"),
        "direct-ipv6": lambda: _ipv6_probe(host_ipv6, port),
        "udp-quic": lambda: _udp_probe(host, port),
    }
    operation = operations.get(mode)
    if operation is None:
        raise CanaryFailure(f"unknown negative probe mode: {mode}")
    try:
        operation()
    except (OSError, socket.gaierror) as exc:
        print(f"EGRESS_BLOCKED mode={mode} error={type(exc).__name__}")
        return 1
    print(f"EGRESS_UNEXPECTED mode={mode}")
    return 2


def _base_probes() -> tuple[dict[str, Any], str, int]:
    if os.environ.get(ACTIVE_ENV) != "1":
        raise CanaryFailure("hard-egress launcher attestation is absent")
    if sys.platform != "linux":
        raise CanaryFailure("hard-egress canary requires a Linux network namespace")
    host = os.environ.get(PEER_IPV4_ENV, "")
    host_ipv6 = os.environ.get(PEER_IPV6_ENV, "")
    try:
        port = int(os.environ.get(PEER_PORT_ENV, ""))
    except ValueError as exc:
        raise CanaryFailure("controlled peer port is invalid") from exc
    if not host or not host_ipv6 or not (1 <= port <= 65535):
        raise CanaryFailure("controlled peer endpoint is absent")

    interfaces = _interfaces()
    ipv4_routes = _ipv4_default_routes()
    ipv6_routes = _ipv6_default_routes()
    if interfaces != ["lo"]:
        raise CanaryFailure(f"non-loopback interfaces present: {interfaces}")
    if ipv4_routes:
        raise CanaryFailure("an IPv4 default route is present")
    if ipv6_routes:
        raise CanaryFailure("an IPv6 default route is present")

    netns = _netns_identity()
    probes: dict[str, Any] = {
        "loopback-positive-control": _loopback_positive_control(),
        "only-loopback-interface": {"status": "PASS", "interfaces": interfaces},
        "no-default-ipv4-route": {"status": "PASS", "routes": ipv4_routes},
        "no-default-ipv6-route": {"status": "PASS", "routes": ipv6_routes},
        "driver-direct-ipv4": _expect_blocked(
            "driver-direct-ipv4",
            lambda: _http_probe(host, port, "/driver-http"),
        ),
        "driver-direct-ipv6": _expect_blocked(
            "driver-direct-ipv6",
            lambda: _ipv6_probe(host_ipv6, port),
        ),
        "driver-dns": _expect_blocked(
            "driver-dns",
            lambda: _dns_probe(host, port),
        ),
        "driver-udp": _expect_blocked(
            "driver-udp",
            lambda: _udp_probe(host, port),
        ),
    }
    probes["child-process-netns"] = _run_child_probe(netns)
    return probes, netns, port


def _run_pytest(
    junit: Path,
    reporter_context: Path | None,
    reporter_result: Path | None,
    repository: Path,
) -> tuple[int, list[str]]:
    if reporter_context is None or reporter_result is None:
        raise CanaryFailure("managed reporter paths are absent")
    try:
        context = json.loads(reporter_context.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryFailure("managed reporter context is unavailable") from exc
    mappings = context.get("mappings") if isinstance(context, dict) else None
    if not isinstance(mappings, list) or not mappings:
        raise CanaryFailure("managed reporter context has no required mappings")
    node_ids = [
        item.get("node_id") for item in mappings if isinstance(item, dict)
    ]
    if len(node_ids) != len(mappings) or any(
        not isinstance(item, str) or not item for item in node_ids
    ):
        raise CanaryFailure("managed reporter context node IDs are invalid")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--rootdir",
        str(repository),
        "-o",
        "addopts=",
        "--strict-config",
        "--strict-markers",
        "-m",
        "browser and not live",
        "-p",
        "tools.utiki_pytest_reporter",
        "--utiki-reporter-context",
        str(reporter_context),
        "--utiki-managed-junit",
        str(junit),
        "--utiki-reporter-result",
        str(reporter_result),
        *node_ids,
    ]
    completed = subprocess.run(command, check=False)
    return completed.returncode, command


def _run_negative_evidence(
    *,
    repository: Path,
    catalog: Path,
    manifest: Path,
    registry: Path,
    output_root: Path,
    job_run_dir: Path,
    isolation_proof: Path,
    run_id: str,
    attempt: int,
    phase: str,
    catalog_sha256: str,
    candidate_sha: str,
    netns: str,
) -> list[dict[str, str]]:
    _write_json(
        isolation_proof,
        {
            "schema_version": 1,
            "status": "PASS",
            "network_mode": "none",
            "candidate_sha": candidate_sha,
            "netns": netns,
        },
    )
    builder = [
        sys.executable,
        str(repository / "tools" / "build_utiki_negative_inputs.py"),
        "--repository-root",
        str(repository),
        "--probe-registry",
        str(registry),
        "--output-root",
        str(output_root),
        "--candidate-sha",
        candidate_sha,
        "--environment",
        "isolated",
    ]
    runner = [
        sys.executable,
        str(repository / "tools" / "run_utiki_negative_job.py"),
        "--repository-root",
        str(repository),
        "--required-catalog",
        str(catalog),
        "--approved-catalog-sha256",
        catalog_sha256,
        "--execution-manifest",
        str(manifest),
        "--probe-registry",
        str(registry),
        "--probe-input-root",
        str(output_root),
        "--run-dir",
        str(job_run_dir),
        "--run-id",
        run_id,
        "--attempt",
        str(attempt),
        "--job-id",
        "browser-contract-isolated",
        "--phase",
        phase,
        "--target",
        "isolated-linux-x64",
        "--candidate-sha",
        candidate_sha,
        "--catalog-sha256",
        catalog_sha256,
        "--isolation-proof",
        str(isolation_proof),
    ]
    for label, command in (("builder", builder), ("runner", runner)):
        completed = subprocess.run(
            command,
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise CanaryFailure(f"isolated negative {label} failed: {detail}")

    try:
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryFailure("negative evidence manifest is unreadable") from exc
    evidence: list[dict[str, str]] = []
    for case in manifest_payload.get("cases", []):
        for execution in case.get("executions", []):
            if (
                execution.get("status") != "MAPPED"
                or execution.get("job_id") != "browser-contract-isolated"
                or execution.get("target") != "isolated-linux-x64"
            ):
                continue
            for item in execution.get("evidence", []):
                if item.get("kind") != "checker-exit-nonzero":
                    continue
                relative = item.get("path")
                if not isinstance(relative, str):
                    raise CanaryFailure("negative evidence path is invalid")
                path = job_run_dir / relative
                if path.is_symlink() or not path.is_file():
                    raise CanaryFailure("isolated negative evidence is missing")
                evidence.append(
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
    if len(evidence) != 12 or len({item["path"] for item in evidence}) != 12:
        raise CanaryFailure("isolated negative evidence set is incomplete")
    return sorted(evidence, key=lambda item: item["path"])


def _child_main(expected_netns: str) -> int:
    try:
        actual = _netns_identity()
        interfaces = _interfaces()
        if actual != expected_netns or interfaces != ["lo"]:
            raise CanaryFailure("child process escaped the isolated network namespace")
        peer = os.environ.get(PEER_IPV4_ENV, "")
        port = int(os.environ.get(PEER_PORT_ENV, ""))
        peer_probes = {
            "direct-ipv4": _expect_blocked(
                "child-direct-ipv4",
                lambda: _http_probe(peer, port, "/child-http"),
            ),
            "dns": _expect_blocked(
                "child-dns",
                lambda: _dns_probe(peer, port),
            ),
            "udp": _expect_blocked(
                "child-udp",
                lambda: _udp_probe(peer, port),
            ),
        }
    except (CanaryFailure, OSError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "netns": actual,
                "interfaces": interfaces,
                "pid": os.getpid(),
                "peer_probes": peer_probes,
            },
            sort_keys=True,
        )
    )
    return 0


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--reporter-context", type=Path)
    parser.add_argument("--reporter-result", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--required-catalog", type=Path)
    parser.add_argument("--execution-manifest", type=Path)
    parser.add_argument("--probe-registry", type=Path)
    parser.add_argument("--negative-output-root", type=Path)
    parser.add_argument("--job-run-dir", type=Path)
    parser.add_argument("--isolation-proof", type=Path)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--phase")
    parser.add_argument("--catalog-sha256")
    parser.add_argument("--child-probe", action="store_true")
    parser.add_argument("--expected-netns", default="")
    parser.add_argument("--negative-probe", type=Path)
    args = parser.parse_args(argv)
    if args.negative_probe is not None:
        try:
            return _negative_probe(args.negative_probe.resolve(strict=True))
        except (CanaryFailure, OSError, subprocess.SubprocessError) as exc:
            sys.stderr.write(f"utiki_egress_canary: {exc}\n")
            return 2
    if args.child_probe:
        return _child_main(args.expected_netns)
    regular_required = {
        "report": args.report,
        "junit": args.junit,
        "reporter-context": args.reporter_context,
        "reporter-result": args.reporter_result,
        "repository-root": args.repository_root,
        "required-catalog": args.required_catalog,
        "execution-manifest": args.execution_manifest,
        "probe-registry": args.probe_registry,
        "negative-output-root": args.negative_output_root,
        "job-run-dir": args.job_run_dir,
        "isolation-proof": args.isolation_proof,
        "attempt": args.attempt,
        "phase": args.phase,
        "catalog-sha256": args.catalog_sha256,
    }
    missing = sorted(name for name, value in regular_required.items() if value is None)
    if missing:
        parser.error(f"regular canary mode lacks required arguments: {missing}")

    started = time.time()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_kind": "utiki-browser-isolation",
        "status": "BLOCKED",
        "run_id": os.environ.get(RUN_ID_ENV, ""),
        "candidate_sha": os.environ.get(CANDIDATE_SHA_ENV, ""),
        "isolation": {
            "mode": "linux-container-network-none",
            "interception_state": "deliberately-not-installed",
            "pid": os.getpid(),
        },
        "started_at_unix": started,
        "finished_at_unix": None,
        "probes": {},
        "pytest": {"exit_code": None, "command": []},
        "negative_evidence": [],
        "reasons": [],
    }
    exit_code = 1
    try:
        probes, netns, _ = _base_probes()
        report["probes"] = probes
        report["isolation"]["netns"] = netns
        report["negative_evidence"] = _run_negative_evidence(
            repository=args.repository_root.resolve(strict=True),
            catalog=args.required_catalog.resolve(strict=True),
            manifest=args.execution_manifest.resolve(strict=True),
            registry=args.probe_registry.resolve(strict=True),
            output_root=args.negative_output_root.resolve(),
            job_run_dir=args.job_run_dir.resolve(strict=True),
            isolation_proof=args.isolation_proof.resolve(),
            run_id=report["run_id"],
            attempt=args.attempt,
            phase=args.phase,
            catalog_sha256=args.catalog_sha256,
            candidate_sha=report["candidate_sha"],
            netns=netns,
        )
        browser_report_path = args.report.parent / "browser-network-probes.json"
        os.environ["TICKETS_HUNTER_BROWSER_PROBE_REPORT"] = str(browser_report_path)
        pytest_exit, command = _run_pytest(
            args.junit,
            args.reporter_context,
            args.reporter_result,
            args.repository_root.resolve(strict=True),
        )
        report["pytest"] = {"exit_code": pytest_exit, "command": command}
        if pytest_exit != 0:
            report["status"] = "FAIL"
            report["reasons"].append("browser pytest command failed")
        else:
            try:
                browser_report = json.loads(
                    browser_report_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CanaryFailure(
                    f"browser network probe report is unavailable: {exc}"
                ) from exc
            browser_probes = browser_report.get("probes")
            if not isinstance(browser_probes, dict):
                raise CanaryFailure("browser network probe report is invalid")
            for name, value in browser_probes.items():
                if not isinstance(value, dict) or value.get("status") != "PASS":
                    raise CanaryFailure(f"browser network probe did not pass: {name}")
                report["probes"][name] = value
            report["status"] = "PENDING_OUTER_AUDIT"
            exit_code = 0
    except (CanaryFailure, OSError, subprocess.SubprocessError) as exc:
        report["status"] = "BLOCKED"
        report["reasons"].append(str(exc))
    finally:
        report["finished_at_unix"] = time.time()
        _write_json(args.report, report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_main())
