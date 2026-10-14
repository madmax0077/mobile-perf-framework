"""Plotly chart builders. Each function takes DataFrames / models and returns an
HTML ``<div>`` (plotly.js is included once by the page template)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from models.report_model import AnalysisFinding, CategoryScore, ComparisonResult, TrendPoint

PALETTE = px.colors.qualitative.Safe
_LAYOUT = {
    "template": "plotly_white",
    "margin": {"l": 50, "r": 20, "t": 50, "b": 45},
    "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    "font": {"family": "Inter, Segoe UI, Roboto, sans-serif", "size": 12},
    "height": 360,
}
_CONFIG = {"responsive": True, "displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


def to_html(fig: go.Figure, height: int | None = None, div_id: str | None = None) -> str:
    fig.update_layout(**_LAYOUT)
    if height:
        fig.update_layout(height=height)
    return fig.to_html(full_html=False, include_plotlyjs=False, config=_CONFIG, div_id=div_id)


def empty(message: str = "No data collected for this metric") -> str:
    return f'<div class="empty-chart text-muted">{message}</div>'


def _title(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(title={"text": title, "x": 0.01, "font": {"size": 15}})
    return fig


# ------------------------------------------------------------------ summary
def score_gauge(score: float | None, rating: str) -> str:
    value = score if score is not None else 0
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={"suffix": " / 100", "font": {"size": 34}},
            title={"text": rating, "font": {"size": 16}},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#1f2937"},
                "steps": [
                    {"range": [0, 70], "color": "#fecaca"},
                    {"range": [70, 80], "color": "#fde68a"},
                    {"range": [80, 90], "color": "#bbf7d0"},
                    {"range": [90, 100], "color": "#4ade80"},
                ],
            },
        )
    )
    fig.update_layout(margin={"l": 35, "r": 35, "t": 40, "b": 10})
    return to_html(fig, height=260)


def category_scores(categories: Sequence[CategoryScore]) -> str:
    rows = [c for c in categories if c.score is not None]
    if not rows:
        return empty("No categories could be scored")
    df = pd.DataFrame(
        {
            "category": [c.category.title() for c in rows],
            "score": [c.score for c in rows],
            "weight": [f"{c.effective_weight:.0f}%" for c in rows],
        }
    )
    fig = px.bar(
        df,
        x="category",
        y="score",
        text="score",
        hover_data=["weight"],
        color="category",
        color_discrete_sequence=PALETTE,
    )
    fig.update_traces(texttemplate="%{text:.0f}", textposition="outside", showlegend=False)
    fig.update_yaxes(range=[0, 110], title="score")
    return to_html(_title(fig, "Category scores (weight re-normalised to available data)"))


# ------------------------------------------------------------------ startup
def startup_mode_trend(df: pd.DataFrame, mode: str) -> str:
    subset = df[df["mode"] == mode] if not df.empty else df
    if subset.empty:
        return empty(f"No {mode} start measurements")
    fig = px.line(
        subset,
        x="iteration",
        y="total_time_ms",
        color="test",
        markers=True,
        color_discrete_sequence=PALETTE,
        hover_data=["launch_state", "method"],
    )
    mean = subset["total_time_ms"].mean()
    fig.add_hline(y=mean, line_dash="dot", annotation_text=f"avg {mean:.0f} ms", line_color="#6b7280")
    fig.update_yaxes(title="launch time (ms)")
    return to_html(_title(fig, f"{mode.title()} start trend"))


def startup_comparison(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No startup measurements")
    agg = df.groupby(["test", "mode"])["total_time_ms"].agg(["mean", "std", "min", "max"]).reset_index()
    fig = px.bar(
        agg,
        x="mode",
        y="mean",
        color="test",
        barmode="group",
        error_y="std",
        text="mean",
        color_discrete_sequence=PALETTE,
        hover_data=["min", "max"],
        category_orders={"mode": ["cold", "warm", "hot"]},
    )
    fig.update_traces(texttemplate="%{text:.0f}", textposition="outside")
    fig.update_yaxes(title="avg launch time (ms)")
    return to_html(_title(fig, "Startup comparison (mean +/- std)"))


# ------------------------------------------------------------------- memory
def memory_timeline(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    fig = px.line(df, x="elapsed_sec", y="pss_mb", color="test", color_discrete_sequence=PALETTE)
    for column, dash in (("native_heap_mb", "dot"), ("java_heap_mb", "dash")):
        if column in df and df[column].notna().any():
            for i, (test, group) in enumerate(df.groupby("test")):
                fig.add_scatter(
                    x=group["elapsed_sec"],
                    y=group[column],
                    mode="lines",
                    name=f"{column.replace('_mb', '')} - {test}",
                    line={"dash": dash, "width": 1, "color": PALETTE[i % len(PALETTE)]},
                    visible="legendonly",
                )
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="PSS (MB)")
    return to_html(_title(fig, "Memory timeline (PSS; heaps toggle in legend)"))


def peak_memory(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    agg = df.groupby("test")["pss_mb"].agg(peak="max", average="mean").reset_index()
    long = agg.melt(id_vars="test", var_name="stat", value_name="MB")
    fig = px.bar(
        long,
        x="test",
        y="MB",
        color="stat",
        barmode="group",
        text="MB",
        color_discrete_sequence=["#ef4444", "#60a5fa"],
    )
    fig.update_traces(texttemplate="%{text:.0f}", textposition="outside")
    return to_html(_title(fig, "Peak vs average memory"))


def leak_detection(df: pd.DataFrame, findings: dict[str, AnalysisFinding]) -> str:
    if df.empty:
        return empty()
    fig = go.Figure()
    for i, (test, group) in enumerate(df.groupby("test")):
        color = PALETTE[i % len(PALETTE)]
        fig.add_scatter(
            x=group["elapsed_sec"],
            y=group["pss_mb"],
            mode="markers",
            name=str(test),
            marker={"size": 5, "color": color, "opacity": 0.6},
        )
        if len(group) >= 3 and group["elapsed_sec"].nunique() > 1:
            slope, intercept = np.polyfit(group["elapsed_sec"], group["pss_mb"], 1)
            xs = np.array([group["elapsed_sec"].min(), group["elapsed_sec"].max()])
            finding = findings.get(str(test))
            flag = " LEAK" if finding and finding.detected else ""
            fig.add_scatter(
                x=xs,
                y=slope * xs + intercept,
                mode="lines",
                name=f"trend {slope * 60:.2f} MB/min{flag} - {test}",
                line={"color": "#dc2626" if flag else color, "width": 3 if flag else 2},
            )
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="PSS (MB)")
    return to_html(_title(fig, "Leak detection (least-squares trend)"))


# ---------------------------------------------------------------------- cpu
def cpu_timeline(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    fig = px.line(df, x="elapsed_sec", y="app_cpu_pct", color="test", color_discrete_sequence=PALETTE)
    if "total_cpu_pct" in df and df["total_cpu_pct"].notna().any():
        fig.add_scatter(
            x=df["elapsed_sec"],
            y=df["total_cpu_pct"],
            mode="lines",
            name="device total",
            line={"color": "#9ca3af", "dash": "dot"},
            visible="legendonly",
        )
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="CPU % (all cores)")
    return to_html(_title(fig, "CPU utilisation timeline"))


def cpu_peaks(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    fig = px.box(
        df, x="test", y="app_cpu_pct", color="test", points="outliers", color_discrete_sequence=PALETTE
    )
    peaks = df.groupby("test")["app_cpu_pct"].max().reset_index()
    fig.add_scatter(
        x=peaks["test"],
        y=peaks["app_cpu_pct"],
        mode="markers+text",
        name="peak",
        text=peaks["app_cpu_pct"].round(1),
        textposition="top center",
        marker={"symbol": "triangle-up", "size": 12, "color": "#dc2626"},
    )
    fig.update_layout(showlegend=False)
    fig.update_yaxes(title="CPU %")
    return to_html(_title(fig, "CPU distribution and peaks"))


# ------------------------------------------------------------------ battery
def battery_trend(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    fig = px.line(
        df,
        x="elapsed_sec",
        y="level_pct",
        color="test",
        color_discrete_sequence=PALETTE,
        hover_data=[c for c in ("temperature_c", "voltage_mv") if c in df],
    )
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="battery level (%)")
    return to_html(_title(fig, "Battery usage trend"))


def battery_drain(df: pd.DataFrame) -> str:
    if df.empty or df["drain_pct"].isna().all():
        return empty("No battery drain attribution available")
    fig = px.bar(
        df,
        x="test",
        y="drain_pct",
        text="drain_pct",
        color="test",
        color_discrete_sequence=PALETTE,
        hover_data=[c for c in ("app_power_mah", "duration_sec", "drain_pct_per_hour") if c in df],
    )
    fig.update_traces(texttemplate="%{text:.3f}%", textposition="outside", showlegend=False)
    fig.update_yaxes(title="drain (% of capacity)")
    return to_html(_title(fig, "Battery drain %"))


# ------------------------------------------------------------------ network
def api_response_times(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No API requests captured (enable network logs / HAR)")
    data = df.assign(
        endpoint=df["url"].str.replace(r"^https?://[^/]+", "", regex=True).str.slice(0, 60),
        outcome=np.where(df["failed"], "failed", "ok"),
    )
    fig = px.box(
        data,
        x="endpoint",
        y="duration_ms",
        color="outcome",
        points="all",
        color_discrete_map={"ok": "#2563eb", "failed": "#dc2626"},
    )
    fig.update_xaxes(title=None, tickangle=-30)
    fig.update_yaxes(title="response time (ms)")
    return to_html(_title(fig, "API response times"), height=400)


def throughput(df: pd.DataFrame) -> str:
    if df.empty:
        return empty()
    long = df.melt(
        id_vars=["elapsed_sec", "test"],
        value_vars=["rx_kbps", "tx_kbps"],
        var_name="direction",
        value_name="kbps",
    )
    fig = px.line(
        long, x="elapsed_sec", y="kbps", color="test", line_dash="direction", color_discrete_sequence=PALETTE
    )
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="throughput (kbit/s)")
    return to_html(_title(fig, "Throughput (rx solid / tx dashed)"))


def failed_requests(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No API requests captured")
    data = df.assign(outcome=np.where(df["failed"], "failed", "ok"))
    counts = data.groupby(["test", "outcome"]).size().reset_index(name="requests")
    fig = px.bar(
        counts,
        x="test",
        y="requests",
        color="outcome",
        text="requests",
        color_discrete_map={"ok": "#22c55e", "failed": "#dc2626"},
    )
    fig.update_traces(textposition="inside")
    return to_html(_title(fig, "Successful vs failed requests"))


# ---------------------------------------------------------------- stability
CRASH_COLORS = {"java_crash": "#dc2626", "native_crash": "#7f1d1d", "anr": "#f59e0b", "app_died": "#6b7280"}


def crashes_by_test(df: pd.DataFrame, tests: Sequence[str]) -> str:
    if df.empty:
        frame = pd.DataFrame({"test": list(tests), "events": [0] * len(tests)})
        fig = px.bar(frame, x="test", y="events", color_discrete_sequence=["#22c55e"])
        fig.update_yaxes(range=[0, 1], dtick=1)
        return to_html(_title(fig, "Crashes / ANRs per test - none detected"))
    counts = df.groupby(["test", "crash_type"]).size().reset_index(name="events")
    fig = px.bar(
        counts, x="test", y="events", color="crash_type", text="events", color_discrete_map=CRASH_COLORS
    )
    fig.update_yaxes(dtick=1)
    return to_html(_title(fig, "Crashes / ANRs per test"))


def crash_timeline(df: pd.DataFrame, steps_df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No crashes or ANRs detected")
    fig = go.Figure()
    if not steps_df.empty:
        fig.add_trace(
            go.Scatter(
                x=steps_df["timestamp"],
                y=steps_df["test"],
                mode="markers",
                marker={"symbol": "line-ns-open", "size": 14, "color": "#9ca3af"},
                text=steps_df["name"],
                hovertemplate="step %{text}<extra></extra>",
                name="steps",
            )
        )
    for crash_type, group in df.groupby("crash_type"):
        fig.add_trace(
            go.Scatter(
                x=group["timestamp"],
                y=group["test"],
                mode="markers",
                marker={"symbol": "x", "size": 14, "color": CRASH_COLORS.get(str(crash_type), "#dc2626")},
                text=group["exception"].fillna("").str.slice(0, 120),
                customdata=group["step"].fillna("-"),
                hovertemplate="%{text}<br>step: %{customdata}<extra></extra>",
                name=str(crash_type),
            )
        )
    return to_html(_title(fig, "Crash timeline (vs test steps)"))


# ---------------------------------------------------------------- drilldown
STEP_COLORS = {"ok": "#22c55e", "slow": "#f59e0b", "failed": "#dc2626", "wait": "#9ca3af"}


def plot_time(value: datetime) -> str:
    """Naive UTC timestamp string (plotly.js has no time-zone support)."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def execution_timeline(
    steps: Sequence[dict[str, Any]],
    series: Sequence[tuple[str, pd.DataFrame, str]],
    crashes: Sequence[dict[str, Any]],
    div_id: str = "timeline",
) -> str:
    """Step Gantt on top, metric series below, all on one time axis; crashes as vertical lines.

    ``steps``: dicts with anchor, name, start, end (datetime), duration_ms, state (STEP_COLORS key).
    ``series``: (title, frame with timestamp + value columns, value column).
    ``crashes``: dicts with timestamp, label, anchor.
    """
    series = [s for s in series if not s[1].empty]
    if not steps and not series:
        return empty("No step timings or telemetry recorded for this test")
    rows = (1 if steps else 0) + len(series)
    titles = (["Steps (click to open)"] if steps else []) + [title for title, _, _ in series]
    heights = ([0.34] if steps else []) + [0.66 / max(1, len(series))] * len(series)
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=titles,
        row_heights=heights,
    )
    row = 1
    if steps:
        for state, color in STEP_COLORS.items():
            group = [s for s in steps if s["state"] == state]
            if not group:
                continue
            fig.add_trace(
                go.Bar(
                    base=[plot_time(s["start"]) for s in group],
                    x=[max(s["duration_ms"], 1) for s in group],
                    y=[s["name"] for s in group],
                    orientation="h",
                    marker_color=color,
                    customdata=[s["anchor"] for s in group],
                    text=[f"{s['duration_ms'] / 1000:.2f} s" for s in group],
                    hovertemplate="%{y}: %{text}<extra></extra>",
                    name=state,
                ),
                row=1,
                col=1,
            )
        fig.update_yaxes(autorange="reversed", type="category", row=1, col=1)
        row = 2
    for i, (title, frame, column) in enumerate(series):
        fig.add_trace(
            go.Scatter(
                x=[plot_time(t) for t in frame["timestamp"]],
                y=frame[column],
                mode="lines+markers",
                marker={"size": 4},
                line={"color": PALETTE[i % len(PALETTE)]},
                name=title,
                showlegend=False,
            ),
            row=row + i,
            col=1,
        )
    for crash in crashes:
        fig.add_shape(
            type="line",
            x0=plot_time(crash["timestamp"]),
            x1=plot_time(crash["timestamp"]),
            y0=0,
            y1=1,
            xref="x",
            yref="paper",
            line={"color": "#dc2626", "width": 2, "dash": "dot"},
            name=crash["anchor"],
        )
    fig.update_xaxes(type="date")
    fig.update_layout(barmode="overlay", bargap=0.35)
    return to_html(fig, height=260 + 170 * rows, div_id=div_id)


# ---------------------------------------------------------------------- fps
def average_fps(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No rendered frames captured")
    fig = px.line(df, x="elapsed_sec", y="fps", color="test", markers=True, color_discrete_sequence=PALETTE)
    fig.add_hline(y=60, line_dash="dot", line_color="#9ca3af", annotation_text="60 fps")
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="FPS", range=[0, 65])
    return to_html(_title(fig, "Average FPS per window"))


def jank_rate(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No rendered frames captured")
    agg = df.groupby("test").agg(frames=("total_frames", "sum"), janky=("janky_frames", "sum")).reset_index()
    agg["jank_pct"] = np.where(agg["frames"] > 0, agg["janky"] / agg["frames"] * 100.0, 0.0)
    fig = px.bar(
        agg,
        x="test",
        y="jank_pct",
        text="jank_pct",
        color="test",
        color_discrete_sequence=PALETTE,
        hover_data=["frames", "janky"],
    )
    fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside", showlegend=False)
    fig.update_yaxes(title="janky frames (%)")
    return to_html(_title(fig, "Jank rate"))


def frame_drops(df: pd.DataFrame) -> str:
    if df.empty:
        return empty("No rendered frames captured")
    fig = px.bar(df, x="elapsed_sec", y="frame_drops", color="test", color_discrete_sequence=PALETTE)
    fig.update_xaxes(title="elapsed (s)")
    fig.update_yaxes(title="dropped frames per window")
    return to_html(_title(fig, "Frame drops"))


# --------------------------------------------------------- comparison/trends
def comparison_chart(result: ComparisonResult) -> str:
    rows = [d for d in result.deltas if d.diff_pct is not None]
    if not rows:
        return empty("Nothing comparable between builds")
    df = pd.DataFrame(
        {
            "metric": [d.metric for d in rows],
            "diff_pct": [d.diff_pct for d in rows],
            "status": [
                "regression" if d.regression else "improved" if d.improvement else "neutral" for d in rows
            ],
        }
    )
    fig = px.bar(
        df,
        x="diff_pct",
        y="metric",
        orientation="h",
        color="status",
        text="diff_pct",
        color_discrete_map={"regression": "#dc2626", "improved": "#16a34a", "neutral": "#9ca3af"},
    )
    fig.update_traces(texttemplate="%{text:+.1f}%", textposition="outside")
    fig.add_vline(x=0, line_color="#111827")
    fig.update_xaxes(title=f"change vs {result.baseline_label} (%)")
    return to_html(_title(fig, f"{result.candidate_label} vs {result.baseline_label}"), height=420)


def run_history(points: Sequence[TrendPoint]) -> str:
    if len(points) < 2:
        return empty("Trend charts appear after two or more runs")
    rows = [
        {"run": f"{p.timestamp:%m-%d %H:%M} {p.build_number or ''}", "score": p.score, **p.metrics}
        for p in points
    ]
    df = pd.DataFrame(rows)
    fig = go.Figure()
    fig.add_scatter(x=df["run"], y=df["score"], name="score", mode="lines+markers", line={"width": 3})
    for i, metric in enumerate(("cold_start_avg_ms", "peak_pss_mb", "avg_cpu_pct", "avg_fps")):
        if metric in df and df[metric].notna().any():
            fig.add_scatter(
                x=df["run"],
                y=df[metric],
                name=metric,
                mode="lines+markers",
                yaxis="y2",
                line={"dash": "dot", "color": PALETTE[i % len(PALETTE)]},
            )
    fig.update_layout(
        yaxis={"title": "score", "range": [0, 105]},
        yaxis2={"title": "metric value", "overlaying": "y", "side": "right"},
    )
    return to_html(_title(fig, "Run-over-run trend"), height=400)
