"""Signed-in smoke test of the profile menu: Personal details, Balance alert and
Subscriptions & automatic payments (which can ask to verify the PIN first).

Screens are only opened; no setting is changed.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from core.recorder import PerfRecorder
from pages import Pages


@pytest.mark.journey
def test_profile_settings(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    protected_step(
        "open_profile", pages.home.open_profile, pages.profile, "personal_details", pages.profile.wait_menu
    )
    with perf_recorder.step("open_personal_details"):
        pages.profile.open_item("personal_details")
        pages.account.wait_titled("PERSONAL DETAILS")
    with perf_recorder.step("back_to_profile", measure=False):
        pages.account.back()
        pages.profile.wait_menu()
    with perf_recorder.step("open_balance_alert"):
        pages.profile.open_item("balance_alert")
        pages.account.wait_titled("BALANCE ALERT")
    with perf_recorder.step("back_to_profile_again", measure=False):
        pages.account.back()
        pages.profile.wait_menu()
    protected_step(
        "open_subscriptions",
        lambda: pages.profile.open_item("subscriptions"),
        pages.account,
        "title",
        lambda: pages.account.wait_titled("SUBSCRIPTIONS & AUTOMATIC PAYMENTS"),
    )
