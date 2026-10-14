"""Detects app crashes / ANRs during a test and collects them for reporting.

Signals, in order of precision:

1. Live device log (Android logcat via Appium) - checked after each step.
2. App state - an app that is no longer running after a failed step.
3. Provider crash reports fetched after the session (``merge``), e.g. TestMu
   ``devicelog`` / ``crashlog`` and app-profiling crash counters.

Events are de-duplicated by signature, so a crash seen live and again in the
post-session device log is reported once.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import TYPE_CHECKING

from models.telemetry_model import CrashEvent, CrashType, StepTiming
from telemetry.parsers.crash import parse_logcat_crashes
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.config import CrashSettings
    from providers.base import DeviceProvider

_log = get_logger("crash")

APP_NOT_RUNNING = 1
_STEP_TOLERANCE = timedelta(seconds=2)


class AppCrashError(AssertionError):
    """Raised when the app crashed during a test that would otherwise pass."""


class CrashMonitor:
    def __init__(self, provider: DeviceProvider, settings: CrashSettings) -> None:
        self.provider = provider
        self.settings = settings
        self.events: list[CrashEvent] = []
        self.last_log = ""
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    @property
    def package(self) -> str:
        return self.provider.app_id or ""

    def check(self, step: str | None = None, *, step_failed: bool = False) -> list[CrashEvent]:
        """Look for crashes since the previous check; returns newly found events."""
        found: list[CrashEvent] = []
        self.last_log = ""
        if self.settings.poll_device_log:
            text = self.provider.read_device_log()
            self.last_log = text or ""
            if text:
                found.extend(
                    parse_logcat_crashes(text, self.package, max_lines=self.settings.max_stacktrace_lines)
                )
        if step_failed and not found and self.provider.app_state() == APP_NOT_RUNNING:
            found.append(
                CrashEvent(
                    crash_type=CrashType.APP_DIED,
                    process=self.package or None,
                    exception=f"{self.package or 'app'} stopped running unexpectedly",
                    source="app_state",
                )
            )
        return self._add(found, step)

    def merge(self, events: list[CrashEvent]) -> list[CrashEvent]:
        """Add post-session crash reports; they supersede bare 'app died' markers."""
        added = self._add(events, None)
        if any(e.crash_type != CrashType.APP_DIED for e in added):
            with self._lock:
                placeholders = [e for e in self.events if e.crash_type == CrashType.APP_DIED]
                for marker in placeholders:
                    target = next((e for e in added if e.step is None), None)
                    if target is not None:
                        target.step = marker.step
                    self.events.remove(marker)
        return added

    def attribute_steps(self, steps: list[StepTiming]) -> None:
        """Assign crashes without a step to the step whose time window contains them."""
        for event in self.events:
            if event.step is not None:
                continue
            for step in steps:
                start = step.timestamp - timedelta(milliseconds=step.duration_ms)
                if start - _STEP_TOLERANCE <= event.timestamp <= step.timestamp + _STEP_TOLERANCE:
                    event.step = step.name
                    break

    def describe(self) -> str:
        parts = []
        for event in self.events:
            where = f" (step: {event.step})" if event.step else ""
            parts.append(f"{event.crash_type.value}: {event.exception or 'unknown'}{where}")
        return f"{len(self.events)} app crash event(s) detected - " + "; ".join(parts)

    def _add(self, events: list[CrashEvent], step: str | None) -> list[CrashEvent]:
        added: list[CrashEvent] = []
        with self._lock:
            for event in events:
                if event.signature in self._seen:
                    continue
                self._seen.add(event.signature)
                if event.step is None:
                    event.step = step
                self.events.append(event)
                added.append(event)
        for event in added:
            _log.warning(
                "App %s detected%s: %s",
                event.crash_type.value,
                f" in step '{event.step}'" if event.step else "",
                event.exception,
            )
        return added
