"""Signed-in smoke tests: notification centre, AI Assistant and the Stores tab.

Each test signs in first (one unmeasured ``sign_in`` step) and then times every
step: the tap plus the wait for the next screen to show its content. The first
screen is opened through ``protected_step``, which answers the PIN lock the app
shows a few seconds after sign-in.

The AI Assistant and Stores mini programs fail with "Error: 10003" when TestMu
instruments the app (network capture / app profiling), like Offers and Orders, so
those tests run with both switched off.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from core.recorder import PerfRecorder
from pages import Pages

uninstrumented_app = pytest.mark.provider_options(
    testmu={"lt_options": {"network": False, "appProfiling": False}}
)


@pytest.mark.journey
def test_notification_centre(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    notifications = pages.notifications
    protected_step(
        "open_notifications",
        pages.home.open_notifications,
        notifications,
        "tab_all",
        notifications.wait_loaded,
    )
    with perf_recorder.step("open_search"):
        notifications.open_search()
    with perf_recorder.step("cancel_search"):
        notifications.cancel_search()
    with perf_recorder.step("tab_my_world"):
        notifications.open_tab("tab_my_world")
    with perf_recorder.step("tab_promotions_and_offers"):
        notifications.open_tab("tab_promotions")
    with perf_recorder.step("tab_services"):
        notifications.open_tab("tab_services")
    with perf_recorder.step("open_preferences"):
        notifications.open_settings()


@pytest.mark.journey
@uninstrumented_app
def test_ai_assistant(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    assistant = pages.ai_assistant
    protected_step(
        "open_ai_assistant", pages.home.open_search, assistant, "topic_best_offers", assistant.wait_loaded
    )
    with perf_recorder.step("ask_best_offers"):
        assistant.ask_best_offers()
    with perf_recorder.step("answer_all_offers"):
        assistant.answer_all_offers()


@pytest.mark.journey
@uninstrumented_app
def test_stores(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    stores = pages.stores
    protected_step("open_stores", pages.home.open_stores, stores, "my_stores", stores.wait_loaded)
    with perf_recorder.step("open_my_stores"):
        stores.open_my_stores()
    with perf_recorder.step("open_store"):
        stores.open_store()
