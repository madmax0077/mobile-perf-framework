"""Scroll smoothness (FPS / janky frames) of the signed-in home and Discover feeds.

Users judge an app's speed as much by scrolling as by load times. This test signs in,
then flings the home feed down and back up ``SCROLL_ROUNDS`` times (default 5), switches
to Discover (timed) and does the same there. Only the frame samples taken while
scrolling are evaluated, so the sign-in does not dilute the result.

Check: janky frames stay under the ``slow_frames`` benchmark limit (Android vitals: a
session with more than 25% slow frames is a slow session). When the provider collects no
frame data during the test (e.g. ``dumpsys gfxinfo`` blocked on the device), the check is
skipped and the run report still shows TestMu's post-session FPS.
"""

from __future__ import annotations

import os

import pytest

from core.analysis import summarize
from core.config import FrameworkConfig
from core.recorder import PerfRecorder
from models.telemetry_model import utcnow
from providers.base import DeviceProvider
from tests.perf_helpers import benchmark_limit, sign_in
from utils.logger import get_logger

SCROLL_ROUNDS = int(os.environ.get("SCROLL_ROUNDS", "5"))
FLINGS_PER_DIRECTION = 3

_log = get_logger("scroll")


def _scroll_feed(pages, perf_recorder: PerfRecorder, feed: str) -> None:
    for i in range(1, SCROLL_ROUNDS + 1):
        perf_recorder.mark_iteration(i)
        with perf_recorder.step(f"{feed}_scroll_down", iteration=i, measure=False):
            for _ in range(FLINGS_PER_DIRECTION):
                pages.home.swipe_feed("up")
        with perf_recorder.step(f"{feed}_scroll_up", iteration=i, measure=False):
            for _ in range(FLINGS_PER_DIRECTION):
                pages.home.swipe_feed("down")


@pytest.mark.journey
def test_home_and_discover_scroll_jank(
    login_credentials: tuple[str, str, str],
    pages,
    provider: DeviceProvider,
    perf_recorder: PerfRecorder,
    perf_config: FrameworkConfig,
) -> None:
    sign_in(pages, perf_recorder, login_credentials)
    started = utcnow()
    _scroll_feed(pages, perf_recorder, "home")
    with perf_recorder.step("open_discover"):
        pages.home.open_discover()
        pages.home.wait_discover_loaded()
    _scroll_feed(pages, perf_recorder, "discover")

    data = provider.get_metrics()
    window = data.model_copy(
        update={
            "fps": [s for s in data.fps if s.timestamp >= started],
            "cpu": [s for s in data.cpu if s.timestamp >= started],
            "memory": [s for s in data.memory if s.timestamp >= started],
        }
    )
    summary = summarize(window)
    _log.info(
        "While scrolling: avg fps %s, min fps %s, jank %s%%, frame drops %s, avg cpu %s%%",
        summary.avg_fps,
        summary.min_fps,
        summary.jank_pct,
        summary.frame_drops,
        summary.avg_cpu_pct,
    )
    if summary.jank_pct is None:
        _log.warning("No frame samples collected while scrolling; jank check skipped")
        return

    limit = benchmark_limit(perf_config, "slow_frames", 25)
    assert summary.jank_pct < limit, (
        f"{summary.jank_pct}% janky frames while scrolling (limit {limit}%), avg {summary.avg_fps} fps"
    )
