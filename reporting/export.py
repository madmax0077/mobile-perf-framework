"""Production-grade data export: ``performance_data.json`` (+ JSON Schema) and tidy CSV files.

CSV files are long-format (one value per row) so they load directly into Grafana,
ELK, BigQuery, Excel or pandas without reshaping:

* ``csv/samples.csv``     every telemetry sample value
* ``csv/steps.csv``       step timings with screenshots and errors
* ``csv/requests.csv``    network requests
* ``csv/kpis.csv``        run and execution KPIs
* ``csv/benchmarks.csv``  production benchmark results
* ``csv/issues.csv``      issues with report deep links
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from core.attribution import operation_stats
from core.benchmarks import execution_metrics
from models.execution_model import RunSummary, TestExecution
from models.performance_data import (
    ExecutionLinks,
    ExecutionRecord,
    IssueRecord,
    MetricStats,
    OperationRecord,
    PerformanceData,
    ReadinessRecord,
    RunRecord,
    StartupRecord,
    StepRecord,
)
from models.report_model import StepBreakdown
from models.telemetry_model import TelemetryData, TelemetrySample
from reporting.links import execution_page, relative_to

# series key -> (TelemetryData field, sample attribute, unit)
SERIES: dict[str, tuple[str, str, str]] = {
    "cpu.app_cpu_pct": ("cpu", "app_cpu_pct", "%"),
    "cpu.total_cpu_pct": ("cpu", "total_cpu_pct", "%"),
    "memory.pss_mb": ("memory", "pss_mb", "MB"),
    "memory.java_heap_mb": ("memory", "java_heap_mb", "MB"),
    "memory.native_heap_mb": ("memory", "native_heap_mb", "MB"),
    "fps.fps": ("fps", "fps", "fps"),
    "fps.jank_pct": ("fps", "jank_pct", "%"),
    "battery.level_pct": ("battery", "level_pct", "%"),
    "battery.temperature_c": ("battery", "temperature_c", "C"),
    "network.rx_kbps": ("network", "rx_kbps", "kbit/s"),
    "network.tx_kbps": ("network", "tx_kbps", "kbit/s"),
    "requests.duration_ms": ("requests", "duration_ms", "ms"),
    "startup.total_time_ms": ("startup", "total_time_ms", "ms"),
    "steps.duration_ms": ("steps", "duration_ms", "ms"),
}


def _series_values(data: TelemetryData, field: str, attr: str) -> list[tuple[TelemetrySample, float]]:
    values = []
    for sample in getattr(data, field):
        if field == "steps" and not (sample.success and sample.measured):
            continue
        value = getattr(sample, attr, None)
        if isinstance(value, int | float):
            values.append((sample, float(value)))
    return values


def _stats(values: Sequence[float], unit: str) -> MetricStats:
    arr = np.asarray(values, dtype=float)
    p50, p90, p95, p99 = (round(float(v), 3) for v in np.percentile(arr, [50, 90, 95, 99]))
    return MetricStats(
        unit=unit,
        count=len(arr),
        min=round(float(arr.min()), 3),
        mean=round(float(arr.mean()), 3),
        p50=p50,
        p90=p90,
        p95=p95,
        p99=p99,
        max=round(float(arr.max()), 3),
    )


def _cause(found: StepBreakdown | None) -> str | None:
    return found.cause if found is not None and found.primary is not None else None


def series_stats(datasets: Iterable[TelemetryData]) -> dict[str, MetricStats]:
    datasets = list(datasets)
    stats: dict[str, MetricStats] = {}
    for key, (field, attr, unit) in SERIES.items():
        values = [v for data in datasets for _, v in _series_values(data, field, attr)]
        if values:
            stats[key] = _stats(values, unit)
    return stats


class PerformanceDataExporter:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def _rel(self, path: str | None) -> str | None:
        return relative_to(path, self.run_dir)

    def _execution(self, execution: TestExecution, slug: str) -> ExecutionRecord:
        meta = execution.provider_metadata
        start = execution.started_at
        breakdowns = {b.step_index: b for b in execution.breakdowns}
        steps = [
            StepRecord(
                index=i,
                name=s.name,
                iteration=s.iteration,
                started_at=s.started_at,
                finished_at=s.timestamp,
                offset_sec=round((s.started_at - start).total_seconds(), 3),
                duration_ms=s.duration_ms,
                success=s.success,
                measured=s.measured,
                error=s.error,
                screenshot=self._rel(s.screenshot),
                link=execution_page(slug, f"step-{i}"),
                cause=_cause(breakdowns.get(i)),
                breakdown=breakdowns.get(i),
            )
            for i, s in enumerate(execution.telemetry.steps, 1)
        ]
        return ExecutionRecord(
            id=slug,
            test_id=execution.test_id,
            name=execution.name,
            type=execution.test_type,
            label=execution.label,
            status=execution.status,
            started_at=start,
            finished_at=execution.finished_at,
            duration_sec=execution.duration_sec,
            device=execution.device,
            app=execution.app,
            provider_session_id=execution.provider_session_id,
            score=execution.score.overall,
            rating=execution.score.rating.value,
            kpis=execution_metrics(execution),
            metrics=series_stats([execution.telemetry]),
            startup=[
                StartupRecord(
                    mode=s.mode.value, iteration=s.iteration, total_time_ms=s.total_time_ms, method=s.method
                )
                for s in execution.telemetry.startup
            ],
            steps=steps,
            operations=[
                OperationRecord(**asdict(op)) for op in operation_stats(execution.telemetry.requests)
            ],
            crashes=execution.telemetry.crashes,
            benchmarks=execution.benchmarks,
            issues=[self._issue(issue, execution, slug) for issue in execution.issues],
            links=ExecutionLinks(
                report=execution_page(slug),
                video=meta.get("video_url"),
                device_logs=meta.get("device_logs_url"),
                crash_logs=meta.get("crash_logs_url"),
                network_logs=meta.get("network_logs_url"),
                final_screenshot=self._rel(
                    execution.artifacts.get("screenshot") or execution.artifacts.get("final_screenshot")
                ),
            ),
            error=execution.error,
        )

    @staticmethod
    def _issue(issue: Any, execution: TestExecution, slug: str) -> IssueRecord:
        return IssueRecord(
            **issue.model_dump(), execution_id=slug, test=execution.name, link=execution_page(slug, issue.id)
        )

    def build(self, run: RunSummary, slugs: list[str]) -> PerformanceData:
        executions = [self._execution(e, slug) for e, slug in zip(run.executions, slugs, strict=True)]
        issues = sorted(
            (i for e in executions for i in e.issues), key=lambda i: (i.severity.rank, i.execution_id, i.id)
        )
        return PerformanceData(
            generated_at=datetime.now(UTC),
            run=RunRecord(
                run_id=run.run_id,
                label=run.label,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_sec=round(run.duration_sec, 2),
                provider=run.provider,
                report="index.html",
                environment=run.environment,
            ),
            app=run.app,
            device=run.device,
            score=run.score,
            readiness=ReadinessRecord(verdict=run.readiness, benchmarks=run.benchmarks),
            kpis=dict(run.metrics),
            metrics=series_stats(e.telemetry for e in run.executions),
            executions=executions,
            issues=issues,
        )

    def write(self, run: RunSummary, slugs: list[str]) -> Path:
        data = self.build(run, slugs)
        path = self.run_dir / "performance_data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        (self.run_dir / "performance_data.schema.json").write_text(
            json.dumps(PerformanceData.model_json_schema(), indent=2), encoding="utf-8"
        )
        self._write_csv(run, slugs, data)
        return path

    # ------------------------------------------------------------------ csv
    def _write_csv(self, run: RunSummary, slugs: list[str], data: PerformanceData) -> None:
        out = self.run_dir / "csv"
        out.mkdir(parents=True, exist_ok=True)
        base = {"run_id": run.run_id}
        samples: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        for execution, slug in zip(run.executions, slugs, strict=True):
            owner = {**base, "execution_id": slug, "test": execution.name}
            for key, (field, attr, unit) in SERIES.items():
                if field == "steps":
                    continue
                for sample, value in _series_values(execution.telemetry, field, attr):
                    samples.append(
                        {
                            **owner,
                            "metric": key,
                            "timestamp": sample.timestamp.isoformat(),
                            "elapsed_sec": sample.elapsed_sec,
                            "value": value,
                            "unit": unit,
                            "source": sample.source,
                        }
                    )
            requests += [
                {**owner, **r.model_dump(include=REQUEST_FIELDS), "timestamp": r.timestamp.isoformat()}
                for r in execution.telemetry.requests
            ]
        steps = [
            {
                **base,
                "execution_id": e.id,
                "test": e.name,
                **s.model_dump(mode="json", exclude={"finished_at", "offset_sec"}),
            }
            for e in data.executions
            for s in e.steps
        ]
        kpis = [
            {**base, "execution_id": "", "test": "(run)", "metric": k, "value": v}
            for k, v in data.kpis.items()
        ]
        kpis += [
            {**base, "execution_id": e.id, "test": e.name, "metric": k, "value": v}
            for e in data.executions
            for k, v in e.kpis.items()
        ]
        benchmarks = [
            {**base, "scope": "run", "execution_id": "", **b.model_dump(mode="json")}
            for b in data.readiness.benchmarks
        ]
        benchmarks += [
            {**base, "scope": "execution", "execution_id": e.id, **b.model_dump(mode="json")}
            for e in data.executions
            for b in e.benchmarks
        ]
        issues = [{**base, **i.model_dump(mode="json", include=ISSUE_FIELDS)} for i in data.issues]
        _csv(out / "samples.csv", samples)
        _csv(out / "requests.csv", requests)
        _csv(out / "steps.csv", steps)
        _csv(out / "kpis.csv", kpis)
        _csv(out / "benchmarks.csv", benchmarks)
        _csv(out / "issues.csv", issues)


REQUEST_FIELDS = {
    "method",
    "url",
    "operation",
    "status",
    "duration_ms",
    "server_ms",
    "size_bytes",
    "failed",
    "trace_id",
}
ISSUE_FIELDS = {
    "execution_id",
    "test",
    "id",
    "severity",
    "category",
    "title",
    "cause",
    "step",
    "step_index",
    "link",
}
_OWNER = ["run_id", "execution_id", "test"]
CSV_HEADERS: dict[str, list[str]] = {
    "samples.csv": [*_OWNER, "metric", "timestamp", "elapsed_sec", "value", "unit", "source"],
    "requests.csv": [
        *_OWNER,
        "timestamp",
        "method",
        "url",
        "operation",
        "status",
        "duration_ms",
        "server_ms",
        "size_bytes",
        "failed",
        "trace_id",
    ],
    "steps.csv": [
        *_OWNER,
        "index",
        "name",
        "iteration",
        "started_at",
        "duration_ms",
        "success",
        "measured",
        "error",
        "screenshot",
        "link",
        "cause",
    ],
    "kpis.csv": [*_OWNER, "metric", "value"],
    "benchmarks.csv": [
        "run_id",
        "scope",
        "execution_id",
        "id",
        "title",
        "metric",
        "value",
        "unit",
        "good",
        "limit",
        "higher_is_better",
        "status",
        "source",
    ],
    "issues.csv": [*_OWNER, "id", "severity", "category", "title", "cause", "step", "step_index", "link"],
}


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS[path.name], extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: "" if v is None else v for k, v in row.items()} for row in rows)
