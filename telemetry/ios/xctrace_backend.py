"""Host ``xcrun xctrace`` backend (macOS, local device or simulator)."""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from models.telemetry_model import CpuSnapshot, LaunchMode, MemorySnapshot, TelemetrySample
from telemetry.ios.backend import IOSMetricsBackend, register_ios_backend
from telemetry.ios.xctrace_parser import parse_activity_monitor
from utils.ios_utils import (
    IOSToolError,
    export_xctrace_table,
    run_xcrun,
    start_xctrace_record,
    stop_process,
    xcrun_available,
)

ACTIVITY_SCHEMA = "activity-monitor-process-live"


def trace_to_samples(
    trace: Path, process_filter: str, started_at: datetime, source: str
) -> list[TelemetrySample]:
    xml = export_xctrace_table(trace, ACTIVITY_SCHEMA)
    samples: list[TelemetrySample] = []
    for row in parse_activity_monitor(xml, process_filter):
        stamp = {
            "timestamp": started_at + timedelta(seconds=row.offset_sec),
            "elapsed_sec": round(row.offset_sec, 3),
            "source": source,
        }
        if row.cpu_pct is not None:
            samples.append(CpuSnapshot(app_cpu_pct=row.cpu_pct, **stamp))
        if row.memory_mb is not None:
            samples.append(MemorySnapshot(pss_mb=row.memory_mb, **stamp))
    return samples


@register_ios_backend("xctrace")
class XctraceBackend(IOSMetricsBackend):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._proc: subprocess.Popen[str] | None = None
        self._trace: Path | None = None
        self._started_at = datetime.now(UTC)

    @property
    def process_filter(self) -> str:
        return str(self.options.get("process_name") or self.bundle_id.rsplit(".", 1)[-1])

    def start_session(self) -> None:
        if not xcrun_available():
            self.log.warning("xctrace backend requires macOS + Xcode; iOS CPU/memory will be empty")
            return
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._trace = self.artifacts_dir / f"activity-{int(time.time())}.trace"
        self._started_at = datetime.now(UTC)
        self._proc = start_xctrace_record("Activity Monitor", self._trace, device=self.udid)

    def stop_session(self) -> list[TelemetrySample]:
        if self._proc is None or self._trace is None:
            return []
        stop_process(self._proc)
        self._proc = None
        try:
            return trace_to_samples(self._trace, self.process_filter, self._started_at, self.name)
        except (IOSToolError, Exception) as exc:
            self.log.warning("Could not export xctrace data: %s", exc)
            return []

    def prepare_launch(self, mode: LaunchMode) -> None:
        if mode != LaunchMode.COLD:
            raise NotImplementedError("xctrace backend measures cold launches only")

    def measure_launch(self, mode: LaunchMode) -> float | None:
        if not xcrun_available() or not self.udid:
            return None
        start = time.perf_counter()
        try:
            run_xcrun(
                "devicectl",
                "device",
                "process",
                "launch",
                "--terminate-existing",
                "--device",
                self.udid,
                self.bundle_id,
            )
        except IOSToolError:
            run_xcrun("simctl", "launch", "--terminate-running-process", self.udid, self.bundle_id)
        return round((time.perf_counter() - start) * 1000.0, 1)
