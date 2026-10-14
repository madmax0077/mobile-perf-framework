"""Signed-in smoke tests for wallet screens: Send & request, transaction details,
loyalty cards and the daily shortcuts.

Screens are only opened: nothing is sent, requested or paid. Loyalty cards and
the shortcuts can ask to verify the PIN first, and so can the first screen after
sign-in (the app's lock); that is timed as a separate ``<step>_after_pin`` step
(see the ``protected_step`` fixture).

The loyalty cards and daily shortcut mini programs fail with "Error: 10003" when
TestMu instruments the app (network capture / app profiling), like Offers and
Orders, so those tests run with both switched off.
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
def test_send_and_request(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    payments = pages.payments
    protected_step(
        "open_send_and_request", pages.home.open_send_request, payments, "sheet", payments.wait_sheet
    )
    with perf_recorder.step("open_send_money"):
        pages.payments.open_send_money()
    with perf_recorder.step("back_home", measure=False):
        pages.payments.back()
        pages.home.wait_balance_loaded()
    with perf_recorder.step("reopen_send_and_request"):
        pages.home.open_send_request()
        pages.payments.wait_sheet()
    with perf_recorder.step("open_request_money"):
        pages.payments.open_request_money()


@pytest.mark.journey
def test_transaction_details(pages: Pages, sign_in: Callable[[], None], protected_step) -> None:
    sign_in()
    transaction = pages.transaction
    protected_step(
        "open_latest_transaction",
        pages.home.open_latest_transaction,
        transaction,
        "details",
        transaction.wait_loaded,
    )


@pytest.mark.journey
@uninstrumented_app
def test_loyalty_cards(pages: Pages, sign_in: Callable[[], None], protected_step) -> None:
    sign_in()
    protected_step(
        "open_loyalty_cards",
        pages.home.open_loyalty_cards,
        pages.loyalty,
        "your_cards",
        pages.loyalty.wait_loaded,
    )


@pytest.mark.journey
@uninstrumented_app
def test_daily_shortcuts(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    shortcuts = pages.shortcuts
    protected_step(
        "open_top_up_phone",
        pages.home.open_top_up_phone,
        shortcuts,
        "phone_top_up",
        shortcuts.wait_phone_top_up,
    )
    with perf_recorder.step("back_home_from_top_up", measure=False):
        shortcuts.back()
        pages.home.wait_balance_loaded()
    protected_step(
        "open_pay_bills", pages.home.open_pay_bills, shortcuts, "pago_pa", shortcuts.wait_pay_bills
    )
    with perf_recorder.step("back_home_from_pay_bills", measure=False):
        shortcuts.back()
        pages.home.wait_balance_loaded()
    protected_step(
        "open_save_on_utilities",
        pages.home.open_save_on_utilities,
        shortcuts,
        "utilities",
        shortcuts.wait_save_on_utilities,
    )
