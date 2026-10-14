"""Signed-in smoke tests for the home screen features: cards, offers, orders, discover.

Each test signs in first (one unmeasured ``sign_in`` step), then times every
feature step: the tap plus the wait for the next screen. Offers, Orders and
Cards & accounts are H5 mini programs; when one shows "Failed to open Mini
Program" the step fails straight away with the app's error code. The first
screen of each test is opened through ``protected_step`` because the app can
lock itself with the "Verify your PIN" sheet a few seconds after sign-in.

The Offers and Orders mini programs fail with "Error: 10003" when TestMu
instruments the app for network capture or app profiling (the mPaaS config
gateway rejects the RPC with 7014 "rpc signature check: sign content is null").
Those two tests switch both features off, so they run without a HAR or cloud
app metrics.
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
def test_add_new_card(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    cards = pages.cards
    protected_step(
        "open_cards_and_accounts",
        pages.home.open_cards_and_accounts,
        cards,
        "add_new_card",
        cards.wait_loaded,
    )
    with perf_recorder.step("add_new_card"):
        cards.add_new_card()


@pytest.mark.journey
@uninstrumented_app
def test_check_offers(
    pages: Pages, perf_recorder: PerfRecorder, sign_in: Callable[[], None], protected_step
) -> None:
    sign_in()
    offers = pages.offers
    protected_step("open_offers", pages.home.open_offers, offers, "my_offers", offers.wait_loaded)
    with perf_recorder.step("open_my_offers"):
        offers.open_my_offers()
    with perf_recorder.step("open_discover_offers"):
        offers.open_discover_offers()
    with perf_recorder.step("open_flash_deals"):
        offers.open_flash_deals()


@pytest.mark.journey
@uninstrumented_app
def test_check_orders(pages: Pages, sign_in: Callable[[], None], protected_step) -> None:
    sign_in()
    orders = pages.orders
    protected_step("open_orders", pages.home.open_orders, orders, "content", orders.wait_loaded)


@pytest.mark.journey
def test_discover(pages: Pages, sign_in: Callable[[], None], protected_step) -> None:
    sign_in()
    home = pages.home
    protected_step("open_discover", home.open_discover, home, "discover_offers", home.wait_discover_loaded)
