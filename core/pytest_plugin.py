"""Pytest plugin wiring the framework into the test lifecycle.

Hooks:
    pytest_sessionstart      load config, create run id / directories
    pytest_runtest_setup     open provider session + driver for perf-marked tests
    pytest_runtest_call      start telemetry before the test body, stop after it
    pytest_runtest_teardown  screenshot on failure, session status, persist execution JSON
    pytest_sessionfinish     aggregate, score, compare, update trends, render reports/index.html

Tests opt in with ``@pytest.mark.perf`` / ``startup`` / ``journey`` / ``endurance`` / ``benchmark``.
"""

from __future__ import annotations

import os
import platform as host_platform
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from core.analysis import ExecutionEvaluator, aggregate_executions, summarize
from core.benchmarks import evaluate_benchmarks, run_metrics
from core.comparison import compare_executions, compare_runs
from core.config import ConfigError, FrameworkConfig, deep_merge, load_config
from core.events import EventBus, EventType
from core.recorder import PerfRecorder
from core.scoring import ScoringEngine
from core.session_manager import PerfSession, SessionManager, safe_name
from core.tracing import DriverTracer
from core.trends import TrendStore, trend_point
from models.execution_model import AppInfo, Platform, RunSummary, TestExecution, TestStatus, TestType
from models.report_model import BenchmarkStatus, ComparisonResult, Readiness, Severity
from providers.base import AppiumDeviceProvider, ProviderNotReadyError
from telemetry.crash_monitor import AppCrashError
from telemetry.parsers.app_log import AppLogAnalyzer
from telemetry.startup_collector import StartupCollector
from utils.logger import configure_logging, get_logger, release_log_file
from utils.net import use_system_trust_store

_log = get_logger("pytest")

PERF_MARKERS: dict[str, TestType] = {
    "startup": TestType.STARTUP,
    "endurance": TestType.ENDURANCE,
    "journey": TestType.JOURNEY,
    "benchmark": TestType.BENCHMARK,
    "perf": TestType.GENERIC,
}
_TELEMETRY_DEFAULT = {TestType.STARTUP: False}


@dataclass
class PerfState:
    config: FrameworkConfig
    run_id: str
    run_dir: Path
    event_bus: EventBus
    sessions: SessionManager
    evaluator: ExecutionEvaluator
    started_at: datetime
    executions: list[TestExecution] = field(default_factory=list)
    comparisons: list[ComparisonResult] = field(default_factory=list)
    report_path: Path | None = None
    run: RunSummary | None = None

    def finalize(self, execution: TestExecution) -> TestExecution:
        self.evaluator.evaluate(execution)
        execution.save(
            self.run_dir / "raw" / f"{safe_name(execution.test_id + (execution.label or ''))}.json"
        )
        self.executions.append(execution)
        return execution


STATE_KEY = pytest.StashKey[PerfState]()
SESSION_KEY = pytest.StashKey[PerfSession | None]()
REPORTS_KEY = pytest.StashKey[dict[str, pytest.TestReport]]()


# ---------------------------------------------------------------- options
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mobile-perf", "Mobile performance framework")
    group.addoption("--provider", help="Device provider: simulated | local | testmu | <plugin>")
    group.addoption("--device", help="Device profile key from config/devices.yaml")
    group.addoption("--platform", choices=["android", "ios"], help="Pick the default device for a platform")
    group.addoption("--app-version", help="App version shown in reports")
    group.addoption("--app-build", help="App build number shown in reports")
    group.addoption("--iterations", type=int, help="Override startup / endurance iterations")
    group.addoption("--perf-baseline", help="Baseline metrics.json for build comparison")
    group.addoption("--perf-report-dir", help="Report output directory (default: reports)")
    group.addoption("--perf-config-dir", help="Directory containing framework/devices/app YAML")
    group.addoption("--perf-label", help="Run label (e.g. CI pipeline / branch)")
    group.addoption("--sampling-interval", type=float, help="Telemetry sampling interval in seconds")
    group.addoption("--no-telemetry", action="store_true", default=False, help="Disable background telemetry")


def pytest_configure(config: pytest.Config) -> None:
    use_system_trust_store()
    config.addinivalue_line("markers", "perf: performance test with device session and telemetry")
    config.addinivalue_line("markers", "startup: startup benchmark")
    config.addinivalue_line("markers", "journey: user journey performance test")
    config.addinivalue_line("markers", "endurance(iterations): endurance test")
    config.addinivalue_line("markers", "benchmark: build comparison benchmark")
    config.addinivalue_line(
        "markers",
        "provider_options(**per_provider): per-test provider option overrides, e.g. "
        "provider_options(testmu={'lt_options': {'network': False}})",
    )


def _overrides(config: pytest.Config) -> dict[str, Any]:
    opt = config.getoption
    overrides: dict[str, Any] = {}

    def put(path: str, value: Any) -> None:
        if value in (None, ""):
            return
        node = overrides
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value

    put("provider.name", opt("--provider"))
    put("provider.device", opt("--device"))
    put("app.version", opt("--app-version"))
    put("app.build_number", opt("--app-build"))
    put("comparison.baseline", opt("--perf-baseline"))
    put("framework.report_dir", opt("--perf-report-dir"))
    put("framework.run_label", opt("--perf-label"))
    put("telemetry.sampling_interval_sec", opt("--sampling-interval"))
    if opt("--no-telemetry"):
        put("telemetry.enabled", False)
    if (iterations := opt("--iterations")) is not None:
        put("startup.iterations", iterations)
        put("endurance.iterations", iterations)
    return overrides


# ------------------------------------------------------------------ hooks
def pytest_sessionstart(session: pytest.Session) -> None:
    pconfig = session.config
    config_dir = pconfig.getoption("--perf-config-dir")
    try:
        cfg = load_config(Path(config_dir) if config_dir else None, _overrides(pconfig))
    except ConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d-%H%M%S}-{safe_name(cfg.framework.run_label, 40)}"
    run_dir = cfg.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(pconfig.getini("log_level") or "INFO", run_dir / "framework.log", console=False)
    bus = EventBus()
    state = PerfState(
        config=cfg,
        run_id=run_id,
        run_dir=run_dir,
        event_bus=bus,
        sessions=SessionManager(cfg, run_dir, event_bus=bus),
        evaluator=ExecutionEvaluator(cfg),
        started_at=started,
    )
    pconfig.stash[STATE_KEY] = state
    bus.publish(EventType.SESSION_STARTED, run_id=run_id, provider=cfg.provider.name)
    _log.info("Run %s started (provider=%s)", run_id, cfg.provider.name)


def _state(config: pytest.Config) -> PerfState:
    return config.stash[STATE_KEY]


def _test_type(item: pytest.Item) -> TestType | None:
    for marker, test_type in PERF_MARKERS.items():
        if item.get_closest_marker(marker) is not None:
            return test_type
    return None


def _platform_option(config: pytest.Config) -> Platform | None:
    value = config.getoption("--platform")
    return Platform(value) if value else None


def _provider_options(item: pytest.Item) -> dict[str, dict[str, Any]]:
    """Merge ``provider_options`` markers; markers closer to the test win."""
    merged: dict[str, dict[str, Any]] = {}
    for marker in reversed(list(item.iter_markers("provider_options"))):
        merged = deep_merge(merged, marker.kwargs)
    return merged


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    item.stash[SESSION_KEY] = None
    test_type = _test_type(item)
    if test_type is None or test_type == TestType.BENCHMARK:
        return
    state = _state(item.config)
    try:
        session = state.sessions.open(
            item.nodeid,
            item.name,
            test_type,
            platform=_platform_option(item.config),
            provider_options=_provider_options(item),
        )
    except ProviderNotReadyError as exc:
        pytest.skip(f"[mobile-perf] {exc}")
    item.stash[SESSION_KEY] = session


def _telemetry_wanted(item: pytest.Item, test_type: TestType) -> bool:
    for name in PERF_MARKERS:
        marker = item.get_closest_marker(name)
        if marker is not None and "telemetry" in marker.kwargs:
            return bool(marker.kwargs["telemetry"])
    return _TELEMETRY_DEFAULT.get(test_type, True)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    session = item.stash.get(SESSION_KEY, None)
    state = _state(item.config)
    if session is not None and _telemetry_wanted(item, session.test_type):
        state.sessions.start_telemetry(session)
    try:
        result = yield
        monitor = session.crash_monitor if session is not None else None
        if monitor is not None:
            monitor.check("test end")
            if monitor.events and state.config.crash.fail_on_crash:
                raise AppCrashError(monitor.describe())
        return result
    finally:
        if session is not None:
            state.sessions.stop_telemetry(session)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[pytest.TestReport]:
    report: pytest.TestReport = yield
    item.stash.setdefault(REPORTS_KEY, {})[report.when] = report
    return report


def _status(item: pytest.Item) -> tuple[TestStatus, str | None]:
    reports = item.stash.get(REPORTS_KEY, {})
    setup, call = reports.get("setup"), reports.get("call")
    if setup is not None and setup.failed:
        return TestStatus.ERROR, setup.longreprtext[-4000:]
    if call is None:
        return TestStatus.SKIPPED, None
    if call.failed:
        return TestStatus.FAILED, call.longreprtext[-4000:]
    if call.skipped:
        return TestStatus.SKIPPED, None
    return TestStatus.PASSED, None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    session = item.stash.get(SESSION_KEY, None)
    if session is None:
        return
    item.stash[SESSION_KEY] = None
    state = _state(item.config)
    status, error = _status(item)
    execution = state.sessions.close(session, status, error)
    state.finalize(execution)


def build_run_summary(state: PerfState) -> RunSummary:
    cfg = state.config
    merged = aggregate_executions(state.executions)
    summary = summarize(merged, cfg.endurance.window_fraction)
    metrics = {k: v for k, v in summary.model_dump().items() if isinstance(v, int | float) or v is None}
    metrics.update(run_metrics(state.executions, summary))
    benchmarks = evaluate_benchmarks(cfg.benchmarks, metrics)
    first = next((e for e in state.executions if e.test_type != TestType.BENCHMARK), None) or (
        state.executions[0] if state.executions else None
    )
    run = RunSummary(
        run_id=state.run_id,
        label=cfg.framework.run_label,
        started_at=state.started_at,
        finished_at=datetime.now(UTC),
        provider=cfg.provider.name,
        app=AppInfo(
            name=cfg.app.name,
            version=cfg.app.version,
            build_number=cfg.app.build_number,
            identifier=first.app.identifier if first else None,
        ),
        device=first.device if first else None,
        executions=state.executions,
        score=ScoringEngine(cfg.scoring).score(summary),
        metrics=metrics,
        benchmarks=benchmarks,
        readiness=Readiness.from_results(benchmarks),
        comparisons=list(state.comparisons),
        environment={
            "python": host_platform.python_version(),
            "host": host_platform.node(),
            "os": f"{host_platform.system()} {host_platform.release()}",
            "ci": str(any(os.environ.get(v) for v in ("CI", "TF_BUILD", "JENKINS_URL"))).lower(),
        },
    )
    baseline_path = cfg.comparison.baseline
    if baseline_path is not None:
        path = baseline_path if baseline_path.is_absolute() else Path.cwd() / baseline_path
        if path.exists():
            run.comparisons.insert(0, compare_runs(RunSummary.load(path), run, cfg.comparison))
        else:
            _log.warning("Baseline %s not found; skipping run comparison", path)
    return run


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(STATE_KEY, None)
    if state is None:
        return
    if not state.executions:
        release_log_file(state.run_dir / "framework.log")
        shutil.rmtree(state.run_dir, ignore_errors=True)
        return
    from reporting.html_report import HtmlReportGenerator

    cfg = state.config
    run = build_run_summary(state)
    state.run = run
    trend = TrendStore(cfg.report_dir / cfg.reporting.history_file, cfg.reporting.history_size).append(
        trend_point(run)
    )
    generator = HtmlReportGenerator(cfg)
    state.report_path = generator.generate(run, trend)
    state.event_bus.publish(EventType.SESSION_FINISHED, run_id=run.run_id, score=run.score.overall)
    state.event_bus.publish(EventType.REPORT_GENERATED, path=str(state.report_path))
    if cfg.comparison.fail_on_regression and any(not c.passed for c in run.comparisons):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if cfg.crash.fail_on_crash and any(e.telemetry.crashes for e in run.executions):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if cfg.benchmarks.fail_on_breach and any(
        b.status == BenchmarkStatus.FAIL for e in run.executions for b in e.benchmarks
    ):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    state = config.stash.get(STATE_KEY, None)
    if state is None or state.run is None:
        return
    run = state.run
    tr = terminalreporter
    tr.section("mobile performance")
    score = f"{run.score.overall:.1f}" if run.score.overall is not None else "n/a"
    tr.write_line(f"Run: {run.run_id}  provider={run.provider}  executions={len(run.executions)}")
    tr.write_line(f"Overall performance score: {score} ({run.score.rating.value})")
    breaches = [b.title for b in run.benchmarks if b.status == BenchmarkStatus.FAIL]
    tr.write_line(
        f"Production readiness: {run.readiness.value}"
        + (f" (limits breached: {', '.join(breaches)})" if breaches else ""),
        red=bool(breaches),
    )
    issues = [i for e in run.executions for i in e.issues]
    if issues:
        counts = [(s, sum(1 for i in issues if i.severity == s)) for s in Severity]
        tr.write_line("Issues: " + ", ".join(f"{n} {s.value}" for s, n in counts if n))
    crashes = [(e.name, c) for e in run.executions for c in e.telemetry.crashes]
    tr.write_line(f"App crashes / ANRs: {len(crashes)}", red=bool(crashes))
    for name, crash in crashes:
        where = f" step={crash.step}" if crash.step else ""
        tr.write_line(f"  {name}: {crash.crash_type.value}{where} - {crash.exception}", red=True)
    for comparison in run.comparisons:
        verdict = "PASS" if comparison.passed else "REGRESSION"
        tr.write_line(f"Comparison {comparison.baseline_label} -> {comparison.candidate_label}: {verdict}")
    if state.report_path:
        tr.write_line(f"HTML report: {state.report_path}")
        tr.write_line(f"All runs: {state.config.report_dir / 'index.html'}")


# --------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def perf_state(pytestconfig: pytest.Config) -> PerfState:
    return _state(pytestconfig)


@pytest.fixture(scope="session")
def perf_config(perf_state: PerfState) -> FrameworkConfig:
    return perf_state.config


@pytest.fixture
def perf_session(request: pytest.FixtureRequest) -> PerfSession:
    session = request.node.stash.get(SESSION_KEY, None)
    if session is None:
        pytest.fail("No device session: mark the test with @pytest.mark.perf / startup / journey / endurance")
    return session


@pytest.fixture
def provider(perf_session: PerfSession) -> Any:
    return perf_session.provider


@pytest.fixture
def driver(perf_session: PerfSession) -> Any:
    return perf_session.driver


@pytest.fixture
def telemetry(perf_session: PerfSession) -> Any:
    provider = perf_session.provider
    if not isinstance(provider, AppiumDeviceProvider):
        pytest.skip("Provider does not expose a TelemetryManager")
    return provider.telemetry


@pytest.fixture
def perf_recorder(perf_session: PerfSession, perf_config: FrameworkConfig) -> PerfRecorder:
    attribution = perf_config.attribution
    trace = attribution.enabled and attribution.trace_driver_commands
    app_log = attribution.enabled and attribution.app_log_signals
    return PerfRecorder(
        perf_session.provider,
        perf_session.crash_monitor,
        check_after_each_step=perf_config.crash.check_after_each_step,
        screenshots=perf_session.screenshots,
        tracer=DriverTracer() if trace else None,
        app_log=AppLogAnalyzer(perf_session.provider.app_id or "", attribution.app_packages)
        if app_log
        else None,
    )


def make_startup_profiler(session: PerfSession) -> StartupCollector:
    provider = session.provider
    return StartupCollector(
        provider.get_launch_strategy(),
        recorder=provider.record,
        settle_time_sec=provider.launch_settle_sec,
        stamp=provider.stamp,
    )


@pytest.fixture
def startup_profiler(perf_session: PerfSession) -> StartupCollector:
    return make_startup_profiler(perf_session)


@pytest.fixture
def pages(perf_session: PerfSession, perf_config: FrameworkConfig) -> Any:
    from pages import Pages

    return Pages(perf_session.driver, perf_config.locators, perf_session.platform)


def _marker_iterations(request: pytest.FixtureRequest, marker: str) -> int | None:
    found = request.node.get_closest_marker(marker)
    if found is None:
        return None
    value = found.kwargs.get("iterations", found.args[0] if found.args else None)
    return int(value) if value is not None else None


@pytest.fixture
def startup_iterations(request: pytest.FixtureRequest, perf_config: FrameworkConfig) -> int:
    if request.config.getoption("--iterations") is not None:
        return perf_config.startup.iterations
    return _marker_iterations(request, "startup") or perf_config.startup.iterations


@pytest.fixture
def endurance_iterations(request: pytest.FixtureRequest, perf_config: FrameworkConfig) -> int:
    if request.config.getoption("--iterations") is not None:
        return perf_config.endurance.iterations
    return _marker_iterations(request, "endurance") or perf_config.endurance.iterations


class BuildSessionFactory:
    """Opens one session per build for Build A vs Build B comparisons."""

    def __init__(self, state: PerfState, item: pytest.Item) -> None:
        self.state = state
        self.item = item
        self.executions: dict[str, TestExecution] = {}

    def app_for(self, build_key: str) -> tuple[str, str]:
        builds = self.state.config.app.builds
        if build_key not in builds:
            pytest.skip(f"[mobile-perf] Build '{build_key}' is not defined in config/app.yaml")
        spec = builds[build_key]
        if spec.app:
            return spec.label, spec.app
        if self.state.config.provider.name in ("simulated", "sim", "mock"):
            return spec.label, f"simulated://{spec.label}"
        pytest.skip(f"[mobile-perf] Set BUILD_{build_key.upper()}_APP to compare builds")

    @contextmanager
    def open_build(self, build_key: str, *, telemetry: bool = True) -> Iterator[PerfSession]:
        label, app = self.app_for(build_key)
        sessions = self.state.sessions
        try:
            session = sessions.open(
                self.item.nodeid,
                self.item.name,
                TestType.BENCHMARK,
                platform=_platform_option(self.item.config),
                app_override=app,
                label=label,
            )
        except ProviderNotReadyError as exc:
            pytest.skip(f"[mobile-perf] {exc}")
        if telemetry:
            sessions.start_telemetry(session)
        status, error = TestStatus.PASSED, None
        try:
            yield session
        except BaseException as exc:
            status, error = TestStatus.FAILED, repr(exc)
            raise
        finally:
            execution = self.state.finalize(sessions.close(session, status, error))
            self.executions[build_key] = execution

    def startup_profiler(self, session: PerfSession) -> StartupCollector:
        return make_startup_profiler(session)

    def compare(self, baseline_key: str = "a", candidate_key: str = "b") -> ComparisonResult:
        result = compare_executions(
            self.executions[baseline_key], self.executions[candidate_key], self.state.config.comparison
        )
        self.executions[candidate_key].comparison = result
        self.state.comparisons.append(result)
        return result


@pytest.fixture
def session_factory(request: pytest.FixtureRequest, perf_state: PerfState) -> BuildSessionFactory:
    return BuildSessionFactory(perf_state, request.node)
