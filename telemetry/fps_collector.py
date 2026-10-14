"""FPS / jank collector: windowed ``dumpsys gfxinfo <pkg> reset`` / iOS backend."""

from __future__ import annotations

import time

from models.telemetry_model import FpsSnapshot, MetricKind, TelemetrySample
from telemetry.base_collector import BaseCollector, CollectorUnavailableError, register_collector
from telemetry.executors import CommandError, CommandNotSupportedError
from telemetry.parsers.android import ParseError, parse_gfxinfo

_MIN_FRAMES_PER_WINDOW = 2


@register_collector("fps")
class FPSCollector(BaseCollector):
    """Each sample covers the window since the previous sample.

    FPS = frames rendered / window, capped at the display refresh rate. Idle
    windows (the UI did not draw) are skipped instead of reported as 0 FPS.
    """

    kind = MetricKind.FPS

    def on_start(self) -> None:
        self._window_start = time.monotonic()
        if self.is_android:
            try:
                self.require_executor().shell(f"dumpsys gfxinfo {self.context.app_id} reset")
            except CommandNotSupportedError as exc:
                raise CollectorUnavailableError(str(exc)) from exc
            except CommandError as exc:
                self.log.debug("gfxinfo reset failed: %s", exc)

    @property
    def refresh_rate(self) -> int:
        return self.context.settings.refresh_rate_hz if self.context.settings else 60

    def collect(self) -> list[TelemetrySample]:
        if not self.is_android:
            backend = self.require_ios_backend()
            fps = backend.fps()
            return [FpsSnapshot(fps=fps, **self.context.stamp(backend.name))] if fps is not None else []

        executor = self.require_executor()
        now = time.monotonic()
        window = max(now - self._window_start, 1e-3)
        try:
            out = executor.shell(f"dumpsys gfxinfo {self.context.app_id} reset")
        except CommandNotSupportedError as exc:
            raise CollectorUnavailableError(str(exc)) from exc
        self._window_start = now
        try:
            info = parse_gfxinfo(out)
        except ParseError:
            return []
        if info.total_frames < _MIN_FRAMES_PER_WINDOW:
            return []
        return [
            FpsSnapshot(
                fps=round(min(float(self.refresh_rate), info.total_frames / window), 2),
                total_frames=info.total_frames,
                janky_frames=info.janky_frames,
                jank_pct=info.jank_pct,
                frame_drops=info.frame_drops,
                p50_ms=info.p50_ms,
                p90_ms=info.p90_ms,
                p95_ms=info.p95_ms,
                p99_ms=info.p99_ms,
                **self.context.stamp("dumpsys-gfxinfo"),
            )
        ]
