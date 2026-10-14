"""Production benchmarks: rate KPIs against production thresholds (Android vitals, SLOs, budgets).

Besides the ``TelemetrySummary`` KPIs, rules can use these derived metrics:

* ``crash_free_sessions_pct`` / ``anr_free_sessions_pct``: share of executed sessions without one
* ``failed_request_pct``: failed / total network requests
* ``slowest_step_ms``: slowest successful step that is a real measurement (``measured=True``)
"""

from __future__ import annotations

from collections.abc import Iterable

from core.config import ProductionBenchmarkSettings
from models.execution_model import TestExecution, TestStatus
from models.report_model import BenchmarkResult, BenchmarkStatus
from models.telemetry_model import CrashType, StepTiming, TelemetrySummary


def measured_steps(steps: Iterable[StepTiming]) -> list[StepTiming]:
    return [s for s in steps if s.success and s.measured]


def _pct(part: int, total: int) -> float | None:
    return round(part / total * 100.0, 2) if total else None


def execution_metrics(execution: TestExecution) -> dict[str, float | None]:
    summary = execution.summary
    metrics: dict[str, float | None] = {
        k: None if v is None else float(v)
        for k, v in summary.model_dump().items()
        if v is None or isinstance(v, int | float)
    }
    crashes = execution.telemetry.crashes
    metrics["crash_free_sessions_pct"] = 0.0 if any(c.crash_type != CrashType.ANR for c in crashes) else 100.0
    metrics["anr_free_sessions_pct"] = 0.0 if any(c.crash_type == CrashType.ANR for c in crashes) else 100.0
    metrics["failed_request_pct"] = _pct(summary.failed_requests, summary.request_count)
    steps = measured_steps(execution.telemetry.steps)
    metrics["slowest_step_ms"] = max((s.duration_ms for s in steps), default=None)
    return metrics


def run_metrics(executions: list[TestExecution], summary: TelemetrySummary) -> dict[str, float | None]:
    """Derived metrics for a whole run; session rates count executions that actually ran."""
    ran = [e for e in executions if e.status != TestStatus.SKIPPED]
    crashed = sum(1 for e in ran if any(c.crash_type != CrashType.ANR for c in e.telemetry.crashes))
    anr = sum(1 for e in ran if any(c.crash_type == CrashType.ANR for c in e.telemetry.crashes))
    steps = [s for e in ran for s in measured_steps(e.telemetry.steps)]
    return {
        "crash_free_sessions_pct": _pct(len(ran) - crashed, len(ran)),
        "anr_free_sessions_pct": _pct(len(ran) - anr, len(ran)),
        "failed_request_pct": _pct(summary.failed_requests, summary.request_count),
        "slowest_step_ms": max((s.duration_ms for s in steps), default=None),
    }


def rate(value: float | None, good: float, limit: float) -> BenchmarkStatus:
    if value is None:
        return BenchmarkStatus.NO_DATA
    sign = 1 if good > limit else -1
    if sign * value >= sign * good:
        return BenchmarkStatus.PASS
    if sign * value <= sign * limit:
        return BenchmarkStatus.FAIL
    return BenchmarkStatus.WARN


def evaluate_benchmarks(
    settings: ProductionBenchmarkSettings, metrics: dict[str, float | None]
) -> list[BenchmarkResult]:
    if not settings.enabled:
        return []
    results: list[BenchmarkResult] = []
    for rule_id, rule in settings.rules.items():
        value = metrics.get(rule.metric)
        results.append(
            BenchmarkResult(
                id=rule_id,
                title=rule.title,
                metric=rule.metric,
                unit=rule.unit,
                value=None if value is None else round(float(value), 3),
                good=rule.good,
                limit=rule.limit,
                higher_is_better=rule.higher_is_better,
                status=rate(value, rule.good, rule.limit),
                source=rule.source,
            )
        )
    return results


def slow_step_limit(settings: ProductionBenchmarkSettings) -> float | None:
    rule = next((r for r in settings.rules.values() if r.metric == "slowest_step_ms"), None)
    return rule.limit if settings.enabled and rule is not None else None
