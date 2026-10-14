"""Critical consumer business journeys from the regression suite (User_Journeys_Optimized.xlsx).

Each test names the journey (UJ-xxx) it covers; ``docs/USER_JOURNEYS.md`` maps every
selected journey to its test. They sign in with the framework's test account
(``APP_TEST_PHONE`` / ``APP_TEST_PIN`` / ``APP_TEST_OTP``), never with accounts from
the spreadsheet.

No money moves: payment and top-up screens are only opened, the payment code is only
shown, and Change PIN is left without entering a PIN. Screens that can be interrupted by
the "Verify your PIN" sheet are opened through ``protected_step``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from core.recorder import PerfRecorder
from pages import Pages
from providers.base import DeviceProvider


@pytest.mark.journey
def test_uj001_relaunch_and_logout(
    pages: Pages,
    driver: Any,
    provider: DeviceProvider,
    perf_recorder: PerfRecorder,
    sign_in: Callable[[], None],
    login_credentials: tuple[str, str, str],
    protected_step,
) -> None:
    """UJ-001: a returning user relaunches the app (TST-220), cancels a logout, then logs out."""
    sign_in()
    pin = login_credentials[1]
    home, profile = pages.home, pages.profile
    app_id = provider.app_id
    assert app_id, "app id (ANDROID_APP_PACKAGE) is not configured"

    with perf_recorder.step("close_app", measure=False):
        driver.terminate_app(app_id)
        time.sleep(2)
    with perf_recorder.step("relaunch_signed_in"):
        driver.activate_app(app_id)
        pin_asked = pages.pin_requested_before(home, "balance")
    if pin_asked:
        with perf_recorder.step("relaunch_pin_to_home"):
            pages.answer_pin(pin, home, "balance")
            home.wait_balance_loaded()

    protected_step("open_profile", home.open_profile, profile, "personal_details", profile.wait_menu)
    with perf_recorder.step("open_logout_dialog"):
        profile.open_logout_dialog()
    with perf_recorder.step("stay_signed_in"):
        profile.stay_signed_in()
    with perf_recorder.step("reopen_logout_dialog", measure=False):
        profile.open_logout_dialog()
    with perf_recorder.step("log_out"):
        profile.confirm_logout()
        landing = pages.wait_signed_out()
    if landing == "logged_out":
        with perf_recorder.step("dismiss_logged_out_message", measure=False):
            pages.login.tap("logged_out_ok")


@pytest.mark.journey
def test_uj005_balance_visibility(
    pages: Pages,
    perf_recorder: PerfRecorder,
    sign_in: Callable[[], None],
    login_credentials: tuple[str, str, str],
) -> None:
    """UJ-005: hide the Rebell Balance with the eye icon, then show it again."""
    sign_in()
    home = pages.home
    with perf_recorder.step("settle", measure=False):
        pages.wait_signed_in(login_credentials[1], settle=5)
        home.balance_text()
    with perf_recorder.step("hide_balance"):
        home.toggle_balance()
    with perf_recorder.step("show_balance"):
        home.toggle_balance()


@pytest.mark.journey
def test_uj020_uj022_scan_and_payment_code(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    """UJ-020 / UJ-021: open scan to pay with the default payment method;
    UJ-022: show the payment code a merchant scans."""
    sign_in()
    scan = pages.scan
    protected_step("open_scan_to_pay", pages.home.open_rebell_qr, scan, "title", scan.wait_loaded)
    with perf_recorder.step("show_payment_code"):
        notice = scan.show_payment_code()
    if notice:
        with perf_recorder.step("dismiss_code_notice", measure=False):
            scan.dismiss_code_notice()
    with perf_recorder.step("close_payment_code"):
        scan.close_payment_code()
    with perf_recorder.step("close_scanner"):
        scan.close()
        pages.home.wait_balance_loaded()


@pytest.mark.journey
def test_uj008_open_change_pin(
    pages: Pages,
    perf_recorder: PerfRecorder,
    sign_in: Callable[[], None],
    protected_step,
) -> None:
    """UJ-008: Profile > Security > Change PIN opens the PIN check (no PIN is changed)."""
    sign_in()
    profile, security = pages.profile, pages.security
    protected_step("open_profile", pages.home.open_profile, profile, "personal_details", profile.wait_menu)
    protected_step(
        "open_security", lambda: profile.open_item("security"), security, "change_pin", security.wait_loaded
    )
    with perf_recorder.step("open_change_pin"):
        security.open_change_pin()
        pages.wait_screen_left("ProfileSecurityActivity", (pages.login, "pin_input"))


@pytest.mark.journey
def test_uj010_uj015_top_up_and_back_to_bank(
    pages: Pages,
    perf_recorder: PerfRecorder,
    sign_in: Callable[[], None],
    login_credentials: tuple[str, str, str],
) -> None:
    """UJ-010 / UJ-011 / UJ-012: open Top up; UJ-015: open Back to bank (unload to a bank account).

    A restricted account gets the "Need assistance? unusual activity" dialog instead; the
    time to that answer is still recorded, and the test is reported as blocked.
    """
    sign_in()
    pin = login_credentials[1]
    home, login = pages.home, pages.login
    blocked = []

    def open_entry(name: str, tap: Callable[[], None]) -> None:
        with perf_recorder.step(name):
            tap()
            screen = pages.wait_screen_left("HomeV2Activity", (home, "restricted"), (login, "pin_input"))
        if screen == "pin_input":
            with perf_recorder.step(f"{name}_after_pin"):
                login.verify_pin(pin)
                try:
                    screen = pages.wait_screen_left("HomeV2Activity", (home, "restricted"), timeout=5)
                except TimeoutError:
                    tap()
                    screen = pages.wait_screen_left("HomeV2Activity", (home, "restricted"))
        with perf_recorder.step(f"back_home_from_{name.removeprefix('open_')}", measure=False):
            if screen == "restricted":
                blocked.append(name)
                home.tap("restricted_back_home")
            else:
                home.back()
            home.wait_balance_loaded()

    open_entry("open_top_up", home.open_top_up)
    open_entry("open_back_to_bank", home.open_back_to_bank)
    if blocked:
        pytest.skip(
            f"Blocked for this test account ('Need assistance? unusual activity'): {', '.join(blocked)}"
        )
