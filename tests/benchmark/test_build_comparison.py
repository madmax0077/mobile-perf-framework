"""Build A vs Build B: same workload on both builds, then % differences.

Configure the builds with ``BUILD_A_APP`` / ``BUILD_B_APP`` (paths, lt:// ids or
URLs). With the simulated provider the build profiles in framework.yaml are used.
"""

from __future__ import annotations

import time

import pytest

from core.comparison import headline
from core.config import FrameworkConfig
from core.pytest_plugin import BuildSessionFactory
from pages import Pages


def _workload(session, factory: BuildSessionFactory, config: FrameworkConfig, iterations: int) -> None:
    profiler = factory.startup_profiler(session)
    profiler.benchmark(["cold", "warm"], iterations, warmup=config.startup.warmup_iterations)
    pages = Pages(session.driver, config.locators, session.platform)
    for _ in range(3):
        pages.search.search(config.app.test_data["search_term"])
        pages.search.open_first_result()
        session.driver.back()
    time.sleep(2 * config.telemetry.sampling_interval_sec)


@pytest.mark.benchmark
def test_build_a_vs_build_b(
    session_factory: BuildSessionFactory, perf_config: FrameworkConfig, startup_iterations: int
) -> None:
    for build in ("a", "b"):
        with session_factory.open_build(build) as session:
            _workload(session, session_factory, perf_config, startup_iterations)

    result = session_factory.compare("a", "b")
    diffs = headline(result)
    print(f"\nBuild comparison {result.candidate_label} vs {result.baseline_label}: {diffs}")

    assert diffs["Startup"] is not None, "startup difference could not be computed"
    if perf_config.comparison.fail_on_regression:
        regressions = [d.metric for d in result.deltas if d.regression]
        assert not regressions, f"regressions vs {result.baseline_label}: {regressions}"
