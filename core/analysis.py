"""Telemetry analysis: KPI summarisation and trend-based anomaly detection.

* ``summarize``                    -> ``TelemetrySummary`` (KPIs used by scoring / reports)
* ``detect_memory_leak``           -> linear-regression slope of PSS + window growth
* ``detect_cpu_growth``            -> same technique on app CPU
* ``detect_response_degradation``  -> first vs last window of per-step durations
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from core.attribution import mark_background, step_breakdowns
from core.benchmarks import evaluate_benchmarks, execution_metrics, slow_step_limit
from core.issues import collect_issues
from models.execution_model import TestExecution, TestStatus, TestType
from models.report_model import AnalysisFinding, BenchmarkStatus
from models.telemetry_model import CrashType, LaunchMode, TelemetryData, TelemetrySample, TelemetrySummary

if TYPE_CHECKING:
    from core.config import EnduranceSettings, FrameworkConfig


def to_frame(samples: list[TelemetrySample]) -> pd.DataFrame:
    if not samples:
        return pd.DataFrame()
    frame = pd.DataFrame([s.model_dump(mode="python") for s in samples])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.sort_values("timestamp").reset_index(drop=True)


def _r(value: float | np.floating | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if np.isnan(value) else round(value, digits)


def _window_growth(series: pd.Series, fraction: float) -> float | None:
    """% change between the mean of the first and last ``fraction`` of the series."""
    n = len(series)
    if n < 4:
        return None
    k = max(1, int(n * fraction))
    first, last = series.iloc[:k].mean(), series.iloc[-k:].mean()
    if not first:
        return None
    return float((last - first) / first * 100.0)


def summarize(data: TelemetryData, window_fraction: float = 0.2) -> TelemetrySummary:
    s = TelemetrySummary()

    if data.startup:
        df = to_frame(data.startup)
        by_mode = df.groupby("mode")["total_time_ms"]
        means = by_mode.mean().to_dict()
        s.cold_start_avg_ms = _r(means.get(LaunchMode.COLD), 1)
        s.warm_start_avg_ms = _r(means.get(LaunchMode.WARM), 1)
        s.hot_start_avg_ms = _r(means.get(LaunchMode.HOT), 1)
        cold = df.loc[df["mode"] == LaunchMode.COLD, "total_time_ms"]
        if not cold.empty:
            s.cold_start_p90_ms = _r(np.percentile(cold, 90), 1)
        s.startup_iterations = len(df)

    if data.memory:
        df = to_frame(data.memory)
        s.peak_pss_mb = _r(df["pss_mb"].max())
        s.avg_pss_mb = _r(df["pss_mb"].mean())
        if "heap_alloc_mb" in df and df["heap_alloc_mb"].notna().any():
            s.peak_heap_mb = _r(df["heap_alloc_mb"].max())
        s.memory_growth_pct = _r(_window_growth(df["pss_mb"], window_fraction))

    if data.cpu:
        df = to_frame(data.cpu)
        s.avg_cpu_pct = _r(df["app_cpu_pct"].mean())
        s.max_cpu_pct = _r(df["app_cpu_pct"].max())
        s.p90_cpu_pct = _r(np.percentile(df["app_cpu_pct"], 90))

    if data.battery_usage:
        usage = data.battery_usage[-1]
        s.battery_drain_pct = _r(usage.drain_pct, 4)
        s.app_power_mah = _r(usage.app_power_mah, 3)
        if usage.drain_pct is not None and usage.duration_sec:
            s.battery_drain_pct_per_hour = _r(usage.drain_pct / usage.duration_sec * 3600.0, 3)
    elif len(data.battery) >= 2:
        first, last = data.battery[0], data.battery[-1]
        hours = (last.timestamp - first.timestamp).total_seconds() / 3600.0
        drop = max(0.0, first.level_pct - last.level_pct)
        s.battery_drain_pct = _r(drop)
        if hours > 0:
            s.battery_drain_pct_per_hour = _r(drop / hours, 3)
    if data.battery:
        temps = [b.temperature_c for b in data.battery if b.temperature_c is not None]
        s.max_temperature_c = _r(max(temps)) if temps else None

    if data.fps:
        df = to_frame(data.fps)
        s.avg_fps = _r(df["fps"].mean())
        s.min_fps = _r(df["fps"].min())
        frames = df["total_frames"].sum()
        s.jank_pct = _r(df["janky_frames"].sum() / frames * 100.0) if frames else _r(df["jank_pct"].mean())
        s.frame_drops = int(df["frame_drops"].sum())

    if data.network:
        df = to_frame(data.network)
        rates = df.iloc[1:] if len(df) > 1 else df
        s.avg_rx_kbps = _r(rates["rx_kbps"].mean())
        s.avg_tx_kbps = _r(rates["tx_kbps"].mean())
        s.total_rx_kb = _r(df["rx_bytes"].max() / 1024.0)
        s.total_tx_kb = _r(df["tx_bytes"].max() / 1024.0)

    if data.requests:
        df = to_frame(data.requests)
        s.request_count = len(df)
        s.failed_requests = int(df["failed"].sum())
        s.avg_response_ms = _r(df["duration_ms"].mean(), 1)
        s.p95_response_ms = _r(np.percentile(df["duration_ms"], 95), 1)

    if data.steps:
        df = to_frame(data.steps)
        s.step_avg_ms = {k: round(float(v), 1) for k, v in df.groupby("name")["duration_ms"].mean().items()}

    s.anr_count = sum(1 for c in data.crashes if c.crash_type == CrashType.ANR)
    s.crash_count = len(data.crashes) - s.anr_count

    samples = data.all_samples()
    if samples:
        s.duration_sec = _r((samples[-1].timestamp - samples[0].timestamp).total_seconds(), 1)
    return s


def _trend(frame: pd.DataFrame, column: str) -> tuple[float | None, pd.Series]:
    """Slope per minute (least squares) for ``column`` against ``elapsed_sec``."""
    clean = frame[["elapsed_sec", column]].dropna()
    if len(clean) < 3 or clean["elapsed_sec"].nunique() < 2:
        return None, clean[column]
    minutes = clean["elapsed_sec"] / 60.0
    slope, _ = np.polyfit(minutes, clean[column], 1)
    return float(slope), clean[column]


def detect_memory_leak(data: TelemetryData, settings: EnduranceSettings) -> AnalysisFinding:
    finding = AnalysisFinding(
        kind="memory_leak", detected=False, metric="pss_mb", threshold=settings.memory_growth_threshold_pct
    )
    if len(data.memory) < settings.min_samples:
        finding.message = f"Insufficient memory samples ({len(data.memory)} < {settings.min_samples})"
        return finding
    slope, series = _trend(to_frame(data.memory), "pss_mb")
    growth = _window_growth(series, settings.window_fraction)
    finding.slope, finding.growth_pct = _r(slope, 3), _r(growth)
    finding.detected = bool(
        slope is not None
        and growth is not None
        and slope > settings.leak_slope_threshold_mb_per_min
        and growth > settings.memory_growth_threshold_pct
    )
    finding.message = (
        f"PSS trend {finding.slope} MB/min, growth {finding.growth_pct}% "
        f"(limits {settings.leak_slope_threshold_mb_per_min} MB/min, {settings.memory_growth_threshold_pct}%)"
    )
    return finding


def detect_cpu_growth(data: TelemetryData, settings: EnduranceSettings) -> AnalysisFinding:
    finding = AnalysisFinding(
        kind="cpu_growth", detected=False, metric="app_cpu_pct", threshold=settings.cpu_growth_threshold_pct
    )
    if len(data.cpu) < settings.min_samples:
        finding.message = f"Insufficient CPU samples ({len(data.cpu)} < {settings.min_samples})"
        return finding
    slope, series = _trend(to_frame(data.cpu), "app_cpu_pct")
    growth = _window_growth(series, settings.window_fraction)
    finding.slope, finding.growth_pct = _r(slope, 3), _r(growth)
    finding.detected = bool(
        slope is not None and slope > 0 and growth is not None and growth > settings.cpu_growth_threshold_pct
    )
    finding.message = f"CPU trend {finding.slope} %/min, growth {finding.growth_pct}%"
    return finding


def detect_response_degradation(data: TelemetryData, settings: EnduranceSettings) -> AnalysisFinding:
    finding = AnalysisFinding(
        kind="response_degradation",
        detected=False,
        metric="step_duration_ms",
        threshold=settings.degradation_threshold_pct,
    )
    if not data.steps:
        finding.message = "No step timings recorded"
        return finding
    df = to_frame(data.steps)
    worst_name, worst_growth = None, None
    for name, group in df.groupby("name"):
        if len(group) < settings.min_samples:
            continue
        growth = _window_growth(group["duration_ms"].reset_index(drop=True), settings.window_fraction)
        if growth is not None and (worst_growth is None or growth > worst_growth):
            worst_name, worst_growth = str(name), growth
    if worst_name is None:
        finding.message = "Not enough iterations per step to evaluate degradation"
        return finding
    finding.growth_pct = _r(worst_growth)
    finding.metric = f"step:{worst_name}"
    finding.detected = bool(worst_growth is not None and worst_growth > settings.degradation_threshold_pct)
    finding.message = f"Slowest-growing step '{worst_name}': {finding.growth_pct}% (first vs last window)"
    return finding


def detect_crashes(data: TelemetryData) -> AnalysisFinding:
    crashes = data.crashes
    if not crashes:
        return AnalysisFinding(
            kind="app_crash", detected=False, metric="crash_count", message="No crashes or ANRs"
        )
    kinds = ", ".join(sorted({c.crash_type.value for c in crashes}))
    first = crashes[0]
    where = f" in step '{first.step}'" if first.step else ""
    return AnalysisFinding(
        kind="app_crash",
        detected=True,
        metric="crash_count",
        message=f"{len(crashes)} event(s) [{kinds}]; first{where}: {first.exception}",
    )


def analyze(data: TelemetryData, settings: EnduranceSettings) -> list[AnalysisFinding]:
    return [
        detect_memory_leak(data, settings),
        detect_cpu_growth(data, settings),
        detect_response_degradation(data, settings),
    ]


class ExecutionEvaluator:
    """Fills summary, findings and score of a ``TestExecution``."""

    def __init__(self, config: FrameworkConfig) -> None:
        from core.scoring import ScoringEngine

        self.config = config
        self.scoring = ScoringEngine(config.scoring)

    def evaluate(self, execution: TestExecution) -> TestExecution:
        execution.summary = summarize(execution.telemetry, self.config.endurance.window_fraction)
        execution.summary.duration_sec = execution.summary.duration_sec or execution.duration_sec
        if execution.test_type in (TestType.ENDURANCE, TestType.JOURNEY) or execution.telemetry.iterations:
            execution.findings = analyze(execution.telemetry, self.config.endurance)
        if self.config.crash.enabled:
            execution.findings.append(detect_crashes(execution.telemetry))
        execution.score = self.scoring.score(execution.summary)
        benchmarks = self.config.benchmarks
        if execution.status != TestStatus.SKIPPED:
            execution.benchmarks = evaluate_benchmarks(benchmarks, execution_metrics(execution))
        attribution = self.config.attribution
        if attribution.enabled:
            mark_background(execution.telemetry.requests, attribution.background_hosts)
            execution.breakdowns = step_breakdowns(execution.telemetry.steps, execution.telemetry.requests)
        execution.issues = collect_issues(execution, slow_step_limit(benchmarks))
        breached = [b.title for b in execution.benchmarks if b.status == BenchmarkStatus.FAIL]
        if breached and benchmarks.fail_on_breach and execution.status == TestStatus.PASSED:
            execution.status = TestStatus.FAILED
            execution.error = "Production benchmark limits breached: " + ", ".join(breached)
        return execution


def aggregate_executions(executions: list[TestExecution]) -> TelemetryData:
    """Combine telemetry of all non-benchmark executions into one dataset."""
    merged = TelemetryData()
    for execution in executions:
        if execution.test_type != TestType.BENCHMARK:
            merged.merge(execution.telemetry.model_copy(deep=True))
    return merged
