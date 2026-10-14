"""Slow-step root cause: backend operations, app classes / methods and test methods."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.attribution import breakdown, mark_background, operation_stats, step_breakdowns
from core.issues import collect_issues
from core.recorder import PerfRecorder
from core.tracing import DriverTracer
from models.execution_model import AppInfo, DeviceInfo, Platform, TestExecution
from models.report_model import BenchmarkResult, BenchmarkStatus, CulpritLayer
from models.telemetry_model import (
    AppSignal,
    AppSignalType,
    DriverCall,
    NetworkRequest,
    StepTiming,
    TelemetryData,
)
from pages.login_page import LoginPage
from telemetry.parsers.app_log import AppLogAnalyzer, app_frame
from telemetry.parsers.har import parse_har

T0 = datetime(2026, 10, 6, 11, 26, 37, tzinfo=UTC)


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "startedDateTime": "2026-10-06T11:26:37.256000+00:00",
        "time": 603,
        "request": {"method": "POST", "url": "https://gw.example.com/mgw.htm", "headers": []},
        "response": {"status": 200, "headers": []},
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    ("request_part", "expected"),
    [
        (
            {
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded; charset=UTF-8",
                    "text": "operationType=ap.mobile.payments.queryPayMethod&requestData=%5B%5D&ts=1",
                }
            },
            "ap.mobile.payments.queryPayMethod",
        ),
        (
            {
                "postData": {
                    "mimeType": "application/json",
                    "text": '{"operationName": "HomeFeed", "query": "{}"}',
                }
            },
            "HomeFeed",
        ),
        (
            {
                "postData": {
                    "mimeType": "application/json",
                    "text": '{"jsonrpc": "2.0", "method": "user.get"}',
                }
            },
            "user.get",
        ),
        ({"headers": [{"name": "Operation-Type", "value": "alipay.user.login"}]}, "alipay.user.login"),
        ({"postData": {"mimeType": "application/json", "text": '{"method": "card", "amount": 5}'}}, None),
        (
            {"postData": {"mimeType": "application/x-www-form-urlencoded", "text": "operationType=a b c"}},
            None,
        ),
    ],
)
def test_backend_operation_is_read_from_gateway_requests(
    request_part: dict[str, Any], expected: str | None
) -> None:
    entry = _entry()
    entry["request"].update(request_part)
    [request] = parse_har([entry])
    assert request.operation == expected
    if expected is None:
        assert request.endpoint == "POST gw.example.com/mgw.htm"


def test_rest_endpoints_group_without_ids_in_the_path() -> None:
    url = (
        "https://cdn.example.com/u/216610000000027522031/avatar/"
        "2b96dcaa-7da7-4ffd-b5a4-1a777e0edde7/01a0d80287797be9a4e9dbcf3eb12d42.png?w=64"
    )
    request = NetworkRequest(url=url, method="GET", timestamp=T0)
    assert request.endpoint == "GET cdn.example.com/u/{id}/avatar/{id}/{id}.png"
    assert NetworkRequest(url="https://api.example.com/v1/2026/cart", method="GET").endpoint.endswith(
        "/2026/cart"
    )


def test_server_time_phases_and_trace_id_come_from_the_har_entry() -> None:
    entry = _entry(
        timings={"dns": -1, "connect": 151, "ssl": 163, "send": 1, "wait": 286, "receive": 1},
    )
    entry["response"]["headers"] = [
        {"name": "x-envoy-upstream-service-time", "value": "166"},
        {"name": "mgw-traceid", "value": "0a650bc6179128599929143611336576"},
    ]
    [request] = parse_har({"log": {"entries": [entry]}})
    assert request.server_ms == 166 and request.trace_id == "0a650bc6179128599929143611336576"
    assert request.phases == {"connect": 151, "ssl": 163, "send": 1, "wait": 286, "receive": 1}

    entry["response"]["headers"] = [
        {"name": "Server-Timing", "value": "db;dur=12.5, total;dur=88"},
        {"name": "traceparent", "value": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
    ]
    [request] = parse_har([entry])
    assert request.server_ms == 88 and request.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


LOGCAT = """\
10-06 11:26:01.000  1596  1764 I ActivityManager: Start proc 20031:com.rebellapp.consumer.uat/u0a201 for top-activity
10-06 11:26:04.200  1596  1596 W Looper  : Slow dispatch took 119ms main h=android.os.Handler c=null m=1
10-06 11:26:09.847  1596  1748 I ActivityTaskManager: Displayed com.rebellapp.consumer.uat/com.adw.home.HomeV2Activity for user 0: +1s388ms
10-06 11:26:10.100 20031 20031 D StrictMode: StrictMode policy violation; ~duration=240 ms: android.os.strictmode.DiskReadViolation
10-06 11:26:10.100 20031 20031 D StrictMode: 	at android.os.StrictMode$AndroidBlockGuardPolicy.onReadFromDisk(StrictMode.java:1666)
10-06 11:26:10.100 20031 20031 D StrictMode: 	at p8.b.b(Undispatched.kt:18)
10-06 11:26:10.100 20031 20031 D StrictMode: 	at com.adw.home.data.HomeCache.load(HomeCache.kt:57)
10-06 11:26:10.100 20031 20031 D StrictMode: 	at com.adw.home.HomeV2Activity.onCreate(HomeV2Activity.kt:88)
10-06 11:26:10.200 20031 20062 D StrictMode: StrictMode policy violation: android.os.strictmode.NetworkViolation
10-06 11:26:10.200 20031 20062 D StrictMode: 	at java.net.Socket.connect(Socket.java:646)
10-06 11:26:10.200 20031 20062 D StrictMode: 	at com.google.firebase.sessions.settings.RemoteSettingsFetcher.doConfigFetch(RemoteSettingsFetcher.kt:12)
10-06 11:26:10.300 20031 20031 I Choreographer: Skipped 60 frames!  The application may be doing too much work on its main thread.
10-06 11:26:10.400 20031 20031 W Looper  : Slow dispatch took 412ms main h=android.app.ActivityThread$H c=com.adw.home.HomeV2Activity$1 m=159
10-06 11:26:10.500  4242  4242 I Choreographer: Skipped 90 frames!  The application may be doing too much work on its main thread.
"""


def test_app_log_names_screen_classes_and_app_methods() -> None:
    signals = AppLogAnalyzer("com.rebellapp.consumer.uat", ["com.adw"]).feed(LOGCAT)
    by_type: dict[AppSignalType, list[AppSignal]] = {}
    for signal in signals:
        by_type.setdefault(signal.type, []).append(signal)

    [screen] = by_type[AppSignalType.SCREEN]
    assert screen.name == "com.adw.home.HomeV2Activity" and screen.duration_ms == 1388
    [skipped] = by_type[AppSignalType.SKIPPED_FRAMES]  # pid 4242 is another app
    assert skipped.duration_ms == pytest.approx(1000, abs=1)
    assert skipped.name == "com.adw.home.HomeV2Activity", "frames are blamed on the screen in the foreground"
    [looper] = by_type[AppSignalType.SLOW_MAIN_THREAD]  # system_server's slow dispatch is ignored
    assert looper.name == "com.adw.home.HomeV2Activity$1" and looper.duration_ms == 412
    strict = {s.name: s for s in by_type[AppSignalType.STRICT_MODE]}
    disk = strict["com.adw.home.data.HomeCache.load"]
    assert disk.main_thread and disk.duration_ms == 240 and "DiskReadViolation" in disk.detail
    network = strict["com.google.firebase.sessions.settings.RemoteSettingsFetcher.doConfigFetch"]
    assert network.main_thread is False


def test_app_frame_skips_platform_and_obfuscated_frames() -> None:
    frames = ["java.io.File.read(File.java:1)", "a.b.c(Unknown Source:1)", "com.shop.Cart.total(Cart.kt:9)"]
    assert app_frame(frames) == "com.shop.Cart.total(Cart.kt:9)"
    assert app_frame(frames, ["com.none"]) == "com.shop.Cart.total(Cart.kt:9)"


class FakeDriver:
    def __init__(self, misses: int) -> None:
        self.misses = misses

    def execute(self, driver_command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        time.sleep(0.005)
        if driver_command == "findElement" and self.misses:
            self.misses -= 1
            raise LookupError("no such element")
        return {"value": None}


class FakePage:
    page_name = "home"

    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def find(self, element: str) -> Any:
        return self.driver.execute("findElement", {"using": "id", "value": f"app:id/{element}"})

    def wait_loaded(self) -> None:
        for _ in range(5):
            try:
                self.find("profile_icon")
                return
            except LookupError:
                time.sleep(0.02)

    def open_profile(self) -> None:
        self.find("profile_icon")
        self.driver.execute("clickElement", {"id": "1"})


def test_tracer_attributes_driver_commands_to_page_object_methods() -> None:
    driver = FakeDriver(misses=2)
    tracer = DriverTracer()
    assert tracer.install(driver) and not tracer.install(driver)
    page = FakePage(driver)

    page.wait_loaded()  # no step open -> not recorded
    tracer.start()
    driver.misses = 2
    page.wait_loaded()
    background = threading.Thread(target=lambda: driver.execute("getPerformanceData"))
    background.start()
    background.join()
    page.open_profile()
    calls = {(c.code, c.command): c for c in tracer.stop()}

    polling = calls[("FakePage.wait_loaded > FakePage.find", "findElement")]
    assert polling.count == 3 and polling.failed == 2 and polling.target == "home.profile_icon"
    assert polling.wall_ms > polling.command_ms, "polling sleeps count towards the method's time"
    assert polling.location.startswith("tests/unit/test_attribution.py:")
    assert calls[("FakePage.open_profile", "clickElement")].count == 1
    assert not any(command == "getPerformanceData" for _, command in calls), "other threads are ignored"


def test_first_visible_polling_is_named_by_page_element() -> None:
    class Element:
        def __init__(self, driver: Any) -> None:
            self.driver = driver

        def is_displayed(self) -> bool:
            return bool(self.driver.execute("isElementDisplayed", {"id": "1"}))

    class Driver(FakeDriver):
        def find_elements(self, by: str, value: str) -> list[Element]:
            self.execute("findElements", {"using": by, "value": value})
            return [Element(self)] if value.endswith("otp") else []

    locators = {
        "login": {"otp_input": {"android": ["id", "otp"]}, "biometrics_later": {"android": ["id", "bio"]}}
    }
    driver = Driver(misses=0)
    tracer = DriverTracer()
    tracer.install(driver)
    tracer.start()
    assert (
        LoginPage(driver, locators, Platform.ANDROID).first_visible("biometrics_later", "otp_input")
        == "otp_input"
    )
    targets = {c.target for c in tracer.stop()}
    assert {"login.biometrics_later", "login.otp_input"} <= targets


def test_recorder_stores_driver_calls_on_the_step() -> None:
    class Provider:
        driver = FakeDriver(misses=1)

        def stamp(self, source: str) -> dict[str, Any]:
            return {"timestamp": datetime.now(UTC), "source": source}

        def record(self, sample: Any) -> None:
            pass

    provider = Provider()
    recorder = PerfRecorder(provider, tracer=DriverTracer())  # type: ignore[arg-type]
    with recorder.step("home"):
        FakePage(provider.driver).wait_loaded()
    [call] = recorder.steps[0].calls
    assert call.code == "FakePage.wait_loaded > FakePage.find" and call.failed == 1


def _step(name: str, seconds: float, **fields: Any) -> StepTiming:
    return StepTiming(
        name=name, duration_ms=seconds * 1000, timestamp=T0 + timedelta(seconds=seconds), **fields
    )


def _request(operation: str, start_s: float, ms: float, server: float | None = None) -> NetworkRequest:
    return NetworkRequest(
        url="https://gw.example.com/mgw.htm",
        method="POST",
        status=200,
        operation=operation,
        duration_ms=ms,
        server_ms=server,
        timestamp=T0 + timedelta(seconds=start_s),
    )


WAIT_CALL = DriverCall(
    code="HomePage.wait_loaded > BasePage.wait_visible > BasePage.find",
    location="tests/smoke/test_login.py:50",
    command="findElement",
    target="home.profile_icon",
    count=12,
    failed=11,
    command_ms=2900.0,
    wall_ms=4100.0,
)


def test_backend_calls_that_fill_the_step_are_the_primary_cause() -> None:
    step = _step("home_after_login", 4.2, calls=[WAIT_CALL])
    requests = [_request(f"ap.mobile.op{i}", 0.07 + i * 0.35, 540) for i in range(9)]
    requests.append(_request("ap.mobile.payments.queryPayMethod", 2.0, 603, server=166))
    requests.append(_request("ap.mobile.before", -2.0, 300))  # finished before the step

    result = breakdown(10, step, requests)
    assert result.primary is not None and result.primary.layer == CulpritLayer.API
    assert result.cause == "ap.mobile.payments.queryPayMethod (603 ms)"
    assert "10 backend call(s)" in result.summary and "server 166 ms" in result.summary
    assert len(result.layer(CulpritLayer.API)) == 10 and result.network_busy_ms == pytest.approx(3340, abs=1)
    assert result.layer(CulpritLayer.TEST)[0].name.startswith("HomePage.wait_loaded")


def test_background_traffic_is_listed_but_never_the_cause() -> None:
    rum = NetworkRequest(
        url="https://ingress.eu2.rum-ingress-coralogix.com/browser/v1beta/logs",
        method="POST",
        duration_ms=1500,
        timestamp=T0 + timedelta(seconds=0.1),
    )
    tracking = _request("ap.mobile.event.tracking.unlogin", 0.1, 1400)
    custom = NetworkRequest(url="https://events.acme.io/v2/batch", duration_ms=1400, timestamp=T0)
    login = _request("ap.mobile.user.login", 0.2, 300)
    mark_background([rum, tracking, custom, login], ["acme.io"])
    assert (rum.background, tracking.background, custom.background, login.background) == (
        True,
        True,
        True,
        False,
    )

    open_profile = DriverCall(
        code="HomePage.open_profile > BasePage.tap",
        location="tests/smoke/test_login.py:34",
        command="clickElement",
        target="home.profile_icon",
        command_ms=1300,
        wall_ms=1300,
    )
    result = breakdown(2, _step("open_profile", 1.65, calls=[open_profile]), [rum, tracking, custom, login])
    assert result.primary is not None and result.primary.layer == CulpritLayer.TEST
    assert result.network_busy_ms == 300
    api = result.layer(CulpritLayer.API)
    assert api[0].name == "ap.mobile.user.login" and {c.kind for c in api[1:]} == {"background_call"}
    assert [s.name for s in operation_stats([rum, login]) if s.background] == [rum.endpoint]


def test_main_thread_blocking_in_app_code_wins_over_network() -> None:
    signal = AppSignal(
        type=AppSignalType.SLOW_MAIN_THREAD,
        name="com.adw.home.HomeV2Activity$1",
        duration_ms=1800,
        main_thread=True,
    )
    step = _step("open_home", 3.0, app_signals=[signal])
    result = breakdown(1, step, [_request("ap.mobile.op", 0.1, 2500)])
    assert result.primary is not None and result.primary.layer == CulpritLayer.APP
    assert result.cause.startswith("com.adw.home.HomeV2Activity$1")


def test_skipped_frames_point_at_main_thread_methods_from_the_same_step() -> None:
    signals = [
        AppSignal(type=AppSignalType.SKIPPED_FRAMES, name="com.adw.home.HomeV2Activity", duration_ms=1500),
        AppSignal(
            type=AppSignalType.STRICT_MODE,
            name="com.adw.home.data.HomeCache.load",
            duration_ms=240,
            main_thread=True,
        ),
    ]
    result = breakdown(1, _step("open_home", 3.0, app_signals=signals), [])
    assert result.cause == "com.adw.home.HomeV2Activity (1.50 s)"
    assert "same step: com.adw.home.data.HomeCache.load" in result.summary


def test_without_network_evidence_the_waiting_test_method_is_named() -> None:
    screen = AppSignal(type=AppSignalType.SCREEN, name="com.adw.home.HomeV2Activity", duration_ms=388)
    result = breakdown(1, _step("home", 4.2, calls=[WAIT_CALL], app_signals=[screen]), [])
    assert result.primary is not None and result.primary.layer == CulpritLayer.TEST
    assert "waited 4.10 s in HomePage.wait_loaded for home.profile_icon" in result.summary
    assert "com.adw.home.HomeV2Activity" in result.summary


def test_issues_carry_the_cause() -> None:
    steps = [_step("home_after_login", 4.2, calls=[WAIT_CALL])]
    requests = [_request("ap.mobile.user.login", 0.1, 1214), _request("ap.mobile.cdp.querySpace", 1.5, 2500)]
    execution = TestExecution(
        test_id="t",
        name="login",
        started_at=T0,
        device=DeviceInfo(name="Pixel", platform=Platform.ANDROID, os_version="14", provider="testmu"),
        app=AppInfo(name="app", version="1", build_number="1"),
        telemetry=TelemetryData(steps=steps, requests=requests),
        benchmarks=[
            BenchmarkResult(
                id="api_latency",
                title="API response time (p95)",
                metric="p95_response_ms",
                unit="ms",
                value=2400,
                good=1000,
                limit=2000,
                higher_is_better=False,
                status=BenchmarkStatus.FAIL,
            )
        ],
    )
    execution.breakdowns = step_breakdowns(steps, requests)
    issues = {i.category: i for i in collect_issues(execution, slow_step_limit_ms=3000)}
    assert issues["slow_step"].cause == "ap.mobile.cdp.querySpace (2.50 s)"
    assert "backend call(s)" in issues["slow_step"].detail
    benchmark = issues["benchmark"]
    assert benchmark.target == "operations" and benchmark.cause.startswith(
        "ap.mobile.cdp.querySpace (max 2.50 s)"
    )


def test_operation_stats_group_by_operation_slowest_p95_first() -> None:
    requests = [
        _request("fast", 0, 100),
        _request("slow", 0, 900, server=200),
        _request("slow", 1, 700, server=100),
    ]
    stats = operation_stats(requests)
    assert [s.name for s in stats] == ["slow", "fast"]
    assert stats[0].count == 2 and stats[0].server_avg_ms == 150 and stats[0].max_ms == 900
