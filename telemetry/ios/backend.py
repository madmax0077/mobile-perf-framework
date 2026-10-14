"""iOS metrics backend abstraction (Strategy).

Implementations:

* ``xcuitest``    - Appium XCUITest ``mobile:`` APIs (battery, launch timing). Works on clouds.
* ``instruments`` - Appium ``mobile: startPerfRecord`` / ``stopPerfRecord`` (Instruments trace,
                    needs ``--allow-insecure perf_record``); trace is parsed on macOS.
* ``xctrace``     - host ``xcrun xctrace`` recording (macOS + local device/simulator).
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from models.telemetry_model import LaunchMode, TelemetrySample
from utils.logger import get_logger


class IOSMetricsBackend(ABC):  # noqa: B024 - every hook has a safe default
    """Every method has a safe default (``None`` = metric not available)."""

    name: str = "abstract"

    def __init__(
        self,
        bundle_id: str,
        *,
        driver: Any = None,
        udid: str | None = None,
        artifacts_dir: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.bundle_id = bundle_id
        self.driver = driver
        self.udid = udid
        self.artifacts_dir = artifacts_dir or Path("reports/artifacts")
        self.options = options or {}
        self.log = get_logger(f"ios.{self.name}")

    # Session lifecycle (trace-based backends record for the whole window)
    def start_session(self) -> None:
        return None

    def stop_session(self) -> list[TelemetrySample]:
        return []

    # Live sampling
    def cpu_usage(self) -> float | None:
        return None

    def memory_usage_mb(self) -> float | None:
        return None

    def battery_level(self) -> float | None:
        return None

    def fps(self) -> float | None:
        return None

    def network_bytes(self) -> tuple[int, int] | None:
        return None

    # Launch measurement
    def prepare_launch(self, mode: LaunchMode) -> None:
        raise NotImplementedError(f"{self.name} cannot control app launch")

    def measure_launch(self, mode: LaunchMode) -> float | None:
        return None


B = TypeVar("B", bound=type[IOSMetricsBackend])
_BACKENDS: dict[str, type[IOSMetricsBackend]] = {}


def register_ios_backend(name: str) -> Callable[[B], B]:
    def decorator(cls: B) -> B:
        cls.name = name
        _BACKENDS[name] = cls
        return cls

    return decorator


def create_ios_backend(
    name: str,
    *,
    driver: Any,
    udid: str | None,
    bundle_id: str,
    artifacts_dir: Path | None,
    options: dict[str, Any] | None = None,
) -> IOSMetricsBackend:
    from telemetry.ios import instruments_backend, xctrace_backend, xcuitest_backend  # noqa: F401

    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise KeyError(f"Unknown iOS backend '{name}'. Available: {sorted(_BACKENDS)}") from None
    return cls(bundle_id, driver=driver, udid=udid, artifacts_dir=artifacts_dir, options=options)
