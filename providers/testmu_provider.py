"""TestMu AI (formerly LambdaTest) real-device cloud provider.

Features: authentication, capability management (``LT:Options``), automatic app
upload, Appium session handling, telemetry over ``lambda-adb`` with an Appium
fallback, app-profiling metrics retrieval, session status and screenshots.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, deep_merge
from core.driver_manager import DriverManager
from models.execution_model import DeviceInfo, Platform
from models.telemetry_model import CrashEvent, CrashType, TelemetryData
from providers.base import AppiumDeviceProvider, ProviderNotReadyError
from providers.registry import register_provider
from providers.testmu_api import TestMuApiClient, app_metrics_crash_info, map_app_metrics
from telemetry.executors import AppiumShellExecutor, CommandExecutor, TestMuAdbExecutor
from telemetry.parsers.crash import parse_crash_report, parse_logcat_crashes
from telemetry.parsers.har import parse_har


@register_provider("testmu", "testmuai", "lambdatest")
class TestMuProvider(AppiumDeviceProvider):
    __test__ = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._executor: CommandExecutor | None = None
        self._api: TestMuApiClient | None = None
        self._final_session_id: str | None = None
        self._details: dict[str, Any] | None = None
        self._app_metrics: Any = None

    # ------------------------------------------------------------ auth / API
    @property
    def username(self) -> str:
        return str(self.options.get("username") or "")

    @property
    def access_key(self) -> str:
        return str(self.options.get("access_key") or "")

    @property
    def api(self) -> TestMuApiClient:
        if self._api is None:
            self._api = TestMuApiClient(
                self.username,
                self.access_key,
                api_base_url=self.options["api_base_url"],
                app_upload_url=self.options["app_upload_url"],
            )
        return self._api

    def hub_url(self) -> str:
        """Hub URL without credentials; they are sent via the client config."""
        return str(self.options["hub_url"])

    def validate(self) -> None:
        if not self.username or not self.access_key:
            raise ProviderNotReadyError(
                "TestMu credentials missing: set TESTMU_USERNAME and TESTMU_ACCESS_KEY."
            )
        if not self.app_location:
            raise ProviderNotReadyError(
                "No app configured for TestMu: set ANDROID_APP / IOS_APP to a local path or lt://APP id."
            )
        super().validate()

    # ------------------------------------------------------- capabilities
    def resolve_app(self) -> str:
        app = self.app_location or ""
        if app.startswith(("lt://", "http://", "https://")):
            return app
        path = Path(app)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise ProviderNotReadyError(f"App binary not found: {path}")
        cache = PROJECT_ROOT / str(self.options.get("upload_cache_file", ".app_upload_cache.json"))
        return self.api.upload_app(path, name=self.config.app.name, cache_file=cache)

    def build_capabilities(self) -> dict[str, Any]:
        build = self.options.get("build") or (
            f"{self.config.app.name} {self.config.app.version} ({self.config.app.build_number})"
        )
        lt_options: dict[str, Any] = {
            **self.options.get("lt_options", {}),
            "platformName": self.platform.value,
            "deviceName": self.device.device_name,
            "platformVersion": self.device.platform_version,
            "app": self.resolve_app(),
            "build": build,
            "project": self.options.get("project", "Mobile Performance"),
            "name": self.test_name,
        }
        lt_options = deep_merge(lt_options, self.device.providers.get(self.name, {}))
        caps: dict[str, Any] = {"platformName": self.platform.value, "LT:Options": lt_options}
        if self.platform == Platform.ANDROID and self.config.app.android.package:
            caps["appPackage"] = self.config.app.android.package
            if self.config.app.android.activity:
                caps["appActivity"] = self.config.app.android.activity
        elif self.platform == Platform.IOS and self.config.app.ios.bundle_id:
            caps["bundleId"] = self.config.app.ios.bundle_id
        return caps

    # -------------------------------------------------------------- driver
    def create_driver(self) -> Any:
        manager = DriverManager(retries=int(self.options.get("connect_retries", 3)))
        self.driver = manager.create(
            self.hub_url(),
            self.platform,
            self.build_capabilities(),
            username=self.username,
            password=self.access_key,
        )
        self._final_session_id = self.driver.session_id
        return self.driver

    def get_command_executor(self) -> CommandExecutor | None:
        if self.platform != Platform.ANDROID:
            return None
        if self._executor is None:
            driver = self._require_driver()
            appium = AppiumShellExecutor(driver, allow_shell=False)
            if self.options.get("telemetry_transport", "lambda_adb") == "lambda_adb":
                self._executor = TestMuAdbExecutor(driver, fallback=appium)
            else:
                self._executor = appium
        return self._executor

    # ----------------------------------------------------- session status
    def mark_session_status(self, passed: bool, reason: str = "") -> None:
        if self.driver is None:
            return
        try:
            self.driver.execute_script(f"lambda-status={'passed' if passed else 'failed'}")
            if reason:
                self.driver.execute_script(f"lambda-exceptions={_js_list(reason)}")
        except Exception as exc:
            self.log.warning("Could not set TestMu session status: %s", exc)

    def device_info(self) -> DeviceInfo:
        info = super().device_info()
        info.is_real_device = bool(self.options.get("lt_options", {}).get("isRealMobile", True))
        caps = getattr(self.driver, "capabilities", None) or {}
        info.udid = caps.get("udid") or caps.get("appium:udid")
        info.model = caps.get("deviceModel") or caps.get("appium:deviceModel")
        return info

    def _session_details(self) -> dict[str, Any]:
        if self._details is None and self._final_session_id:
            try:
                self._details = self.api.session_details(self._final_session_id)
            except Exception as exc:
                self.log.warning("Could not fetch TestMu session details: %s", exc)
                return {}
        return self._details or {}

    def session_metadata(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"session_id": self._final_session_id, "provider": "TestMu AI"}
        details = self._session_details()
        for key in (
            "video_url",
            "public_url",
            "status_ind",
            "device_name",
            "os_version",
            "build_name",
            "device_logs_url",
            "crash_logs_url",
        ):
            if key in details:
                meta[key] = details[key]
        return meta

    def _poll_settings(self) -> tuple[int, float]:
        return (
            int(self.options.get("app_metrics_retries", 5)),
            float(self.options.get("app_metrics_retry_delay_sec", 6)),
        )

    def _session_start(self) -> datetime | None:
        value = self._session_details().get("start_timestamp")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def collect_post_session_metrics(self) -> TelemetryData | None:
        """Pull app-profiling metrics and the HAR network log, which TestMu
        publishes after the session ends. Only kinds missing from live
        telemetry are merged by the session manager."""
        session_id = self._final_session_id
        if not session_id:
            return None
        lt_options = self.options.get("lt_options", {})
        retries, delay = self._poll_settings()
        data = TelemetryData()

        if lt_options.get("network"):
            har = self.api.poll(lambda: self.api.network_log(session_id), retries=retries, delay_sec=delay)
            if har:
                data.extend(parse_har(har))

        if not (lt_options.get("appProfiling") and self.options.get("fetch_app_metrics", True)):
            return data
        payload = self.api.poll(lambda: self.api.app_metrics(session_id), retries=retries, delay_sec=delay)
        if not payload:
            return data
        self._app_metrics = payload
        samples = map_app_metrics(payload, start=self._session_start())
        for sample in samples:
            if sample.timestamp.tzinfo is None:
                sample.timestamp = sample.timestamp.replace(tzinfo=UTC)
        data.extend(samples)
        self.log.info("Fetched %d app-profiling samples for %s", len(samples), session_id)
        return data

    def collect_post_session_crashes(self) -> list[CrashEvent]:
        """Crashes from the TestMu device log, crash log and app-profiling counters."""
        session_id = self._final_session_id
        if not session_id or self.platform != Platform.ANDROID:
            return []
        package = self.app_id or ""
        max_lines = self.config.crash.max_stacktrace_lines
        lt_options = self.options.get("lt_options", {})
        retries, delay = self._poll_settings()
        events: list[CrashEvent] = []

        if lt_options.get("devicelog"):
            text = self.api.poll(lambda: self.api.device_log(session_id), retries=retries, delay_sec=delay)
            if text:
                events.extend(parse_logcat_crashes(text, package, max_lines=max_lines))
        try:
            crash_text = self.api.crash_log(session_id)
        except Exception as exc:
            self.log.debug("TestMu crash log unavailable: %s", exc)
            crash_text = ""
        if crash_text.strip():
            events.extend(
                parse_crash_report(crash_text, package, source="testmu_crashlog", max_lines=max_lines)
            )

        crash_count, anr_count, anr_logs = app_metrics_crash_info(self._app_metrics)
        if anr_logs.strip():
            events.extend(e for e in parse_logcat_crashes(anr_logs, package) if e.crash_type == CrashType.ANR)
        for crash_type, reported in ((CrashType.JAVA_CRASH, crash_count), (CrashType.ANR, anr_count)):
            seen = sum(1 for e in events if (e.crash_type == CrashType.ANR) == (crash_type == CrashType.ANR))
            for i in range(max(0, reported - seen)):
                events.append(
                    CrashEvent(
                        crash_type=crash_type,
                        process=package or None,
                        pid=-(i + 1),
                        exception=f"Reported by TestMu app profiling ({crash_type.value}); see logs",
                        source="testmu_app_profiling",
                    )
                )
        return events


def _js_list(text: str) -> str:
    import json

    return json.dumps([text[:500]])
