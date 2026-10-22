"""The new bootstrap consumes trusted core control without a legacy route."""

import asyncio
import json
from dataclasses import replace

import pytest

from platforms.utiki.application.engine import SessionHandle, UtikiEngine
from platforms.utiki.application.ports import CoreControl, CoreGate, QuantityObservation
from platforms.utiki.bootstrap import create_engine
from platforms.utiki.domain.effects import EffectReceipt, RunningEffect
from platforms.utiki.domain.identity import DocumentKey, OperationId, OperationStamp, TabKey
from platforms.utiki.domain.plans import QuantityPlan
from platforms.utiki.domain.snapshots import QuantitySnapshot
from platforms.utiki.selection import parse_quantity_snapshot
from tests.ctbc_support import make_ctbc_quantity_snapshot, make_effective_policy
from platforms.utiki.application.flow_settings import FlowSettings
from utiki_config import CurrentPolicyState, apply_current_policy_reload


class FakeCore:
    def __init__(self) -> None:
        self.controls: dict[TabKey, CoreControl] = {}

    def current_control(self, tab: TabKey) -> CoreControl:
        return self.controls[tab]

    def set(self, tab: TabKey, revision: int, gate: CoreGate) -> None:
        self.controls[tab] = CoreControl(revision, gate)


class FakeBrowser:
    def __init__(self, snapshot: QuantitySnapshot) -> None:
        self.snapshot = snapshot
        self.actual_source: OperationStamp | None = None
        self.observed: list[OperationStamp] = []
        self.commits = 0

    async def observe_page(self, expected: OperationStamp) -> QuantityObservation:
        self.observed.append(expected)
        assert self.actual_source is not None
        return QuantityObservation(self.actual_source, self.snapshot)

    async def commit_quantity(
        self, plan: QuantityPlan, claim: RunningEffect
    ) -> EffectReceipt:
        self.commits += 1
        raise AssertionError("planning must not dispatch a page mutation")


def _snapshot() -> QuantitySnapshot:
    snapshot = parse_quantity_snapshot(make_ctbc_quantity_snapshot())
    assert isinstance(snapshot, QuantitySnapshot)
    return snapshot


def _profile(keyword: str = "全票") -> str:
    return json.dumps({"utiki": {
        "ticket_type_keyword": keyword,
        "seat_sorting": "row_seat",
        "seat_selection_mode": "manual",
    }}, ensure_ascii=False)


def _policy():
    return make_effective_policy(apply_current_policy_reload(None, _profile()))


def _stamp(
    engine: UtikiEngine, handle: SessionHandle, document: str, operation: str
) -> OperationStamp:
    context = engine.context(handle)
    assert context is not None
    return OperationStamp(
        handle.tab, context.session_generation, OperationId(operation),
        DocumentKey(document), context.policy_revision, context.control_revision,
    )


def test_u01_fake_core_host_controls_one_bootstrap_engine() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    assert engine.attach(tab) == handle

    first_stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-1")
    browser.actual_source = first_stamp
    first = asyncio.run(engine.plan_quantity(handle, first_stamp, 2))
    assert isinstance(first, QuantityPlan)
    assert first.target_key == "FULL"
    assert len(browser.observed) == 1
    assert browser.commits == 0

    core.set(tab, 2, CoreGate.PAUSED)
    assert engine.update_control(handle)
    assert engine.context(handle) is None
    assert asyncio.run(engine.plan_quantity(handle, first_stamp, 2)) is None
    assert len(browser.observed) == 1

    core.set(tab, 3, CoreGate.PURCHASE_ALLOWED)
    assert engine.update_control(handle)
    resumed_stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-2")
    browser.actual_source = resumed_stamp
    assert isinstance(asyncio.run(engine.plan_quantity(handle, resumed_stamp, 2)), QuantityPlan)
    assert len(browser.observed) == 2

    core.set(tab, 4, CoreGate.CLOSING)
    assert engine.update_control(handle)
    assert engine.close(handle)
    with pytest.raises(ValueError, match="closing control"):
        engine.attach(tab)
    next_tab = TabKey(2, "fake-core-tab")
    core.set(next_tab, 1, CoreGate.PURCHASE_ALLOWED)
    replacement = engine.attach(next_tab)
    assert replacement.generation > handle.generation
    assert asyncio.run(engine.plan_quantity(handle, resumed_stamp, 2)) is None
    assert browser.commits == 0


def test_u01_pause_during_observation_discards_old_decision() -> None:
    snapshot = _snapshot()
    started = asyncio.Event()
    release = asyncio.Event()

    class DelayedBrowser(FakeBrowser):
        async def observe_page(self, expected: OperationStamp) -> QuantityObservation:
            self.observed.append(expected)
            started.set()
            await release.wait()
            assert self.actual_source is not None
            return QuantityObservation(self.actual_source, self.snapshot)

    browser = DelayedBrowser(snapshot)
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    stamp = _stamp(engine, handle, snapshot.document_id, "quantity-op-1")
    browser.actual_source = stamp

    async def exercise() -> QuantityPlan | None:
        pending = asyncio.create_task(engine.plan_quantity(handle, stamp, 2))
        await started.wait()
        core.set(tab, 2, CoreGate.PAUSED)
        assert engine.update_control(handle)
        release.set()
        return await pending

    assert asyncio.run(exercise()) is None
    assert browser.commits == 0


def test_u01_close_during_observation_does_not_revive_old_handle() -> None:
    snapshot = _snapshot()
    started = asyncio.Event()
    release = asyncio.Event()

    class DelayedBrowser(FakeBrowser):
        async def observe_page(self, expected: OperationStamp) -> QuantityObservation:
            self.observed.append(expected)
            started.set()
            await release.wait()
            assert self.actual_source is not None
            return QuantityObservation(self.actual_source, self.snapshot)

    browser = DelayedBrowser(snapshot)
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    stamp = _stamp(engine, handle, snapshot.document_id, "quantity-op-1")
    browser.actual_source = stamp

    async def exercise() -> QuantityPlan | None:
        pending = asyncio.create_task(engine.plan_quantity(handle, stamp, 2))
        await started.wait()
        core.set(tab, 2, CoreGate.CLOSING)
        assert engine.update_control(handle)
        assert engine.close(handle)
        with pytest.raises(ValueError, match="closing control"):
            engine.attach(tab)
        release.set()
        return await pending

    assert asyncio.run(exercise()) is None
    assert browser.commits == 0


def test_u01_unconfirmed_close_latches_until_core_confirms_closing() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-1")
    browser.actual_source = stamp

    assert not engine.close(handle)
    assert engine.context(handle) is None
    with pytest.raises(ValueError, match="reattach"):
        engine.attach(tab)
    assert asyncio.run(engine.plan_quantity(handle, stamp, 2)) is None
    core.set(tab, 1, CoreGate.CLOSING)
    assert not engine.close(handle)
    assert engine.context(handle) is None
    with pytest.raises(ValueError, match="closing control"):
        engine.attach(tab)
    core.set(tab, 2, CoreGate.CLOSING)
    assert engine.close(handle)
    with pytest.raises(ValueError, match="closing control"):
        engine.attach(tab)


def test_u01_policy_updates_in_place_and_invalid_reload_blocks_planning() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    initial = _policy()
    engine = create_engine(initial, browser, core)
    handle = engine.attach(tab)
    old_stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-1")
    browser.actual_source = old_stamp
    assert asyncio.run(engine.plan_quantity(handle, old_stamp, 2)) is not None

    initial_behavior = apply_current_policy_reload(None, _profile())
    blocked = apply_current_policy_reload(initial_behavior, '{"utiki":')
    assert engine.update_policy(make_effective_policy(blocked))
    assert engine.context(handle) is None
    assert asyncio.run(engine.plan_quantity(handle, old_stamp, 2)) is None

    changed = apply_current_policy_reload(blocked, _profile("其他票"))
    assert engine.update_policy(make_effective_policy(changed))
    new_stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-2")
    browser.actual_source = new_stamp
    assert asyncio.run(engine.plan_quantity(handle, old_stamp, 2)) is None
    planned = asyncio.run(engine.plan_quantity(handle, new_stamp, 2))
    assert isinstance(planned, QuantityPlan)
    assert planned.target_key == "OTHER"
    assert handle == engine.attach(tab)


def test_u01_wrong_observation_source_is_rejected_for_same_document() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-1")

    for source in (
        replace(stamp, tab=TabKey(1, "other-tab")),
        replace(stamp, session_generation=stamp.session_generation + 1),
    ):
        browser.actual_source = source
        assert asyncio.run(engine.plan_quantity(handle, stamp, 2)) is None
    assert browser.observed == [stamp, stamp]
    assert browser.commits == 0


def test_u01_stale_control_and_invalid_factory_input_fail_closed() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    tab = TabKey(1, "fake-core-tab")
    core.set(tab, 5, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    handle = engine.attach(tab)
    stamp = _stamp(engine, handle, browser.snapshot.document_id, "quantity-op-1")
    browser.actual_source = stamp

    core.set(tab, 1, CoreGate.PURCHASE_ALLOWED)
    assert not engine.update_control(handle)
    assert engine.context(handle) is None
    assert asyncio.run(engine.plan_quantity(handle, stamp, 2)) is None
    assert browser.observed == []

    with pytest.raises(ValueError, match="validated effective policy"):
        create_engine({"ctbc": {}}, browser, core)  # type: ignore[arg-type]


def test_one_run_rejects_second_tab_and_wrong_bound_adapter() -> None:
    browser = FakeBrowser(_snapshot())
    core = FakeCore()
    first = TabKey(1, "first")
    second = TabKey(1, "second")
    core.set(first, 1, CoreGate.PURCHASE_ALLOWED)
    core.set(second, 1, CoreGate.PURCHASE_ALLOWED)
    engine = create_engine(_policy(), browser, core)
    engine.attach(first)
    with pytest.raises(ValueError, match="one active tab"):
        engine.attach(second)

    browser.bound_tab = first
    another = create_engine(_policy(), browser, core)
    with pytest.raises(ValueError, match="another tab"):
        another.attach(second)


def test_missing_required_flow_port_fails_during_assembly() -> None:
    browser = FakeBrowser(_snapshot())
    complete_flow = FlowSettings(True, True, 2)
    with pytest.raises(ValueError, match="browser capability missing: observe_date"):
        create_engine(make_effective_policy(
            apply_current_policy_reload(None, _profile()), complete_flow,
        ), browser, FakeCore())
