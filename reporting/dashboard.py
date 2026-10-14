"""Assembles the dashboard view-model (sections, KPIs, charts, tables) from a run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from core.comparison import headline
from core.issues import FINDING_LABELS, fmt_value
from models.execution_model import RunSummary, TestExecution
from models.report_model import BenchmarkStatus, Rating, Readiness, Severity, TrendPoint
from reporting import charts
from reporting.links import execution_page, execution_slugs, why_anchor

RATING_CLASS = {
    Rating.EXCELLENT: "success",
    Rating.GOOD: "primary",
    Rating.ACCEPTABLE: "warning",
    Rating.NEEDS_IMPROVEMENT: "danger",
    Rating.NOT_RATED: "secondary",
}
STATUS_CLASS = {"passed": "success", "failed": "danger", "error": "danger", "skipped": "secondary"}
SEVERITY_CLASS = {Severity.CRITICAL: "danger", Severity.MAJOR: "warning", Severity.MINOR: "info"}
BENCH_CLASS = {
    BenchmarkStatus.PASS: "success",
    BenchmarkStatus.WARN: "warning",
    BenchmarkStatus.FAIL: "danger",
    BenchmarkStatus.NO_DATA: "secondary",
}
READINESS_CLASS = {
    Readiness.READY: "success",
    Readiness.AT_RISK: "warning",
    Readiness.NOT_READY: "danger",
    Readiness.NOT_EVALUATED: "secondary",
}


@dataclass
class Kpi:
    label: str
    value: str
    hint: str = ""


@dataclass
class ChartBlock:
    html: str
    width: int = 6


@dataclass
class Section:
    id: str
    title: str
    kpis: list[Kpi] = field(default_factory=list)
    charts: list[ChartBlock] = field(default_factory=list)
    note: str = ""


@dataclass
class DashboardContext:
    title: str
    run: RunSummary
    generated_at: str
    summary_rows: list[tuple[str, str]]
    rating_class: str
    gauge: str
    category_chart: str
    sections: list[Section]
    executions: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    trend_chart: str
    crashes: list[dict[str, Any]] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    issue_counts: dict[str, int] = field(default_factory=dict)
    readiness: dict[str, Any] = field(default_factory=dict)


def fmt(value: Any, unit: str = "", digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if isinstance(value, int | float):
        text = f"{value:,.{digits}f}" if isinstance(value, float) else f"{value:,}"
        return f"{text} {unit}".strip()
    return str(value)


def frame(executions: list[TestExecution], kind: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for execution in executions:
        for sample in getattr(execution.telemetry, kind):
            rows.append({**sample.model_dump(mode="json"), "test": execution.name})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    return df.sort_values(["test", "timestamp"]).reset_index(drop=True)


class DashboardBuilder:
    def __init__(self, title: str) -> None:
        self.title = title

    def build(
        self, run: RunSummary, trend: list[TrendPoint], slugs: list[str] | None = None
    ) -> DashboardContext:
        ex = run.executions
        slugs = slugs or execution_slugs(run)
        slug_of = {id(e): slug for e, slug in zip(ex, slugs, strict=True)}
        m = run.metrics
        device = run.device
        summary_rows = [
            ("Test Suite", run.label),
            ("Tests", ", ".join(e.name for e in ex) or "-"),
            ("App", f"{run.app.name} ({run.app.identifier or 'n/a'})"),
            ("App Version", run.app.version),
            ("Build Number", run.app.build_number),
            (
                "Device Name",
                f"{device.name} ({device.model})"
                if device and device.model
                else (device.name if device else "-"),
            ),
            ("OS", f"{device.platform.value.title()} {device.os_version}" if device else "-"),
            ("Provider", run.provider),
            ("Execution Time", f"{run.started_at:%Y-%m-%d %H:%M:%S} UTC - {run.duration_sec:,.0f} s"),
            ("Overall Performance Score", f"{fmt(run.score.overall)} / 100 ({run.score.rating.value})"),
            ("Production Readiness", run.readiness.value),
        ]
        crash_df = frame(ex, "crashes")
        crashed_tests = sorted(set(crash_df["test"])) if not crash_df.empty else []
        summary_rows.append(
            (
                "App Crashes / ANRs",
                f"{len(crash_df)} in {len(crashed_tests)} test(s)" if len(crash_df) else "None detected",
            )
        )

        findings_by_test = {e.name: f for e in ex for f in e.findings if f.kind == "memory_leak"}
        startup_df = frame(ex, "startup")
        memory_df = frame(ex, "memory")
        cpu_df = frame(ex, "cpu")
        battery_df = frame(ex, "battery")
        fps_df = frame(ex, "fps")
        network_df = frame(ex, "network")
        requests_df = frame(ex, "requests")
        drain_df = pd.DataFrame(
            [
                {
                    "test": e.name,
                    "drain_pct": e.summary.battery_drain_pct,
                    "drain_pct_per_hour": e.summary.battery_drain_pct_per_hour,
                    "app_power_mah": e.summary.app_power_mah,
                    "duration_sec": e.summary.duration_sec,
                }
                for e in ex
                if e.summary.battery_drain_pct is not None
            ]
        )

        type_counts = crash_df["crash_type"].value_counts().to_dict() if not crash_df.empty else {}
        sections = [
            Section(
                "stability",
                "Stability Dashboard",
                [
                    Kpi("Crashes", str(len(crash_df) - type_counts.get("anr", 0))),
                    Kpi("ANRs", str(type_counts.get("anr", 0))),
                    Kpi("Native crashes", str(type_counts.get("native_crash", 0))),
                    Kpi("Tests affected", f"{len(crashed_tests)} / {len(ex)}"),
                ],
                [
                    ChartBlock(charts.crashes_by_test(crash_df, [e.name for e in ex])),
                    ChartBlock(charts.crash_timeline(crash_df, frame(ex, "steps"))),
                ],
                note="Sources: live device log (logcat) and app state after each step, plus provider "
                "crash/device logs and app-profiling crash counters collected after the session.",
            ),
            Section(
                "startup",
                "Startup Dashboard",
                [
                    Kpi("Cold start (avg)", fmt(m.get("cold_start_avg_ms"), "ms", 0)),
                    Kpi("Cold start (p90)", fmt(m.get("cold_start_p90_ms"), "ms", 0)),
                    Kpi("Warm start (avg)", fmt(m.get("warm_start_avg_ms"), "ms", 0)),
                    Kpi("Hot start (avg)", fmt(m.get("hot_start_avg_ms"), "ms", 0)),
                ],
                [
                    ChartBlock(charts.startup_mode_trend(startup_df, "cold")),
                    ChartBlock(charts.startup_mode_trend(startup_df, "warm")),
                    ChartBlock(charts.startup_comparison(startup_df), 12),
                ],
            ),
            Section(
                "memory",
                "Memory Dashboard",
                [
                    Kpi("Peak PSS", fmt(m.get("peak_pss_mb"), "MB")),
                    Kpi("Average PSS", fmt(m.get("avg_pss_mb"), "MB")),
                    Kpi("Peak heap", fmt(m.get("peak_heap_mb"), "MB")),
                    Kpi("Leaks detected", str(sum(1 for f in findings_by_test.values() if f.detected))),
                ],
                [
                    ChartBlock(charts.memory_timeline(memory_df), 12),
                    ChartBlock(charts.peak_memory(memory_df)),
                    ChartBlock(charts.leak_detection(memory_df, findings_by_test)),
                ],
            ),
            Section(
                "cpu",
                "CPU Dashboard",
                [
                    Kpi("Average CPU", fmt(m.get("avg_cpu_pct"), "%")),
                    Kpi("Max CPU", fmt(m.get("max_cpu_pct"), "%")),
                    Kpi("p90 CPU", fmt(m.get("p90_cpu_pct"), "%")),
                ],
                [ChartBlock(charts.cpu_timeline(cpu_df), 8), ChartBlock(charts.cpu_peaks(cpu_df), 4)],
            ),
            Section(
                "battery",
                "Battery Dashboard",
                [
                    Kpi("Drain", fmt(m.get("battery_drain_pct"), "%", 3)),
                    Kpi("Drain / hour", fmt(m.get("battery_drain_pct_per_hour"), "%/h", 2)),
                    Kpi("App power", fmt(m.get("app_power_mah"), "mAh", 2)),
                    Kpi("Max temperature", fmt(m.get("max_temperature_c"), "°C")),
                ],
                [ChartBlock(charts.battery_trend(battery_df)), ChartBlock(charts.battery_drain(drain_df))],
            ),
            Section(
                "network",
                "Network Dashboard",
                [
                    Kpi("Requests", fmt(m.get("request_count"))),
                    Kpi("Failed", fmt(m.get("failed_requests"))),
                    Kpi("p95 response", fmt(m.get("p95_response_ms"), "ms", 0)),
                    Kpi("Avg rx", fmt(m.get("avg_rx_kbps"), "kbit/s")),
                ],
                [
                    ChartBlock(charts.api_response_times(requests_df), 12),
                    ChartBlock(charts.throughput(network_df)),
                    ChartBlock(charts.failed_requests(requests_df)),
                ],
            ),
            Section(
                "fps",
                "FPS Dashboard",
                [
                    Kpi("Average FPS", fmt(m.get("avg_fps"))),
                    Kpi("Min FPS", fmt(m.get("min_fps"))),
                    Kpi("Jank rate", fmt(m.get("jank_pct"), "%", 2)),
                    Kpi("Frame drops", fmt(m.get("frame_drops"))),
                ],
                [
                    ChartBlock(charts.average_fps(fps_df), 12),
                    ChartBlock(charts.jank_rate(fps_df)),
                    ChartBlock(charts.frame_drops(fps_df)),
                ],
            ),
        ]

        executions = [
            {
                "name": e.name,
                "slug": slug_of[id(e)],
                "page": execution_page(slug_of[id(e)]),
                "steps": len(e.telemetry.steps),
                "failed_steps": sum(1 for s in e.telemetry.steps if not s.success),
                "shots": sum(1 for s in e.telemetry.steps if s.screenshot),
                "issues": {sev.value: sum(1 for i in e.issues if i.severity == sev) for sev in Severity},
                "type": e.test_type.value,
                "status": e.status.value,
                "duration": fmt(e.duration_sec, "s"),
                "score": fmt(e.score.overall),
                "rating": e.score.rating.value,
                "rating_class": RATING_CLASS[e.score.rating],
                "samples": e.telemetry.sample_count,
                "crashes": len(e.telemetry.crashes),
                "device": f"{e.device.name} / {e.device.platform.value} {e.device.os_version}",
                "session": e.provider_session_id or "-",
                "video": e.provider_metadata.get("video_url"),
                "screenshot": e.artifacts.get("screenshot"),
                "error": (e.error or "")[-1500:],
            }
            for e in ex
        ]
        findings = [
            {
                "test": e.name,
                "kind": FINDING_LABELS.get(f.kind, f.kind.replace("_", " ")),
                "detected": f.detected,
                "message": f.message,
                "link": execution_page(slug_of[id(e)], "findings"),
            }
            for e in ex
            for f in e.findings
        ]
        crashes = [
            {
                "test": e.name,
                "link": execution_page(slug_of[id(e)], f"crash-{n}"),
                "type": c.crash_type.value,
                "time": f"{c.timestamp:%Y-%m-%d %H:%M:%S} UTC",
                "device_time": c.device_time or "-",
                "step": c.step or "-",
                "process": c.process or "-",
                "pid": c.pid if c.pid and c.pid > 0 else "-",
                "exception": c.exception or "-",
                "stacktrace": c.stacktrace,
                "source": c.source,
                "logs_url": e.provider_metadata.get("crash_logs_url")
                or e.provider_metadata.get("device_logs_url"),
                "video": e.provider_metadata.get("video_url"),
            }
            for e in ex
            for n, c in enumerate(e.telemetry.crashes, 1)
        ]
        issues = sorted(
            (
                {
                    "anchor": f"issue-{slug_of[id(e)]}-{i.id}",
                    "test": e.name,
                    "test_link": execution_page(slug_of[id(e)]),
                    "link": execution_page(slug_of[id(e)], i.id),
                    "severity": i.severity.value,
                    "rank": i.severity.rank,
                    "severity_class": SEVERITY_CLASS[i.severity],
                    "category": i.category.replace("_", " "),
                    "title": i.title,
                    "detail": i.detail,
                    "cause": i.cause,
                    "cause_link": execution_page(slug_of[id(e)], why_anchor(i, e))
                    if why_anchor(i, e)
                    else None,
                    "step": i.step,
                    "step_link": execution_page(slug_of[id(e)], f"step-{i.step_index}")
                    if i.step_index
                    else None,
                }
                for e in ex
                for i in e.issues
            ),
            key=lambda row: row["rank"],
        )
        issue_counts = {sev.value: sum(1 for i in issues if i["severity"] == sev.value) for sev in Severity}
        readiness = {
            "verdict": run.readiness.value,
            "class": READINESS_CLASS[run.readiness],
            "rows": [
                {
                    "title": b.title,
                    "metric": b.metric,
                    "value": fmt_value(b.value, b.unit),
                    "good": fmt_value(b.good, b.unit),
                    "limit": fmt_value(b.limit, b.unit),
                    "status": b.status.value.replace("_", " "),
                    "status_class": BENCH_CLASS[b.status],
                    "source": b.source,
                    "per_test": [
                        {
                            "status_class": BENCH_CLASS[r.status],
                            "status": r.status.value.replace("_", " "),
                            "value": fmt_value(r.value, r.unit),
                            "link": execution_page(slug_of[id(e)], f"bench-{r.id}"),
                        }
                        if (r := next((x for x in e.benchmarks if x.id == b.id), None)) is not None
                        else None
                        for e in ex
                    ],
                }
                for b in run.benchmarks
            ],
            "tests": [{"name": e.name, "link": execution_page(slug_of[id(e)])} for e in ex],
        }
        comparisons = [
            {
                "result": c,
                "headline": headline(c),
                "chart": charts.comparison_chart(c),
            }
            for c in run.comparisons
        ]
        return DashboardContext(
            title=self.title,
            run=run,
            generated_at=f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC",
            summary_rows=summary_rows,
            rating_class=RATING_CLASS[run.score.rating],
            gauge=charts.score_gauge(run.score.overall, run.score.rating.value),
            category_chart=charts.category_scores(run.score.categories),
            sections=sections,
            executions=executions,
            findings=findings,
            comparisons=comparisons,
            trend_chart=charts.run_history(trend),
            crashes=crashes,
            issues=issues,
            issue_counts=issue_counts,
            readiness=readiness,
        )
