"""Models produced by scoring, analysis, comparison and trend tracking."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Rating(StrEnum):
    EXCELLENT = "Excellent"
    GOOD = "Good"
    ACCEPTABLE = "Acceptable"
    NEEDS_IMPROVEMENT = "Needs Improvement"
    NOT_RATED = "Not Rated"

    @classmethod
    def from_score(cls, score: float | None) -> Rating:
        if score is None:
            return cls.NOT_RATED
        if score >= 90:
            return cls.EXCELLENT
        if score >= 80:
            return cls.GOOD
        if score >= 70:
            return cls.ACCEPTABLE
        return cls.NEEDS_IMPROVEMENT


class CategoryScore(BaseModel):
    category: str
    metric: str
    value: float | None = None
    score: float | None = None
    weight: float
    effective_weight: float = 0.0


class PerformanceScore(BaseModel):
    overall: float | None = None
    rating: Rating = Rating.NOT_RATED
    categories: list[CategoryScore] = Field(default_factory=list)

    def category(self, name: str) -> CategoryScore | None:
        return next((c for c in self.categories if c.category == name), None)


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"

    @property
    def rank(self) -> int:
        return {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2}[self]


class Issue(BaseModel):
    """A problem found in one execution; ``id`` is the deep-link anchor on its drilldown page."""

    id: str
    severity: Severity
    category: str = Field(
        description="crash | anr | test_failure | step_failure | slow_step | benchmark | finding | network"
    )
    title: str
    detail: str = ""
    step: str | None = None
    step_index: int | None = Field(default=None, description="1-based index into the execution's steps")
    target: str | None = Field(default=None, description="Anchor of the related element (step, crash, ...)")
    timestamp: datetime | None = None
    cause: str | None = Field(
        default=None, description="The method / class / backend operation that made it slow, when known"
    )


class CulpritLayer(StrEnum):
    APP = "app"
    API = "api"
    TEST = "test"

    @property
    def label(self) -> str:
        return {
            CulpritLayer.APP: "App code",
            CulpritLayer.API: "Backend API",
            CulpritLayer.TEST: "Test code",
        }[self]


class Culprit(BaseModel):
    """One named piece of code that consumed part of a step's time."""

    layer: CulpritLayer
    name: str = Field(description="Exact class / method / backend operation")
    kind: str = Field(description="e.g. api_call, driver_command, strict_mode, screen, skipped_frames")
    detail: str = ""
    duration_ms: float | None = None
    share_pct: float | None = Field(default=None, description="Share of the step's duration")
    offset_ms: float | None = Field(default=None, description="Start relative to the step start")
    location: str | None = Field(default=None, description="Source line, stack frame or URL")
    trace_id: str | None = None


class StepBreakdown(BaseModel):
    """Where one step's time went, with the culprit named at app, backend and test level."""

    step_index: int
    step: str
    duration_ms: float
    cause: str = Field(description="One-line root cause, e.g. 'ap.mobile.payments.queryPayMethod (603 ms)'")
    summary: str = Field(description="Explanation of the cause")
    primary: Culprit | None = None
    culprits: list[Culprit] = Field(default_factory=list)
    network_busy_ms: float | None = Field(default=None, description="Union of API calls overlapping the step")
    driver_ms: float | None = Field(default=None, description="Time inside driver commands")

    def layer(self, layer: CulpritLayer) -> list[Culprit]:
        return [c for c in self.culprits if c.layer == layer]


class BenchmarkStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NO_DATA = "no_data"


class BenchmarkResult(BaseModel):
    """A metric checked against a production benchmark (``good`` = healthy, ``limit`` = bad behaviour)."""

    id: str
    title: str
    metric: str
    unit: str = ""
    value: float | None = None
    good: float
    limit: float
    higher_is_better: bool
    status: BenchmarkStatus = BenchmarkStatus.NO_DATA
    source: str = ""


class Readiness(StrEnum):
    READY = "Ready"
    AT_RISK = "At Risk"
    NOT_READY = "Not Ready"
    NOT_EVALUATED = "Not Evaluated"

    @classmethod
    def from_results(cls, results: list[BenchmarkResult]) -> Readiness:
        statuses = {r.status for r in results}
        if BenchmarkStatus.FAIL in statuses:
            return cls.NOT_READY
        if BenchmarkStatus.WARN in statuses:
            return cls.AT_RISK
        if BenchmarkStatus.PASS in statuses:
            return cls.READY
        return cls.NOT_EVALUATED


class AnalysisFinding(BaseModel):
    kind: str = Field(description="memory_leak | cpu_growth | response_degradation")
    detected: bool
    metric: str
    slope: float | None = None
    growth_pct: float | None = None
    threshold: float | None = None
    message: str = ""


class MetricDelta(BaseModel):
    metric: str
    baseline: float | None = None
    candidate: float | None = None
    diff_pct: float | None = None
    higher_is_better: bool = False
    threshold_pct: float | None = None
    regression: bool = False
    improvement: bool = False


class ComparisonResult(BaseModel):
    baseline_label: str
    candidate_label: str
    deltas: list[MetricDelta] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(d.regression for d in self.deltas)

    def delta(self, metric: str) -> MetricDelta | None:
        return next((d for d in self.deltas if d.metric == metric), None)


class TrendPoint(BaseModel):
    run_id: str
    timestamp: datetime
    label: str
    app_version: str | None = None
    build_number: str | None = None
    device: str | None = None
    score: float | None = None
    metrics: dict[str, float | None] = Field(default_factory=dict)
