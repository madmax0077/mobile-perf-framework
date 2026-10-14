from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from core.events import EventBus, EventType
from models.execution_model import Platform
from models.telemetry_model import CpuSnapshot, MetricKind, StepTiming, TelemetrySample, parse_sample
from telemetry.base_collector import BaseCollector, CollectorContext, CollectorUnavailableError
from telemetry.telemetry_manager import TelemetryManager


class CountingCollector(BaseCollector):
    kind = MetricKind.CPU
    name = "counting"

    def collect(self) -> list[TelemetrySample]:
        return [CpuSnapshot(app_cpu_pct=10.0, **self.context.stamp("test"))]


class BrokenCollector(BaseCollector):
    kind = MetricKind.CPU
    name = "broken"

    def __init__(self, context: CollectorContext) -> None:
        super().__init__(context)
        self.calls = 0

    def collect(self) -> list[TelemetrySample]:
        self.calls += 1
        raise RuntimeError("boom")


class UnavailableCollector(BaseCollector):
    kind = MetricKind.CPU
    name = "unavailable"

    def collect(self) -> list[TelemetrySample]:
        raise CollectorUnavailableError("not here")


def _context() -> CollectorContext:
    return CollectorContext(platform=Platform.ANDROID, app_id="com.x")


def test_parallel_collection_with_isolated_failures(tmp_path: Path) -> None:
    ctx = _context()
    broken = BrokenCollector(ctx)
    bus = EventBus()
    events: list[EventType] = []
    bus.subscribe(None, lambda e: events.append(e.type))
    manager = TelemetryManager(
        [CountingCollector(ctx), broken, UnavailableCollector(ctx)],
        interval_sec=0.05,
        event_bus=bus,
        max_consecutive_errors=3,
    )
    sink = tmp_path / "raw.jsonl"
    manager.start(sink_path=sink)
    manager.record(StepTiming(name="login", duration_ms=120.0))
    time.sleep(0.5)
    data = manager.stop()

    assert len(data.cpu) >= 5
    assert len(data.steps) == 1
    assert broken.calls == 3
    assert set(manager.disabled) == {"broken", "unavailable"}
    assert EventType.TELEMETRY_STARTED in events and EventType.TELEMETRY_STOPPED in events
    assert EventType.COLLECTOR_FAILED in events

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == data.sample_count
    assert isinstance(parse_sample(json.loads(lines[0])), TelemetrySample)


def test_record_without_running_and_snapshot_isolation() -> None:
    manager = TelemetryManager([], interval_sec=1)
    manager.record(StepTiming(name="x", duration_ms=1))
    snap = manager.snapshot()
    snap.steps.clear()
    assert len(manager.snapshot().steps) == 1
    assert manager.stop().sample_count == 1


def test_async_context_manager() -> None:
    async def run() -> int:
        manager = TelemetryManager([CountingCollector(_context())], interval_sec=0.05)
        async with manager:
            await asyncio.sleep(0.2)
        return len(manager.snapshot().cpu)

    assert asyncio.run(run()) >= 2
