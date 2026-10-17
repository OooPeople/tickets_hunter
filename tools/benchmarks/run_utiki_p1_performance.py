#!/usr/bin/env python3
"""Measure the P1 uTiki source and production-origin browser baseline.

This program is the in-container half of the performance isolation launcher.
It refuses to run without the Linux hard-egress attestation.  Its five managed
pytest nodes only inspect the report produced earlier in this same process.
The outer launcher must still audit the controlled peer before the evidence is
eligible for a PASS.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import enum
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


REPOSITORY = Path(__file__).resolve().parents[2]
SRC = REPOSITORY / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from tools import utiki_egress_canary as egress  # noqa: E402
from tools.benchmarks.utiki_p1_performance import (  # noqa: E402
    JOB_ID,
    NODE_IDS,
    PHASE,
    REPORT_KIND,
    SCHEMA_VERSION,
    TARGET,
    PerformanceReportError,
    execution_results,
    load_contract,
    load_json,
    node_ids,
    performance_phase,
    sha256_file,
    sha256_json,
    summarize_samples,
    validate_raw_report,
    write_json,
)


PRODUCTION_FIXTURE_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=PRODUCT_FIXTURE"
)
FIXTURE_PATH = REPOSITORY / "tests" / "fixtures" / "ctbc" / "UTK0201_date.html"
PRODUCTION_SCRIPT_PATH = REPOSITORY / "src" / "platforms" / "utiki" / "dom.py"
SOURCE_BENCHMARK_PATH = REPOSITORY / "tools" / "benchmarks" / "bench_utiki.py"
HOT_PATH_SCRIPT_PATH = REPOSITORY / "tools" / "benchmarks" / "utiki_p2_hot_path.py"


class PerformanceRunError(RuntimeError):
    """The isolated performance run failed its fixed contract."""


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _verify_candidate_attestation(
    path: Path, candidate_sha: str
) -> tuple[dict[str, Any], dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise PerformanceRunError("candidate workspace attestation is absent or unsafe")
    payload = load_json(path)
    if set(payload) != {
        "schema_version",
        "attestation_kind",
        "candidate_sha",
        "archive_sha256",
        "tree_sha256",
        "tracked_file_count",
        "git_directory_present",
        "files",
    }:
        raise PerformanceRunError("candidate workspace attestation fields mismatch")
    if (
        payload.get("schema_version") != 1
        or payload.get("attestation_kind")
        != "utiki-sanitized-candidate-workspace"
        or payload.get("candidate_sha") != candidate_sha
        or payload.get("git_directory_present") is not False
        or (REPOSITORY / ".git").exists()
    ):
        raise PerformanceRunError("candidate workspace attestation context is invalid")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise PerformanceRunError("candidate workspace attestation has no files")
    normalized: list[dict[str, Any]] = []
    by_path: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size",
            "executable",
        }:
            raise PerformanceRunError("candidate workspace file entry is invalid")
        relative = item["path"]
        pure = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            pure is None
            or pure.is_absolute()
            or ".." in pure.parts
            or str(pure) != relative
            or relative in by_path
        ):
            raise PerformanceRunError("candidate workspace file path is unsafe")
        target = REPOSITORY.joinpath(*pure.parts)
        if target.is_symlink() or not target.is_file():
            raise PerformanceRunError("candidate workspace file is absent or unsafe")
        data = target.read_bytes()
        actual_digest = hashlib.sha256(data).hexdigest()
        actual = {
            "path": relative,
            "sha256": actual_digest,
            "size": len(data),
            "executable": bool(target.stat().st_mode & 0o111),
        }
        if actual != item:
            raise PerformanceRunError("candidate workspace file digest mismatch")
        normalized.append(actual)
        by_path[relative] = actual_digest
    normalized.sort(key=lambda item: item["path"])
    actual_paths = {
        item.relative_to(REPOSITORY).as_posix()
        for item in REPOSITORY.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    if actual_paths != set(by_path):
        raise PerformanceRunError("candidate workspace file set mismatch")
    canonical = json.dumps(
        normalized, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if (
        payload.get("tracked_file_count") != len(normalized)
        or payload.get("tree_sha256") != hashlib.sha256(canonical).hexdigest()
        or not isinstance(payload.get("archive_sha256"), str)
        or len(payload["archive_sha256"]) != 64
    ):
        raise PerformanceRunError("candidate workspace tree attestation mismatch")
    return payload, by_path


def _run_source_benchmark(
    output: Path,
    *,
    run_id: str,
    measurement: Mapping[str, Any],
    candidate_attestation_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SOURCE_BENCHMARK_PATH),
        "--output",
        str(output),
        "--run-id",
        f"{run_id}-source",
        "--mode",
        str(measurement["source_mode"]),
        "--seed",
        str(measurement["seed"]),
        "--samples",
        str(measurement["warm_samples"]),
        "--warmup",
        str(measurement["warmup_per_batch"]),
        "--batches",
        str(measurement["batches"]),
        "--cold-samples",
        str(measurement["cold_samples_per_case"]),
        "--lag-samples",
        str(measurement["event_loop_samples"]),
    ]
    environment = os.environ.copy()
    environment["TICKETS_HUNTER_CANDIDATE_ATTESTATION"] = str(
        candidate_attestation_path
    )
    completed = subprocess.run(
        command,
        cwd=REPOSITORY,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-1000:]
        raise PerformanceRunError(f"source benchmark failed: {detail}")
    return load_json(output)


def _browser_version() -> str:
    for command in (("chromium", "--version"), ("chromium-browser", "--version")):
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = completed.stdout.strip() or completed.stderr.strip()
        if completed.returncode == 0 and value:
            return value
    raise PerformanceRunError("browser version cannot be identified")


def _loaded_production_subjects() -> list[dict[str, str]]:
    paths: set[Path] = set()
    for module in tuple(sys.modules.values()):
        raw_path = getattr(module, "__file__", None)
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        if path.suffix == ".pyc":
            try:
                path = Path(importlib.util.source_from_cache(str(path))).resolve()
            except (ValueError, NotImplementedError):
                continue
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            path.relative_to(SRC)
        except ValueError:
            continue
        paths.add(path)
    return [
        {
            "path": path.relative_to(REPOSITORY).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(REPOSITORY).as_posix())
    ]


async def _measure_browser(measurement: Mapping[str, Any]) -> dict[str, Any]:
    import zendriver as uc
    from zendriver import cdp

    from platforms.utiki import dom
    from platforms.utiki.profiles import PageKind, classify_utiki_page

    browser = None
    tab = None
    profile = tempfile.TemporaryDirectory(prefix="tickets-hunter-p1-performance-")
    intercepted: list[str] = []
    rejected: list[str] = []
    handler_errors: list[str] = []
    fixture = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        "<head>", '<head><link rel="icon" href="data:,">', 1
    )
    fixture_body = base64.b64encode(fixture.encode("utf-8")).decode("ascii")
    try:
        try:
            browser = await uc.start(
                user_data_dir=profile.name,
                headless=True,
                sandbox=False,
                browser_args=[
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--no-default-browser-check",
                    "--no-first-run",
                ],
            )
        except Exception as exc:
            raise PerformanceRunError(
                f"isolated browser launch failed: {type(exc).__name__}"
            ) from exc
        tab = browser.main_tab

        async def handle_request(event: Any) -> None:
            try:
                request_url = event.request.url
                intercepted.append(request_url)
                if request_url == PRODUCTION_FIXTURE_URL:
                    await tab.send(
                        cdp.fetch.fulfill_request(
                            request_id=event.request_id,
                            response_code=200,
                            response_headers=[
                                cdp.fetch.HeaderEntry(
                                    name="Content-Type",
                                    value="text/html; charset=utf-8",
                                ),
                                cdp.fetch.HeaderEntry(
                                    name="Cache-Control", value="no-store"
                                ),
                            ],
                            body=fixture_body,
                        )
                    )
                else:
                    rejected.append(request_url)
                    await tab.send(
                        cdp.fetch.fail_request(
                            request_id=event.request_id,
                            error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                        )
                    )
            except Exception as exc:  # pragma: no cover - real browser boundary
                handler_errors.append(type(exc).__name__)

        tab.add_handler(cdp.fetch.RequestPaused, handle_request)
        await tab.send(
            cdp.fetch.enable(
                [
                    cdp.fetch.RequestPattern(
                        url_pattern="*", request_stage=cdp.fetch.RequestStage.REQUEST
                    )
                ]
            )
        )
        await asyncio.wait_for(tab.get(PRODUCTION_FIXTURE_URL), timeout=15.0)
        actual_url = await tab.evaluate("location.href")
        actual_origin = await tab.evaluate("location.origin")
        if (
            actual_url != PRODUCTION_FIXTURE_URL
            or actual_origin != measurement["production_origin"]
            or classify_utiki_page(actual_url) is not PageKind.DATE
            or intercepted.count(PRODUCTION_FIXTURE_URL) != 1
            or rejected
            or handler_errors
        ):
            raise PerformanceRunError("production-origin fixture setup failed")

        for _ in range(int(measurement["browser_warmup"])):
            snapshot = await dom.read_date_snapshot(tab)
            if not snapshot.ok:
                raise PerformanceRunError("production reader warmup failed")

        original_send = tab.send
        active_labels: list[str] | None = None
        command_labels: dict[str, int] = {}

        async def counted_send(cdp_object: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal active_labels
            if active_labels is not None:
                code = getattr(cdp_object, "gi_code", None)
                label = getattr(code, "co_name", None) or type(cdp_object).__name__
                active_labels.append(str(label))
                command_labels[str(label)] = command_labels.get(str(label), 0) + 1
            return await original_send(cdp_object, *args, **kwargs)

        tab.send = counted_send
        latency_samples: list[int] = []
        command_samples: list[int] = []
        request_samples: list[int] = []
        semantic_digests: set[str] = set()
        try:
            for _ in range(int(measurement["browser_samples"])):
                labels: list[str] = []
                active_labels = labels
                requests_before = len(intercepted)
                started = time.perf_counter_ns()
                snapshot = await dom.read_date_snapshot(tab)
                latency_samples.append(time.perf_counter_ns() - started)
                active_labels = None
                if not snapshot.ok:
                    raise PerformanceRunError("production reader measurement failed")
                command_samples.append(len(labels))
                request_samples.append(len(intercepted) - requests_before)
                semantic_digests.add(sha256_json(_jsonable(snapshot)))
        finally:
            active_labels = None
            tab.send = original_send
        if len(semantic_digests) != 1:
            raise PerformanceRunError("production reader output was not deterministic")
        return {
            "origin": actual_origin,
            "path": measurement["production_path"],
            "classifier": "DATE",
            "interception_stage": "Request",
            "interception_installed": True,
            "classifier_patched": False,
            "allowlist_entries": 1,
            "navigation_fulfilled_requests": 1,
            "rejected_unknown_requests": len(rejected),
            "live_authorization_used": False,
            "semantic_output_sha256": next(iter(semantic_digests)),
            "command_labels": dict(sorted(command_labels.items())),
            "metrics": {
                "observation_latency": summarize_samples(latency_samples, "ns"),
                "cdp_commands_per_observation": summarize_samples(
                    command_samples, "count"
                ),
                "ticketing_requests_per_observation": summarize_samples(
                    request_samples, "count"
                ),
            },
        }
    finally:
        try:
            if browser is not None and tab is not None:
                try:
                    tab.remove_handlers(cdp.fetch.RequestPaused)
                    await tab.send(cdp.fetch.disable())
                except Exception:
                    pass
                await browser.stop()
        finally:
            profile.cleanup()


def _run_browser_measurement(measurement: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return asyncio.run(_measure_browser(measurement))
    except PerformanceRunError:
        raise
    except Exception as exc:
        raise PerformanceRunError(
            f"isolated browser boundary failed: {type(exc).__name__}"
        ) from exc


def _manifest_mappings(manifest: Mapping[str, Any], phase: str = "P1") -> list[dict[str, str]]:
    mappings: list[dict[str, str]] = []
    for case in manifest.get("cases", []):
        if not isinstance(case, dict):
            continue
        for execution in case.get("executions", []):
            if (
                isinstance(execution, dict)
                and execution.get("status") == "MAPPED"
                and execution.get("job_id") == JOB_ID
                and execution.get("target") == TARGET
            ):
                mappings.append(
                    {
                        "case_id": case["id"],
                        "obligation_id": execution["obligation_id"],
                        "variant_id": execution["variant_id"],
                        "node_id": execution["node_id"],
                        "layer": execution["layer"],
                    }
                )
    by_node = {item["node_id"]: item for item in mappings}
    required_nodes = node_ids(phase)
    if set(by_node) != set(required_nodes) or len(mappings) != len(required_nodes):
        raise PerformanceRunError("execution manifest performance nodes are not exact")
    return [by_node[node_id] for node_id in required_nodes]


def _run_assertion_nodes(
    *,
    raw_report: Path,
    contract_path: Path,
    manifest_path: Path,
    junit: Path,
    reporter_result: Path,
    context: Mapping[str, Any],
    fixture_digest: str,
    script_digest: str,
    phase: str = "P1",
) -> tuple[int, list[str]]:
    manifest = load_json(manifest_path)
    mappings = _manifest_mappings(manifest, phase)
    reporter_context = {
        "schema_version": 1,
        "run_id": context["run_id"],
        "attempt": context["attempt"],
        "job_id": JOB_ID,
        "phase": phase,
        "target": TARGET,
        "candidate_sha": context["candidate_sha"],
        "catalog_sha256": manifest["catalog_sha256"],
        "execution_manifest_sha256": sha256_file(manifest_path),
        "fixture_digest": fixture_digest,
        "script_digest": script_digest,
        "mappings": mappings,
    }
    context_path = junit.parent / "performance-pytest-context.json"
    write_json(context_path, reporter_context)
    environment = os.environ.copy()
    environment["TICKETS_HUNTER_P1_PERFORMANCE_REPORT"] = str(raw_report)
    environment["TICKETS_HUNTER_P1_PERFORMANCE_CONTRACT"] = str(contract_path)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(REPOSITORY / "pytest.ini"),
        "--rootdir",
        str(REPOSITORY),
        "-o",
        "addopts=",
        "--strict-config",
        "--strict-markers",
        "-m",
        "browser and not live",
        "-p",
        "tools.utiki_pytest_reporter",
        "--utiki-reporter-context",
        str(context_path),
        "--utiki-managed-junit",
        str(junit),
        "--utiki-reporter-result",
        str(reporter_result),
        *node_ids(phase),
    ]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    (junit.parent / "performance-pytest.stdout").write_bytes(completed.stdout)
    (junit.parent / "performance-pytest.stderr").write_bytes(completed.stderr)
    return completed.returncode, command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--execution-manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--inner-report", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--reporter-result", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-attestation", type=Path, required=True)
    arguments = parser.parse_args(argv)

    outputs = (
        arguments.report,
        arguments.inner_report,
        arguments.junit,
        arguments.reporter_result,
    )
    inner: dict[str, Any] = {
        "schema_version": 1,
        "report_kind": f"utiki-{arguments.phase.lower()}-performance-inner",
        "status": "BLOCKED",
        "context": {
            "run_id": arguments.run_id,
            "attempt": arguments.attempt,
            "phase": arguments.phase,
            "job_id": JOB_ID,
            "target": TARGET,
            "candidate_sha": arguments.candidate_sha,
        },
        "probes": {},
        "benchmark_report_sha256": None,
        "junit_sha256": None,
        "pytest": {"exit_code": None, "command": []},
        "reasons": [],
    }
    try:
        if any(path.exists() for path in outputs):
            raise PerformanceRunError("performance output already exists")
        if arguments.phase not in {"P1", "P2"} or arguments.attempt < 1:
            raise PerformanceRunError("performance run context is invalid")
        contract_path = arguments.contract.resolve(strict=True)
        manifest_path = arguments.execution_manifest.resolve(strict=True)
        contract = load_contract(contract_path)
        if performance_phase(contract) != arguments.phase:
            raise PerformanceRunError("performance contract phase is invalid")
        manifest = load_json(manifest_path)
        if (
            manifest.get("phase") != arguments.phase
            or manifest.get("catalog_sha256") != arguments.catalog_sha256
        ):
            raise PerformanceRunError("execution manifest context is invalid")
        _manifest_mappings(manifest, arguments.phase)
        attestation, attested_files = _verify_candidate_attestation(
            arguments.candidate_attestation.resolve(strict=True),
            arguments.candidate_sha,
        )
        probes, netns, _ = egress._base_probes()
        inner["probes"] = probes
        if platform.system() != "Linux" or platform.machine() not in {
            "x86_64",
            "amd64",
        }:
            raise PerformanceRunError("P1 performance target must be Linux x64")

        source_path = arguments.report.parent / "source-benchmark.json"
        if source_path.exists():
            raise PerformanceRunError("source benchmark output already exists")
        source = _run_source_benchmark(
            source_path,
            run_id=arguments.run_id,
            measurement=contract["measurement"],
            candidate_attestation_path=arguments.candidate_attestation.resolve(
                strict=True
            ),
        )
        browser_result = _run_browser_measurement(contract["measurement"])
        hot_path = None
        if arguments.phase == "P2":
            from tools.benchmarks.utiki_p2_hot_path import measure_hot_path

            relative_hot_path = HOT_PATH_SCRIPT_PATH.relative_to(REPOSITORY).as_posix()
            if attested_files.get(relative_hot_path) != sha256_file(HOT_PATH_SCRIPT_PATH):
                raise PerformanceRunError("P2 hot-path script differs from candidate")
            hot_path = measure_hot_path(**contract["hot_path"])
        production_subjects = _loaded_production_subjects()
        if any(
            attested_files.get(item["path"]) != item["sha256"]
            for item in production_subjects
        ):
            raise PerformanceRunError(
                "production subject manifest differs from candidate attestation"
            )
        context = inner["context"]
        raw_report = {
            "schema_version": SCHEMA_VERSION,
            "report_kind": f"utiki-{arguments.phase.lower()}-performance",
            "status": "PENDING_OUTER_AUDIT",
            "context": context,
            "environment": {
                "os": "linux",
                "architecture": "x86_64",
                "python": platform.python_version(),
                "browser": _browser_version(),
            },
            "method": contract["measurement"],
            "digests": {
                "contract_sha256": sha256_file(contract_path),
                "execution_manifest_sha256": sha256_file(manifest_path),
                "fixture_sha256": sha256_file(FIXTURE_PATH),
                "production_script_sha256": sha256_file(PRODUCTION_SCRIPT_PATH),
                "source_benchmark_script_sha256": sha256_file(
                    SOURCE_BENCHMARK_PATH
                ),
                "inner_harness_sha256": sha256_file(Path(__file__).resolve()),
                "semantic_output_sha256": browser_result[
                    "semantic_output_sha256"
                ],
                "production_subject_manifest_sha256": sha256_json(
                    production_subjects
                ),
                **({"hot_path_script_sha256": sha256_file(HOT_PATH_SCRIPT_PATH)}
                   if arguments.phase == "P2" else {}),
            },
            "production_subjects": production_subjects,
            "isolation": {
                "mode": "linux-container-network-none",
                "only_loopback_interface": probes[
                    "only-loopback-interface"
                ]["interfaces"]
                == ["lo"],
                "no_default_ipv4_route": probes["no-default-ipv4-route"][
                    "routes"
                ]
                == [],
                "no_default_ipv6_route": probes["no-default-ipv6-route"][
                    "routes"
                ]
                == [],
                "inner_probe_status": "PASS",
                "outer_peer_audit_required": True,
            },
            "source": source,
            "browser": browser_result,
            "execution_results": execution_results(arguments.phase),
            **({"hot_path": hot_path} if arguments.phase == "P2" else {}),
        }
        validate_raw_report(
            raw_report,
            contract=contract,
            expected_candidate_sha=arguments.candidate_sha,
        )
        write_json(arguments.report, raw_report)
        pytest_exit, pytest_command = _run_assertion_nodes(
            raw_report=arguments.report.resolve(),
            contract_path=contract_path,
            manifest_path=manifest_path,
            junit=arguments.junit,
            reporter_result=arguments.reporter_result,
            context=context,
            fixture_digest=raw_report["digests"]["fixture_sha256"],
            script_digest=raw_report["digests"]["production_script_sha256"],
            phase=arguments.phase,
        )
        inner["pytest"] = {"exit_code": pytest_exit, "command": pytest_command}
        if pytest_exit != 0:
            raise PerformanceRunError("managed performance assertion nodes failed")
        reporter_result = load_json(arguments.reporter_result)
        if reporter_result.get("status") != "PASS":
            raise PerformanceRunError("managed performance reporter did not pass")
        inner["benchmark_report_sha256"] = sha256_file(arguments.report)
        inner["junit_sha256"] = sha256_file(arguments.junit)
        inner["netns"] = netns
        inner["status"] = "PENDING_OUTER_AUDIT"
        return_code = 0
    except (
        PerformanceRunError,
        PerformanceReportError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        inner["status"] = "BLOCKED"
        inner["reasons"].append(str(exc))
        return_code = 2
    finally:
        write_json(arguments.inner_report, inner)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
