from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.analysis import ExecutionEvaluator
from core.config import CrashSettings, FrameworkConfig
from core.events import EventBus
from core.pytest_plugin import PerfState, build_run_summary
from core.recorder import PerfRecorder
from core.session_manager import SessionManager
from models.execution_model import TestStatus, TestType
from models.telemetry_model import (
    CpuSnapshot,
    CrashEvent,
    CrashType,
    MemorySnapshot,
    StartupMetrics,
    StepTiming,
    TelemetryData,
)
from providers.testmu_api import _decompress_text, app_metrics_crash_info, map_app_metrics
from reporting.html_report import HtmlReportGenerator
from telemetry.crash_monitor import CrashMonitor
from telemetry.parsers.crash import parse_crash_report, parse_logcat_crashes
from telemetry.parsers.har import parse_har

PKG = "com.rebellapp.consumer.uat"
FIXTURES = Path(__file__).parent / "fixtures"


def test_logcat_crashes_for_app_only(fixture_text) -> None:
    events = parse_logcat_crashes(fixture_text("logcat_crashes.txt"), PKG)

    assert [e.crash_type for e in events] == [CrashType.JAVA_CRASH, CrashType.ANR, CrashType.NATIVE_CRASH]
    java, anr, native = events
    assert java.pid == 5120 and java.process == PKG
    assert java.exception.startswith("java.lang.NullPointerException")
    assert "WelcomeActivity.onNext" in java.stacktrace
    assert java.timestamp.tzinfo is not None and java.device_time == "10-06 09:40:12.101"
    assert anr.pid == 5300 and anr.exception.startswith("ANR - Input dispatching timed out")
    assert "Start proc" not in anr.stacktrace
    assert native.pid == 5500 and "SIGSEGV" in native.exception and "strlen" in native.stacktrace


def test_logcat_noise_and_other_apps_are_ignored(fixture_text) -> None:
    all_apps = parse_logcat_crashes(fixture_text("logcat_crashes.txt"), "")
    assert {e.process for e in all_apps} == {PKG, "com.other.app"}
    assert parse_logcat_crashes(fixture_text("logcat_crashes.txt"), "com.not.installed") == []


def test_raw_stacktrace_crash_report() -> None:
    text = "java.lang.IllegalStateException: boom\n\tat com.x.Main.run(Main.kt:1)\n"
    [event] = parse_crash_report(text, PKG, source="crashlog")
    assert event.exception == "java.lang.IllegalStateException: boom" and event.source == "crashlog"


class _FakeProvider:
    app_id = PKG

    def __init__(self, logs: list[str], state: int = 4) -> None:
        self.logs, self.state = logs, state

    def read_device_log(self) -> str | None:
        return self.logs.pop(0) if self.logs else None

    def app_state(self) -> int:
        return self.state


def test_monitor_dedupes_and_post_session_reports_replace_app_died(fixture_text) -> None:
    log = fixture_text("logcat_crashes.txt")
    provider = _FakeProvider([log, log])
    monitor = CrashMonitor(provider, CrashSettings())  # type: ignore[arg-type]

    assert len(monitor.check("enter_phone")) == 3
    assert monitor.check("submit_phone") == []
    assert {e.step for e in monitor.events} == {"enter_phone"}

    died = CrashMonitor(_FakeProvider([], state=1), CrashSettings())  # type: ignore[arg-type]
    [marker] = died.check("submit_pin", step_failed=True)
    assert marker.crash_type == CrashType.APP_DIED
    assert died.check("teardown", step_failed=True) == []
    died.merge(parse_logcat_crashes(log, PKG)[:1])
    assert [(e.crash_type, e.step) for e in died.events] == [(CrashType.JAVA_CRASH, "submit_pin")]


def test_monitor_attributes_crash_to_step_window() -> None:
    now = datetime.now(UTC)
    monitor = CrashMonitor(_FakeProvider([]), CrashSettings())  # type: ignore[arg-type]
    monitor.merge([CrashEvent(crash_type=CrashType.JAVA_CRASH, pid=1, timestamp=now - timedelta(seconds=3))])
    monitor.attribute_steps(
        [
            StepTiming(name="first", duration_ms=2_000, timestamp=now - timedelta(seconds=8)),
            StepTiming(name="second", duration_ms=5_000, timestamp=now),
        ]
    )
    assert monitor.events[0].step == "second"


def test_simulated_crash_fails_test_and_reaches_report(unit_config: FrameworkConfig) -> None:
    unit_config.provider.providers["simulated"]["crash_after_actions"] = 3
    unit_config.telemetry.enabled = False
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
    session = sessions.open("tests/unit::crash", "crashing journey", TestType.JOURNEY)
    session.provider.get_launch_strategy().measure("cold")
    recorder = PerfRecorder(session.provider, session.crash_monitor)
    for i in range(1, 5):
        with recorder.step(f"tap_{i}"):
            session.driver.find_element("id", f"button_{i}").click()

    execution = state.finalize(sessions.close(session, TestStatus.PASSED))

    [crash] = execution.telemetry.crashes
    assert crash.crash_type == CrashType.JAVA_CRASH and crash.step == "tap_3"
    assert execution.status == TestStatus.FAILED and "java_crash" in (execution.error or "")
    assert execution.summary.crash_count == 1
    assert any(f.kind == "app_crash" and f.detected for f in execution.findings)

    run = build_run_summary(state)
    html = HtmlReportGenerator(unit_config).generate(run, []).read_text(encoding="utf-8")
    assert "Stability Dashboard" in html and "simulated crash after" in html
    metrics = json.loads((unit_config.report_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["metrics"]["crash_count"] == 1
    assert metrics["executions"][0]["crashes"][0]["step"] == "tap_3"


def test_testmu_app_profiling_series_mapping() -> None:
    payload = json.loads((FIXTURES / "testmu_appmetrics.json").read_text(encoding="utf-8"))
    start = datetime(2026, 10, 6, 9, 35, 43, tzinfo=UTC)
    samples = map_app_metrics(json.dumps(payload), start=start)

    cpu = [s for s in samples if isinstance(s, CpuSnapshot)]
    memory = [s for s in samples if isinstance(s, MemorySnapshot)]
    startup = {s.mode.value: s.total_time_ms for s in samples if isinstance(s, StartupMetrics)}
    assert len(cpu) == len(payload["cpu"]) and len(memory) == len(payload["mem"])
    assert memory[1].pss_mb == round(payload["mem"][1]["app"], 10)
    assert memory[1].timestamp == start + timedelta(seconds=1) and memory[1].elapsed_sec == 1
    assert startup == {"cold": 547.0, "hot": 50.0}
    assert app_metrics_crash_info(payload) == (0, 0, "")


def test_testmu_series_mapping_without_session_start() -> None:
    payload = json.loads((FIXTURES / "testmu_appmetrics.json").read_text(encoding="utf-8"))
    samples = map_app_metrics(payload)
    assert any(isinstance(s, CpuSnapshot) for s in samples)


def test_all_zero_live_cpu_is_replaced_by_post_session_series() -> None:
    now = datetime.now(UTC)
    live = TelemetryData(
        cpu=[
            CpuSnapshot(app_cpu_pct=0.0, timestamp=now, elapsed_sec=i, source="appium-perf") for i in range(3)
        ],
        memory=[MemorySnapshot(pss_mb=100.0, timestamp=now, elapsed_sec=0, source="dumpsys-meminfo")],
    )
    post = TelemetryData(
        cpu=[CpuSnapshot(app_cpu_pct=12.5, timestamp=now, elapsed_sec=0, source="testmu_app_profiling")],
        memory=[MemorySnapshot(pss_mb=200.0, timestamp=now, elapsed_sec=0, source="testmu_app_profiling")],
    )

    assert live.merge_missing(post) == ["cpu"]
    assert [s.app_cpu_pct for s in live.cpu] == [12.5]
    assert [s.pss_mb for s in live.memory] == [100.0]


def test_har_entry_list_and_gzip_crash_log() -> None:
    entries: list[dict[str, Any]] = [
        {"request": {"url": "https://api/x", "method": "GET"}, "response": {"status": 500}, "time": 12}
    ]
    [request] = parse_har(entries)
    assert request.failed and request.duration_ms == 12
    assert _decompress_text(gzip.compress(b"FATAL EXCEPTION")) == "FATAL EXCEPTION"
    assert _decompress_text(b"") == ""
