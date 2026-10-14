from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.analysis import detect_cpu_growth, detect_memory_leak, detect_response_degradation, summarize
from core.config import EnduranceSettings
from models.telemetry_model import (
    BatteryUsage,
    CpuSnapshot,
    FpsSnapshot,
    LaunchMode,
    MemorySnapshot,
    NetworkRequest,
    StartupMetrics,
    StepTiming,
    TelemetryData,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _memory(values: list[float], step_sec: float = 10) -> TelemetryData:
    data = TelemetryData()
    for i, v in enumerate(values):
        data.add(
            MemorySnapshot(pss_mb=v, elapsed_sec=i * step_sec, timestamp=T0 + timedelta(seconds=i * step_sec))
        )
    return data


def test_leak_detected_for_steady_growth() -> None:
    data = _memory([200 + i * 3 for i in range(30)])  # +3 MB / 10 s = 18 MB/min
    finding = detect_memory_leak(data, EnduranceSettings())
    assert finding.detected
    assert finding.slope == pytest.approx(18.0, rel=0.01)
    assert finding.growth_pct > 10


def test_no_leak_for_flat_memory() -> None:
    data = _memory([200 + (1 if i % 2 else -1) for i in range(30)])
    assert not detect_memory_leak(data, EnduranceSettings()).detected


def test_leak_needs_minimum_samples() -> None:
    finding = detect_memory_leak(_memory([100, 200, 300]), EnduranceSettings())
    assert not finding.detected
    assert "Insufficient" in finding.message


def test_cpu_growth() -> None:
    data = TelemetryData()
    for i in range(20):
        data.add(
            CpuSnapshot(app_cpu_pct=10 + i * 2, elapsed_sec=i * 5, timestamp=T0 + timedelta(seconds=i * 5))
        )
    assert detect_cpu_growth(data, EnduranceSettings()).detected


def test_response_degradation() -> None:
    data = TelemetryData()
    for i in range(40):
        data.add(
            StepTiming(
                name="search", duration_ms=500 + i * 20, iteration=i, timestamp=T0 + timedelta(seconds=i)
            )
        )
    finding = detect_response_degradation(data, EnduranceSettings())
    assert finding.detected
    assert finding.metric == "step:search"


def test_summarize_kpis() -> None:
    data = _memory([150, 180, 170])
    for i, ms in enumerate([1000, 1200]):
        data.add(StartupMetrics(mode=LaunchMode.COLD, iteration=i + 1, total_time_ms=ms))
    data.add(StartupMetrics(mode=LaunchMode.WARM, iteration=1, total_time_ms=500))
    data.add(CpuSnapshot(app_cpu_pct=10))
    data.add(CpuSnapshot(app_cpu_pct=30))
    data.add(FpsSnapshot(fps=50, total_frames=100, janky_frames=10, frame_drops=4))
    data.add(FpsSnapshot(fps=60, total_frames=100, janky_frames=0, frame_drops=0))
    data.add(BatteryUsage(drain_pct=0.5, duration_sec=1800))
    data.add(NetworkRequest(url="https://x/api", duration_ms=100))
    data.add(NetworkRequest(url="https://x/api", duration_ms=300, status=500, failed=True))

    s = summarize(data)
    assert s.cold_start_avg_ms == 1100
    assert s.warm_start_avg_ms == 500
    assert s.hot_start_avg_ms is None
    assert s.peak_pss_mb == 180
    assert s.avg_cpu_pct == 20
    assert s.max_cpu_pct == 30
    assert s.avg_fps == 55
    assert s.jank_pct == 5.0
    assert s.frame_drops == 4
    assert s.battery_drain_pct_per_hour == 1.0
    assert s.request_count == 2
    assert s.failed_requests == 1
