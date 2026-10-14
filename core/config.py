"""Typed configuration loaded from ``config/*.yaml`` with env-var interpolation.

Interpolation syntax: ``${VAR}`` or ``${VAR:-default}``. It is applied to
string leaves *after* YAML parsing, so environment values can never inject
YAML structure.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

from models.execution_model import Platform

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


class ConfigError(ValueError):
    """Raised for invalid or incomplete framework configuration."""


def interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m["name"], m["default"] or ""), value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


class FrameworkSettings(BaseModel):
    name: str = "Mobile Performance Framework"
    report_dir: Path = Path("reports")
    run_label: str = "local"
    plugins: list[str] = Field(default_factory=list)


class ProviderSettings(BaseModel):
    name: str = "simulated"
    device: str | None = None
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    normalize_device = field_validator("device", mode="before")(_blank_to_none)


class TelemetrySettings(BaseModel):
    enabled: bool = True
    sampling_interval_sec: float = Field(default=1.0, gt=0)
    collectors: list[str] = Field(default_factory=lambda: ["cpu", "memory", "battery", "network", "fps"])
    ios_backend: str = "xcuitest"
    queue_maxsize: int = 10_000
    max_consecutive_errors: int = 5
    refresh_rate_hz: int = 60
    battery_simulate_unplug: bool = True


class StartupSettings(BaseModel):
    iterations: int = Field(default=5, ge=1)
    warmup_iterations: int = Field(default=1, ge=0)
    modes: list[str] = Field(default_factory=lambda: ["cold", "warm", "hot"])
    settle_time_sec: float = 2.0


class CrashSettings(BaseModel):
    enabled: bool = True
    check_after_each_step: bool = True
    poll_device_log: bool = True
    collect_post_session: bool = True
    fail_on_crash: bool = True
    max_stacktrace_lines: int = Field(default=60, ge=5)


class AttributionSettings(BaseModel):
    """Root-cause attribution of slow steps (see ``core.attribution``)."""

    enabled: bool = True
    # Time every Appium/WebDriver command and the test / page-object method that issued it
    trace_driver_commands: bool = True
    # Extract screen classes, StrictMode stacks, slow main-thread messages and skipped
    # frames from the device log read after each step (needs crash.check_after_each_step)
    app_log_signals: bool = True
    # Package prefixes of the app's own code (e.g. com.adw); preferred when naming the
    # method in a stack trace. Defaults to the application id.
    app_packages: list[str] = Field(default_factory=list)
    # Extra hosts (suffix match) whose calls never block the UI, on top of the built-in
    # analytics / logging / crash-reporting / OS-update list. They are still listed, but
    # never named as the cause of a slow step.
    background_hosts: list[str] = Field(default_factory=list)

    @field_validator("app_packages", "background_hosts", mode="before")
    @classmethod
    def _split(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class EnduranceSettings(BaseModel):
    iterations: int = Field(default=50, ge=1)
    leak_slope_threshold_mb_per_min: float = 2.0
    memory_growth_threshold_pct: float = 10.0
    min_samples: int = Field(default=8, ge=3)
    cpu_growth_threshold_pct: float = 20.0
    degradation_threshold_pct: float = 20.0
    window_fraction: float = Field(default=0.2, gt=0, le=0.5)
    fail_on_leak: bool = False


class ThresholdSpec(BaseModel):
    metric: str
    target: float
    limit: float

    @property
    def higher_is_better(self) -> bool:
        return self.target > self.limit

    @model_validator(mode="after")
    def _distinct(self) -> ThresholdSpec:
        if self.target == self.limit:
            raise ValueError(f"threshold for {self.metric}: target and limit must differ")
        return self


class ScoringSettings(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "startup": 25,
            "memory": 20,
            "cpu": 20,
            "battery": 10,
            "fps": 15,
            "network": 10,
        }
    )
    thresholds: dict[str, ThresholdSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> ScoringSettings:
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("scoring weights must not be negative")
        if sum(self.weights.values()) <= 0:
            raise ValueError("scoring weights must sum to a positive number")
        missing = set(self.weights) - set(self.thresholds)
        if missing and self.thresholds:
            raise ValueError(f"missing scoring thresholds for: {sorted(missing)}")
        return self


class ComparisonSettings(BaseModel):
    baseline: Path | None = None
    fail_on_regression: bool = False
    max_regression_pct: dict[str, float] = Field(default_factory=dict)

    normalize_baseline = field_validator("baseline", mode="before")(_blank_to_none)


class ScreenshotMode(StrEnum):
    EVERY_STEP = "every_step"
    ON_FAILURE = "on_failure"
    OFF = "off"


class ReportingSettings(BaseModel):
    title: str = "Mobile Performance Dashboard"
    plotly_mode: str = "cdn"
    history_file: Path = Path("history/trend.json")
    history_size: int = 50
    screenshots: ScreenshotMode = ScreenshotMode.EVERY_STEP
    screenshot_max_width: int = Field(default=540, ge=0, description="0 keeps the original PNG")
    screenshot_quality: int = Field(default=70, ge=10, le=95)
    keep_runs: int = Field(default=30, ge=0, description="Run folders kept under reports/runs (0 = all)")


class BenchmarkRule(BaseModel):
    """``good`` = healthy production value, ``limit`` = bad-behaviour threshold."""

    title: str
    metric: str
    good: float
    limit: float
    unit: str = ""
    source: str = ""

    @property
    def higher_is_better(self) -> bool:
        return self.good > self.limit

    @model_validator(mode="after")
    def _distinct(self) -> BenchmarkRule:
        if self.good == self.limit:
            raise ValueError(f"benchmark {self.metric}: good and limit must differ")
        return self


class ProductionBenchmarkSettings(BaseModel):
    enabled: bool = True
    rules: dict[str, BenchmarkRule] = Field(default_factory=dict)
    fail_on_breach: bool = False


class DeviceProfile(BaseModel):
    key: str = ""
    platform: Platform
    device_name: str
    platform_version: str
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PlatformApp(BaseModel):
    package: str | None = None
    activity: str | None = None
    bundle_id: str | None = None
    app: str | None = None

    normalize_blanks = field_validator("package", "activity", "bundle_id", "app", mode="before")(
        _blank_to_none
    )


class BuildSpec(BaseModel):
    label: str
    app: str | None = None

    normalize_blanks = field_validator("app", mode="before")(_blank_to_none)


class AppSettings(BaseModel):
    name: str = "Sample App"
    version: str = "1.0.0"
    build_number: str = "local"
    android: PlatformApp = Field(default_factory=PlatformApp)
    ios: PlatformApp = Field(default_factory=PlatformApp)
    builds: dict[str, BuildSpec] = Field(default_factory=dict)
    test_data: dict[str, Any] = Field(default_factory=dict)

    def for_platform(self, platform: Platform) -> PlatformApp:
        return self.android if platform == Platform.ANDROID else self.ios

    def identifier(self, platform: Platform) -> str | None:
        app = self.for_platform(platform)
        return app.package if platform == Platform.ANDROID else app.bundle_id


class FrameworkConfig(BaseModel):
    framework: FrameworkSettings = Field(default_factory=FrameworkSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    startup: StartupSettings = Field(default_factory=StartupSettings)
    endurance: EnduranceSettings = Field(default_factory=EnduranceSettings)
    crash: CrashSettings = Field(default_factory=CrashSettings)
    attribution: AttributionSettings = Field(default_factory=AttributionSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    comparison: ComparisonSettings = Field(default_factory=ComparisonSettings)
    reporting: ReportingSettings = Field(default_factory=ReportingSettings)
    benchmarks: ProductionBenchmarkSettings = Field(default_factory=ProductionBenchmarkSettings)
    devices: dict[str, DeviceProfile] = Field(default_factory=dict)
    device_defaults: dict[Platform, str] = Field(default_factory=dict)
    app: AppSettings = Field(default_factory=AppSettings)
    locators: dict[str, dict[str, dict[str, list[str]]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys(self) -> FrameworkConfig:
        for key, device in self.devices.items():
            device.key = key
        return self

    @property
    def report_dir(self) -> Path:
        path = self.framework.report_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    def run_dir(self, run_id: str) -> Path:
        """Self-contained folder of one run: report, drilldown pages, exports, raw data, screenshots."""
        return self.report_dir / "runs" / run_id

    def provider_options(self, name: str | None = None) -> dict[str, Any]:
        return dict(self.provider.providers.get(name or self.provider.name, {}))

    def device_profile(self, key: str | None = None, platform: Platform | None = None) -> DeviceProfile:
        key = key or self.provider.device
        if not key and platform:
            key = self.device_defaults.get(platform)
        if not key:
            key = self.device_defaults.get(Platform.ANDROID) or next(iter(self.devices), None)
        if not key or key not in self.devices:
            raise ConfigError(f"Unknown device profile '{key}'. Available: {sorted(self.devices)}")
        return self.devices[key]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return data


def load_config(
    config_dir: Path | None = None,
    overrides: dict[str, Any] | None = None,
    *,
    env_file: Path | None = None,
) -> FrameworkConfig:
    """Load framework.yaml + devices.yaml + app.yaml into a ``FrameworkConfig``.

    ``overrides`` is a nested dict deep-merged last (used for CLI options).
    """
    config_dir = config_dir or Path(os.environ.get("PERF_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    framework = _read_yaml(config_dir / "framework.yaml")
    devices = _read_yaml(config_dir / "devices.yaml")
    app = _read_yaml(config_dir / "app.yaml")

    raw: dict[str, Any] = dict(framework)
    raw["devices"] = devices.get("devices", {})
    raw["device_defaults"] = devices.get("defaults", {})
    raw["app"] = app.get("app", {})
    raw["locators"] = app.get("locators", {})
    raw = interpolate_env(raw)
    if overrides:
        raw = deep_merge(raw, overrides)
    try:
        return FrameworkConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid framework configuration: {exc}") from exc
