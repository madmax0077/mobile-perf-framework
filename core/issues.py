"""Turns an evaluated execution into a ranked list of issues for the drilldown report.

Issue ids (``issue-<n>``) and targets are HTML anchors on the execution page:
``step-<n>`` (1-based step index), ``crash-<n>``, ``bench-<rule id>``, ``findings``,
``network``, ``operations`` and ``error``. ``cause`` names the method / class /
backend operation behind the issue when ``core.attribution`` found one.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from core.attribution import slowest_operations_text
from models.execution_model import TestExecution, TestStatus
from models.report_model import BenchmarkResult, BenchmarkStatus, Issue, Severity, StepBreakdown
from models.telemetry_model import CrashEvent, CrashType, NetworkRequest, StepTiming
from utils.formatting import fmt_value

__all__ = ["CRASH_LABELS", "FINDING_LABELS", "collect_issues", "fmt_value", "step_index_at"]

CRASH_LABELS = {
    CrashType.JAVA_CRASH: "Java crash",
    CrashType.NATIVE_CRASH: "Native crash",
    CrashType.ANR: "ANR",
    CrashType.APP_DIED: "App stopped",
}
FINDING_LABELS = {
    "memory_leak": "Memory leak",
    "cpu_growth": "CPU growth",
    "response_degradation": "Response-time degradation",
}
# Represented by crash, step and network issues instead of a generic benchmark issue
_COVERED_METRICS = {"crash_free_sessions_pct", "anr_free_sessions_pct", "failed_request_pct"}


def _first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def step_index_at(steps: list[StepTiming], when: datetime | None, name: str | None) -> int | None:
    """1-based index of the step running at ``when`` (falling back to the first step called ``name``)."""
    if when is not None:
        for i, step in enumerate(steps, 1):
            if step.started_at <= when <= step.timestamp:
                return i
    if name:
        for i, step in enumerate(steps, 1):
            if step.name == name:
                return i
    return None


def _crash_issues(crashes: list[CrashEvent], steps: list[StepTiming]) -> list[Issue]:
    issues = []
    for i, crash in enumerate(crashes, 1):
        index = step_index_at(steps, crash.timestamp, crash.step)
        where = f" during step '{crash.step}'" if crash.step else ""
        issues.append(
            Issue(
                id="",
                severity=Severity.CRITICAL,
                category="anr" if crash.crash_type == CrashType.ANR else "crash",
                title=f"{CRASH_LABELS[crash.crash_type]}: {crash.exception or 'no exception recorded'}",
                detail=f"Detected{where} (source: {crash.source}).",
                step=crash.step,
                step_index=index,
                target=f"crash-{i}",
                timestamp=crash.timestamp,
            )
        )
    return issues


def _step_issues(
    steps: list[StepTiming], slow_limit_ms: float | None, breakdowns: dict[int, StepBreakdown]
) -> list[Issue]:
    issues = []
    slow: dict[str, list[tuple[int, StepTiming]]] = defaultdict(list)
    for i, step in enumerate(steps, 1):
        if not step.success:
            found = breakdowns.get(i)
            issues.append(
                Issue(
                    id="",
                    severity=Severity.CRITICAL,
                    category="step_failure",
                    title=f"Step '{step.name}' failed",
                    detail=step.error or "",
                    step=step.name,
                    step_index=i,
                    target=f"step-{i}",
                    timestamp=step.timestamp,
                    cause=found.cause if found and found.primary else None,
                )
            )
        elif slow_limit_ms and step.measured and step.duration_ms >= slow_limit_ms:
            slow[step.name].append((i, step))
    for name, hits in slow.items():
        index, worst = max(hits, key=lambda hit: hit[1].duration_ms)
        repeats = f" ({len(hits)} slow runs)" if len(hits) > 1 else ""
        detail = f"Production limit for a user step is {fmt_value(slow_limit_ms, 'ms')}."
        found = breakdowns.get(index)
        if found and found.primary:
            detail += f" {found.summary}"
        issues.append(
            Issue(
                id="",
                severity=Severity.MAJOR,
                category="slow_step",
                title=f"Step '{name}' took {fmt_value(worst.duration_ms, 'ms')}{repeats}",
                detail=detail,
                step=name,
                step_index=index,
                target=f"step-{index}",
                timestamp=worst.timestamp,
                cause=found.cause if found and found.primary else None,
            )
        )
    return issues


def _benchmark_issues(
    results: list[BenchmarkResult],
    steps: list[StepTiming],
    requests: list[NetworkRequest],
    breakdowns: dict[int, StepBreakdown],
) -> list[Issue]:
    issues = []
    for result in results:
        if result.status not in (BenchmarkStatus.FAIL, BenchmarkStatus.WARN):
            continue
        if result.metric in _COVERED_METRICS:
            continue
        failed = result.status == BenchmarkStatus.FAIL
        target, index, step, cause = f"bench-{result.id}", None, None, None
        if result.metric == "slowest_step_ms":
            if failed:
                continue  # each step over the limit is its own slow_step issue
            match = next((i for i, s in enumerate(steps, 1) if s.duration_ms == result.value), None)
            if match is not None:
                target, index, step = f"step-{match}", match, steps[match - 1].name
                found = breakdowns.get(match)
                cause = found.cause if found and found.primary else None
        elif result.metric in ("p95_response_ms", "avg_response_ms"):
            target = "operations" if any(r.operation for r in requests) else target
            cause = slowest_operations_text(requests)
        bound = (
            f"limit {fmt_value(result.limit, result.unit)}"
            if failed
            else f"target {fmt_value(result.good, result.unit)}"
        )
        issues.append(
            Issue(
                id="",
                severity=Severity.MAJOR if failed else Severity.MINOR,
                category="benchmark",
                title=f"{result.title}: {fmt_value(result.value, result.unit)} ({bound})",
                detail=result.source,
                step=step,
                step_index=index,
                target=target,
                cause=cause,
            )
        )
    return issues


def _network_issue(execution: TestExecution) -> Issue | None:
    failed = [r for r in execution.telemetry.requests if r.failed]
    if not failed:
        return None
    total = len(execution.telemetry.requests)
    bench = next((b for b in execution.benchmarks if b.metric == "failed_request_pct"), None)
    examples = "; ".join(f"{r.endpoint} -> {r.status or 'no response'}" for r in failed[:5])
    operations = sorted(
        {r.endpoint + (" (background)" if r.background else "") for r in failed},
        key=lambda name: (name.endswith("(background)"), name),
    )
    return Issue(
        id="",
        severity=Severity.MAJOR if bench and bench.status == BenchmarkStatus.FAIL else Severity.MINOR,
        category="network",
        title=f"{len(failed)} of {total} API requests failed",
        detail=examples + ("; ..." if len(failed) > 5 else ""),
        target="network",
        cause=", ".join(operations[:3]) + (", ..." if len(operations) > 3 else ""),
    )


def collect_issues(execution: TestExecution, slow_step_limit_ms: float | None = None) -> list[Issue]:
    steps = execution.telemetry.steps
    crashes = execution.telemetry.crashes
    breakdowns = {b.step_index: b for b in execution.breakdowns}
    issues = _crash_issues(crashes, steps) + _step_issues(steps, slow_step_limit_ms, breakdowns)
    failed_steps = any(not s.success for s in steps)
    if execution.status in (TestStatus.FAILED, TestStatus.ERROR) and not failed_steps and not crashes:
        when = "during setup" if execution.status == TestStatus.ERROR else ""
        issues.append(
            Issue(
                id="",
                severity=Severity.CRITICAL,
                category="test_failure",
                title=f"Test {execution.status.value} {when}".strip(),
                detail=_first_line(_last_error_line(execution.error)),
                target="error",
            )
        )
    for finding in execution.findings:
        if finding.detected and finding.kind != "app_crash":
            issues.append(
                Issue(
                    id="",
                    severity=Severity.MAJOR,
                    category="finding",
                    title=f"{FINDING_LABELS.get(finding.kind, finding.kind.replace('_', ' '))} detected",
                    detail=finding.message,
                    target="findings",
                )
            )
    if (network := _network_issue(execution)) is not None:
        issues.append(network)
    issues += _benchmark_issues(execution.benchmarks, steps, execution.telemetry.requests, breakdowns)
    issues.sort(key=lambda issue: (issue.severity.rank, issue.step_index or 10**6))
    for n, issue in enumerate(issues, 1):
        issue.id = f"issue-{n}"
    return issues


def _last_error_line(error: str | None) -> str:
    """pytest long reprs end with ``E   <message>`` lines; prefer the first of those."""
    lines = (error or "").splitlines()
    marked = [line[1:].strip() for line in lines if line.startswith("E ")]
    return marked[0] if marked else (lines[-1] if lines else "")
