"""View-model for one execution's drilldown page (``executions/<slug>.html``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from core.attribution import describe_request, operation_stats
from core.issues import CRASH_LABELS, FINDING_LABELS, fmt_value, step_index_at
from models.execution_model import TestExecution
from models.report_model import Culprit, CulpritLayer, StepBreakdown
from models.telemetry_model import StepTiming
from reporting import charts
from reporting.dashboard import BENCH_CLASS, RATING_CLASS, SEVERITY_CLASS, STATUS_CLASS, Kpi, fmt
from reporting.links import EXECUTIONS_DIR, relative_to, why_anchor

LAYER_CLASS = {CulpritLayer.APP: "danger", CulpritLayer.API: "primary", CulpritLayer.TEST: "secondary"}


@dataclass
class ExecutionView:
    slug: str
    execution: TestExecution
    status_class: str
    rating_class: str
    kpis: list[Kpi]
    steps: list[dict[str, Any]]
    shots: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    crashes: list[dict[str, Any]]
    benchmarks: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    operations: list[dict[str, Any]]
    why: list[dict[str, Any]]
    startup: list[dict[str, Any]]
    timeline: str
    step_windows: dict[str, list[str]]
    links: dict[str, str]
    meta_rows: list[tuple[str, str]]
    nav: dict[str, str | None] = field(default_factory=dict)


def _frame(samples: list[Any], column: str) -> pd.DataFrame:
    rows = [{"timestamp": s.timestamp, column: getattr(s, column)} for s in samples]
    frame = pd.DataFrame(rows)
    return frame.dropna() if not frame.empty else frame


def _cause(found: StepBreakdown | None) -> dict[str, str] | None:
    if found is None or found.primary is None:
        return None
    return {
        "text": found.cause,
        "layer": found.primary.layer.label,
        "class": LAYER_CLASS[found.primary.layer],
    }


def _pct(value: float, total: float) -> float:
    return round(max(0.0, min(100.0, value / total * 100.0)), 2) if total > 0 else 0.0


class ExecutionPageBuilder:
    def __init__(self, run_dir: Path, slow_step_limit_ms: float | None) -> None:
        self.page_dir = run_dir / EXECUTIONS_DIR
        self.slow_limit = slow_step_limit_ms

    def _rel(self, path: str | None) -> str | None:
        return relative_to(path, self.page_dir)

    @staticmethod
    def _why(step: StepTiming, found: StepBreakdown, row: dict[str, Any]) -> dict[str, Any]:
        """Root-cause card of one step: the primary culprit plus evidence per layer."""
        total = max(found.duration_ms, 1.0)
        primary = found.primary

        def is_primary(culprit: Culprit) -> bool:
            return primary is not None and culprit == primary

        api = []
        for c in found.layer(CulpritLayer.API):
            start = max(c.offset_ms or 0.0, 0.0)
            end = min((c.offset_ms or 0.0) + (c.duration_ms or 0.0), total)
            api.append(
                {
                    "name": c.name,
                    "offset": f"{(c.offset_ms or 0) / 1000:+.2f} s",
                    "duration": fmt_value(c.duration_ms, "ms"),
                    "share": fmt(c.share_pct, "%", 0),
                    "detail": c.detail,
                    "url": c.location,
                    "trace_id": c.trace_id,
                    "background": c.kind == "background_call",
                    "bar_left": _pct(start, total),
                    "bar_width": max(0.8, _pct(end - start, total)),
                    "primary": is_primary(c),
                }
            )
        app = [
            {
                "kind": c.kind.replace("_", " "),
                "name": c.name,
                "duration": fmt_value(c.duration_ms, "ms"),
                "thread": "main"
                if signal.main_thread
                else ("background" if signal.main_thread is False else "-"),
                "detail": c.detail,
                "frames": signal.frames,
                "primary": is_primary(c),
            }
            for signal, c in zip(step.app_signals, found.layer(CulpritLayer.APP), strict=False)
        ]
        test = [
            {
                "code": c.name.split(" > "),
                "detail": c.detail,
                "duration": fmt_value(c.duration_ms, "ms"),
                "share": fmt(c.share_pct, "%", 0),
                "location": c.location,
                "primary": is_primary(c),
            }
            for c in found.layer(CulpritLayer.TEST)
        ]
        return {
            "anchor": f"why-step-{found.step_index}",
            "step_anchor": row["anchor"],
            "index": found.step_index,
            "name": found.step,
            "duration": row["duration"],
            "state": row["state"],
            "open": row["state"] in ("slow", "failed"),
            "cause": _cause(found),
            "summary": found.summary,
            "network_busy": fmt_value(found.network_busy_ms, "ms")
            if found.network_busy_ms is not None
            else None,
            "driver": fmt_value(found.driver_ms, "ms") if found.driver_ms is not None else None,
            "api": api,
            "app": app,
            "test": test,
        }

    def build(self, execution: TestExecution, slug: str) -> ExecutionView:
        t = execution.telemetry
        s = execution.summary
        issues_by_target: dict[str, list[str]] = {}
        for issue in execution.issues:
            for key in {issue.target, f"step-{issue.step_index}" if issue.step_index else None} - {None}:
                issues_by_target.setdefault(str(key), []).append(issue.id)

        crash_steps = {i: step_index_at(t.steps, c.timestamp, c.step) for i, c in enumerate(t.crashes, 1)}
        breakdowns = {b.step_index: b for b in execution.breakdowns}
        steps, shots, windows = [], [], {}
        for i, step in enumerate(t.steps, 1):
            anchor = f"step-{i}"
            slow = bool(
                self.slow_limit and step.measured and step.success and step.duration_ms >= self.slow_limit
            )
            state = (
                "failed" if not step.success else "wait" if not step.measured else "slow" if slow else "ok"
            )
            shot = self._rel(step.screenshot)
            offset = (step.started_at - execution.started_at).total_seconds()
            steps.append(
                {
                    "index": i,
                    "anchor": anchor,
                    "name": step.name,
                    "iteration": step.iteration,
                    "start": step.started_at,
                    "end": step.timestamp,
                    "offset": f"+{offset:,.1f} s",
                    "duration_ms": step.duration_ms,
                    "duration": fmt_value(step.duration_ms, "ms"),
                    "state": state,
                    "measured": step.measured,
                    "error": step.error,
                    "screenshot": shot,
                    "crashes": [n for n, idx in crash_steps.items() if idx == i],
                    "issues": issues_by_target.get(anchor, []),
                    "cause": _cause(breakdowns.get(i)),
                }
            )
            windows[anchor] = [charts.plot_time(step.started_at), charts.plot_time(step.timestamp)]
            if shot:
                shots.append(
                    {
                        "anchor": anchor,
                        "src": shot,
                        "caption": f"{i}. {step.name} ({steps[-1]['duration']})",
                        "failed": not step.success,
                    }
                )
        failure = execution.artifacts.get("screenshot")
        final = failure or execution.artifacts.get("final_screenshot")
        if final:
            shots.append(
                {
                    "anchor": "final-screen",
                    "src": self._rel(final),
                    "caption": f"Final screen ({'failure' if failure else 'end of test'})",
                    "failed": bool(failure),
                }
            )

        crashes = [
            {
                "index": i,
                "anchor": f"crash-{i}",
                "label": CRASH_LABELS[c.crash_type],
                "type": c.crash_type.value,
                "time": f"{c.timestamp:%H:%M:%S} UTC",
                "device_time": c.device_time or "-",
                "step": c.step or "-",
                "step_anchor": f"step-{crash_steps[i]}" if crash_steps[i] else None,
                "process": c.process or "-",
                "pid": c.pid if c.pid and c.pid > 0 else "-",
                "exception": c.exception or "-",
                "stacktrace": c.stacktrace,
                "source": c.source,
                "issues": issues_by_target.get(f"crash-{i}", []),
            }
            for i, c in enumerate(t.crashes, 1)
        ]
        issues = [
            {
                "id": issue.id,
                "severity": issue.severity.value,
                "severity_class": SEVERITY_CLASS[issue.severity],
                "category": issue.category.replace("_", " "),
                "title": issue.title,
                "detail": issue.detail,
                "step": issue.step,
                "target": issue.target,
                "step_anchor": f"step-{issue.step_index}" if issue.step_index else None,
                "cause": issue.cause,
                "why": why_anchor(issue, execution),
            }
            for issue in execution.issues
        ]
        benchmarks = [
            {
                "anchor": f"bench-{b.id}",
                "title": b.title,
                "metric": b.metric,
                "value": fmt_value(b.value, b.unit),
                "good": fmt_value(b.good, b.unit),
                "limit": fmt_value(b.limit, b.unit),
                "direction": "higher is better" if b.higher_is_better else "lower is better",
                "status": b.status.value.replace("_", " "),
                "status_class": BENCH_CLASS[b.status],
                "source": b.source,
                "issues": issues_by_target.get(f"bench-{b.id}", []),
            }
            for b in execution.benchmarks
        ]
        failed = [r for r in t.requests if r.failed]
        slowest = sorted((r for r in t.requests if not r.failed), key=lambda r: r.duration_ms, reverse=True)
        requests = [
            {
                "method": r.method,
                "url": r.url,
                "operation": r.operation,
                "status": r.status or "-",
                "duration": fmt_value(r.duration_ms, "ms"),
                "server": fmt_value(r.server_ms, "ms"),
                "detail": describe_request(r),
                "trace_id": r.trace_id,
                "size": fmt(r.size_bytes / 1024.0, "KB"),
                "failed": r.failed,
                "time": f"{r.timestamp:%H:%M:%S}",
                "step_anchor": (
                    f"step-{index}" if (index := step_index_at(t.steps, r.timestamp, None)) else None
                ),
            }
            for r in failed + slowest[: max(0, 25 - len(failed))]
        ]
        operations = [
            {
                "anchor": f"op-{n}",
                "name": op.name,
                "count": op.count,
                "failed": op.failed,
                "avg": fmt_value(op.avg_ms, "ms"),
                "p95": fmt_value(op.p95_ms, "ms"),
                "max": fmt_value(op.max_ms, "ms"),
                "server": fmt_value(op.server_avg_ms, "ms"),
                "setup": fmt_value(op.setup_avg_ms, "ms"),
                "total": fmt_value(op.total_ms, "ms"),
                "trace_id": op.trace_id,
                "background": op.background,
            }
            for n, op in enumerate(operation_stats(t.requests), 1)
        ]
        why = [
            self._why(t.steps[b.step_index - 1], b, steps[b.step_index - 1])
            for b in execution.breakdowns
            if b.culprits and b.step_index <= len(t.steps)
        ]
        startup = [
            {
                "mode": st.mode.value,
                "iteration": st.iteration,
                "total": fmt_value(st.total_time_ms, "ms"),
                "method": st.method,
                "state": st.launch_state or "-",
            }
            for st in t.startup
        ]
        series = [
            ("App CPU (%)", _frame(t.cpu, "app_cpu_pct"), "app_cpu_pct"),
            ("Memory PSS (MB)", _frame(t.memory, "pss_mb"), "pss_mb"),
            ("FPS", _frame(t.fps, "fps"), "fps"),
        ]
        timeline = charts.execution_timeline(
            steps,
            series,
            [{"timestamp": c.timestamp, "anchor": f"crash-{i}"} for i, c in enumerate(t.crashes, 1)],
        )
        meta = execution.provider_metadata
        links = {
            k: v
            for k, v in {
                "Session video": meta.get("video_url"),
                "Device logs": meta.get("device_logs_url"),
                "Crash logs": meta.get("crash_logs_url"),
                "Network logs": meta.get("network_logs_url"),
                "Raw telemetry": self._rel(execution.artifacts.get("raw_telemetry")),
            }.items()
            if v
        }
        device = execution.device
        meta_rows = [
            ("Test id", execution.test_id),
            ("Type", execution.test_type.value),
            ("Device", f"{device.name} ({device.platform.value} {device.os_version})"),
            ("Provider / session", f"{device.provider} / {execution.provider_session_id or '-'}"),
            ("App", f"{execution.app.name} {execution.app.version} ({execution.app.build_number})"),
            ("Started", f"{execution.started_at:%Y-%m-%d %H:%M:%S} UTC"),
            ("Duration", fmt(execution.duration_sec, "s")),
        ]
        kpis = [
            Kpi("Steps", f"{sum(1 for x in steps if x['state'] != 'failed')} / {len(steps)} ok"),
            Kpi("Issues", str(len(issues))),
            Kpi("Crashes / ANRs", str(len(crashes))),
            Kpi("Cold start", fmt(s.cold_start_avg_ms, "ms", 0)),
            Kpi("Avg / max CPU", f"{fmt(s.avg_cpu_pct, '%')} / {fmt(s.max_cpu_pct, '%')}"),
            Kpi("Peak PSS", fmt(s.peak_pss_mb, "MB")),
            Kpi("Avg FPS / jank", f"{fmt(s.avg_fps)} / {fmt(s.jank_pct, '%')}"),
            Kpi("API p95", fmt(s.p95_response_ms, "ms", 0)),
        ]
        findings = [
            {
                "kind": FINDING_LABELS.get(f.kind, f.kind.replace("_", " ")),
                "detected": f.detected,
                "message": f.message,
            }
            for f in execution.findings
        ]
        return ExecutionView(
            slug=slug,
            execution=execution,
            status_class=STATUS_CLASS.get(execution.status.value, "secondary"),
            rating_class=RATING_CLASS[execution.score.rating],
            kpis=kpis,
            steps=steps,
            shots=shots,
            issues=issues,
            crashes=crashes,
            benchmarks=benchmarks,
            findings=findings,
            requests=requests,
            operations=operations,
            why=why,
            startup=startup,
            timeline=timeline,
            step_windows=windows,
            links=links,
            meta_rows=meta_rows,
        )
