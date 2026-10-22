"""Measure P2 planning hot paths without changing the purchase runtime."""

from __future__ import annotations

import asyncio
import builtins
import copy
import dataclasses
import hashlib
import json
import sys
import time
from types import CodeType, FrameType
from typing import Callable

from tools.benchmarks import bench_utiki
from tools.benchmarks.utiki_p1_performance import summarize_samples


class HotPathError(RuntimeError):
    """A planned operation or bounded-retention check did not hold."""


class _Core:
    def __init__(self, control: object) -> None:
        self.control = control

    def current_control(self, tab: object) -> object:
        return self.control


class _Browser:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot
        self.source: object | None = None

    async def observe_page(self, expected: object) -> object:
        from platforms.utiki.application.ports import QuantityObservation

        if self.source is None:
            raise HotPathError("quantity observation source is absent")
        return QuantityObservation(self.source, self.snapshot)

    async def commit_quantity(self, plan: object, claim: object) -> object:
        raise HotPathError("planning benchmark must not commit")


def _trace_calls(
    functions: dict[str, Callable[..., object]], calls: dict[str, int],
    *, allowed_callers: dict[str, CodeType] | None = None,
) -> Callable[[FrameType, str, object], None]:
    """Count original call targets even when a consumer saved an import alias."""
    by_code: dict[CodeType, str] = {}
    by_builtin: dict[int, str] = {}
    for label, function in functions.items():
        code = getattr(function, "__code__", None)
        if isinstance(code, CodeType):
            by_code[code] = label
        else:
            by_builtin[id(function)] = label

    def trace(frame: FrameType, event: str, argument: object) -> None:
        if event == "call":
            label = by_code.get(frame.f_code)
        elif event == "c_call":
            label = by_builtin.get(id(argument))
        else:
            return
        if label is not None and label in (allowed_callers or {}):
            caller = frame.f_back if event == "call" else frame
            if caller is not None and caller.f_code is allowed_callers[label]:
                return
        if label is not None:
            calls[label] += 1

    return trace


def _tracked_graph(root: object) -> list[object]:
    """Hold typed snapshot containers and records to detect retained subgraphs."""
    tracked: list[object] = []
    seen: set[int] = set()
    pending = [root]
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            children = [getattr(value, field.name) for field in dataclasses.fields(value)]
        elif isinstance(value, (tuple, list, frozenset, set)):
            children = list(value)
        elif isinstance(value, dict):
            children = [*value.keys(), *value.values()]
        else:
            continue
        seen.add(id(value))
        tracked.append(value)
        pending.extend(children)
    return tracked


def _reference_counts(values: list[object]) -> tuple[int, ...]:
    return tuple(sys.getrefcount(value) for value in values)


def _selection_and_config() -> tuple[object, object]:
    if str(bench_utiki.SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(bench_utiki.SRC_ROOT))
    return bench_utiki._load_selection_module(), __import__("utiki_config")


def _measure_v07(samples: int, warmup: int) -> dict[str, object]:
    selection, config = _selection_and_config()
    from keyword_rules import parse_keyword_string_to_array
    from tools.migrations import convert_utiki_config
    from platforms.utiki.application.ports import CoreControl, CoreGate
    from platforms.utiki.application.flow_settings import EffectivePolicy, FlowSettings
    from platforms.utiki.bootstrap import create_engine
    from platforms.utiki.domain.identity import DocumentKey, OperationId, OperationStamp, TabKey

    fixture = bench_utiki.build_synthetic_fixtures()["quantity"]
    snapshot = selection.parse_quantity_snapshot(fixture)
    if snapshot is None:
        raise HotPathError("quantity fixture did not parse")
    current_policy = config.apply_current_policy_reload(
        None,
        json.dumps({"utiki": {
            "ticket_type_keyword": "FULL",
            "seat_sorting": "row_seat",
            "seat_selection_mode": "manual",
        }}),
    )
    policy = EffectivePolicy(
        current_policy.last_valid, FlowSettings(False, False, 2),
        current_policy.policy_revision, current_policy.control_revision,
        current_policy.block_reason,
    )
    tab = TabKey(1, "p2-hot-path")
    browser = _Browser(snapshot)
    engine = create_engine(policy, browser, _Core(CoreControl(1, CoreGate.PURCHASE_ALLOWED)))
    handle = engine.attach(tab)
    context = engine.context(handle)
    if context is None:
        raise HotPathError("current policy did not open planning")
    stamp = OperationStamp(
        tab, context.session_generation, OperationId("p2-hot-path"),
        DocumentKey(snapshot.document_id), context.policy_revision, context.control_revision,
    )
    browser.source = stamp
    calls = {name: 0 for name in (
        "parse_profile_json", "load_current_policy", "inspect_upgrade_input",
        "apply_current_policy_reload", "convert_profile_mapping", "json.loads",
        "hash", "hashlib.new", "sha256", "sha1", "md5", "blake2b",
    )}
    trace = _trace_calls({
        "parse_profile_json": config.parse_profile_json,
        "load_current_policy": config.load_current_policy,
        "inspect_upgrade_input": config.inspect_upgrade_input,
        "apply_current_policy_reload": config.apply_current_policy_reload,
        "convert_profile_mapping": convert_utiki_config.convert_profile_mapping,
        "json.loads": json.loads,
        "hash": builtins.hash,
        "hashlib.new": hashlib.new,
        "sha256": hashlib.sha256,
        "sha1": hashlib.sha1,
        "md5": hashlib.md5,
        "blake2b": hashlib.blake2b,
    }, calls, allowed_callers={
        "json.loads": parse_keyword_string_to_array.__code__,
        "hash": TabKey.__hash__.__code__,
    })

    async def run() -> list[int]:
        values: list[int] = []
        previous = sys.getprofile()
        sys.setprofile(trace)
        try:
            for index in range(samples + warmup):
                started = time.perf_counter_ns()
                plan = await engine.plan_quantity(handle, stamp, 2)
                elapsed = time.perf_counter_ns() - started
                if plan is None or plan.target_key != "TYPE_0099":
                    raise HotPathError("high-frequency quantity plan changed")
                if index >= warmup:
                    values.append(elapsed)
        finally:
            sys.setprofile(previous)
        if any(calls.values()):
            violated = ", ".join(name for name, count in calls.items() if count)
            raise HotPathError(f"unchanged policy was reprocessed: {violated}")
        return values

    values = asyncio.run(run())
    return {
        "policy_revision": policy.policy_revision,
        "latency_includes_call_tracing": True,
        "forbidden_calls": calls,
        "latency": summarize_samples(values, "ns"),
    }


def _measure_v08(samples: int, warmup: int, seat_count: int) -> dict[str, object]:
    selection, _ = _selection_and_config()
    from platforms.utiki.domain.snapshots import SeatRow, SeatSnapshot, TicketTypeRow
    sizes = {**bench_utiki.DEFAULT_SIZES, "seat_count": seat_count}
    raw = bench_utiki.build_synthetic_fixtures(sizes)["seat"]
    snapshot = selection.parse_seat_snapshot(raw)
    if snapshot is None or len(snapshot.seats) != seat_count:
        raise HotPathError("large seat fixture did not parse")
    graph = _tracked_graph(snapshot)
    before_refs = _reference_counts(graph)
    calls = {
        "deepcopy": 0, "copy": 0, "asdict": 0,
        "SeatRow.__init__": 0, "SeatSnapshot.__init__": 0,
        "TicketTypeRow.__init__": 0,
    }
    trace = _trace_calls({
        "deepcopy": copy.deepcopy,
        "copy": copy.copy,
        "asdict": dataclasses.asdict,
        "SeatRow.__init__": SeatRow.__init__,
        "SeatSnapshot.__init__": SeatSnapshot.__init__,
        "TicketTypeRow.__init__": TicketTypeRow.__init__,
    }, calls)
    valid_values: list[int] = []
    invalid_values: list[int] = []

    def one(valid: bool) -> int:
        started = time.perf_counter_ns()
        plan, reason = selection.select_seat_snapshot(
            snapshot, "FULL" if valid else "MISSING", 2,
            allow_non_adjacent=False, seat_sorting="row_seat",
        )
        elapsed = time.perf_counter_ns() - started
        if valid and (plan is None or reason):
            raise HotPathError("large valid seat tick did not plan")
        if not valid and (plan is not None or not reason):
            raise HotPathError("large invalid seat tick was accepted")
        return elapsed

    async def run() -> tuple[int, int]:
        before_tasks = asyncio.all_tasks()
        previous = sys.getprofile()
        sys.setprofile(trace)
        try:
            for index in range(warmup + samples):
                valid = one(True)
                invalid = one(False)
                if index >= warmup:
                    valid_values.append(valid)
                    invalid_values.append(invalid)
        finally:
            sys.setprofile(previous)
        if any(calls.values()):
            violated = ", ".join(name for name, count in calls.items() if count)
            raise HotPathError(f"large seat tick reconstructed a typed snapshot: {violated}")
        after_tasks = asyncio.all_tasks()
        extra_tasks = after_tasks - before_tasks
        if extra_tasks:
            for task in extra_tasks:
                task.cancel()
            await asyncio.gather(*extra_tasks, return_exceptions=True)
            raise HotPathError("large seat tick retained pending tasks")
        return len(before_tasks), len(after_tasks)

    before_tasks, after_tasks = asyncio.run(run())
    after_refs = _reference_counts(graph)
    max_reference_delta = max((after - before for before, after in zip(before_refs, after_refs)), default=0)
    if max_reference_delta > 0:
        raise HotPathError("large seat tick retained a typed snapshot subgraph")
    return {
        "seat_count": seat_count,
        "latency_includes_call_tracing": True,
        "tracked_objects": len(graph),
        "max_reference_delta": max_reference_delta,
        "pending_tasks_before": before_tasks,
        "pending_tasks_after": after_tasks,
        "forbidden_calls": calls,
        "valid_latency": summarize_samples(valid_values, "ns"),
        "invalid_latency": summarize_samples(invalid_values, "ns"),
    }


def measure_hot_path(*, samples: int = 30, warmup: int = 5, seat_count: int = 2000) -> dict[str, object]:
    """Record raw P2 source samples; hard-egress attestation belongs to the caller."""
    if samples <= 0 or warmup < 0 or not 100 <= seat_count <= 5000:
        raise ValueError("P2 hot-path sample parameters are invalid")
    return {
        "schema_version": 1,
        "phase": "P2",
        "method": {"samples": samples, "warmup": warmup, "seat_count": seat_count},
        "v07": _measure_v07(samples, warmup),
        "v08": _measure_v08(samples, warmup, seat_count),
    }
