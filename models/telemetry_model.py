"""Telemetry data models.

Every sample collected by a collector is a ``TelemetrySample`` subclass with a
``kind`` discriminator, which lets raw JSON Lines files be parsed back into
typed objects (``parse_sample``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class MetricKind(StrEnum):
    CPU = "cpu"
    MEMORY = "memory"
    BATTERY = "battery"
    BATTERY_USAGE = "battery_usage"
    FPS = "fps"
    NETWORK = "network"
    REQUEST = "request"
    STARTUP = "startup"
    STEP = "step"
    ITERATION = "iteration"
    CRASH = "crash"


class CrashType(StrEnum):
    JAVA_CRASH = "java_crash"
    NATIVE_CRASH = "native_crash"
    ANR = "anr"
    APP_DIED = "app_died"


class LaunchMode(StrEnum):
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"


def utcnow() -> datetime:
    return datetime.now(UTC)


class TelemetrySample(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=False)

    kind: MetricKind
    timestamp: datetime = Field(default_factory=utcnow)
    elapsed_sec: float = 0.0
    source: str = "unknown"
    tags: dict[str, str] = Field(default_factory=dict)


class CpuSnapshot(TelemetrySample):
    kind: Literal[MetricKind.CPU] = MetricKind.CPU
    app_cpu_pct: float = Field(description="App CPU normalised to 0-100 across all cores")
    app_cpu_raw_pct: float | None = Field(default=None, description="Per-core sum as reported by top")
    total_cpu_pct: float | None = None
    cores: int | None = None


class MemorySnapshot(TelemetrySample):
    kind: Literal[MetricKind.MEMORY] = MetricKind.MEMORY
    pss_mb: float
    rss_mb: float | None = None
    java_heap_mb: float | None = None
    native_heap_mb: float | None = None
    dalvik_heap_mb: float | None = None
    heap_alloc_mb: float | None = None


class BatterySnapshot(TelemetrySample):
    kind: Literal[MetricKind.BATTERY] = MetricKind.BATTERY
    level_pct: float
    temperature_c: float | None = None
    voltage_mv: float | None = None
    charging: bool | None = None


class BatteryUsage(TelemetrySample):
    """End-of-window battery consumption attributed to the app."""

    kind: Literal[MetricKind.BATTERY_USAGE] = MetricKind.BATTERY_USAGE
    app_power_mah: float | None = None
    capacity_mah: float | None = None
    drain_pct: float | None = None
    duration_sec: float | None = None


class FpsSnapshot(TelemetrySample):
    kind: Literal[MetricKind.FPS] = MetricKind.FPS
    fps: float
    total_frames: int = 0
    janky_frames: int = 0
    jank_pct: float = 0.0
    frame_drops: int = 0
    p50_ms: float | None = None
    p90_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None


class NetworkSnapshot(TelemetrySample):
    kind: Literal[MetricKind.NETWORK] = MetricKind.NETWORK
    rx_bytes: int
    tx_bytes: int
    rx_kbps: float = 0.0
    tx_kbps: float = 0.0


_ID_IN_PATH = re.compile(
    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    r"|(?<![0-9A-Za-z])(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{16,}(?![0-9A-Za-z])"
    r"|(?<!\d)\d{5,}(?!\d)"
)


class NetworkRequest(TelemetrySample):
    kind: Literal[MetricKind.REQUEST] = MetricKind.REQUEST
    url: str
    method: str = "GET"
    status: int = 0
    duration_ms: float = 0.0
    size_bytes: int = 0
    failed: bool = False
    operation: str | None = Field(
        default=None,
        description="Backend method behind a shared gateway URL (mPaaS operationType, GraphQL operationName)",
    )
    server_ms: float | None = Field(
        default=None, description="Server processing time reported by the backend"
    )
    phases: dict[str, float] = Field(
        default_factory=dict, description="HAR timings > 0: blocked, dns, connect, ssl, send, wait, receive"
    )
    trace_id: str | None = Field(default=None, description="Backend trace / request id for APM lookup")
    background: bool = Field(
        default=False, description="Analytics / logging / OS traffic that does not block the UI"
    )

    @property
    def started_at(self) -> datetime:
        return self.timestamp

    @property
    def ended_at(self) -> datetime:
        return self.timestamp + timedelta(milliseconds=self.duration_ms)

    @property
    def endpoint(self) -> str:
        """Operation name, else ``METHOD host/path`` without the query string and with
        ids in the path (UUIDs, hashes, long numbers) replaced by ``{id}``."""
        if self.operation:
            return self.operation
        path = _ID_IN_PATH.sub("{id}", self.url.split("?", 1)[0].split("://", 1)[-1])
        return f"{self.method} {path}"


class StartupMetrics(TelemetrySample):
    kind: Literal[MetricKind.STARTUP] = MetricKind.STARTUP
    mode: LaunchMode
    iteration: int
    total_time_ms: float
    wait_time_ms: float | None = None
    display_time_ms: float | None = None
    launch_state: str | None = Field(default=None, description="LaunchState reported by the OS")
    method: str = "adb"


class DriverCall(BaseModel):
    """Driver commands of one step, grouped by the test code that issued them."""

    code: str = Field(description="Call chain below the test, e.g. 'HomePage.wait_loaded > BasePage.find'")
    location: str | None = Field(default=None, description="Test source line, e.g. tests/x.py:42")
    command: str = Field(description="WebDriver command, e.g. findElement, clickElement")
    target: str | None = Field(default=None, description="Page element (page.element) or locator")
    count: int = 1
    failed: int = Field(default=0, description="Calls that raised, e.g. element not found while polling")
    command_ms: float = Field(description="Time inside the driver commands")
    wall_ms: float = Field(description="Command time plus test-side time spent between them (polling sleeps)")

    @property
    def method(self) -> str:
        return self.code.split(" > ", 1)[0]


class AppSignalType(StrEnum):
    SCREEN = "screen"
    STRICT_MODE = "strict_mode"
    SLOW_MAIN_THREAD = "slow_main_thread"
    SKIPPED_FRAMES = "skipped_frames"
    SLOW_FRAME = "slow_frame"


class AppSignal(BaseModel):
    """App-side evidence from the device log, naming the class / method involved."""

    type: AppSignalType
    name: str = Field(description="Activity, handler / callback class or app method")
    duration_ms: float | None = None
    count: int = 1
    main_thread: bool | None = None
    detail: str = ""
    frames: list[str] = Field(default_factory=list, description="Top stack frames (StrictMode)")
    device_time: str | None = None


class StepTiming(TelemetrySample):
    kind: Literal[MetricKind.STEP] = MetricKind.STEP
    name: str
    duration_ms: float
    iteration: int | None = None
    success: bool = True
    error: str | None = None
    screenshot: str | None = Field(default=None, description="Screenshot taken when the step ended")
    measured: bool = Field(
        default=True,
        description="False for steps dominated by deliberate test waits; excluded from latency budgets",
    )
    calls: list[DriverCall] = Field(default_factory=list)
    app_signals: list[AppSignal] = Field(default_factory=list)

    @property
    def started_at(self) -> datetime:
        return self.timestamp - timedelta(milliseconds=self.duration_ms)


class IterationMarker(TelemetrySample):
    kind: Literal[MetricKind.ITERATION] = MetricKind.ITERATION
    iteration: int


class CrashEvent(TelemetrySample):
    """An app crash, ANR or unexpected process death."""

    kind: Literal[MetricKind.CRASH] = MetricKind.CRASH
    crash_type: CrashType
    process: str | None = None
    pid: int | None = None
    exception: str | None = Field(default=None, description="Exception / signal / ANR reason line")
    stacktrace: str = ""
    step: str | None = Field(default=None, description="Test step during which the crash was detected")
    device_time: str | None = Field(default=None, description="Raw device log timestamp")

    @property
    def signature(self) -> str:
        return f"{self.crash_type}|{self.pid}|{self.exception}"


AnySample = Annotated[
    CpuSnapshot
    | MemorySnapshot
    | BatterySnapshot
    | BatteryUsage
    | FpsSnapshot
    | NetworkSnapshot
    | NetworkRequest
    | StartupMetrics
    | StepTiming
    | IterationMarker
    | CrashEvent,
    Field(discriminator="kind"),
]

_SAMPLE_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnySample)


def parse_sample(data: dict[str, Any] | str) -> TelemetrySample:
    if isinstance(data, str):
        return _SAMPLE_ADAPTER.validate_json(data)
    return _SAMPLE_ADAPTER.validate_python(data)


_KIND_TO_FIELD: dict[MetricKind, str] = {
    MetricKind.CPU: "cpu",
    MetricKind.MEMORY: "memory",
    MetricKind.BATTERY: "battery",
    MetricKind.BATTERY_USAGE: "battery_usage",
    MetricKind.FPS: "fps",
    MetricKind.NETWORK: "network",
    MetricKind.REQUEST: "requests",
    MetricKind.STARTUP: "startup",
    MetricKind.STEP: "steps",
    MetricKind.ITERATION: "iterations",
    MetricKind.CRASH: "crashes",
}


class TelemetryData(BaseModel):
    """All telemetry captured for one test execution, grouped by kind."""

    cpu: list[CpuSnapshot] = Field(default_factory=list)
    memory: list[MemorySnapshot] = Field(default_factory=list)
    battery: list[BatterySnapshot] = Field(default_factory=list)
    battery_usage: list[BatteryUsage] = Field(default_factory=list)
    fps: list[FpsSnapshot] = Field(default_factory=list)
    network: list[NetworkSnapshot] = Field(default_factory=list)
    requests: list[NetworkRequest] = Field(default_factory=list)
    startup: list[StartupMetrics] = Field(default_factory=list)
    steps: list[StepTiming] = Field(default_factory=list)
    iterations: list[IterationMarker] = Field(default_factory=list)
    crashes: list[CrashEvent] = Field(default_factory=list)

    def add(self, sample: TelemetrySample) -> None:
        getattr(self, _KIND_TO_FIELD[sample.kind]).append(sample)

    def extend(self, samples: list[TelemetrySample]) -> None:
        for sample in samples:
            self.add(sample)

    def merge(self, other: TelemetryData) -> TelemetryData:
        for field in _KIND_TO_FIELD.values():
            getattr(self, field).extend(getattr(other, field))
        return self

    def merge_missing(self, other: TelemetryData) -> list[str]:
        """Merge only the kinds this instance has no usable data for; returns merged field names.

        A CPU series that is zero throughout counts as missing: a foreground app is never
        at exactly 0%, so it means the live source (e.g. Appium ``cpuinfo``) saw nothing.
        """
        merged: list[str] = []
        for field in _KIND_TO_FIELD.values():
            incoming = getattr(other, field)
            if not incoming:
                continue
            current = getattr(self, field)
            if field == "cpu" and current and all(not s.app_cpu_pct for s in current):
                current.clear()
            if not current:
                current.extend(incoming)
                merged.append(field)
        return merged

    def all_samples(self) -> list[TelemetrySample]:
        samples: list[TelemetrySample] = []
        for field in _KIND_TO_FIELD.values():
            samples.extend(getattr(self, field))
        return sorted(samples, key=lambda s: s.timestamp)

    @property
    def sample_count(self) -> int:
        return sum(len(getattr(self, field)) for field in _KIND_TO_FIELD.values())

    def is_empty(self) -> bool:
        return self.sample_count == 0


class TelemetrySummary(BaseModel):
    """Aggregated KPIs derived from ``TelemetryData`` (see ``core.analysis.summarize``)."""

    cold_start_avg_ms: float | None = None
    warm_start_avg_ms: float | None = None
    hot_start_avg_ms: float | None = None
    cold_start_p90_ms: float | None = None
    startup_iterations: int = 0

    peak_pss_mb: float | None = None
    avg_pss_mb: float | None = None
    peak_heap_mb: float | None = None
    memory_growth_pct: float | None = None

    avg_cpu_pct: float | None = None
    max_cpu_pct: float | None = None
    p90_cpu_pct: float | None = None

    battery_drain_pct: float | None = None
    battery_drain_pct_per_hour: float | None = None
    app_power_mah: float | None = None
    max_temperature_c: float | None = None

    avg_fps: float | None = None
    min_fps: float | None = None
    jank_pct: float | None = None
    frame_drops: int | None = None

    avg_rx_kbps: float | None = None
    avg_tx_kbps: float | None = None
    total_rx_kb: float | None = None
    total_tx_kb: float | None = None
    request_count: int = 0
    failed_requests: int = 0
    avg_response_ms: float | None = None
    p95_response_ms: float | None = None

    crash_count: int = 0
    anr_count: int = 0

    step_avg_ms: dict[str, float] = Field(default_factory=dict)
    duration_sec: float | None = None

    def metric(self, name: str) -> float | None:
        value = getattr(self, name, None)
        return float(value) if isinstance(value, int | float) else None
