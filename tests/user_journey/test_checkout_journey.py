"""User journey performance: Login -> Search -> Checkout -> Profile with live telemetry."""

from __future__ import annotations

import time

import pytest

from core.config import FrameworkConfig
from core.recorder import PerfRecorder

STEP_BUDGET_MS = {
    "login": 8_000,
    "search": 5_000,
    "open_product": 4_000,
    "add_to_cart": 3_000,
    "checkout": 10_000,
    "profile": 4_000,
}


@pytest.mark.journey
def test_login_search_checkout_profile(
    pages, perf_recorder: PerfRecorder, perf_config: FrameworkConfig
) -> None:
    data = perf_config.app.test_data
    think_time = float(data.get("think_time_sec", 1.0))

    journey = [
        ("login", lambda: pages.login.login(data.get("phone", ""), data.get("pin", ""), data.get("otp", ""))),
        ("search", lambda: pages.search.search(data["search_term"])),
        ("open_product", pages.search.open_first_result),
        ("add_to_cart", pages.checkout.add_to_cart),
        ("checkout", pages.checkout.checkout),
        ("profile", pages.home.open_profile),
    ]
    for name, action in journey:
        with perf_recorder.step(name):
            action()
        time.sleep(think_time)

    over_budget = {
        s.name: s.duration_ms
        for s in perf_recorder.steps
        if s.duration_ms > STEP_BUDGET_MS.get(s.name, 10_000)
    }
    assert not over_budget, f"steps over budget: {over_budget}"
