"""Startup benchmark: cold / warm / hot launches over N iterations with averages."""

from __future__ import annotations

import pytest

from core.config import FrameworkConfig
from telemetry.startup_collector import StartupCollector


@pytest.mark.startup
def test_startup_benchmark(
    startup_profiler: StartupCollector, perf_config: FrameworkConfig, startup_iterations: int
) -> None:
    results = startup_profiler.benchmark(
        perf_config.startup.modes, startup_iterations, warmup=perf_config.startup.warmup_iterations
    )
    averages = StartupCollector.averages(results)

    for mode, samples in results.items():
        assert len(samples) == startup_iterations, f"missing {mode.value} iterations"
        assert all(s.total_time_ms > 0 for s in samples)

    cold_limit = perf_config.scoring.thresholds["startup"].limit
    assert averages["cold"] < cold_limit, f"cold start {averages['cold']} ms exceeds {cold_limit} ms"
    if "warm" in averages:
        assert averages["warm"] <= averages["cold"] * 1.1, "warm start should not be slower than cold start"


@pytest.mark.startup(iterations=3)
def test_cold_start_sla(
    startup_profiler: StartupCollector, perf_config: FrameworkConfig, startup_iterations: int
) -> None:
    results = startup_profiler.benchmark(["cold"], startup_iterations)
    target = perf_config.scoring.thresholds["startup"].target
    average = StartupCollector.averages(results)["cold"]
    assert average < target * 2, f"cold start average {average} ms is above 2x target ({target} ms)"
