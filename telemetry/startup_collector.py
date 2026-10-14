"""Startup measurement: cold / warm / hot launches via pluggable launch strategies.

* ``AdbLaunchStrategy``   - ``am start -W`` (TotalTime / WaitTime / LaunchState)
                            plus logcat ``Displayed`` time.
* ``DriverLaunchStrategy`` - Appium ``terminate_app`` / ``activate_app`` timing
                            (works on any cloud, lower precision).
* ``IOSBackendLaunchStrategy`` - delegates to the configured iOS backend.
* ``FallbackLaunchStrategy`` - chain of the above; unsupported strategies are
                            dropped at the first ``CommandNotSupportedError``.
"""

from __future__ import annotations

import contextlib
import statistics
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from models.telemetry_model import LaunchMode, StartupMetrics, TelemetrySample
from telemetry.executors import CommandError, CommandExecutor, CommandNotSupportedError
from telemetry.parsers.android import parse_am_start, parse_displayed_time
from utils.logger import get_logger

if TYPE_CHECKING:
    from telemetry.ios.backend import IOSMetricsBackend

_log = get_logger("startup")


@dataclass(slots=True)
class LaunchResult:
    total_time_ms: float
    wait_time_ms: float | None = None
    display_time_ms: float | None = None
    launch_state: str | None = None
    method: str = "unknown"


class LaunchStrategy(ABC):
    name: str = "abstract"

    @abstractmethod
    def prepare(self, mode: LaunchMode) -> None:
        """Bring the app into the state required for ``mode``."""

    @abstractmethod
    def launch(self, mode: LaunchMode) -> LaunchResult:
        """Launch the app and return timing."""

    def measure(self, mode: LaunchMode) -> LaunchResult:
        self.prepare(mode)
        return self.launch(mode)


class AdbLaunchStrategy(LaunchStrategy):
    name = "adb"

    def __init__(
        self, executor: CommandExecutor, package: str, activity: str, *, pause_sec: float = 1.0
    ) -> None:
        self.executor = executor
        self.package = package
        self.pause_sec = pause_sec
        self.component = activity if "/" in activity else f"{package}/{activity}"

    def _shell(self, command: str) -> str:
        return self.executor.shell(command, timeout=60)

    def prepare(self, mode: LaunchMode) -> None:
        if mode == LaunchMode.COLD:
            self._shell(f"am force-stop {self.package}")
            return
        self._shell(f"am start -W -n {self.component}")
        time.sleep(self.pause_sec)
        if mode == LaunchMode.WARM:
            # BACK destroys the root activity but keeps the process (pre-Android 12);
            # on 12+ the OS may report HOT - the actual LaunchState is recorded.
            self._shell("input keyevent 4")
            self._shell("input keyevent 4")
        else:
            self._shell("input keyevent 3")
        time.sleep(self.pause_sec)

    def launch(self, mode: LaunchMode) -> LaunchResult:
        with contextlib.suppress(CommandError):
            self._shell("logcat -c")
        result = parse_am_start(self._shell(f"am start -W -n {self.component}"))
        display: float | None = None
        try:
            log = self._shell("logcat -d -s ActivityTaskManager:I ActivityManager:I")
            display = parse_displayed_time(log, self.package)
        except CommandError:
            pass
        return LaunchResult(
            total_time_ms=result.total_time_ms,
            wait_time_ms=result.wait_time_ms,
            display_time_ms=display,
            launch_state=result.launch_state,
            method=self.name,
        )


class DriverLaunchStrategy(LaunchStrategy):
    name = "appium"

    def __init__(
        self,
        driver: Any,
        app_id: str,
        ready_check: Callable[[], Any] | None = None,
        *,
        pause_sec: float = 1.0,
    ) -> None:
        self.driver = driver
        self.app_id = app_id
        self.ready_check = ready_check
        self.pause_sec = pause_sec

    def prepare(self, mode: LaunchMode) -> None:
        if mode == LaunchMode.COLD:
            self.driver.terminate_app(self.app_id)
            return
        self.driver.activate_app(self.app_id)
        time.sleep(self.pause_sec)
        if mode == LaunchMode.WARM:
            self.driver.back()
        else:
            self.driver.background_app(-1)
        time.sleep(self.pause_sec)

    def launch(self, mode: LaunchMode) -> LaunchResult:
        start = time.perf_counter()
        self.driver.activate_app(self.app_id)
        if self.ready_check is not None:
            self.ready_check()
        elapsed = (time.perf_counter() - start) * 1000.0
        return LaunchResult(
            total_time_ms=round(elapsed, 1), launch_state=mode.value.upper(), method=self.name
        )


class IOSBackendLaunchStrategy(LaunchStrategy):
    name = "ios-backend"

    def __init__(self, backend: IOSMetricsBackend, bundle_id: str) -> None:
        self.backend = backend
        self.bundle_id = bundle_id

    def prepare(self, mode: LaunchMode) -> None:
        self.backend.prepare_launch(mode)

    def launch(self, mode: LaunchMode) -> LaunchResult:
        total = self.backend.measure_launch(mode)
        if total is None:
            raise CommandNotSupportedError(f"{self.backend.name} cannot measure launch time")
        return LaunchResult(total_time_ms=total, launch_state=mode.value.upper(), method=self.backend.name)


class FallbackLaunchStrategy(LaunchStrategy):
    name = "fallback"

    def __init__(self, strategies: list[LaunchStrategy]) -> None:
        if not strategies:
            raise ValueError("at least one launch strategy is required")
        self.strategies = list(strategies)

    def prepare(self, mode: LaunchMode) -> None:  # pragma: no cover - measure() is overridden
        self.strategies[0].prepare(mode)

    def launch(self, mode: LaunchMode) -> LaunchResult:  # pragma: no cover
        return self.strategies[0].launch(mode)

    def measure(self, mode: LaunchMode) -> LaunchResult:
        while True:
            strategy = self.strategies[0]
            try:
                return strategy.measure(mode)
            except (CommandNotSupportedError, NotImplementedError) as exc:
                if len(self.strategies) == 1:
                    raise
                _log.info("Launch strategy %s unsupported (%s); falling back", strategy.name, exc)
                self.strategies.pop(0)


class StartupCollector:
    """Runs startup iterations and records ``StartupMetrics`` samples."""

    def __init__(
        self,
        strategy: LaunchStrategy,
        *,
        recorder: Callable[[TelemetrySample], None] | None = None,
        settle_time_sec: float = 2.0,
        stamp: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.strategy = strategy
        self.recorder = recorder
        self.settle_time_sec = settle_time_sec
        self._stamp = stamp or (lambda source: {"source": source})
        self.results: list[StartupMetrics] = []

    def measure(self, mode: LaunchMode | str, iteration: int = 1, *, record: bool = True) -> StartupMetrics:
        mode = LaunchMode(mode)
        result = self.strategy.measure(mode)
        metrics = StartupMetrics(
            mode=mode,
            iteration=iteration,
            total_time_ms=result.total_time_ms,
            wait_time_ms=result.wait_time_ms,
            display_time_ms=result.display_time_ms,
            launch_state=result.launch_state,
            method=result.method,
            **self._stamp(result.method),
        )
        _log.info(
            "%s start #%d: %.0f ms (state=%s, method=%s)",
            mode.value,
            iteration,
            metrics.total_time_ms,
            metrics.launch_state,
            metrics.method,
        )
        if record:
            self.results.append(metrics)
            if self.recorder is not None:
                self.recorder(metrics)
        time.sleep(self.settle_time_sec)
        return metrics

    def benchmark(
        self, modes: list[str] | list[LaunchMode], iterations: int, warmup: int = 0
    ) -> dict[LaunchMode, list[StartupMetrics]]:
        results: dict[LaunchMode, list[StartupMetrics]] = {}
        for raw_mode in modes:
            mode = LaunchMode(raw_mode)
            for i in range(warmup):
                self.measure(mode, -(i + 1), record=False)
            results[mode] = [self.measure(mode, i + 1) for i in range(iterations)]
        return results

    @staticmethod
    def averages(results: dict[LaunchMode, list[StartupMetrics]]) -> dict[str, float]:
        return {
            mode.value: round(statistics.fmean(m.total_time_ms for m in samples), 1)
            for mode, samples in results.items()
            if samples
        }
