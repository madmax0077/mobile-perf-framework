"""Device provider abstraction (Strategy) and a reusable Appium base class.

``DeviceProvider`` is the plugin contract every device source implements.
``AppiumDeviceProvider`` implements the telemetry half of the contract using the
transport-agnostic telemetry engine, so concrete providers mostly only decide
*how to create a driver* and *how to run shell commands*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from core.config import deep_merge
from core.events import EventBus
from models.execution_model import DeviceInfo, Platform
from models.telemetry_model import CrashEvent, TelemetryData, TelemetrySample
from telemetry.base_collector import CollectorContext, create_collectors
from telemetry.executors import CommandExecutor
from telemetry.ios.backend import IOSMetricsBackend, create_ios_backend
from telemetry.startup_collector import (
    AdbLaunchStrategy,
    DriverLaunchStrategy,
    FallbackLaunchStrategy,
    IOSBackendLaunchStrategy,
    LaunchStrategy,
)
from telemetry.telemetry_manager import TelemetryManager
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.config import DeviceProfile, FrameworkConfig


class ProviderError(RuntimeError):
    """Base error for provider failures."""


class ProviderNotReadyError(ProviderError):
    """Configuration or environment is incomplete; tests should be skipped, not failed."""


class DeviceProvider(ABC):
    """Contract for device sources (cloud or local)."""

    name: ClassVar[str] = "abstract"

    def __init__(
        self,
        config: FrameworkConfig,
        device: DeviceProfile,
        *,
        event_bus: EventBus | None = None,
        test_name: str = "perf-test",
        app_override: str | None = None,
        artifacts_dir: Path | None = None,
        option_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.device = device
        self.event_bus = event_bus or EventBus()
        self.test_name = test_name
        self.app_override = app_override
        self.artifacts_dir = artifacts_dir or config.report_dir / "artifacts"
        self.options: dict[str, Any] = deep_merge(config.provider_options(self.name), option_overrides or {})
        self.driver: Any = None
        self.log = get_logger(f"provider.{self.name}")

    # ----------------------------------------------------------- contract
    @abstractmethod
    def create_driver(self) -> Any:
        """Create and return an Appium/Selenium WebDriver session."""

    @abstractmethod
    def start_telemetry(self, sink_path: Path | None = None) -> None:
        """Start background telemetry collection."""

    @abstractmethod
    def stop_telemetry(self) -> TelemetryData:
        """Stop background telemetry and return everything collected."""

    @abstractmethod
    def get_metrics(self) -> TelemetryData:
        """Return telemetry collected so far without stopping collection."""

    # ------------------------------------------------- optional extension hooks
    @property
    def platform(self) -> Platform:
        return self.device.platform

    @property
    def app_id(self) -> str | None:
        return self.config.app.identifier(self.platform)

    @property
    def app_location(self) -> str | None:
        return self.app_override or self.config.app.for_platform(self.platform).app

    @property
    def session_id(self) -> str | None:
        return getattr(self.driver, "session_id", None)

    @property
    def launch_settle_sec(self) -> float:
        """Pause between launches so the device returns to a steady state."""
        return self.config.startup.settle_time_sec

    def validate(self) -> None:
        """Raise ``ProviderNotReadyError`` when the provider cannot run."""
        if not self.app_id:
            key = "ANDROID_APP_PACKAGE" if self.platform == Platform.ANDROID else "IOS_BUNDLE_ID"
            raise ProviderNotReadyError(
                f"App under test is not configured for {self.platform.value}: set {key} "
                "(config/app.yaml) to run device tests."
            )

    def build_capabilities(self) -> dict[str, Any]:
        return {}

    def get_command_executor(self) -> CommandExecutor | None:
        return None

    def get_ios_backend(self) -> IOSMetricsBackend | None:
        return None

    def get_launch_strategy(self) -> LaunchStrategy:
        raise ProviderError(f"{self.name} does not support startup measurement")

    def record(self, sample: TelemetrySample) -> None:
        """Record an out-of-band sample (startup, step timing, iteration marker)."""

    def stamp(self, source: str) -> dict[str, Any]:
        return {"source": source}

    def mark_session_status(self, passed: bool, reason: str = "") -> None:
        """Report pass/fail to the provider dashboard, when supported."""

    def take_screenshot(self, path: Path) -> Path | None:
        if self.driver is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.driver.get_screenshot_as_file(str(path))
            return path
        except Exception as exc:
            self.log.warning("Screenshot failed: %s", exc)
            return None

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            name=self.device.device_name,
            platform=self.platform,
            os_version=self.device.platform_version,
            provider=self.name,
        )

    def session_metadata(self) -> dict[str, Any]:
        return {"session_id": self.session_id}

    def collect_post_session_metrics(self) -> TelemetryData | None:
        """Hook for providers that publish telemetry after the session ends."""
        return None

    def read_device_log(self) -> str | None:
        """Device log lines (logcat ``threadtime`` format) since the previous call."""
        return None

    def app_state(self) -> int | None:
        """Appium ``ApplicationState`` of the app under test (1 = not running, 4 = foreground)."""
        return None

    def collect_post_session_crashes(self) -> list[CrashEvent]:
        """Crash reports the provider publishes after the session ends."""
        return []

    def quit(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as exc:
                self.log.warning("driver.quit() failed: %s", exc)
            finally:
                self.driver = None


class AppiumDeviceProvider(DeviceProvider):
    """Implements the telemetry half of ``DeviceProvider`` on top of ``TelemetryManager``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._telemetry: TelemetryManager | None = None
        self._ios_backend: IOSMetricsBackend | None = None
        self._context: CollectorContext | None = None
        self._device_log_supported = True

    def _require_driver(self) -> Any:
        if self.driver is None:
            raise ProviderError("Driver not created; call create_driver() first")
        return self.driver

    def get_ios_backend(self) -> IOSMetricsBackend | None:
        if self.platform != Platform.IOS:
            return None
        if self._ios_backend is None:
            self._ios_backend = create_ios_backend(
                self.config.telemetry.ios_backend,
                driver=self.driver,
                udid=self.device.providers.get(self.name, {}).get("udid"),
                bundle_id=self.app_id or "",
                artifacts_dir=self.artifacts_dir,
            )
        return self._ios_backend

    def _collector_context(self) -> CollectorContext:
        if self._context is None:
            self._context = CollectorContext(
                platform=self.platform,
                app_id=self.app_id or "",
                executor=self.get_command_executor() if self.platform == Platform.ANDROID else None,
                ios_backend=self.get_ios_backend(),
                settings=self.config.telemetry,
                har_loader=self.load_network_log,
            )
        return self._context

    def stamp(self, source: str) -> dict[str, Any]:
        """Timestamp / elapsed fields aligned with the telemetry clock."""
        return self._collector_context().stamp(source)

    def load_network_log(self) -> dict[str, Any] | None:
        """Return a HAR document for the session, if the provider exposes one."""
        return None

    @property
    def telemetry(self) -> TelemetryManager:
        if self._telemetry is None:
            settings = self.config.telemetry
            context = self._collector_context()
            collectors = create_collectors(settings.collectors, context)
            self._telemetry = TelemetryManager(
                collectors,
                interval_sec=settings.sampling_interval_sec,
                event_bus=self.event_bus,
                queue_maxsize=settings.queue_maxsize,
                max_consecutive_errors=settings.max_consecutive_errors,
                session_hooks=[context.ios_backend] if context.ios_backend else [],
            )
        return self._telemetry

    def start_telemetry(self, sink_path: Path | None = None) -> None:
        self.telemetry.start(sink_path=sink_path)

    def stop_telemetry(self) -> TelemetryData:
        return self.telemetry.stop()

    def get_metrics(self) -> TelemetryData:
        return self.telemetry.snapshot()

    def record(self, sample: TelemetrySample) -> None:
        self.telemetry.record(sample)

    def read_device_log(self) -> str | None:
        if self.platform != Platform.ANDROID or self.driver is None or not self._device_log_supported:
            return None
        try:
            entries = self.driver.get_log("logcat")
        except Exception as exc:
            self._device_log_supported = False
            self.log.info("Live device log unavailable (%s); using app state and post-session logs", exc)
            return None
        return "\n".join(str(entry.get("message", "")) for entry in entries or [])

    def app_state(self) -> int | None:
        if self.driver is None or not self.app_id:
            return None
        try:
            return int(self.driver.query_app_state(self.app_id))
        except Exception as exc:
            self.log.debug("query_app_state failed: %s", exc)
            return None

    def get_launch_strategy(self) -> LaunchStrategy:
        driver = self._require_driver()
        app_id = self.app_id or ""
        strategies: list[LaunchStrategy] = []
        if self.platform == Platform.ANDROID:
            executor = self.get_command_executor()
            activity = self.config.app.android.activity
            if executor is not None and activity:
                strategies.append(AdbLaunchStrategy(executor, app_id, activity))
        else:
            backend = self.get_ios_backend()
            if backend is not None:
                strategies.append(IOSBackendLaunchStrategy(backend, app_id))
        strategies.append(DriverLaunchStrategy(driver, app_id))
        return FallbackLaunchStrategy(strategies)
