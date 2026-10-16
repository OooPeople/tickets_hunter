#!/usr/bin/env python3
"""Offline source benchmark for the current uTiki selection pipeline.

This harness intentionally exercises only deterministic synthetic snapshots and
pure Python selection code. Browser, CDP, network, frozen-build, and native
handler measurements remain explicitly blocked until their isolation and
instrumentation requirements exist.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import enum
import hashlib
import importlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
BENCHMARK_NAME = "utiki-source"
CASES = ("date", "area", "seat", "quantity")
DEFAULT_SIZES = {
    "date_rows": 200,
    "area_rows": 200,
    "seat_count": 500,
    "quantity_rows": 100,
}
DEFAULT_WARMUP = 20
DEFAULT_SAMPLES = 300
DEFAULT_BATCHES = 3
DEFAULT_COLD_SAMPLES = 3
DEFAULT_LAG_SAMPLES = 300
DEFAULT_SEED = 1701
MODES = ("all", "warm", "cold")
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


class BenchmarkOperationError(RuntimeError):
    """Raised when a synthetic case does not produce its expected plan."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _digest_files(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        resolved = path.resolve()
        try:
            label = resolved.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            label = resolved.as_posix()
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(resolved.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _loaded_subject_files() -> list[Path]:
    """Return the real repository import graph loaded by the benchmark subject."""

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
        if path.suffix != ".py":
            continue
        try:
            path.relative_to(SRC_ROOT)
        except ValueError:
            continue
        if path.is_file():
            paths.add(path)
    return sorted(paths, key=lambda item: item.relative_to(REPO_ROOT).as_posix())


def _interpreter_label() -> str:
    """Describe the interpreter without persisting an absolute user path."""

    executable = Path(sys.executable).resolve()
    try:
        return executable.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return executable.name


def _hardware_metadata() -> dict[str, Any]:
    """Return stable, non-identifying hardware fields for paired comparisons."""

    memory_bytes = None
    try:
        memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        pass
    return {
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "physical_memory_bytes": memory_bytes,
    }


def build_benchmark_config() -> dict[str, Any]:
    """Return the fixed, non-secret policy used by all synthetic cases."""

    return {
        "ticket_number": 2,
        "keyword_exclude": "",
        "date_auto_select": {
            "date_keyword": "TARGET-DATE",
            "mode": "from top to bottom",
        },
        "date_auto_fallback": False,
        "area_auto_select": {
            "area_keyword": "TARGET-AREA",
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
        "utiki": {
            "ticket_type_keyword": "FULL",
            "seat_sorting": "row_seat",
            "seat_selection_mode": "manual",
        },
    }


def _date_control() -> dict[str, Any]:
    return {
        "actionable": True,
        "tag_name": "BUTTON",
        "id": "buy_btn",
        "href": "",
        "resolved_href": "",
        "role": "",
        "type": "button",
        "target": "",
        "download": "",
        "ping": "",
        "formaction": "",
        "has_form": False,
        "onclick": (
            "location.href='UTK0204_?"
            "PERFORMANCE_ID=PERF_FIXTURE&PRODUCT_ID=PRODUCT_FIXTURE'"
        ),
    }


def _build_date_fixture(row_count: int) -> dict[str, Any]:
    rows = []
    for index in range(row_count):
        marker = "TARGET-DATE" if index == row_count - 1 else "DATE"
        rows.append({
            "text": f"{marker}-{index:04d}",
            "controls": [_date_control()],
        })
    return {
        "ok": True,
        "page_url": (
            "https://tix.ctbcsports.com/BROTHERS/UTK0201_?"
            "PRODUCT_ID=PRODUCT_FIXTURE"
        ),
        "document_id": "document-fixture",
        "rows": rows,
    }


def _build_area_fixture(row_count: int) -> dict[str, Any]:
    rows = []
    areas = []
    for index in range(row_count):
        area_id = f"area_{index:04d}"
        area_name = (
            f"TARGET-AREA-{index:04d}"
            if index == row_count - 1
            else f"AREA-{index:04d}"
        )
        price = str(1000 + index)
        price_area_id = f"PRICE_{index:04d}"
        group_id = f"GROUP_{index:04d}"
        rows.append({
            "area": area_name,
            "price": price,
            "status": "50",
            "rel": area_id,
        })
        areas.append({
            "id": area_id,
            "href": (
                "javascript:Send('0205','PERF_FIXTURE',"
                f"'{price_area_id}','{group_id}','50')"
            ),
            "title": f"票區:{area_name} 票價：{price} 尚餘：50",
        })
    return {
        "ok": True,
        "page_url": (
            "https://tix.ctbcsports.com/BROTHERS/UTK0204_?"
            "PERFORMANCE_ID=PERF_FIXTURE&PRODUCT_ID=PRODUCT_FIXTURE"
        ),
        "document_id": "document-fixture",
        "rows": rows,
        "areas": areas,
    }


def _build_seat_fixture(seat_count: int) -> dict[str, Any]:
    seats = []
    seats_per_row = 20
    for index in range(seat_count):
        row_number = index // seats_per_row + 1
        seat_number = index % seats_per_row + 1
        title = f"A-{row_number}排-{seat_number}號"
        seats.append({
            "fingerprint": ["seat", index, title],
            "title": title,
            "row_index": row_number,
            "cell_index": seat_number,
            "center_x": 10 + (seat_number - 1) * 20,
            "center_y": 10 + (row_number - 1) * 20,
            "width": 20,
            "height": 20,
            "available": True,
            "selected": False,
        })
    return {
        "ok": True,
        "page_url": (
            "https://tix.ctbcsports.com/BROTHERS/UTK0205_?"
            "PERFORMANCE_ID=PERF_FIXTURE&GROUP_ID=GROUP_FIXTURE&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FIXTURE"
        ),
        "document_id": "document-fixture",
        "performance_id": "PERF_FIXTURE",
        "group_id": "GROUP_FIXTURE",
        "price_area_id": "PRICE_FIXTURE",
        "product_id": "PRODUCT_FIXTURE",
        "product_hidden_present": True,
        "quantity_limit_present": True,
        "quantity_limit_raw": "4",
        "ticket_types": [{
            "fingerprint": ["ticket", "FULL", "setType('FULL')"],
            "text": "FULL",
            "disabled": False,
            "type_id": "FULL",
            "type_name": "FULL",
            "counter_present": True,
            "counter_raw": "0",
        }],
        "seats": seats,
        "submitted_seats": [],
        "counters": [
            {"id": "FULL", "counter_raw": "0"},
            {"id": "OTHER", "counter_raw": "0"},
        ],
        "visual_selected_count": 0,
    }


def _build_quantity_fixture(row_count: int) -> dict[str, Any]:
    rows = []
    for index in range(row_count):
        name = "FULL" if index == row_count - 1 else f"TYPE-{index:04d}"
        rows.append({
            "key": f"TYPE_{index:04d}",
            "name": name,
            "text": name,
            "disabled": False,
            "remaining_present": True,
            "remaining_raw": "50",
            "value": "0",
        })
    return {
        "ok": True,
        "page_url": (
            "https://tix.ctbcsports.com/BROTHERS/UTK0203_?"
            "PERFORMANCE_ID=PERF_FIXTURE&GROUP_ID=&"
            "PERFORMANCE_PRICE_AREA_ID=PRICE_FIXTURE"
        ),
        "document_id": "document-fixture",
        "performance_id": "PERF_FIXTURE",
        "group_id": "",
        "price_area_id": "PRICE_FIXTURE",
        "quantity_limit_raw": "4",
        "performance_hidden_present": True,
        "product_hidden_present": False,
        "group_hidden_present": True,
        "price_area_hidden_present": True,
        "quantity_limit_present": True,
        "product_id": "",
        "rows": rows,
    }


def build_synthetic_fixtures(
    sizes: Mapping[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build deterministic source-only snapshots without external I/O."""

    selected_sizes = dict(DEFAULT_SIZES if sizes is None else sizes)
    required = set(DEFAULT_SIZES)
    if set(selected_sizes) != required or any(
        type(value) is not int or value <= 0 for value in selected_sizes.values()
    ):
        raise ValueError(f"sizes must contain positive integers for {sorted(required)}")
    if selected_sizes["date_rows"] > 200:
        raise ValueError("date_rows exceeds the current source contract")
    if selected_sizes["area_rows"] > 200:
        raise ValueError("area_rows exceeds the current source contract")
    if selected_sizes["seat_count"] > 5000:
        raise ValueError("seat_count exceeds the current source contract")
    if selected_sizes["quantity_rows"] > 100:
        raise ValueError("quantity_rows exceeds the current source contract")
    return {
        "date": _build_date_fixture(selected_sizes["date_rows"]),
        "area": _build_area_fixture(selected_sizes["area_rows"]),
        "seat": _build_seat_fixture(selected_sizes["seat_count"]),
        "quantity": _build_quantity_fixture(selected_sizes["quantity_rows"]),
    }


def _load_selection_module():
    src_text = str(SRC_ROOT)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)
    return importlib.import_module("platforms.utiki.selection")


def execute_case(
    selection: Any,
    case: str,
    raw_snapshot: Mapping[str, Any],
    config: Mapping[str, Any],
) -> Any:
    """Run parse, select, and submit-plan construction for one case."""

    if case == "date":
        snapshot = selection.parse_date_snapshot(raw_snapshot)
        plan = selection.select_date_snapshot(snapshot, dict(config))
    elif case == "area":
        snapshot = selection.parse_area_snapshot(raw_snapshot)
        plan = selection.select_area_snapshot(
            snapshot,
            dict(config),
            "PERF_FIXTURE",
        )
    elif case == "seat":
        snapshot = selection.parse_seat_snapshot(raw_snapshot)
        plan, reason = selection.select_seat_snapshot(
            snapshot,
            config["utiki"]["ticket_type_keyword"],
            config["ticket_number"],
            allow_non_adjacent=False,
            seat_sorting=config["utiki"]["seat_sorting"],
        )
        if reason:
            raise BenchmarkOperationError(f"seat selection failed: {reason}")
        plan = selection.build_submit_plan(plan)
    elif case == "quantity":
        snapshot = selection.parse_quantity_snapshot(raw_snapshot)
        plan = selection.select_quantity_snapshot(
            snapshot,
            config["utiki"]["ticket_type_keyword"],
            config["ticket_number"],
        )
        plan = selection.build_submit_plan(plan)
    else:
        raise ValueError(f"unknown benchmark case: {case}")
    if plan is None:
        raise BenchmarkOperationError(f"{case} did not produce a plan")
    return plan


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


def _percentile_nearest_rank(samples: Sequence[int], percentile: float) -> int:
    if not samples:
        raise ValueError("cannot summarize an empty sample set")
    ordered = sorted(samples)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize_samples(
    samples_ns: Sequence[int],
    failures: Sequence[Mapping[str, Any]] = (),
    *,
    batch_sizes: Sequence[int] = (),
) -> dict[str, Any]:
    """Return raw samples and exact aggregate fields used by comparisons."""

    clean_samples = [int(value) for value in samples_ns]
    if any(value < 0 for value in clean_samples):
        raise ValueError("sample duration cannot be negative")
    summary: dict[str, Any] = {
        "unit": "ns",
        "sample_count": len(clean_samples),
        "samples_ns": clean_samples,
        "failure_count": len(failures),
        "failures": [dict(item) for item in failures],
        "batch_sizes": [int(value) for value in batch_sizes],
    }
    if clean_samples:
        summary.update({
            "median_ns": int(statistics.median(clean_samples)),
            "p95_ns": _percentile_nearest_rank(clean_samples, 0.95),
            "p99_ns": _percentile_nearest_rank(clean_samples, 0.99),
            "min_ns": min(clean_samples),
            "max_ns": max(clean_samples),
        })
    else:
        summary.update({
            "median_ns": None,
            "p95_ns": None,
            "p99_ns": None,
            "min_ns": None,
            "max_ns": None,
        })
    return summary


def _failure(index: int, exc: BaseException) -> dict[str, Any]:
    return {
        "sample_index": index,
        "exception": type(exc).__name__,
        "message": str(exc)[:200],
    }


def _batch_sample_counts(samples: int, batches: int) -> list[int]:
    if samples <= 0 or batches <= 0 or batches > samples:
        raise ValueError("samples and batches must be positive, with batches <= samples")
    base, remainder = divmod(samples, batches)
    return [base + (1 if index < remainder else 0) for index in range(batches)]


def measure_operation(
    operation: Callable[[], Any],
    *,
    samples: int,
    warmup: int,
    batches: int,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    """Measure one synchronous operation while retaining every raw sample."""

    if warmup < 0:
        raise ValueError("warmup cannot be negative")
    batch_sizes = _batch_sample_counts(samples, batches)
    values: list[int] = []
    failures: list[dict[str, Any]] = []
    sample_index = 0
    for batch_size in batch_sizes:
        for _ in range(warmup):
            operation()
        for _ in range(batch_size):
            started_ns = clock_ns()
            try:
                operation()
            except Exception as exc:  # pragma: no cover - exercised by callers
                failures.append(_failure(sample_index, exc))
            else:
                values.append(clock_ns() - started_ns)
            sample_index += 1
    return summarize_samples(values, failures, batch_sizes=batch_sizes)


async def measure_event_loop_lag(
    *,
    samples: int,
    warmup: int,
    batches: int,
    clock_ns: Callable[[], int] = time.perf_counter_ns,
    schedule: Callable[[Callable[[], None]], Any] | None = None,
) -> dict[str, Any]:
    """Measure call_soon scheduling lag without sleeping or external I/O."""

    if warmup < 0:
        raise ValueError("warmup cannot be negative")
    batch_sizes = _batch_sample_counts(samples, batches)
    loop = asyncio.get_running_loop()
    scheduler = schedule or loop.call_soon

    async def one_sample() -> int:
        future = loop.create_future()
        started_ns = clock_ns()

        def complete() -> None:
            if not future.done():
                future.set_result(clock_ns() - started_ns)

        scheduler(complete)
        return int(await future)

    values: list[int] = []
    failures: list[dict[str, Any]] = []
    sample_index = 0
    for batch_size in batch_sizes:
        for _ in range(warmup):
            await one_sample()
        for _ in range(batch_size):
            try:
                values.append(await one_sample())
            except Exception as exc:  # pragma: no cover - defensive boundary
                failures.append(_failure(sample_index, exc))
            sample_index += 1
    return summarize_samples(values, failures, batch_sizes=batch_sizes)


def _run_git(repo_root: Path, *args: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def _git_metadata(repo_root: Path) -> dict[str, Any]:
    head = _run_git(repo_root, "rev-parse", "HEAD")
    status = _run_git(repo_root, "status", "--porcelain=v1", "-z")
    if head is None or status is None:
        return {
            "candidate_sha": "UNKNOWN",
            "dirty": None,
            "working_tree_digest": None,
        }
    digest = hashlib.sha256()
    digest.update(status)
    for args in (
        ("diff", "--binary", "HEAD"),
        ("diff", "--cached", "--binary", "HEAD"),
    ):
        payload = _run_git(repo_root, *args)
        digest.update(payload or b"")
    untracked = _run_git(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    for raw_path in sorted((untracked or b"").split(b"\0")):
        if not raw_path:
            continue
        digest.update(raw_path)
        digest.update(b"\0")
        try:
            digest.update((repo_root / os.fsdecode(raw_path)).read_bytes())
        except OSError as exc:
            digest.update(f"UNREADABLE:{type(exc).__name__}".encode("ascii"))
        digest.update(b"\0")
    return {
        "candidate_sha": head.decode("ascii").strip(),
        "dirty": bool(status),
        "working_tree_digest": digest.hexdigest(),
    }


def _cold_worker(case: str) -> int:
    try:
        payload = json.load(sys.stdin)
        started_import_ns = time.perf_counter_ns()
        selection = _load_selection_module()
        import_ns = time.perf_counter_ns() - started_import_ns
        started_operation_ns = time.perf_counter_ns()
        plan = execute_case(
            selection,
            case,
            payload["fixture"],
            payload["config"],
        )
        operation_ns = time.perf_counter_ns() - started_operation_ns
        result = {
            "case": case,
            "import_ns": import_ns,
            "first_operation_ns": operation_ns,
            "result_digest": _sha256_json(_jsonable(plan)),
        }
    except Exception as exc:
        result = {
            "case": case,
            "error": {
                "exception": type(exc).__name__,
                "message": str(exc)[:200],
            },
        }
        json.dump(result, sys.stdout, sort_keys=True)
        return 1
    json.dump(result, sys.stdout, sort_keys=True)
    return 0


def measure_cold_case(
    case: str,
    fixture: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    samples: int,
    seed: int = DEFAULT_SEED,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Measure import and first operation inside fresh Python processes."""

    if samples <= 0:
        raise ValueError("cold samples must be positive")
    import_samples: list[int] = []
    operation_samples: list[int] = []
    failures: list[dict[str, Any]] = []
    result_digests: set[str] = set()
    payload = _canonical_json_bytes({"fixture": fixture, "config": config})
    command = [sys.executable, str(Path(__file__).resolve()), "--_cold-worker", case]
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(seed)
    for sample_index in range(samples):
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            failures.append(_failure(sample_index, exc))
            continue
        if completed.returncode != 0 or "error" in response:
            error = response.get("error", {})
            failures.append({
                "sample_index": sample_index,
                "exception": str(error.get("exception") or "ColdWorkerError"),
                "message": str(error.get("message") or "cold worker failed")[:200],
            })
            continue
        import_samples.append(int(response["import_ns"]))
        operation_samples.append(int(response["first_operation_ns"]))
        result_digests.add(str(response["result_digest"]))
    if len(result_digests) > 1:
        failures.append({
            "sample_index": -1,
            "exception": "NonDeterministicResult",
            "message": "cold workers produced different plan digests",
        })
    result_digest = next(iter(result_digests), None)
    return (
        summarize_samples(import_samples, failures, batch_sizes=[samples]),
        summarize_samples(operation_samples, failures, batch_sizes=[samples]),
        result_digest,
    )


def _blocked_metrics() -> dict[str, dict[str, str]]:
    reasons = {
        "cdp_commands_per_transaction": (
            "No browser was started and no CDP instrumentation was installed."
        ),
        "ticketing_requests_per_intent": (
            "Synthetic source cases perform no HTTP requests; website request "
            "instrumentation is unavailable."
        ),
        "browser_observation_ms": (
            "Browser fixtures require CDP interception plus independent hard-egress isolation."
        ),
        "ready_to_dispatch_ms": (
            "No isolated document clock or browser dispatch marker is available."
        ),
        "native_handler_ms": (
            "Native browser handlers are outside this source-only benchmark."
        ),
        "frozen_startup_ms": (
            "No source/frozen paired artifact was built for this run."
        ),
    }
    return {
        name: {"status": "BLOCKED", "reason": reason}
        for name, reason in reasons.items()
    }


def run_benchmark(
    *,
    samples: int = DEFAULT_SAMPLES,
    warmup: int = DEFAULT_WARMUP,
    batches: int = DEFAULT_BATCHES,
    cold_samples: int = DEFAULT_COLD_SAMPLES,
    lag_samples: int = DEFAULT_LAG_SAMPLES,
    run_id: str | None = None,
    sizes: Mapping[str, int] | None = None,
    mode: str = "all",
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run the complete offline source benchmark and return its raw report."""

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")

    started_at = dt.datetime.now(dt.timezone.utc)
    started_ns = time.perf_counter_ns()
    fixtures = build_synthetic_fixtures(sizes)
    config = build_benchmark_config()
    selection = _load_selection_module()
    subject_files = _loaded_subject_files()
    metrics: dict[str, dict[str, Any]] = {}
    case_results: dict[str, str | None] = {}
    for case in CASES:
        cold_result = None
        if mode in {"all", "cold"}:
            cold_import, cold_operation, cold_result = measure_cold_case(
                case,
                fixtures[case],
                config,
                samples=cold_samples,
                seed=seed,
            )
            metrics[f"{case}.cold_import"] = cold_import
            metrics[f"{case}.cold_first_operation"] = cold_operation
        operation = lambda selected_case=case: execute_case(
            selection,
            selected_case,
            fixtures[selected_case],
            config,
        )
        result = operation()
        warm_result = _sha256_json(_jsonable(result))
        if mode == "all" and cold_result is not None and cold_result != warm_result:
            raise BenchmarkOperationError(
                f"{case} cold and warm operations produced different plans"
            )
        case_results[case] = warm_result
        if mode in {"all", "warm"}:
            metrics[f"{case}.warm_parse_select_plan"] = measure_operation(
                operation,
                samples=samples,
                warmup=warmup,
                batches=batches,
            )
    if mode in {"all", "warm"}:
        metrics["event_loop.scheduling_lag"] = asyncio.run(measure_event_loop_lag(
            samples=lag_samples,
            warmup=warmup,
            batches=min(batches, lag_samples),
        ))
    finished_at = dt.datetime.now(dt.timezone.utc)
    elapsed_ns = time.perf_counter_ns() - started_ns
    script_path = Path(__file__).resolve()
    report = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": BENCHMARK_NAME,
        "run": {
            "run_id": run_id or (
                f"utiki-source-{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{uuid.uuid4().hex[:8]}"
            ),
            "started_at_utc": started_at.isoformat(),
            "finished_at_utc": finished_at.isoformat(),
            "elapsed_ns": elapsed_ns,
            "git": _git_metadata(REPO_ROOT),
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable": _interpreter_label(),
            },
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "hardware": _hardware_metadata(),
            "scope": "offline-source-only",
            "mode": mode,
            "hard_egress_isolation": False,
        },
        "digests": {
            "fixture_sha256": _sha256_json(fixtures),
            "config_sha256": _sha256_json(config),
            "script_sha256": _digest_files([script_path]),
            "subject_sha256": _digest_files(subject_files),
        },
        "subject_files": [
            path.relative_to(REPO_ROOT).as_posix() for path in subject_files
        ],
        "parameters": {
            "mode": mode,
            "seed": seed,
            "warmup_per_batch": warmup,
            "warm_samples_total": samples,
            "batches": batches,
            "cold_samples_per_case": cold_samples,
            "event_loop_samples_total": lag_samples,
            "fixture_sizes": dict(DEFAULT_SIZES if sizes is None else sizes),
        },
        "config": config,
        "case_results": case_results,
        "metrics": metrics,
        "blocked_metrics": _blocked_metrics(),
    }
    return report


def _write_report(report: Mapping[str, Any], output: str) -> None:
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(target)


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value cannot be negative")
    return number


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="-", help="JSON output path, or - for stdout")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--mode", choices=MODES, default="all")
    parser.add_argument("--seed", type=_non_negative, default=DEFAULT_SEED)
    parser.add_argument("--samples", type=_positive, default=DEFAULT_SAMPLES)
    parser.add_argument("--warmup", type=_non_negative, default=DEFAULT_WARMUP)
    parser.add_argument("--batches", type=_positive, default=DEFAULT_BATCHES)
    parser.add_argument(
        "--cold-samples",
        type=_positive,
        default=None,
    )
    parser.add_argument(
        "--lag-samples",
        type=_positive,
        default=None,
    )
    parser.add_argument("--_cold-worker", choices=CASES, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args._cold_worker:
        return _cold_worker(args._cold_worker)
    cold_samples = (
        args.samples if args.mode == "cold" and args.cold_samples is None
        else args.cold_samples or DEFAULT_COLD_SAMPLES
    )
    lag_samples = args.lag_samples or (
        args.samples if args.mode == "warm" else DEFAULT_LAG_SAMPLES
    )
    if args.mode in {"all", "warm"} and (
        args.batches > args.samples or args.batches > lag_samples
    ):
        raise SystemExit("--batches cannot exceed --samples or --lag-samples")
    report = run_benchmark(
        samples=args.samples,
        warmup=args.warmup,
        batches=args.batches,
        cold_samples=cold_samples,
        lag_samples=lag_samples,
        run_id=args.run_id,
        mode=args.mode,
        seed=args.seed,
    )
    _write_report(report, args.output)
    return 1 if any(
        metric.get("failure_count", 0) for metric in report["metrics"].values()
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
