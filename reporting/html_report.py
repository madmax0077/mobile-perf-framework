"""HTML reporting engine.

Every run gets a self-contained folder ``reports/runs/<run_id>/``::

    index.html                    run report (dashboards, readiness, issues)
    executions/<slug>.html        drilldown page per test (steps, screenshots, issues, crashes)
    metrics.json                  run summary (schema of ``RunSummary`` without raw samples)
    performance_data.json         versioned export (+ performance_data.schema.json, csv/)
    raw/, artifacts/, framework.log

``reports/index.html`` lists all runs; ``reports/latest.html`` redirects to the newest one;
``reports/metrics.json`` and ``reports/performance_data.json`` are copies of the latest run
(stable paths for CI).
"""

from __future__ import annotations

import json
import shutil
from html import escape
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.benchmarks import slow_step_limit
from core.config import FrameworkConfig
from models.execution_model import RunSummary
from models.performance_data import SCHEMA_VERSION
from models.report_model import TrendPoint
from reporting import charts
from reporting.dashboard import READINESS_CLASS, DashboardBuilder
from reporting.execution_page import ExecutionPageBuilder
from reporting.export import PerformanceDataExporter
from reporting.links import execution_page, execution_slugs, relative_to
from reporting.portal import INDEX_FILE, load_entries, write_entry
from utils.fs import prune_dirs
from utils.logger import get_logger

TEMPLATE_DIR = Path(__file__).parent / "templates"
_log = get_logger("report")


def plotly_script(mode: str) -> str:
    from plotly.offline import get_plotlyjs, get_plotlyjs_version

    if mode == "inline":
        return f"<script>{get_plotlyjs()}</script>"
    return (
        f'<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js" charset="utf-8"></script>'
    )


class HtmlReportGenerator:
    def __init__(self, config: FrameworkConfig) -> None:
        self.config = config
        self.report_dir = config.report_dir
        self.env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _render(self, template: str, path: Path, **context: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        html = self.env.get_template(template).render(
            title=self.config.reporting.title,
            plotly_js=plotly_script(self.config.reporting.plotly_mode),
            **context,
        )
        path.write_text(html, encoding="utf-8")
        return path

    def write_metrics(self, run: RunSummary, run_dir: Path | None = None) -> Path:
        """``metrics.json``: run summary without raw samples (raw data lives in the run folder)."""
        run_dir = run_dir or self.config.run_dir(run.run_id)
        payload = run.model_dump(mode="json", exclude={"executions": {"__all__": {"telemetry"}}})
        for row, execution in zip(payload["executions"], run.executions, strict=True):
            row["crashes"] = [c.model_dump(mode="json") for c in execution.telemetry.crashes]
            row["steps"] = [s.model_dump(mode="json") for s in execution.telemetry.steps]
        text = json.dumps(payload, indent=2)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "metrics.json").write_text(text, encoding="utf-8")
        latest = self.report_dir / "metrics.json"
        latest.write_text(text, encoding="utf-8")
        return latest

    def generate(self, run: RunSummary, trend: list[TrendPoint]) -> Path:
        run_dir = self.config.run_dir(run.run_id)
        slugs = execution_slugs(run)
        context = DashboardBuilder(self.config.reporting.title).build(run, trend, slugs)
        for row in context.executions:
            row["screenshot"] = relative_to(row["screenshot"], run_dir)
        index = self._render(
            "run.html.j2",
            run_dir / "index.html",
            ctx=context,
            home="../../index.html",
            trend_file="../../history/trend.json",
            schema_version=SCHEMA_VERSION,
        )

        pages = ExecutionPageBuilder(run_dir, slow_step_limit(self.config.benchmarks))
        views = [pages.build(e, slug) for e, slug in zip(run.executions, slugs, strict=True)]
        for i, view in enumerate(views):
            view.nav = {
                "prev": f"{views[i - 1].slug}.html" if i > 0 else None,
                "next": f"{views[i + 1].slug}.html" if i + 1 < len(views) else None,
                "run": f"../index.html#exec-{view.slug}",
            }
            self._render(
                "execution.html.j2",
                run_dir / execution_page(view.slug),
                v=view,
                run=run,
                home="../../../index.html",
                run_home="../index.html",
            )

        metrics = self.write_metrics(run, run_dir)
        perf = PerformanceDataExporter(run_dir).write(run, slugs)
        shutil.copyfile(perf, self.report_dir / "performance_data.json")
        write_entry(run, run_dir)
        removed = prune_dirs(
            self.report_dir / "runs", self.config.reporting.keep_runs, protect={run.run_id}, marker=INDEX_FILE
        )
        if removed:
            _log.info(
                "Removed %d old run folder(s) (keep_runs=%d)", len(removed), self.config.reporting.keep_runs
            )
        self.write_portal(trend)
        (self.report_dir / "latest.html").write_text(
            f'<!doctype html><meta http-equiv="refresh" content="0; url=runs/{run.run_id}/index.html">'
            f'<a href="runs/{run.run_id}/index.html">Latest run {escape(run.run_id)}</a>',
            encoding="utf-8",
        )
        _log.info("Report written to %s (metrics: %s, performance data: %s)", index, metrics, perf)
        return index

    def write_portal(self, trend: list[TrendPoint]) -> Path:
        entries = load_entries(self.report_dir)
        return self._render(
            "portal.html.j2",
            self.report_dir / "index.html",
            entries=entries,
            readiness_class={r.value: c for r, c in READINESS_CLASS.items()},
            trend_chart=charts.run_history(trend),
            home="index.html",
        )
