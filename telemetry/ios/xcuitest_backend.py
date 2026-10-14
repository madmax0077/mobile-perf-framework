"""XCUITest ``mobile:`` API backend - portable across local and cloud devices."""

from __future__ import annotations

import threading
import time

from models.telemetry_model import LaunchMode
from telemetry.ios.backend import IOSMetricsBackend, register_ios_backend


@register_ios_backend("xcuitest")
class XCUITestMetricsBackend(IOSMetricsBackend):
    """Battery level and launch timing via Appium. CPU / memory are not exposed by
    XCUITest - combine with ``instruments`` / ``xctrace`` or a cloud profiler."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()

    def _exec(self, script: str, args: dict | None = None):  # type: ignore[no-untyped-def]
        if self.driver is None:
            raise NotImplementedError(f"{self.name} requires an Appium driver")
        with self._lock:
            return self.driver.execute_script(script, args or {})

    def battery_level(self) -> float | None:
        try:
            info = self._exec("mobile: batteryInfo") or {}
        except Exception as exc:
            self.log.debug("batteryInfo failed: %s", exc)
            return None
        level = info.get("level")
        return round(float(level) * 100.0, 2) if level is not None and level >= 0 else None

    def prepare_launch(self, mode: LaunchMode) -> None:
        if mode == LaunchMode.COLD:
            self._exec("mobile: terminateApp", {"bundleId": self.bundle_id})
        else:
            self._exec("mobile: activateApp", {"bundleId": self.bundle_id})
            time.sleep(1.0)
            self._exec("mobile: backgroundApp", {"seconds": -1})
        time.sleep(1.0)

    def measure_launch(self, mode: LaunchMode) -> float | None:
        script = "mobile: launchApp" if mode == LaunchMode.COLD else "mobile: activateApp"
        start = time.perf_counter()
        self._exec(script, {"bundleId": self.bundle_id})
        return round((time.perf_counter() - start) * 1000.0, 1)
