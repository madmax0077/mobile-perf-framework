"""Test-facing helpers to record step timings and iteration markers."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from core.screenshots import ScreenshotRecorder
from core.tracing import DriverTracer
from models.telemetry_model import AppSignal, IterationMarker, StepTiming
from providers.base import DeviceProvider
from telemetry.crash_monitor import CrashMonitor
from telemetry.parsers.app_log import AppLogAnalyzer


def describe_exception(exc: BaseException, max_len: int = 500) -> str:
    """``Type: first non-empty message line`` (Selenium messages carry long stack traces)."""
    first = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
    return f"{type(exc).__name__}: {first}"[:max_len] if first else type(exc).__name__


class PerfRecorder:
    """Records user-journey step durations into the session telemetry.

    When a ``CrashMonitor`` is attached, the app is checked for crashes after
    every step (and always after a failed one), so crashes carry their step.
    When a ``ScreenshotRecorder`` is attached, the screen is captured as each
    step ends (outside the measured duration). A ``DriverTracer`` records which
    test / page-object methods and driver commands the step's time went to, and
    an ``AppLogAnalyzer`` extracts app-side evidence from the device log read by
    the crash check.

    Usage::

        with perf_recorder.step("login"):
            pages.login.login(user, password)

        # A step that mostly waits on purpose (optional prompt, fixed delay) is kept out
        # of the production latency budgets:
        with perf_recorder.step("biometrics_prompt", measure=False):
            pages.login.dismiss_biometrics_prompt()
    """

    def __init__(
        self,
        provider: DeviceProvider,
        crash_monitor: CrashMonitor | None = None,
        *,
        check_after_each_step: bool = True,
        screenshots: ScreenshotRecorder | None = None,
        tracer: DriverTracer | None = None,
        app_log: AppLogAnalyzer | None = None,
    ) -> None:
        self.provider = provider
        self.crash_monitor = crash_monitor
        self.check_after_each_step = check_after_each_step
        self.screenshots = screenshots
        self.tracer = tracer
        self.app_log = app_log
        self.steps: list[StepTiming] = []
        if tracer is not None and provider.driver is not None:
            tracer.install(provider.driver)

    @contextmanager
    def step(self, name: str, iteration: int | None = None, *, measure: bool = True) -> Iterator[None]:
        error: str | None = None
        if self.tracer is not None:
            self.tracer.start()
        start = time.perf_counter()
        try:
            yield
        except BaseException as exc:
            error = describe_exception(exc)
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            calls = self.tracer.stop() if self.tracer is not None else []
            stamp = self.provider.stamp("step")
            success = error is None
            shot = (
                self.screenshots.capture(name, failed=not success, repeating=iteration is not None)
                if self.screenshots is not None
                else None
            )
            signals: list[AppSignal] = []
            if self.crash_monitor is not None and (self.check_after_each_step or not success):
                self.crash_monitor.check(name, step_failed=not success)
                if self.app_log is not None:
                    signals = self.app_log.feed(self.crash_monitor.last_log)
            timing = StepTiming(
                name=name,
                duration_ms=duration_ms,
                iteration=iteration,
                success=success,
                error=error,
                screenshot=str(shot) if shot else None,
                measured=measure,
                calls=calls,
                app_signals=signals,
                **stamp,
            )
            self.steps.append(timing)
            self.provider.record(timing)

    def mark_iteration(self, iteration: int) -> None:
        self.provider.record(IterationMarker(iteration=iteration, **self.provider.stamp("iteration")))

    def durations(self, name: str) -> list[float]:
        return [s.duration_ms for s in self.steps if s.name == name]
