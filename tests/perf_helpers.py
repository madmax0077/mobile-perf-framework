"""Small helpers shared by the performance test modules."""

from __future__ import annotations

from core.config import FrameworkConfig
from core.recorder import PerfRecorder


def benchmark_limit(config: FrameworkConfig, rule: str, default: float) -> float:
    """``limit`` of a production benchmark rule in config/framework.yaml (``default`` if absent)."""
    found = config.benchmarks.rules.get(rule)
    return float(found.limit) if found is not None else default


def p90(values: list[float]) -> float:
    """90th percentile (nearest rank) of a non-empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]


def median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def sign_in(pages, perf_recorder: PerfRecorder, credentials: tuple[str, str, str]) -> None:
    """Sign in as an unmeasured step and wait until the wallet balance is shown.

    Called from the test body (not a fixture) so it runs after telemetry has started.
    """
    with perf_recorder.step("sign_in", measure=False):
        pages.sign_in(*credentials)
        pages.home.wait_balance_loaded()
