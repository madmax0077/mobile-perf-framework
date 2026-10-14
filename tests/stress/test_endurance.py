"""Endurance test: repeat a flow N times and detect memory leaks, CPU growth and
response degradation.

Iteration profiles come from ``ENDURANCE_PROFILES`` (default ``50``), e.g.::

    ENDURANCE_PROFILES=50,100,500 pytest tests/stress
"""

from __future__ import annotations

import os

import pytest

from core.analysis import analyze
from core.config import FrameworkConfig
from core.recorder import PerfRecorder

PROFILES = [int(n) for n in os.environ.get("ENDURANCE_PROFILES", "50").split(",") if n.strip()]


@pytest.mark.endurance
@pytest.mark.parametrize("iterations", PROFILES, ids=lambda n: f"{n}x")
def test_search_endurance(
    iterations: int, pages, driver, provider, perf_recorder: PerfRecorder, perf_config: FrameworkConfig
) -> None:
    term = perf_config.app.test_data["search_term"]
    for i in range(1, iterations + 1):
        perf_recorder.mark_iteration(i)
        with perf_recorder.step("search", iteration=i):
            pages.search.search(term)
        with perf_recorder.step("open_product", iteration=i):
            pages.search.open_first_result()
        driver.back()

    assert len(perf_recorder.durations("search")) == iterations

    findings = analyze(provider.get_metrics(), perf_config.endurance)
    detected = [f.message for f in findings if f.detected]
    if perf_config.endurance.fail_on_leak:
        assert not detected, f"endurance regressions detected: {detected}"
