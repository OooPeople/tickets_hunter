import asyncio
import socket

import pytest

from tools.benchmarks import bench_utiki


class FakeClock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value

    def advance(self, amount):
        self.value += amount


def test_synthetic_cases_are_deterministic_and_produce_plans(monkeypatch):
    def reject_network(*_args, **_kwargs):
        raise AssertionError("offline source benchmark attempted network access")

    monkeypatch.setattr(socket, "socket", reject_network)
    first = bench_utiki.build_synthetic_fixtures()
    second = bench_utiki.build_synthetic_fixtures()
    assert bench_utiki._sha256_json(first) == bench_utiki._sha256_json(second)

    selection = bench_utiki._load_selection_module()
    config = bench_utiki.build_benchmark_config()
    digests = {
        case: bench_utiki._sha256_json(bench_utiki._jsonable(
            bench_utiki.execute_case(selection, case, first[case], config)
        ))
        for case in bench_utiki.CASES
    }
    assert set(digests) == set(bench_utiki.CASES)
    assert all(len(value) == 64 for value in digests.values())


def test_measure_operation_retains_raw_fake_clock_samples():
    clock = FakeClock()

    def operation():
        clock.advance(250)

    metric = bench_utiki.measure_operation(
        operation,
        samples=4,
        warmup=1,
        batches=2,
        clock_ns=clock,
    )
    assert metric["samples_ns"] == [250, 250, 250, 250]
    assert metric["batch_sizes"] == [2, 2]
    assert metric["median_ns"] == 250
    assert metric["p95_ns"] == 250
    assert metric["p99_ns"] == 250
    assert metric["failure_count"] == 0


def test_event_loop_lag_supports_a_deterministic_scheduler():
    clock = FakeClock()

    def schedule(callback):
        clock.advance(125)
        callback()

    metric = asyncio.run(bench_utiki.measure_event_loop_lag(
        samples=3,
        warmup=1,
        batches=1,
        clock_ns=clock,
        schedule=schedule,
    ))
    assert metric["samples_ns"] == [125, 125, 125]
    assert metric["median_ns"] == 125


def test_small_report_records_trust_roots_and_blocked_browser_metrics():
    report = bench_utiki.run_benchmark(
        samples=2,
        warmup=0,
        batches=1,
        cold_samples=1,
        lag_samples=2,
        run_id="test-run",
        sizes={
            "date_rows": 2,
            "area_rows": 2,
            "seat_count": 4,
            "quantity_rows": 2,
        },
    )
    assert report["run"]["run_id"] == "test-run"
    assert report["run"]["scope"] == "offline-source-only"
    assert report["run"]["mode"] == "all"
    assert report["run"]["hard_egress_isolation"] is False
    assert report["run"]["hardware"]["logical_cpu_count"]
    assert report["run"]["git"]["candidate_sha"]
    assert isinstance(report["run"]["git"]["dirty"], bool)
    assert set(report["case_results"]) == set(bench_utiki.CASES)
    assert report["metrics"]["seat.warm_parse_select_plan"]["sample_count"] == 2
    assert report["metrics"]["event_loop.scheduling_lag"]["sample_count"] == 2
    assert all(
        item["status"] == "BLOCKED"
        for item in report["blocked_metrics"].values()
    )
    assert len(report["digests"]["fixture_sha256"]) == 64
    assert len(report["digests"]["config_sha256"]) == 64
    assert len(report["digests"]["script_sha256"]) == 64
    assert report["subject_files"]
    assert "src/platforms/utiki/selection.py" in report["subject_files"]


def test_plan_cli_mode_and_seed_contract_is_accepted():
    args = bench_utiki._parse_args([
        "--mode", "warm", "--samples", "300", "--seed", "1701"
    ])
    assert args.mode == "warm"
    assert args.samples == 300
    assert args.seed == 1701


def test_warm_mode_omits_cold_metrics():
    report = bench_utiki.run_benchmark(
        samples=2,
        warmup=0,
        batches=1,
        cold_samples=1,
        lag_samples=2,
        mode="warm",
        seed=1701,
        sizes={
            "date_rows": 2,
            "area_rows": 2,
            "seat_count": 4,
            "quantity_rows": 2,
        },
    )
    assert report["parameters"]["mode"] == "warm"
    assert all(".cold_" not in name for name in report["metrics"])


@pytest.mark.parametrize("key", sorted(bench_utiki.DEFAULT_SIZES))
def test_fixture_sizes_reject_contract_overflow(key):
    sizes = dict(bench_utiki.DEFAULT_SIZES)
    limits = {
        "date_rows": 200,
        "area_rows": 200,
        "seat_count": 5000,
        "quantity_rows": 100,
    }
    sizes[key] = limits[key] + 1
    with pytest.raises(ValueError):
        bench_utiki.build_synthetic_fixtures(sizes)
