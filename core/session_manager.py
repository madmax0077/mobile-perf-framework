"""Per-test device session lifecycle: provider -> driver -> telemetry -> execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import FrameworkConfig
from core.events import EventBus, EventType
from core.provider_factory import ProviderFactory
from core.screenshots import ScreenshotRecorder
from models.execution_model import AppInfo, Platform, TestExecution, TestStatus, TestType
from models.telemetry_model import TelemetryData
from providers.base import DeviceProvider
from telemetry.crash_monitor import CrashMonitor
from utils.fs import safe_name
from utils.logger import get_logger

__all__ = ["PerfSession", "SessionManager", "safe_name"]

_log = get_logger("session")


@dataclass
class PerfSession:
    test_id: str
    name: str
    test_type: TestType
    provider: DeviceProvider
    started_at: datetime
    raw_path: Path
    artifacts_dir: Path
    label: str | None = None
    telemetry_running: bool = False
    artifacts: dict[str, str] = field(default_factory=dict)
    crash_monitor: CrashMonitor | None = None
    screenshots: ScreenshotRecorder | None = None

    @property
    def driver(self) -> Any:
        return self.provider.driver

    @property
    def platform(self) -> Platform:
        return self.provider.platform


class SessionManager:
    def __init__(
        self,
        config: FrameworkConfig,
        run_dir: Path,
        *,
        event_bus: EventBus | None = None,
        factory: ProviderFactory | None = None,
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.event_bus = event_bus or EventBus()
        self.factory = factory or ProviderFactory(config, self.event_bus)

    def open(
        self,
        test_id: str,
        name: str,
        test_type: TestType = TestType.GENERIC,
        *,
        provider_name: str | None = None,
        device: str | None = None,
        platform: Platform | None = None,
        app_override: str | None = None,
        label: str | None = None,
        create_driver: bool = True,
        provider_options: dict[str, dict[str, Any]] | None = None,
    ) -> PerfSession:
        key = safe_name(f"{test_id}-{label}" if label else test_id)
        artifacts_dir = self.run_dir / "artifacts" / key
        provider = self.factory.create(
            provider_name,
            device=device,
            platform=platform,
            test_name=f"{name} [{label}]" if label else name,
            app_override=app_override,
            artifacts_dir=artifacts_dir,
            option_overrides=provider_options,
        )
        provider.validate()
        session = PerfSession(
            test_id=test_id,
            name=name,
            test_type=test_type,
            provider=provider,
            started_at=datetime.now(UTC),
            raw_path=self.run_dir / "raw" / f"{key}.jsonl",
            artifacts_dir=artifacts_dir,
            label=label,
            crash_monitor=CrashMonitor(provider, self.config.crash) if self.config.crash.enabled else None,
            screenshots=ScreenshotRecorder(provider, artifacts_dir / "screenshots", self.config.reporting),
        )
        if create_driver:
            provider.create_driver()
        self.event_bus.publish(EventType.TEST_STARTED, test_id=test_id, provider=provider.name, label=label)
        return session

    def start_telemetry(self, session: PerfSession) -> None:
        if not self.config.telemetry.enabled or session.telemetry_running:
            return
        session.provider.start_telemetry(sink_path=session.raw_path)
        session.telemetry_running = True

    def stop_telemetry(self, session: PerfSession) -> TelemetryData:
        if not session.telemetry_running:
            return session.provider.get_metrics()
        session.telemetry_running = False
        return session.provider.stop_telemetry()

    def close(self, session: PerfSession, status: TestStatus, error: str | None = None) -> TestExecution:
        provider = session.provider
        data = TelemetryData()
        device_info = provider.device_info()
        session_id = provider.session_id
        monitor = session.crash_monitor
        try:
            if monitor is not None:
                monitor.check("teardown", step_failed=status in (TestStatus.FAILED, TestStatus.ERROR))
            data = self.stop_telemetry(session)
            failed = status in (TestStatus.FAILED, TestStatus.ERROR)
            if session.screenshots is not None and status != TestStatus.SKIPPED:
                shot = session.screenshots.capture("failure" if failed else "final", failed=failed)
                if shot is not None:
                    session.artifacts["screenshot" if failed else "final_screenshot"] = str(shot)
            provider.mark_session_status(status == TestStatus.PASSED, error or "")
        except Exception:
            _log.exception("Error while finalising session for %s", session.test_id)
        finally:
            provider.quit()

        try:
            post = provider.collect_post_session_metrics()
            if post is not None:
                merged = data.merge_missing(post)
                if merged:
                    _log.info("Merged post-session telemetry: %s", merged)
        except Exception as exc:
            _log.warning("Post-session metrics unavailable: %s", exc)

        if monitor is not None:
            if self.config.crash.collect_post_session:
                try:
                    monitor.merge(provider.collect_post_session_crashes())
                except Exception as exc:
                    _log.warning("Post-session crash reports unavailable: %s", exc)
            monitor.attribute_steps(data.steps)
            data.crashes = list(monitor.events)
            if data.crashes and status == TestStatus.PASSED and self.config.crash.fail_on_crash:
                status, error = TestStatus.FAILED, monitor.describe()

        try:
            metadata = provider.session_metadata()
        except Exception as exc:
            metadata = {"error": str(exc)}
        finished = datetime.now(UTC)
        app = self.config.app
        execution = TestExecution(
            test_id=session.test_id,
            name=session.name if not session.label else f"{session.name} [{session.label}]",
            test_type=session.test_type,
            label=session.label,
            status=status,
            started_at=session.started_at,
            finished_at=finished,
            duration_sec=round((finished - session.started_at).total_seconds(), 2),
            device=device_info,
            app=AppInfo(
                name=app.name,
                version=app.version,
                build_number=session.label or app.build_number,
                identifier=provider.app_id,
            ),
            provider_session_id=session_id or metadata.get("session_id"),
            telemetry=data,
            artifacts={
                **session.artifacts,
                **({"raw_telemetry": str(session.raw_path)} if session.raw_path.exists() else {}),
            },
            provider_metadata=metadata,
            error=error,
        )
        self.event_bus.publish(EventType.TEST_FINISHED, execution=execution)
        return execution
