"""TelemetryManager - runs collectors in parallel while a test executes.

Threading model::

    collector thread (cpu) ─┐
    collector thread (mem) ─┼─> queue.Queue ─> writer thread ─> TelemetryData + JSONL sink
    collector thread (fps) ─┘                               └─> EventBus(SAMPLE_COLLECTED)

* Each collector samples on its own daemon thread every ``interval_sec``.
* Collector failures are isolated: after ``max_consecutive_errors`` (or a
  ``CollectorUnavailableError``) only that collector is disabled.
* A single writer thread owns the JSON Lines file, so writes never interleave.
* ``record()`` lets tests push out-of-band samples (steps, startup) safely.
* ``astart()`` / ``astop()`` and ``async with`` make it usable from asyncio code.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any, Protocol

from core.events import EventBus, EventType
from models.telemetry_model import TelemetryData, TelemetrySample
from telemetry.base_collector import BaseCollector, CollectorUnavailableError
from utils.logger import get_logger

_log = get_logger("telemetry")
_SENTINEL = object()


class SessionHook(Protocol):
    """Objects recording for the whole window (e.g. iOS trace backends)."""

    name: str

    def start_session(self) -> None: ...

    def stop_session(self) -> list[TelemetrySample]: ...


class TelemetryManager:
    def __init__(
        self,
        collectors: Iterable[BaseCollector],
        *,
        interval_sec: float = 1.0,
        event_bus: EventBus | None = None,
        queue_maxsize: int = 10_000,
        max_consecutive_errors: int = 5,
        session_hooks: Iterable[SessionHook] = (),
    ) -> None:
        self.collectors = list(collectors)
        self.interval_sec = interval_sec
        self.event_bus = event_bus
        self.max_consecutive_errors = max_consecutive_errors
        self.session_hooks = list(session_hooks)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_maxsize)
        self._data = TelemetryData()
        self._data_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._writer: threading.Thread | None = None
        self._sink: IO[str] | None = None
        self._running = False
        self._state_lock = threading.Lock()
        self.dropped_samples = 0
        self.disabled: dict[str, str] = {}
        self.errors: dict[str, list[str]] = {}

    @property
    def running(self) -> bool:
        return self._running

    # ----------------------------------------------------------- lifecycle
    def start(self, sink_path: Path | None = None) -> None:
        with self._state_lock:
            if self._running:
                return
            self._stop_event.clear()
            if sink_path is not None:
                sink_path.parent.mkdir(parents=True, exist_ok=True)
                self._sink = sink_path.open("a", encoding="utf-8")
            for context in {id(c.context): c.context for c in self.collectors}.values():
                context.reset_clock()
            for hook in self.session_hooks:
                try:
                    hook.start_session()
                except Exception:
                    _log.exception("Session hook %s failed to start", getattr(hook, "name", hook))
            self._writer = threading.Thread(target=self._writer_loop, name="telemetry-writer", daemon=True)
            self._writer.start()
            self._threads = [
                threading.Thread(
                    target=self._run_collector, args=(c,), name=f"telemetry-{c.name}", daemon=True
                )
                for c in self.collectors
            ]
            for thread in self._threads:
                thread.start()
            self._running = True
        _log.info("Telemetry started: %s every %.1fs", [c.name for c in self.collectors], self.interval_sec)
        self._publish(EventType.TELEMETRY_STARTED, collectors=[c.name for c in self.collectors])

    def stop(self, timeout: float = 60.0) -> TelemetryData:
        with self._state_lock:
            if not self._running:
                return self.snapshot()
            self._stop_event.set()
            deadline = time.monotonic() + timeout
            for thread in self._threads:
                thread.join(max(0.1, deadline - time.monotonic()))
                if thread.is_alive():
                    _log.warning("Collector thread %s did not stop in time", thread.name)
            for hook in self.session_hooks:
                try:
                    self._enqueue(hook.stop_session())
                except Exception:
                    _log.exception("Session hook %s failed to stop", getattr(hook, "name", hook))
            self._queue.put(_SENTINEL)
            if self._writer is not None:
                self._writer.join(timeout)
            if self._sink is not None:
                self._sink.close()
                self._sink = None
            self._running = False
        data = self.snapshot()
        _log.info("Telemetry stopped: %d samples (%d dropped)", data.sample_count, self.dropped_samples)
        self._publish(
            EventType.TELEMETRY_STOPPED,
            samples=data.sample_count,
            dropped=self.dropped_samples,
            disabled=dict(self.disabled),
        )
        return data

    def snapshot(self) -> TelemetryData:
        with self._data_lock:
            return self._data.model_copy(deep=True)

    def record(self, sample: TelemetrySample) -> None:
        if self._running:
            self._enqueue([sample])
        else:
            self._store(sample)

    # ----------------------------------------------------- context managers
    def __enter__(self) -> TelemetryManager:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    async def astart(self, sink_path: Path | None = None) -> None:
        await asyncio.to_thread(self.start, sink_path)

    async def astop(self) -> TelemetryData:
        return await asyncio.to_thread(self.stop)

    async def __aenter__(self) -> TelemetryManager:
        await self.astart()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.astop()

    # ------------------------------------------------------------ internals
    def _publish(self, event: EventType, **payload: Any) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event, **payload)

    def _enqueue(self, samples: Iterable[TelemetrySample]) -> None:
        for sample in samples:
            try:
                self._queue.put(sample, timeout=1.0)
            except queue.Full:
                self.dropped_samples += 1

    def _store(self, sample: TelemetrySample) -> None:
        with self._data_lock:
            self._data.add(sample)
        if self._sink is not None:
            self._sink.write(sample.model_dump_json() + "\n")
        self._publish(EventType.SAMPLE_COLLECTED, sample=sample)

    def _writer_loop(self) -> None:
        processed = 0
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                break
            try:
                self._store(item)
                processed += 1
                if self._sink is not None and processed % 50 == 0:
                    self._sink.flush()
            except Exception:
                _log.exception("Failed to store telemetry sample")

    def _disable(self, collector: BaseCollector, reason: str) -> None:
        self.disabled[collector.name] = reason
        _log.warning("Collector '%s' disabled: %s", collector.name, reason)
        self._publish(EventType.COLLECTOR_FAILED, collector=collector.name, reason=reason)

    def _run_collector(self, collector: BaseCollector) -> None:
        try:
            collector.on_start()
        except CollectorUnavailableError as exc:
            self._disable(collector, str(exc))
            return
        except Exception as exc:
            _log.warning("Collector '%s' on_start failed: %s", collector.name, exc)

        consecutive = 0
        while True:
            started = time.monotonic()
            try:
                self._enqueue(collector.collect())
                consecutive = 0
            except CollectorUnavailableError as exc:
                self._disable(collector, str(exc))
                break
            except Exception as exc:
                consecutive += 1
                self.errors.setdefault(collector.name, []).append(str(exc))
                _log.debug("Collector '%s' error %d: %s", collector.name, consecutive, exc)
                if consecutive >= self.max_consecutive_errors:
                    self._disable(collector, f"{consecutive} consecutive errors, last: {exc}")
                    break
            remaining = self.interval_sec - (time.monotonic() - started)
            if self._stop_event.wait(max(0.0, remaining)):
                break
        try:
            self._enqueue(collector.on_stop())
        except Exception as exc:
            _log.warning("Collector '%s' on_stop failed: %s", collector.name, exc)
