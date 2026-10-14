"""Versioned, machine-readable export of one run (``performance_data.json``).

Bump ``SCHEMA_VERSION`` on breaking changes (removed / renamed / retyped fields);
adding optional fields is backwards compatible. The JSON Schema is published next
to the data as ``performance_data.schema.json``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from models.execution_model import AppInfo, DeviceInfo, TestStatus, TestType
from models.report_model import BenchmarkResult, Issue, PerformanceScore, Readiness, StepBreakdown
from models.telemetry_model import CrashEvent

SCHEMA_ID = "mobile-perf/performance-data"
# 1.1: steps[].cause / breakdown, executions[].operations
SCHEMA_VERSION = "1.1"


class MetricStats(BaseModel):
    """Distribution of one time series (all values in ``unit``)."""

    unit: str
    count: int
    min: float | None = None
    mean: float | None = None
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None
    max: float | None = None


class StepRecord(BaseModel):
    index: int = Field(description="1-based position in the execution")
    name: str
    iteration: int | None = None
    started_at: datetime
    finished_at: datetime
    offset_sec: float = Field(description="Seconds from execution start to step start")
    duration_ms: float
    success: bool
    measured: bool
    error: str | None = None
    screenshot: str | None = Field(default=None, description="Path relative to the run folder")
    link: str = Field(description="Report deep link relative to the run folder")
    cause: str | None = Field(
        default=None, description="Method / class / backend operation that took the most time"
    )
    breakdown: StepBreakdown | None = Field(default=None, description="Evidence per layer (app, API, test)")


class OperationRecord(BaseModel):
    """Latency of one backend operation (or URL path) within an execution."""

    name: str
    count: int
    failed: int
    avg_ms: float
    p95_ms: float
    max_ms: float
    server_avg_ms: float | None = None
    setup_avg_ms: float | None = Field(default=None, description="DNS + TCP + TLS")
    total_ms: float
    trace_id: str | None = Field(default=None, description="Trace id of the slowest call")
    background: bool = Field(default=False, description="Analytics / logging / OS traffic")


class StartupRecord(BaseModel):
    mode: str
    iteration: int
    total_time_ms: float
    method: str


class IssueRecord(Issue):
    execution_id: str
    test: str
    link: str = Field(description="Report deep link relative to the run folder")


class ExecutionLinks(BaseModel):
    report: str
    video: str | None = None
    device_logs: str | None = None
    crash_logs: str | None = None
    network_logs: str | None = None
    final_screenshot: str | None = None


class ExecutionRecord(BaseModel):
    id: str = Field(description="Stable within the run; also the drilldown page name")
    test_id: str
    name: str
    type: TestType
    label: str | None = None
    status: TestStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_sec: float
    device: DeviceInfo
    app: AppInfo
    provider_session_id: str | None = None
    score: float | None = None
    rating: str
    kpis: dict[str, float | None] = Field(description="Summary KPIs plus derived benchmark metrics")
    metrics: dict[str, MetricStats] = Field(description="Distribution per time series, e.g. cpu.app_cpu_pct")
    startup: list[StartupRecord] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    operations: list[OperationRecord] = Field(default_factory=list, description="Slowest p95 first")
    crashes: list[CrashEvent] = Field(default_factory=list)
    benchmarks: list[BenchmarkResult] = Field(default_factory=list)
    issues: list[IssueRecord] = Field(default_factory=list)
    links: ExecutionLinks
    error: str | None = None


class RunRecord(BaseModel):
    run_id: str
    label: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_sec: float
    provider: str
    report: str = Field(description="Run report, relative to the run folder")
    environment: dict[str, str] = Field(default_factory=dict)


class ReadinessRecord(BaseModel):
    verdict: Readiness
    benchmarks: list[BenchmarkResult] = Field(default_factory=list)


class PerformanceData(BaseModel):
    schema_id: str = SCHEMA_ID
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run: RunRecord
    app: AppInfo
    device: DeviceInfo | None = None
    score: PerformanceScore
    readiness: ReadinessRecord
    kpis: dict[str, float | None] = Field(description="Run-level KPIs across all executions")
    metrics: dict[str, MetricStats] = Field(description="Run-level distribution per time series")
    executions: list[ExecutionRecord] = Field(default_factory=list)
    issues: list[IssueRecord] = Field(default_factory=list, description="All issues, most severe first")
