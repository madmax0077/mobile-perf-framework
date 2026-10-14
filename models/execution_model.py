"""Execution-level models: device, app, per-test execution and run summary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from models.report_model import (
    AnalysisFinding,
    BenchmarkResult,
    ComparisonResult,
    Issue,
    PerformanceScore,
    Readiness,
    StepBreakdown,
)
from models.telemetry_model import TelemetryData, TelemetrySummary


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class TestStatus(StrEnum):
    __test__ = False

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class TestType(StrEnum):
    __test__ = False

    STARTUP = "startup"
    JOURNEY = "journey"
    ENDURANCE = "endurance"
    BENCHMARK = "benchmark"
    GENERIC = "perf"


class DeviceInfo(BaseModel):
    name: str
    platform: Platform
    os_version: str
    provider: str
    udid: str | None = None
    model: str | None = None
    is_real_device: bool | None = None


class AppInfo(BaseModel):
    name: str
    version: str
    build_number: str
    identifier: str | None = Field(default=None, description="Android package or iOS bundle id")


class TestExecution(BaseModel):
    __test__ = False

    test_id: str
    name: str
    test_type: TestType = TestType.GENERIC
    label: str | None = Field(default=None, description="Build label for build-comparison sessions")
    status: TestStatus = TestStatus.PASSED
    started_at: datetime
    finished_at: datetime | None = None
    duration_sec: float = 0.0
    device: DeviceInfo
    app: AppInfo
    provider_session_id: str | None = None
    telemetry: TelemetryData = Field(default_factory=TelemetryData)
    summary: TelemetrySummary = Field(default_factory=TelemetrySummary)
    score: PerformanceScore = Field(default_factory=PerformanceScore)
    findings: list[AnalysisFinding] = Field(default_factory=list)
    benchmarks: list[BenchmarkResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    breakdowns: list[StepBreakdown] = Field(
        default_factory=list, description="Where each step's time went (see core.attribution)"
    )
    comparison: ComparisonResult | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path


class RunSummary(BaseModel):
    run_id: str
    label: str
    started_at: datetime
    finished_at: datetime | None = None
    provider: str
    app: AppInfo
    device: DeviceInfo | None = None
    executions: list[TestExecution] = Field(default_factory=list)
    score: PerformanceScore = Field(default_factory=PerformanceScore)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    benchmarks: list[BenchmarkResult] = Field(default_factory=list)
    readiness: Readiness = Readiness.NOT_EVALUATED
    comparisons: list[ComparisonResult] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        if not self.finished_at:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> RunSummary:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
