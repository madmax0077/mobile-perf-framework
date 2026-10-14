from __future__ import annotations

from typing import Any

import pytest

from core.config import FrameworkConfig
from core.provider_factory import ProviderFactory
from models.telemetry_model import TelemetryData
from providers.base import DeviceProvider, ProviderNotReadyError
from providers.local_provider import LocalProvider
from providers.registry import (
    UnknownProviderError,
    available_providers,
    get_provider_class,
    register_provider,
)
from providers.simulated_provider import SimulatedProvider
from providers.testmu_provider import TestMuProvider
from telemetry.executors import CommandNotSupportedError, TestMuAdbExecutor


def test_builtin_providers_registered() -> None:
    names = available_providers()
    assert {"testmu", "lambdatest", "local", "simulated"} <= set(names)
    assert get_provider_class("TestMu") is TestMuProvider
    assert get_provider_class("lambdatest") is TestMuProvider


def test_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError):
        get_provider_class("does-not-exist")


def test_custom_provider_plugin(unit_config: FrameworkConfig) -> None:
    @register_provider("unit-dummy")
    class DummyProvider(DeviceProvider):
        def create_driver(self) -> Any:
            self.driver = object()
            return self.driver

        def start_telemetry(self, sink_path=None) -> None: ...

        def stop_telemetry(self) -> TelemetryData:
            return TelemetryData()

        def get_metrics(self) -> TelemetryData:
            return TelemetryData()

    provider = ProviderFactory(unit_config).create("unit-dummy")
    assert isinstance(provider, DummyProvider)
    assert provider.name == "unit-dummy"


def test_factory_builds_simulated(unit_config: FrameworkConfig) -> None:
    provider = ProviderFactory(unit_config).create()
    assert isinstance(provider, SimulatedProvider)
    provider.validate()


def test_testmu_requires_credentials(unit_config: FrameworkConfig) -> None:
    unit_config.provider.providers["testmu"].update(username="", access_key="")
    provider = ProviderFactory(unit_config).create("testmu")
    with pytest.raises(ProviderNotReadyError, match="TESTMU_USERNAME"):
        provider.validate()


def test_testmu_capabilities(unit_config: FrameworkConfig) -> None:
    unit_config.provider.providers["testmu"].update(username="user@x", access_key="k3y/+")
    unit_config.app.android.app = "lt://APP1234567890"
    unit_config.app.android.package = "com.example.app"
    unit_config.app.android.activity = ".MainActivity"
    provider = ProviderFactory(unit_config).create("testmu", device="galaxy_s24", test_name="cold start")
    provider.validate()

    caps = provider.build_capabilities()
    lt = caps["LT:Options"]
    assert caps["platformName"] == "android"
    assert caps["appPackage"] == "com.example.app"
    assert lt["deviceName"] == "Galaxy S24"
    assert lt["app"] == "lt://APP1234567890"
    assert lt["appProfiling"] is True and lt["isRealMobile"] is True
    assert lt["name"] == "cold start"
    assert provider.hub_url() == "https://mobile-hub.lambdatest.com/wd/hub"


def test_per_test_option_overrides_only_apply_to_their_provider(unit_config: FrameworkConfig) -> None:
    unit_config.provider.providers["testmu"].update(username="user@x", access_key="k3y")
    unit_config.app.android.app = "lt://APP1234567890"
    overrides = {"testmu": {"lt_options": {"network": False, "appProfiling": False}}, "local": {"x": 1}}
    provider = ProviderFactory(unit_config).create("testmu", option_overrides=overrides)

    lt = provider.build_capabilities()["LT:Options"]
    assert lt["network"] is False and lt["appProfiling"] is False
    assert lt["isRealMobile"] is True
    assert "x" not in provider.options
    assert unit_config.provider_options("testmu")["lt_options"]["appProfiling"] is True


def test_testmu_sends_raw_credentials_as_basic_auth(
    unit_config: FrameworkConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    from appium import webdriver

    captured: dict[str, Any] = {}

    def fake_remote(url: str, **kwargs: Any) -> Any:
        captured.update(url=url, **kwargs)
        return type("Driver", (), {"session_id": "s1"})()

    monkeypatch.setattr(webdriver, "Remote", fake_remote)
    unit_config.provider.providers["testmu"].update(username="user@x", access_key="k3y/+")
    unit_config.app.android.app = "lt://APP1234567890"
    unit_config.app.android.package = "com.example.app"
    ProviderFactory(unit_config).create("testmu").create_driver()

    assert "@" not in captured["url"]
    header = captured["client_config"].get_auth_header()["Authorization"]
    assert base64.b64decode(header.split()[1]).decode() == "user@x:k3y/+"


def test_local_capabilities(unit_config: FrameworkConfig) -> None:
    unit_config.app.android.package = "com.example.app"
    provider = ProviderFactory(unit_config).create("local", device="pixel_8")
    assert isinstance(provider, LocalProvider)
    caps = provider.build_capabilities()
    assert caps["automationName"] == "UiAutomator2"
    assert caps["appPackage"] == "com.example.app"


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute_script(self, script: str, args: Any = None) -> Any:
        self.calls.append((script, args))
        if script == "lambda-adb" and args["text"].startswith("top"):
            raise RuntimeError("command not allowed")
        return "OUTPUT"


def test_testmu_adb_executor_falls_back_and_remembers() -> None:
    driver = _FakeDriver()
    executor = TestMuAdbExecutor(driver)
    assert executor.shell("dumpsys meminfo com.x") == "OUTPUT"
    with pytest.raises(CommandNotSupportedError):
        executor.shell("top -b -n 1")
    with pytest.raises(CommandNotSupportedError):
        executor.shell("top -b -n 1 -p 1")
    assert sum(1 for s, a in driver.calls if a and a.get("text", "").startswith("top")) == 1
