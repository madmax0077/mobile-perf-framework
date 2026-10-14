"""Local devices / emulators / simulators through a local Appium 2.x server.

Android telemetry runs over the host ``adb`` binary; iOS telemetry uses the
configured iOS backend (xctrace requires macOS + Xcode).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, deep_merge
from core.driver_manager import DriverManager
from models.execution_model import DeviceInfo, Platform
from providers.base import AppiumDeviceProvider, ProviderNotReadyError
from providers.registry import register_provider
from telemetry.executors import AdbExecutor, CommandExecutor
from utils.adb_utils import AdbClient


@register_provider("local")
class LocalProvider(AppiumDeviceProvider):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._executor: CommandExecutor | None = None

    @property
    def device_overrides(self) -> dict[str, Any]:
        return dict(self.device.providers.get(self.name, {}))

    @property
    def udid(self) -> str | None:
        key = "android_serial" if self.platform == Platform.ANDROID else "ios_udid"
        return self.device_overrides.get("udid") or self.options.get(key) or None

    def adb(self) -> AdbClient:
        return AdbClient(adb_path=str(self.options.get("adb_path", "adb")), serial=self.udid)

    def validate(self) -> None:
        super().validate()
        if self.platform == Platform.ANDROID and not self.adb().is_available():
            raise ProviderNotReadyError(
                "adb not found on PATH (set ADB_PATH) - required for Android telemetry."
            )

    def build_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {
            **self.options.get("capabilities", {}),
            "platformName": self.platform.value,
            "deviceName": self.device.device_name,
            "platformVersion": self.device.platform_version,
        }
        if self.udid:
            caps["udid"] = self.udid
        app = self.app_location
        if app:
            path = Path(app)
            caps["app"] = str(path if path.is_absolute() or "://" in app else PROJECT_ROOT / path)
        if self.platform == Platform.ANDROID:
            caps["automationName"] = "UiAutomator2"
            caps["appPackage"] = self.config.app.android.package
            if self.config.app.android.activity:
                caps["appActivity"] = self.config.app.android.activity
        else:
            caps["automationName"] = "XCUITest"
            caps["bundleId"] = self.config.app.ios.bundle_id
        overrides = {k: v for k, v in self.device_overrides.items() if k != "udid"}
        return deep_merge(caps, overrides)

    def create_driver(self) -> Any:
        manager = DriverManager(retries=int(self.options.get("connect_retries", 2)))
        self.driver = manager.create(
            str(self.options.get("appium_url", "http://127.0.0.1:4723")),
            self.platform,
            self.build_capabilities(),
        )
        return self.driver

    def get_command_executor(self) -> CommandExecutor | None:
        if self.platform != Platform.ANDROID:
            return None
        if self._executor is None:
            self._executor = AdbExecutor(self.adb())
        return self._executor

    def device_info(self) -> DeviceInfo:
        info = super().device_info()
        info.udid = self.udid
        if self.platform == Platform.ANDROID:
            try:
                adb = self.adb()
                info.model = adb.getprop("ro.product.model") or None
                info.os_version = adb.getprop("ro.build.version.release") or info.os_version
                info.is_real_device = adb.getprop("ro.kernel.qemu") != "1"
            except Exception as exc:
                self.log.debug("Could not read device props: %s", exc)
        return info
