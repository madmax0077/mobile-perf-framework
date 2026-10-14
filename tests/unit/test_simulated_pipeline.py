"""End-to-end framework pipeline on the simulated provider (no device needed)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from core.analysis import ExecutionEvaluator
from core.config import FrameworkConfig
from core.events import EventBus
from core.pytest_plugin import PerfState, build_run_summary
from core.recorder import PerfRecorder
from core.session_manager import SessionManager
from core.trends import TrendStore, trend_point
from models.execution_model import TestStatus, TestType
from reporting.html_report import HtmlReportGenerator
from telemetry.startup_collector import StartupCollector


def test_simulated_session_to_report(unit_config: FrameworkConfig, tmp_path: Path) -> None:
    unit_config.telemetry.sampling_interval_sec = 0.2
    run_dir = unit_config.report_dir / "raw" / "unit"
    bus = EventBus()
    sessions = SessionManager(unit_config, run_dir, event_bus=bus)
    state = PerfState(
        config=unit_config,
        run_id="unit",
        run_dir=run_dir,
        event_bus=bus,
        sessions=sessions,
        evaluator=ExecutionEvaluator(unit_config),
        started_at=datetime.now(UTC),
    )

    session = sessions.open("tests/unit::sim", "sim journey", TestType.JOURNEY)
    sessions.start_telemetry(session)
    provider = session.provider
    profiler = StartupCollector(
        provider.get_launch_strategy(), recorder=provider.record, settle_time_sec=0, stamp=provider.stamp
    )
    profiler.benchmark(["cold", "warm", "hot"], 2)
    recorder = PerfRecorder(provider)
    for i in range(5):
        with recorder.step("search", iteration=i):
            session.driver.find_element("id", "search").click()
    time.sleep(1.2)
    execution = state.finalize(sessions.close(session, TestStatus.PASSED))

    assert execution.telemetry.cpu and execution.telemetry.memory and execution.telemetry.fps
    assert execution.telemetry.battery_usage and execution.telemetry.requests
    states = {s.mode.value: s.launch_state for s in execution.telemetry.startup}
    assert states == {"cold": "COLD", "warm": "WARM", "hot": "HOT"}
    assert execution.summary.cold_start_avg_ms > execution.summary.hot_start_avg_ms
    assert execution.score.overall is not None
    assert Path(execution.artifacts["raw_telemetry"]).exists()

    run = build_run_summary(state)
    trend = TrendStore(unit_config.report_dir / "history" / "trend.json").append(trend_point(run))
    index = HtmlReportGenerator(unit_config).generate(run, trend)
    html = index.read_text(encoding="utf-8")
    for heading in (
        "Executive Summary",
        "Startup Dashboard",
        "Memory Dashboard",
        "CPU Dashboard",
        "Battery Dashboard",
        "Network Dashboard",
        "FPS Dashboard",
        "Trend Report",
    ):
        assert heading in html
    assert (unit_config.report_dir / "metrics.json").exists()
