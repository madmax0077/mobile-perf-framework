"""Navigation endurance: open and close the three H5 mini programs again and again.

Every mini program open creates a WebView; a WebView (or JS bridge / listener) that is not
released when the user goes back is the most common memory leak in super-app style
clients. This test signs in once, then for N iterations goes
Home -> Cards & accounts -> Home -> Offers -> Home -> Orders -> Home, and finally leaves the
app idle for ``IDLE_AFTER_SEC`` seconds so the report shows whether memory is released.

The framework's endurance analysis then flags:

* memory leak: PSS regression slope (MB/min) and first-vs-last window growth;
* CPU growth across the run;
* response degradation: a step that gets slower from the first to the last iterations.

Findings are reported; the test fails on them only with ``PERF_FAIL_ON_LEAK=true``.
Crashes / ANRs fail it as usual. Iterations: ``--iterations N`` (default 20 here).

Runs without TestMu network capture / app profiling because Offers and Orders need that.
"""

from __future__ import annotations

import os
import time

import pytest

from core.analysis import analyze
from core.config import FrameworkConfig
from core.recorder import PerfRecorder
from providers.base import DeviceProvider
from tests.perf_helpers import sign_in
from utils.logger import get_logger

IDLE_AFTER_SEC = float(os.environ.get("IDLE_AFTER_SEC", "15"))

_log = get_logger("endurance")


@pytest.mark.endurance(iterations=20)
@pytest.mark.provider_options(testmu={"lt_options": {"network": False, "appProfiling": False}})
def test_mini_program_navigation_endurance(
    login_credentials: tuple[str, str, str],
    pages,
    provider: DeviceProvider,
    perf_recorder: PerfRecorder,
    perf_config: FrameworkConfig,
    endurance_iterations: int,
) -> None:
    sign_in(pages, perf_recorder, login_credentials)
    for i in range(1, endurance_iterations + 1):
        perf_recorder.mark_iteration(i)
        with perf_recorder.step("open_cards_and_accounts", iteration=i):
            pages.home.open_cards_and_accounts()
            pages.cards.wait_loaded()
        with perf_recorder.step("home_from_cards", iteration=i):
            pages.home.go_home()
        with perf_recorder.step("open_offers", iteration=i):
            pages.home.open_offers()
            pages.offers.wait_loaded()
        with perf_recorder.step("home_from_offers", iteration=i):
            pages.home.go_home()
        with perf_recorder.step("open_orders", iteration=i):
            pages.home.open_orders()
            pages.orders.wait_loaded()
        with perf_recorder.step("home_from_orders", iteration=i):
            pages.home.go_home()

    with perf_recorder.step("idle_on_home", measure=False):
        time.sleep(IDLE_AFTER_SEC)

    assert len(perf_recorder.durations("open_offers")) == endurance_iterations

    findings = analyze(provider.get_metrics(), perf_config.endurance)
    for finding in findings:
        _log.info("%s detected=%s: %s", finding.kind, finding.detected, finding.message)
    detected = [f.message for f in findings if f.detected]
    if perf_config.endurance.fail_on_leak:
        assert not detected, f"endurance regressions detected: {detected}"
