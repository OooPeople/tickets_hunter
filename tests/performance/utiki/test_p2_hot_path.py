"""P2 source hot-path probes keep their failures causal and local."""

import copy
import dataclasses
import hashlib
import json

import pytest

from tools.benchmarks import utiki_p2_hot_path


def test_v07_unchanged_policy_uses_current_engine_without_reparsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)
    assert legal["v07"]["latency"]["sample_count"] == 2
    assert set(legal["v07"]["forbidden_calls"].values()) == {0}

    from platforms.utiki.application.engine import UtikiEngine
    import utiki_config

    original = UtikiEngine.context
    parse_alias = utiki_config.parse_profile_json

    def reparse(self: UtikiEngine, handle: object):
        parse_alias('{"utiki": {}}')
        return original(self, handle)  # type: ignore[arg-type]

    monkeypatch.setattr(UtikiEngine, "context", reparse)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="parse_profile_json"):
        utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)


@pytest.mark.parametrize("operation", ["json.loads", "hash", "hashlib.new", "convert_profile_mapping"])
def test_v07_rejects_direct_policy_reprocessing_aliases(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)
    assert set(legal["v07"]["forbidden_calls"].values()) == {0}

    from platforms.utiki.application.engine import UtikiEngine
    from tools.migrations.convert_utiki_config import convert_profile_mapping
    import utiki_config

    original = UtikiEngine.context
    json_alias = json.loads
    hash_alias = hash
    digest_alias = hashlib.new
    migration_alias = convert_profile_mapping

    def reprocess(self: UtikiEngine, handle: object):
        if operation == "json.loads":
            json_alias("{}")
        elif operation == "hash":
            hash_alias("unchanged-policy")
        elif operation == "hashlib.new":
            digest_alias("sha256", b"unchanged-policy")
        else:
            migration_alias({"utiki": dataclasses.asdict(utiki_config.DEFAULT_BEHAVIOR)})
        return original(self, handle)  # type: ignore[arg-type]

    monkeypatch.setattr(UtikiEngine, "context", reprocess)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match=operation.replace(".", r"\.")):
        utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)


def test_v07_rejects_indirect_json_parse_in_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)
    assert legal["v07"]["forbidden_calls"]["json.loads"] == 0

    from platforms.utiki.application.engine import UtikiEngine

    original = UtikiEngine.context
    json_alias = json.loads

    def helper() -> None:
        json_alias('{"utiki": {}}')

    def reprocess(self: UtikiEngine, handle: object):
        helper()
        return original(self, handle)  # type: ignore[arg-type]

    monkeypatch.setattr(UtikiEngine, "context", reprocess)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match=r"json\.loads"):
        utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)


def test_v08_large_seat_tick_rejects_deep_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)
    assert legal["v08"]["valid_latency"]["sample_count"] == 2
    assert legal["v08"]["invalid_latency"]["sample_count"] == 2
    assert legal["v08"]["max_reference_delta"] == 0
    assert legal["v08"]["pending_tasks_before"] == legal["v08"]["pending_tasks_after"]

    from platforms.utiki import selection

    original = selection.select_seat_snapshot
    copy_alias = copy.deepcopy

    def copy_first(snapshot: object, *args: object, **kwargs: object):
        copy_alias(snapshot)
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", copy_first)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="deepcopy"):
        utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)


def test_v08_rejects_discarded_full_seat_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)
    assert legal["v08"]["forbidden_calls"]["SeatRow.__init__"] == 0

    from platforms.utiki import selection

    original = selection.select_seat_snapshot
    replace_alias = dataclasses.replace

    def reconstruct(snapshot: object, *args: object, **kwargs: object):
        tuple(replace_alias(seat) for seat in snapshot.seats)
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", reconstruct)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match=r"SeatRow\.__init__"):
        utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)


def test_v08_rejects_discarded_shallow_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legal = utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)
    assert legal["v08"]["forbidden_calls"]["copy"] == 0

    from platforms.utiki import selection

    original = selection.select_seat_snapshot
    copy_alias = copy.copy

    def reconstruct(snapshot: object, *args: object, **kwargs: object):
        tuple(copy_alias(seat) for seat in snapshot.seats)
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", reconstruct)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match=r"copy"):
        utiki_p2_hot_path.measure_hot_path(samples=1, warmup=0, seat_count=100)


def test_v07_rejects_reparse_only_on_last_measured_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platforms.utiki.application.engine import UtikiEngine
    import utiki_config

    original = UtikiEngine.context
    parse_alias = utiki_config.parse_profile_json
    calls = 0

    def late_reparse(self: UtikiEngine, handle: object):
        nonlocal calls
        calls += 1
        if calls == 9:
            parse_alias('{"utiki": {}}')
        return original(self, handle)  # type: ignore[arg-type]

    monkeypatch.setattr(UtikiEngine, "context", late_reparse)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="parse_profile_json"):
        utiki_p2_hot_path.measure_hot_path(samples=3, warmup=1, seat_count=100)
    assert calls == 9


def test_v08_rejects_copy_only_on_late_measured_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platforms.utiki import selection

    original = selection.select_seat_snapshot
    copy_alias = copy.deepcopy
    calls = 0

    def late_copy(snapshot: object, *args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 7:
            copy_alias(snapshot)
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", late_copy)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="deepcopy"):
        utiki_p2_hot_path.measure_hot_path(samples=3, warmup=1, seat_count=100)
    assert calls == 8


def test_v08_rejects_retained_nested_snapshot_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platforms.utiki import selection

    original = selection.select_seat_snapshot
    retained: list[object] = []

    def retain_seats(snapshot: object, *args: object, **kwargs: object):
        retained.append(snapshot.seats)
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", retain_seats)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="subgraph"):
        utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)


def test_v08_rejects_retained_pending_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from platforms.utiki import selection

    original = selection.select_seat_snapshot

    def retain_task(snapshot: object, *args: object, **kwargs: object):
        asyncio.create_task(asyncio.sleep(60))
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(selection, "select_seat_snapshot", retain_task)
    with pytest.raises(utiki_p2_hot_path.HotPathError, match="pending tasks"):
        utiki_p2_hot_path.measure_hot_path(samples=2, warmup=1, seat_count=100)
