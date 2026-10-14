"""Signed-in launch performance: what a returning user actually waits for.

The startup benchmark times the launcher activity only. A returning user waits until the
signed-in home screen shows the wallet balance (session restore, token refresh, balance
API). These tests time exactly that, after a cold start and after a resume from the
background.

* ``test_cold_start_to_signed_in_home``: kill the app, relaunch it, wait for the balance.
  p90 must stay under the ``cold_start`` benchmark limit (Android vitals: 5 s).
* ``test_resume_from_background``: send the app to the background for
  ``RESUME_BACKGROUND_SEC`` seconds (default 30), bring it back, wait for the balance.
  p90 must stay under the ``warm_start`` limit (2 s): a resume is a hot start, but the
  measurement includes the Appium round trips, so the looser warm-start limit is used.

Both also check that the user is still signed in after every relaunch. A PIN or login
prompt instead of the balance fails the step (session not restored).

Iterations: ``--iterations N`` or ``STARTUP_ITERATIONS`` (default 5).
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from core.config import FrameworkConfig
from core.recorder import PerfRecorder
from providers.base import DeviceProvider
from tests.perf_helpers import benchmark_limit, p90, sign_in

BACKGROUND_SEC = float(os.environ.get("RESUME_BACKGROUND_SEC", "30"))


@pytest.mark.journey
def test_cold_start_to_signed_in_home(
    login_credentials: tuple[str, str, str],
    pages,
    driver: Any,
    provider: DeviceProvider,
    perf_recorder: PerfRecorder,
    perf_config: FrameworkConfig,
    startup_iterations: int,
) -> None:
    sign_in(pages, perf_recorder, login_credentials)
    app_id = provider.app_id
    assert app_id, "app id (ANDROID_APP_PACKAGE / IOS_BUNDLE_ID) is not configured"
    settle = perf_config.startup.settle_time_sec

    for i in range(1, startup_iterations + 1):
        perf_recorder.mark_iteration(i)
        driver.terminate_app(app_id)
        time.sleep(settle)
        with perf_recorder.step("cold_start_signed_in", iteration=i):
            driver.activate_app(app_id)
            pages.home.wait_balance_loaded()

    durations = perf_recorder.durations("cold_start_signed_in")
    assert len(durations) == startup_iterations
    limit = benchmark_limit(perf_config, "cold_start", 5_000)
    assert p90(durations) < limit, (
        f"signed-in cold start p90 {p90(durations):.0f} ms exceeds {limit:.0f} ms: {durations}"
    )


@pytest.mark.journey
def test_resume_from_background(
    login_credentials: tuple[str, str, str],
    pages,
    driver: Any,
    provider: DeviceProvider,
    perf_recorder: PerfRecorder,
    perf_config: FrameworkConfig,
    startup_iterations: int,
) -> None:
    sign_in(pages, perf_recorder, login_credentials)
    app_id = provider.app_id
    assert app_id, "app id (ANDROID_APP_PACKAGE / IOS_BUNDLE_ID) is not configured"

    for i in range(1, startup_iterations + 1):
        perf_recorder.mark_iteration(i)
        with perf_recorder.step("in_background", iteration=i, measure=False):
            driver.background_app(-1)
            time.sleep(BACKGROUND_SEC)
        with perf_recorder.step("resume_signed_in", iteration=i):
            driver.activate_app(app_id)
            pages.home.wait_balance_loaded()

    durations = perf_recorder.durations("resume_signed_in")
    assert len(durations) == startup_iterations
    limit = benchmark_limit(perf_config, "warm_start", 2_000)
    assert p90(durations) < limit, (
        f"resume after {BACKGROUND_SEC:.0f}s in background: p90 {p90(durations):.0f} ms "
        f"exceeds {limit:.0f} ms: {durations}"
    )
