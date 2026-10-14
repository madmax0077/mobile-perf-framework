"""Thread-safe publish/subscribe event bus (Observer pattern)."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from utils.logger import get_logger

_log = get_logger("events")


class EventType(StrEnum):
    SESSION_STARTED = "session_started"
    TEST_STARTED = "test_started"
    TELEMETRY_STARTED = "telemetry_started"
    SAMPLE_COLLECTED = "sample_collected"
    COLLECTOR_FAILED = "collector_failed"
    TELEMETRY_STOPPED = "telemetry_stopped"
    TEST_FINISHED = "test_finished"
    SESSION_FINISHED = "session_finished"
    REPORT_GENERATED = "report_generated"


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


Handler = Callable[[Event], None]


class EventBus:
    """Synchronous event bus. Handler exceptions are logged, never propagated."""

    def __init__(self) -> None:
        self._handlers: dict[EventType | None, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event_type: EventType | None, handler: Handler) -> Callable[[], None]:
        """Subscribe to one event type, or to all events with ``None``. Returns an unsubscribe callable."""
        with self._lock:
            self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers[event_type]:
                    self._handlers[event_type].remove(handler)

        return unsubscribe

    def publish(self, event_type: EventType, **payload: Any) -> None:
        event = Event(event_type, payload)
        with self._lock:
            handlers = [*self._handlers.get(event_type, []), *self._handlers.get(None, [])]
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                _log.exception("Event handler %r failed for %s", handler, event_type)
