"""Collector contract, shared context and collector registry."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from models.execution_model import Platform
from models.telemetry_model import MetricKind, TelemetrySample
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.config import TelemetrySettings
    from telemetry.executors import CommandExecutor
    from telemetry.ios.backend import IOSMetricsBackend


class CollectorUnavailableError(RuntimeError):
    """The collector cannot work in this environment; it is disabled for the session."""


@dataclass
class CollectorContext:
    platform: Platform
    app_id: str
    executor: CommandExecutor | None = None
    ios_backend: IOSMetricsBackend | None = None
    settings: TelemetrySettings | None = None
    har_loader: Callable[[], dict[str, Any] | None] | None = None
    _t0: float = field(default_factory=time.monotonic)

    def reset_clock(self) -> None:
        self._t0 = time.monotonic()

    def stamp(self, source: str) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(UTC),
            "elapsed_sec": round(time.monotonic() - self._t0, 3),
            "source": source,
        }

    @property
    def interval_sec(self) -> float:
        return self.settings.sampling_interval_sec if self.settings else 1.0


class BaseCollector(ABC):
    """A periodic metric collector run on its own thread by ``TelemetryManager``.

    Lifecycle: ``on_start`` -> ``collect`` every interval -> ``on_stop``.
    ``collect`` should return ``[]`` when the app is transiently unavailable and
    raise ``CollectorUnavailableError`` when it can never work here.
    """

    kind: ClassVar[MetricKind]
    name: ClassVar[str]

    def __init__(self, context: CollectorContext) -> None:
        self.context = context
        self.log = get_logger(f"collector.{self.name}")

    def on_start(self) -> None:
        """Prepare (reset counters, resolve pid...). Called on the collector thread."""

    @abstractmethod
    def collect(self) -> list[TelemetrySample]:
        """Take one sample."""

    def on_stop(self) -> list[TelemetrySample]:
        """Finalise and return any end-of-window samples."""
        return []

    @property
    def is_android(self) -> bool:
        return self.context.platform == Platform.ANDROID

    def require_executor(self) -> CommandExecutor:
        if self.context.executor is None:
            raise CollectorUnavailableError(f"{self.name}: no command executor for this provider")
        return self.context.executor

    def require_ios_backend(self) -> IOSMetricsBackend:
        if self.context.ios_backend is None:
            raise CollectorUnavailableError(f"{self.name}: no iOS metrics backend configured")
        return self.context.ios_backend


C = TypeVar("C", bound=type[BaseCollector])
_COLLECTORS: dict[str, type[BaseCollector]] = {}


def register_collector(name: str) -> Callable[[C], C]:
    def decorator(cls: C) -> C:
        cls.name = name
        _COLLECTORS[name] = cls
        return cls

    return decorator


def _ensure_builtin_collectors() -> None:
    from telemetry import (  # noqa: F401
        battery_collector,
        cpu_collector,
        fps_collector,
        memory_collector,
        network_collector,
    )


def available_collectors() -> list[str]:
    _ensure_builtin_collectors()
    return sorted(_COLLECTORS)


def create_collectors(names: Iterable[str], context: CollectorContext) -> list[BaseCollector]:
    _ensure_builtin_collectors()
    collectors: list[BaseCollector] = []
    for name in names:
        cls = _COLLECTORS.get(name)
        if cls is None:
            raise KeyError(f"Unknown collector '{name}'. Available: {sorted(_COLLECTORS)}")
        collectors.append(cls(context))
    return collectors
