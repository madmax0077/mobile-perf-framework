"""Names the code that made each step slow: backend operation, app class / method or test method.

Evidence per step, from three layers:

* **Backend API** - network requests overlapping the step window, named by backend
  operation (``ap.mobile.user.login``) with server vs connection time and trace id.
* **App code** - screen class, StrictMode stacks, slow main-thread messages and
  skipped frames from the device log (``telemetry.parsers.app_log``).
* **Test code** - page-object methods and driver commands (``core.tracing``).

The primary culprit is picked in that order of precedence: app code that blocked
the main thread for a large share of the step, then backend calls when they kept
the app busy for most of the step, then the test method that took the most time.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import numpy as np

from models.report_model import Culprit, CulpritLayer, StepBreakdown
from models.telemetry_model import AppSignalType, NetworkRequest, StepTiming
from utils.formatting import fmt_value

APP_SHARE = 0.25
NETWORK_SHARE = 0.4
MAX_API_CULPRITS = 15
_BLOCKING = {AppSignalType.SKIPPED_FRAMES, AppSignalType.SLOW_MAIN_THREAD, AppSignalType.SLOW_FRAME}

BACKGROUND_HOSTS = (
    "app-measurement.com",
    "google-analytics.com",
    "doubleclick.net",
    "crashlytics.com",
    "firebaselogging-pa.googleapis.com",
    "firebaseinstallations.googleapis.com",
    "update.googleapis.com",
    "play.googleapis.com",
    "android.clients.google.com",
    "gvt1.com",
    "gvt2.com",
    "gvt3.com",
    "sentry.io",
    "bugsnag.com",
    "datadoghq.com",
    "datadoghq.eu",
    "coralogix.com",
    "nr-data.net",
    "newrelic.com",
    "appsflyer.com",
    "adjust.com",
    "mixpanel.com",
    "amplitude.com",
)
BACKGROUND_WORDS = frozenset(
    {"rum", "log", "logs", "logging", "analytics", "telemetry", "tracking", "crashlytics", "beacon"}
)


def is_background(request: NetworkRequest, extra_hosts: Iterable[str] = ()) -> bool:
    """Analytics, logging, crash reporting and OS / browser updates: traffic that runs
    alongside the app but does not hold up the screen."""
    host = (urlsplit(request.url).hostname or "").lower()
    hosts = BACKGROUND_HOSTS + tuple(h.lower().lstrip(".") for h in extra_hosts if h)
    if any(host == h or host.endswith("." + h) for h in hosts):
        return True
    words = set(re.split(r"[^a-z0-9]+", f"{host} {request.endpoint}".lower()))
    return bool(words & BACKGROUND_WORDS)


def mark_background(requests: Iterable[NetworkRequest], extra_hosts: Iterable[str] = ()) -> None:
    extra = tuple(extra_hosts)
    for r in requests:
        r.background = is_background(r, extra)


def _ms(value: float | None) -> str:
    return fmt_value(value, "ms")


def _union_ms(intervals: list[tuple[float, float]]) -> float:
    total, current_start, current_end = 0.0, None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None and current_start is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None and current_start is not None:
        total += current_end - current_start
    return total


def connection_setup_ms(request: NetworkRequest) -> float | None:
    """DNS + TCP connect + TLS (HAR's ``connect`` should include ``ssl``; some proxies report them apart)."""
    p = request.phases
    if not any(k in p for k in ("dns", "connect", "ssl")):
        return None
    connect, ssl = p.get("connect", 0.0), p.get("ssl", 0.0)
    return p.get("dns", 0.0) + (connect if ssl <= connect else connect + ssl)


def describe_request(request: NetworkRequest) -> str:
    parts = [f"HTTP {request.status or 'no response'}"]
    if request.server_ms is not None:
        parts.append(f"server {_ms(request.server_ms)}")
    setup = connection_setup_ms(request)
    if setup:
        parts.append(f"connection setup {_ms(setup)}")
    if "wait" in request.phases:
        parts.append(f"waiting for response {_ms(request.phases['wait'])}")
    return ", ".join(parts)


def _api_culprits(step: StepTiming, requests: list[NetworkRequest]) -> tuple[list[Culprit], float]:
    start, end = step.started_at, step.timestamp
    total = max(step.duration_ms, 1.0)
    culprits, intervals = [], []
    for r in requests:
        if not (r.started_at < end and r.ended_at > start):
            continue
        offset = (r.started_at - start).total_seconds() * 1000.0
        clipped = (max(offset, 0.0), min(offset + r.duration_ms, step.duration_ms))
        if not r.background:
            intervals.append(clipped)
        culprits.append(
            Culprit(
                layer=CulpritLayer.API,
                name=r.endpoint,
                kind="background_call" if r.background else "api_call",
                detail=("Background (analytics / logging / OS); " if r.background else "")
                + describe_request(r),
                duration_ms=round(r.duration_ms, 1),
                share_pct=round((clipped[1] - clipped[0]) / total * 100.0, 1),
                offset_ms=round(offset, 1),
                location=r.url.split("?", 1)[0],
                trace_id=r.trace_id,
            )
        )
    culprits.sort(key=lambda c: (c.kind == "api_call", c.duration_ms or 0.0), reverse=True)
    return culprits[:MAX_API_CULPRITS], round(_union_ms(intervals), 1)


def _app_culprits(step: StepTiming) -> list[Culprit]:
    total = max(step.duration_ms, 1.0)
    culprits = []
    for s in step.app_signals:
        repeats = f" ({s.count} times)" if s.count > 1 else ""
        culprits.append(
            Culprit(
                layer=CulpritLayer.APP,
                name=s.name,
                kind=s.type.value,
                detail=s.detail + repeats,
                duration_ms=s.duration_ms,
                share_pct=round(min(s.duration_ms, total) / total * 100.0, 1) if s.duration_ms else None,
                location=next((f for f in s.frames if f.startswith(s.name + "(")), None),
            )
        )
    return culprits


def _blocking(step: StepTiming, app: list[Culprit]) -> list[Culprit]:
    """App culprits (same order as ``step.app_signals``) that held the main thread."""
    return [
        culprit
        for signal, culprit in zip(step.app_signals, app, strict=True)
        if culprit.duration_ms
        and (signal.type in _BLOCKING or (signal.type == AppSignalType.STRICT_MODE and signal.main_thread))
    ]


def _test_culprits(step: StepTiming) -> list[Culprit]:
    total = max(step.duration_ms, 1.0)
    culprits = []
    for c in step.calls:
        if c.command == "-":
            detail = "No driver commands; time spent in test code"
        else:
            detail = f"{c.count} x {c.command}" + (f" on {c.target}" if c.target else "")
            if c.failed and c.command.startswith("find"):
                detail += f" ({c.failed} not found yet - polling until it appears)"
            elif c.failed:
                detail += f" ({c.failed} failed)"
            detail += f"; {_ms(c.command_ms)} inside the driver"
        culprits.append(
            Culprit(
                layer=CulpritLayer.TEST,
                name=c.code,
                kind="driver_command",
                detail=detail,
                duration_ms=c.wall_ms,
                share_pct=round(min(c.wall_ms, total) / total * 100.0, 1),
                location=c.location,
            )
        )
    return culprits


def _screen_note(app: list[Culprit]) -> str:
    screens = [c for c in app if c.kind == AppSignalType.SCREEN.value]
    if not screens:
        return ""
    last = screens[-1]
    return f" Screen shown: {last.name} (+{_ms(last.duration_ms)})."


def _summarise(
    step: StepTiming, primary: Culprit | None, api: list[Culprit], busy_ms: float, app: list[Culprit]
) -> tuple[str, str]:
    total = _ms(step.duration_ms)
    if primary is None:
        return "Not enough data", "No driver, app-log or network evidence was captured for this step."
    cause = f"{primary.name} ({_ms(primary.duration_ms)})"
    if primary.layer == CulpritLayer.APP:
        where = (
            f" at {primary.location}" if primary.location and primary.location not in primary.detail else ""
        )
        sources = [
            s.name
            for s in step.app_signals
            if s.main_thread
            and s.type in (AppSignalType.STRICT_MODE, AppSignalType.SLOW_MAIN_THREAD)
            and s.name != primary.name
        ]
        also = f" Main-thread work logged in the same step: {', '.join(sources[:3])}." if sources else ""
        return cause, f"App code blocked the main thread: {primary.detail}{where}.{also}"
    if primary.layer == CulpritLayer.API:
        server = next(
            (part for part in primary.detail.split(", ") if part.startswith("server ")),
            None,
        )
        slowest = f"{primary.name} ({_ms(primary.duration_ms)}{', ' + server if server else ''})"
        background = sum(1 for c in api if c.kind == "background_call")
        ignored = f" {background} background analytics / logging call(s) not counted." if background else ""
        return cause, (
            f"{len(api) - background} backend call(s) overlapped this step and kept the app busy for "
            f"{_ms(busy_ms)} of {total}; the slowest was {slowest}.{ignored}{_screen_note(app)}"
        )
    method = primary.name.split(" > ")[0]
    call = next((c for c in step.calls if c.code == primary.name), None)
    if call is not None and call.failed and call.command.startswith("find"):
        text = (
            f"The test waited {_ms(call.wall_ms)} in {method} for {call.target or 'an element'} to appear "
            f"({call.count} attempts) - the app had not rendered it yet."
        )
    else:
        text = f"{_ms(primary.duration_ms)} of {total} went to {method}: {primary.detail}."
    return cause, text + _screen_note(app)


def breakdown(index: int, step: StepTiming, requests: list[NetworkRequest]) -> StepBreakdown:
    total = max(step.duration_ms, 1.0)
    api, busy_ms = _api_culprits(step, requests)
    app = _app_culprits(step)
    test = _test_culprits(step)
    blocking = _blocking(step, app)
    primary: Culprit | None = None
    if blocking and max(c.duration_ms or 0.0 for c in blocking) >= APP_SHARE * total:
        primary = max(blocking, key=lambda c: c.duration_ms or 0.0)
    elif api and busy_ms >= NETWORK_SHARE * total:
        primary = api[0]
    elif test:
        primary = test[0]
    elif api:
        primary = api[0]
    cause, summary = _summarise(step, primary, api, busy_ms, app)
    driver_ms = round(sum(c.command_ms for c in step.calls), 1) if step.calls else None
    return StepBreakdown(
        step_index=index,
        step=step.name,
        duration_ms=step.duration_ms,
        cause=cause,
        summary=summary,
        primary=primary,
        culprits=app + api + test,
        network_busy_ms=busy_ms if api else None,
        driver_ms=driver_ms,
    )


def step_breakdowns(steps: list[StepTiming], requests: list[NetworkRequest]) -> list[StepBreakdown]:
    ordered = sorted(requests, key=lambda r: r.timestamp)
    return [breakdown(i, step, ordered) for i, step in enumerate(steps, 1)]


@dataclass
class OperationStats:
    """Latency of one backend operation across an execution."""

    name: str
    count: int
    failed: int
    avg_ms: float
    p95_ms: float
    max_ms: float
    server_avg_ms: float | None
    setup_avg_ms: float | None
    total_ms: float
    trace_id: str | None
    background: bool = False


def operation_stats(requests: list[NetworkRequest]) -> list[OperationStats]:
    """Per-operation latency, slowest p95 first."""
    groups: dict[str, list[NetworkRequest]] = defaultdict(list)
    for r in requests:
        groups[r.endpoint].append(r)
    stats = []
    for name, items in groups.items():
        durations = np.asarray([r.duration_ms for r in items], dtype=float)
        server = [r.server_ms for r in items if r.server_ms is not None]
        setup = [s for r in items if (s := connection_setup_ms(r)) is not None]
        slowest = max(items, key=lambda r: r.duration_ms)
        stats.append(
            OperationStats(
                name=name,
                count=len(items),
                failed=sum(1 for r in items if r.failed),
                avg_ms=round(float(durations.mean()), 1),
                p95_ms=round(float(np.percentile(durations, 95)), 1),
                max_ms=round(float(durations.max()), 1),
                server_avg_ms=round(sum(server) / len(server), 1) if server else None,
                setup_avg_ms=round(sum(setup) / len(setup), 1) if setup else None,
                total_ms=round(float(durations.sum()), 1),
                trace_id=slowest.trace_id,
                background=all(r.background for r in items),
            )
        )
    return sorted(stats, key=lambda s: s.p95_ms, reverse=True)


def slowest_operations_text(requests: list[NetworkRequest], limit: int = 3) -> str | None:
    stats = [s for s in operation_stats(requests) if s.count and not s.background]
    if not stats:
        return None
    return ", ".join(
        f"{s.name} (max {_ms(s.max_ms)})" for s in sorted(stats, key=lambda s: -s.max_ms)[:limit]
    )
