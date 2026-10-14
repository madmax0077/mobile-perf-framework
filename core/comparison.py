"""Build-to-build comparison (Build A vs Build B) and CLI.

Usage::

    python -m core.comparison --baseline reports_a/metrics.json --candidate reports_b/metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from core.config import ComparisonSettings
from models.execution_model import RunSummary, TestExecution
from models.report_model import ComparisonResult, MetricDelta

DEFAULT_METRICS: tuple[str, ...] = (
    "cold_start_avg_ms",
    "warm_start_avg_ms",
    "hot_start_avg_ms",
    "peak_pss_mb",
    "avg_pss_mb",
    "avg_cpu_pct",
    "max_cpu_pct",
    "avg_fps",
    "jank_pct",
    "p95_response_ms",
    "battery_drain_pct_per_hour",
)
HIGHER_IS_BETTER = frozenset({"avg_fps", "min_fps"})
HEADLINE = {
    "Startup": "cold_start_avg_ms",
    "Memory": "peak_pss_mb",
    "CPU": "avg_cpu_pct",
    "FPS": "avg_fps",
}
DEFAULT_REGRESSION_PCT = 10.0
IMPROVEMENT_PCT = 1.0


def compare_metrics(
    baseline: Mapping[str, float | None],
    candidate: Mapping[str, float | None],
    *,
    baseline_label: str,
    candidate_label: str,
    thresholds: Mapping[str, float] | None = None,
    metrics: Iterable[str] = DEFAULT_METRICS,
) -> ComparisonResult:
    thresholds = thresholds or {}
    deltas: list[MetricDelta] = []
    for metric in metrics:
        base, cand = baseline.get(metric), candidate.get(metric)
        if base is None and cand is None:
            continue
        higher = metric in HIGHER_IS_BETTER
        threshold = thresholds.get(metric, DEFAULT_REGRESSION_PCT)
        delta = MetricDelta(
            metric=metric, baseline=base, candidate=cand, higher_is_better=higher, threshold_pct=threshold
        )
        if base not in (None, 0) and cand is not None:
            diff = (cand - base) / abs(base) * 100.0  # type: ignore[operator]
            delta.diff_pct = round(diff, 2)
            worse = diff < 0 if higher else diff > 0
            delta.regression = worse and abs(diff) > threshold
            delta.improvement = (not worse) and abs(diff) >= IMPROVEMENT_PCT
        deltas.append(delta)
    return ComparisonResult(baseline_label=baseline_label, candidate_label=candidate_label, deltas=deltas)


def compare_runs(
    baseline: RunSummary, candidate: RunSummary, settings: ComparisonSettings
) -> ComparisonResult:
    return compare_metrics(
        baseline.metrics,
        candidate.metrics,
        baseline_label=f"{baseline.label} ({baseline.app.version}/{baseline.app.build_number})",
        candidate_label=f"{candidate.label} ({candidate.app.version}/{candidate.app.build_number})",
        thresholds=settings.max_regression_pct,
    )


def compare_executions(
    baseline: TestExecution, candidate: TestExecution, settings: ComparisonSettings
) -> ComparisonResult:
    return compare_metrics(
        baseline.summary.model_dump(),
        candidate.summary.model_dump(),
        baseline_label=baseline.label or baseline.name,
        candidate_label=candidate.label or candidate.name,
        thresholds=settings.max_regression_pct,
    )


def headline(result: ComparisonResult) -> dict[str, float | None]:
    """'Startup / Memory / CPU / FPS difference %' as shown in the report."""
    return {name: (d.diff_pct if (d := result.delta(metric)) else None) for name, metric in HEADLINE.items()}


def format_table(result: ComparisonResult) -> str:
    lines = [
        f"Baseline : {result.baseline_label}",
        f"Candidate: {result.candidate_label}",
        "",
        f"{'metric':<28}{'baseline':>12}{'candidate':>12}{'diff %':>10}  status",
        "-" * 72,
    ]
    for d in result.deltas:
        status = "REGRESSION" if d.regression else ("improved" if d.improvement else "ok")
        fmt = lambda v: f"{v:.2f}" if isinstance(v, int | float) else "-"  # noqa: E731
        lines.append(
            f"{d.metric:<28}{fmt(d.baseline):>12}{fmt(d.candidate):>12}{fmt(d.diff_pct):>10}  {status}"
        )
    lines += ["", "RESULT: " + ("PASS" if result.passed else "FAIL - regressions detected")]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two mobile performance runs (metrics.json).")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Write the comparison as JSON")
    parser.add_argument("--max-regression", type=float, default=DEFAULT_REGRESSION_PCT)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    baseline, candidate = RunSummary.load(args.baseline), RunSummary.load(args.candidate)
    settings = ComparisonSettings(max_regression_pct={m: args.max_regression for m in DEFAULT_METRICS})
    result = compare_runs(baseline, candidate, settings)
    print(format_table(result))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({**result.model_dump(mode="json"), "headline": headline(result)}, indent=2),
            encoding="utf-8",
        )
    return 1 if args.fail_on_regression and not result.passed else 0


if __name__ == "__main__":
    sys.exit(main())
