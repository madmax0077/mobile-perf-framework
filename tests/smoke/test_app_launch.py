"""Smoke test: the app under test installs, launches and reaches the foreground."""

from __future__ import annotations

from typing import Any

import pytest

from models.execution_model import Platform
from models.telemetry_model import LaunchMode
from providers.base import DeviceProvider
from telemetry.startup_collector import StartupCollector
from utils.logger import get_logger

RUNNING_IN_FOREGROUND = 4

_log = get_logger("smoke")


@pytest.mark.startup(telemetry=False)
def test_app_launch(driver: Any, provider: DeviceProvider, startup_profiler: StartupCollector) -> None:
    app_id = provider.app_id
    assert app_id, "app id (ANDROID_APP_PACKAGE / IOS_BUNDLE_ID) is not configured"
    assert driver.query_app_state(app_id) == RUNNING_IN_FOREGROUND, f"{app_id} is not in the foreground"
    if provider.platform == Platform.ANDROID and hasattr(driver, "current_activity"):
        _log.info("Launched %s, current activity: %s", app_id, driver.current_activity)

    cold = startup_profiler.measure(LaunchMode.COLD)

    assert cold.total_time_ms > 0
    assert driver.query_app_state(app_id) == RUNNING_IN_FOREGROUND, f"{app_id} did not relaunch"
