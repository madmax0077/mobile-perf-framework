"""Step screenshots, production benchmarks, issues, per-run report folders and data export."""

from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from core.analysis import ExecutionEvaluator
from core.benchmarks import evaluate_benchmarks, rate
from core.config import FrameworkConfig, ReportingSettings, ScreenshotMode
from core.events import EventBus
from core.issues import collect_issues
from core.pytest_plugin import PerfState, build_run_summary
from core.recorder import PerfRecorder
from core.screenshots import ScreenshotRecorder
from core.session_manager import SessionManager
from core.tracing import DriverTracer
from models.execution_model import AppInfo, DeviceInfo, Platform, TestExecution, TestStatus, TestType
from models.performance_data import SCHEMA_VERSION, PerformanceData
from models.report_model import BenchmarkStatus, CulpritLayer, Readiness, Severity
from models.telemetry_model import CrashEvent, CrashType, NetworkRequest, StepTiming, TelemetryData
from reporting.html_report import HtmlReportGenerator
from telemetry.parsers.app_log import AppLogAnalyzer


class FakeProvider:
    """Records stamps / samples and writes a real PNG as screenshot."""

    def __init__(self) -> None:
        self.recorded: list[Any] = []
        self.calls: list[str] = []

    def stamp(self, source: str) -> dict[str, Any]:
        self.calls.append("stamp")
        return {"timestamp": datetime.now(UTC), "elapsed_sec": 0.0, "source": source}

    def record(self, sample: Any) -> None:
        self.recorded.append(sample)

    def take_screenshot(self, path: Path) -> Path:
        self.calls.append("screenshot")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1080, 2400), "#3366cc").save(path)
        return path


def _settings(mode: ScreenshotMode, max_width: int = 540) -> ReportingSettings:
    return ReportingSettings(screenshots=mode, screenshot_max_width=max_width)


def test_step_screenshots_are_numbered_downscaled_and_taken_after_the_stamp(tmp_path: Path) -> None:
    provider = FakeProvider()
    shots = ScreenshotRecorder(provider, tmp_path, _settings(ScreenshotMode.EVERY_STEP))
    recorder = PerfRecorder(provider, screenshots=shots)

    with recorder.step("open profile"):
        pass
    with pytest.raises(RuntimeError), recorder.step("submit", measure=False):
        raise RuntimeError("Message: element not found\nStacktrace:\n  at ...")

    first, second = recorder.steps
    assert provider.calls == ["stamp", "screenshot", "stamp", "screenshot"]
    assert Path(first.screenshot).name == "001_open_profile.jpg"
    with Image.open(first.screenshot) as image:
        assert image.size == (540, 1200)
    assert not list(tmp_path.glob("*.png"))
    assert not second.success and not second.measured
    assert second.error == "RuntimeError: Message: element not found"


def test_screenshot_modes_and_repeating_steps(tmp_path: Path) -> None:
    provider = FakeProvider()
    every = ScreenshotRecorder(provider, tmp_path / "every", _settings(ScreenshotMode.EVERY_STEP, 0))
    assert every.capture("search", repeating=True) is not None
    assert every.capture("search", repeating=True) is None
    assert every.capture("search", repeating=True, failed=True) is not None

    on_failure = ScreenshotRecorder(provider, tmp_path / "fail", _settings(ScreenshotMode.ON_FAILURE))
    assert on_failure.capture("step") is None
    assert on_failure.capture("step", failed=True) is not None

    off = ScreenshotRecorder(provider, tmp_path / "off", _settings(ScreenshotMode.OFF))
    assert off.capture("step", failed=True) is None


@pytest.mark.parametrize(
    ("value", "good", "limit", "expected"),
    [
        (1500, 2000, 5000, BenchmarkStatus.PASS),
        (3000, 2000, 5000, BenchmarkStatus.WARN),
        (5000, 2000, 5000, BenchmarkStatus.FAIL),
        (100.0, 99.9, 98.91, BenchmarkStatus.PASS),
        (99.0, 99.9, 98.91, BenchmarkStatus.WARN),
        (0.0, 99.9, 98.91, BenchmarkStatus.FAIL),
        (None, 1, 2, BenchmarkStatus.NO_DATA),
    ],
)
def test_benchmark_rating(value: float | None, good: float, limit: float, expected: BenchmarkStatus) -> None:
    assert rate(value, good, limit) == expected


def test_readiness_verdict(unit_config: FrameworkConfig) -> None:
    results = evaluate_benchmarks(unit_config.benchmarks, {"cold_start_p90_ms": 1200, "jank_pct": 30})
    by_id = {r.id: r.status for r in results}
    assert by_id["cold_start"] == BenchmarkStatus.PASS and by_id["slow_frames"] == BenchmarkStatus.FAIL
    assert by_id["warm_start"] == BenchmarkStatus.NO_DATA
    assert Readiness.from_results(results) == Readiness.NOT_READY
    assert Readiness.from_results([]) == Readiness.NOT_EVALUATED


def _execution(steps: list[StepTiming], **telemetry: Any) -> TestExecution:
    return TestExecution(
        test_id="tests/x.py::test_login",
        name="test_login",
        test_type=TestType.JOURNEY,
        started_at=steps[0].started_at if steps else datetime.now(UTC),
        device=DeviceInfo(name="Pixel 8", platform=Platform.ANDROID, os_version="14", provider="testmu"),
        app=AppInfo(name="app", version="1", build_number="1"),
        telemetry=TelemetryData(steps=steps, **telemetry),
    )


def test_issues_are_ranked_and_point_at_steps_and_crashes(unit_config: FrameworkConfig) -> None:
    now = datetime.now(UTC)
    steps = [
        StepTiming(name="launch", duration_ms=800, timestamp=now - timedelta(seconds=20)),
        StepTiming(name="home", duration_ms=7200, timestamp=now - timedelta(seconds=10)),
        StepTiming(name="prompt", duration_ms=8000, measured=False, timestamp=now - timedelta(seconds=2)),
        StepTiming(name="verify", duration_ms=900, success=False, error="TimeoutException", timestamp=now),
    ]
    crash = CrashEvent(
        crash_type=CrashType.JAVA_CRASH,
        exception="java.lang.NullPointerException",
        step="home",
        timestamp=now - timedelta(seconds=12),
    )
    requests = [
        NetworkRequest(url="https://api/a", status=200),
        NetworkRequest(url="https://api/b", status=503, failed=True),
    ]
    execution = _execution(steps, crashes=[crash], requests=requests)
    execution.status = TestStatus.FAILED
    ExecutionEvaluator(unit_config).evaluate(execution)

    issues = execution.issues
    assert [i.id for i in issues] == [f"issue-{n}" for n in range(1, len(issues) + 1)]
    assert [i.severity for i in issues[:2]] == [Severity.CRITICAL, Severity.CRITICAL]
    by_category = {i.category: i for i in issues}
    assert by_category["crash"].target == "crash-1" and by_category["crash"].step_index == 2
    assert by_category["step_failure"].target == "step-4"
    assert by_category["slow_step"].target == "step-2" and "7.20 s" in by_category["slow_step"].title
    assert "prompt" not in {i.step for i in issues}, "unmeasured waits are not slow steps"
    assert by_category["network"].severity == Severity.MAJOR and "1 of 2" in by_category["network"].title
    assert "test_failure" not in by_category, "a failed step already explains the failure"


def test_failed_test_without_failed_step_gets_test_failure_issue() -> None:
    execution = _execution([StepTiming(name="only", duration_ms=10)])
    execution.status = TestStatus.FAILED
    execution.error = "def test():\n>   assert shown == phone\nE   AssertionError: profile shows ''\n"
    [issue] = collect_issues(execution)
    assert issue.category == "test_failure" and issue.detail == "AssertionError: profile shows ''"


def _run_simulated_journey(unit_config: FrameworkConfig, run_id: str) -> tuple[PerfState, Path]:
    unit_config.provider.providers["simulated"]["crash_after_actions"] = 3
    unit_config.telemetry.collectors = ["network"]
    run_dir = unit_config.run_dir(run_id)
    bus = EventBus()
    sessions = SessionManager(unit_config, run_dir, event_bus=bus)
    state = PerfState(
        config=unit_config,
        run_id=run_id,
        run_dir=run_dir,
        event_bus=bus,
        sessions=sessions,
        evaluator=ExecutionEvaluator(unit_config),
        started_at=datetime.now(UTC),
    )
    session = sessions.open("tests/unit::journey", "crashing journey", TestType.JOURNEY)
    sessions.start_telemetry(session)
    recorder = PerfRecorder(
        session.provider,
        session.crash_monitor,
        screenshots=session.screenshots,
        tracer=DriverTracer(),
        app_log=AppLogAnalyzer(session.provider.app_id or ""),
    )
    for i in range(1, 5):
        with recorder.step(f"tap_{i}"):
            session.driver.find_element("id", f"button_{i}").click()
    state.finalize(sessions.close(session, TestStatus.PASSED))
    return state, run_dir


def test_run_folder_drilldown_pages_and_exports(unit_config: FrameworkConfig) -> None:
    state, run_dir = _run_simulated_journey(unit_config, "20260101-000000-unit")
    run = build_run_summary(state)
    index = HtmlReportGenerator(unit_config).generate(run, [])

    assert index == run_dir / "index.html"
    page = run_dir / "executions" / "01-crashing_journey.html"
    html = page.read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    assert {"step-1", "step-4", "crash-1", "issue-1", "timeline", "bench-crash_free_sessions"} <= ids
    assert html.count("data-shot") >= 4, "every step screenshot is in the filmstrip / step table"
    run_html = index.read_text(encoding="utf-8")
    assert "Production Readiness" in run_html and "executions/01-crashing_journey.html#issue-1" in run_html

    data = PerformanceData.model_validate_json(
        (run_dir / "performance_data.json").read_text(encoding="utf-8")
    )
    assert data.schema_version == SCHEMA_VERSION and data.readiness.verdict == Readiness.NOT_READY
    [execution] = data.executions
    assert [s.name for s in execution.steps] == ["tap_1", "tap_2", "tap_3", "tap_4"]
    assert execution.steps[0].screenshot.startswith("artifacts/") and execution.steps[2].link.endswith(
        "#step-3"
    )
    first = execution.steps[0]
    assert first.cause and first.breakdown is not None
    test_layer = [c for c in first.breakdown.culprits if c.layer == CulpritLayer.TEST]
    assert test_layer and test_layer[0].name == "_run_simulated_journey"
    assert test_layer[0].location.startswith("tests/unit/test_reporting_drilldown.py:")
    assert any(op.name.startswith("com.example.") for op in execution.operations)
    assert {"why", "why-step-1", "operations"} <= ids and "clickElement" in html
    with (run_dir / "csv" / "requests.csv").open(encoding="utf-8") as handle:
        operations = {row["operation"] for row in csv.DictReader(handle)}
    assert "com.example.button.query" in operations
    assert data.issues[0].category == "crash" and data.issues[0].link.endswith("#issue-1")
    assert json.loads((run_dir / "performance_data.schema.json").read_text(encoding="utf-8"))["title"]
    with (run_dir / "csv" / "steps.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 4
    assert (unit_config.report_dir / "performance_data.json").exists()
    assert (unit_config.report_dir / "metrics.json").exists()

    portal = (unit_config.report_dir / "index.html").read_text(encoding="utf-8")
    assert f"runs/{run.run_id}/index.html" in portal and "Not Ready" in portal
    assert f"runs/{run.run_id}/index.html" in (unit_config.report_dir / "latest.html").read_text(
        encoding="utf-8"
    )


def test_old_run_folders_are_pruned(unit_config: FrameworkConfig) -> None:
    unit_config.reporting.keep_runs = 2
    runs = unit_config.report_dir / "runs"
    for name in ("20250101-000000-a", "20250102-000000-b", "20250103-000000-c"):
        (runs / name).mkdir(parents=True)
        (runs / name / "run_index.json").write_text("{}", encoding="utf-8")
    (runs / "20240101-000000-unfinished").mkdir()
    state, _ = _run_simulated_journey(unit_config, "20260101-000000-unit")
    HtmlReportGenerator(unit_config).generate(build_run_summary(state), [])
    assert sorted(p.name for p in runs.iterdir()) == [
        "20240101-000000-unfinished",
        "20250103-000000-c",
        "20260101-000000-unit",
    ]
