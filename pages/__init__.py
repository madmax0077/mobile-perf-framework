"""Page objects for the application under test."""

from __future__ import annotations

import time
from functools import cached_property
from typing import Any

from selenium.common.exceptions import WebDriverException

from models.execution_model import Platform
from pages.account_pages import AccountPage, LoyaltyCardsPage, ShortcutsPage, TransactionPage
from pages.ai_assistant_page import AiAssistantPage
from pages.base_page import BasePage, LocatorNotConfiguredError
from pages.cards_page import CardsPage
from pages.checkout_page import CheckoutPage
from pages.home_page import HomePage
from pages.login_page import LoginPage
from pages.mini_program_page import MiniProgramLoadError, MiniProgramPage
from pages.notifications_page import NotificationsPage
from pages.offers_page import OffersPage
from pages.orders_page import OrdersPage
from pages.payments_page import PaymentsPage
from pages.profile_page import ProfilePage
from pages.scan_page import ScanPage
from pages.search_page import SearchPage
from pages.security_page import SecurityPage
from pages.stores_page import StoresPage


class Pages:
    """Lazy page-object container exposed through the ``pages`` fixture."""

    def __init__(self, driver: Any, locators: dict[str, Any], platform: Platform) -> None:
        self._args = (driver, locators, platform)

    @cached_property
    def home(self) -> HomePage:
        return HomePage(*self._args)

    @cached_property
    def login(self) -> LoginPage:
        return LoginPage(*self._args)

    @cached_property
    def search(self) -> SearchPage:
        return SearchPage(*self._args)

    @cached_property
    def checkout(self) -> CheckoutPage:
        return CheckoutPage(*self._args)

    @cached_property
    def profile(self) -> ProfilePage:
        return ProfilePage(*self._args)

    @cached_property
    def cards(self) -> CardsPage:
        return CardsPage(*self._args)

    @cached_property
    def offers(self) -> OffersPage:
        return OffersPage(*self._args)

    @cached_property
    def orders(self) -> OrdersPage:
        return OrdersPage(*self._args)

    @cached_property
    def notifications(self) -> NotificationsPage:
        return NotificationsPage(*self._args)

    @cached_property
    def ai_assistant(self) -> AiAssistantPage:
        return AiAssistantPage(*self._args)

    @cached_property
    def stores(self) -> StoresPage:
        return StoresPage(*self._args)

    @cached_property
    def payments(self) -> PaymentsPage:
        return PaymentsPage(*self._args)

    @cached_property
    def transaction(self) -> TransactionPage:
        return TransactionPage(*self._args)

    @cached_property
    def loyalty(self) -> LoyaltyCardsPage:
        return LoyaltyCardsPage(*self._args)

    @cached_property
    def account(self) -> AccountPage:
        return AccountPage(*self._args)

    @cached_property
    def shortcuts(self) -> ShortcutsPage:
        return ShortcutsPage(*self._args)

    @cached_property
    def scan(self) -> ScanPage:
        return ScanPage(*self._args)

    @cached_property
    def security(self) -> SecurityPage:
        return SecurityPage(*self._args)

    def wait_signed_out(self, timeout: float = 30.0) -> str:
        """After logging out: wait for the signed-out state ("Logged out successfully", Sign in
        button, phone entry, or a home screen without the wallet balance) and return which one
        showed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screen = self._screen(
                (self.login, "logged_out"),
                (self.profile, "sign_in"),
                (self.login, "phone_input"),
                (self.home, "profile_icon"),
            )
            if screen in ("logged_out", "sign_in", "phone_input"):
                return screen
            if screen == "profile_icon" and self._screen((self.home, "balance"), timeout=0.5) is None:
                return "guest_home"
        raise TimeoutError(f"signed-out screen not shown within {timeout:.0f}s")

    def wait_screen_left(
        self, activity: str, *candidates: tuple[BasePage, str], timeout: float = 30.0
    ) -> str:
        """Wait until one of ``candidates`` shows or the app leaves ``activity``; returns the
        candidate's name or the new activity."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screen = self._screen(*candidates, timeout=0.5) if candidates else None
            if screen is not None:
                return screen
            current = self.home.driver.current_activity or ""
            if activity not in current:
                return current
        raise TimeoutError(f"still on {activity} after {timeout:.0f}s")

    def pin_requested_before(self, page: BasePage, *elements: str, timeout: float = 30.0) -> bool:
        """After opening a PIN-protected screen: True when the "Verify your PIN" sheet shows
        before any of ``page``'s ``elements`` (the app only asks when the last check expired)."""
        candidates = [(self.login, "pin_input"), *((page, element) for element in elements)]
        if isinstance(page, MiniProgramPage):
            candidates.append((page, "load_error"))
        screen = self._screen(*candidates, timeout=timeout)
        if screen is None:
            names = ", ".join(f"{page.page_name}.{e}" for e in elements)
            raise TimeoutError(f"neither the PIN sheet nor {names} appeared within {timeout:.0f}s")
        return screen == "pin_input"

    def answer_pin(self, pin: str, page: BasePage, *elements: str, timeout: float = 30.0) -> bool:
        """Verify the PIN, then wait for ``page``'s ``elements``; answers the biometrics prompt
        the app can show in between.

        Returns True when the app went back to the home screen instead: the sheet was its
        random lock screen, not a check for ``page``, and the entry has to be opened again.
        """
        self.login.verify_pin(pin)
        waiting = [(page, element) for element in elements]
        screen = self._screen(
            (self.login, "biometrics_later"), *waiting, (self.home, "balance"), timeout=timeout
        )
        if screen == "biometrics_later":
            self.login.dismiss_biometrics_prompt(1)
            screen = self._screen(*waiting, (self.home, "balance"), timeout=timeout)
        return screen == "balance"

    def sign_in(self, phone: str, pin: str, otp: str) -> None:
        """Home -> Profile -> Sign in -> phone / PIN / OTP -> back on the signed-in home screen."""
        self.home.wait_loaded()
        self.home.open_profile()
        self.profile.start_sign_in()
        self.login.login(phone, pin, otp)
        self.wait_signed_in(pin)

    def wait_signed_in(self, pin: str, timeout: float = 60.0, settle: float = 3.0) -> None:
        """Wait until the signed-in home stays on screen.

        On the way the app may show, in any order: the "Verify your PIN" sheet (on
        launch, and at random a few seconds after sign-in) and a late biometrics
        prompt. Both are answered; the screen can change while that happens.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screen = self._screen(
                (self.login, "pin_input"),
                (self.login, "biometrics_later"),
                (self.home, "balance"),
                (self.login, "sign_in_error"),
            )
            if screen == "sign_in_error":
                raise RuntimeError("sign-in rejected by the server: 'Sorry, something went wrong'")
            try:
                if screen == "pin_input":
                    self.login.verify_pin(pin)
                elif screen == "biometrics_later":
                    self.login.dismiss_biometrics_prompt(1)
                elif screen == "balance":
                    time.sleep(settle)
                    if (
                        self._screen((self.login, "pin_input"), (self.login, "biometrics_later"), timeout=1)
                        is None
                    ):
                        self.home.wait_loaded()
                        return
            except WebDriverException:
                continue
        raise TimeoutError(f"signed-in home screen not shown within {timeout:.0f}s")

    def _screen(self, *candidates: tuple[BasePage, str], timeout: float = 2.0) -> str | None:
        """Name of the first candidate element on screen within ``timeout``, else None."""
        deadline = time.monotonic() + timeout
        while True:
            for page, element in candidates:
                try:
                    if any(
                        found.is_displayed() for found in page.driver.find_elements(*page.locator(element))
                    ):
                        return element
                except WebDriverException:
                    pass
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)


__all__ = [
    "AccountPage",
    "AiAssistantPage",
    "BasePage",
    "CardsPage",
    "CheckoutPage",
    "HomePage",
    "LocatorNotConfiguredError",
    "LoginPage",
    "LoyaltyCardsPage",
    "MiniProgramLoadError",
    "MiniProgramPage",
    "NotificationsPage",
    "OffersPage",
    "OrdersPage",
    "Pages",
    "PaymentsPage",
    "ProfilePage",
    "ScanPage",
    "SearchPage",
    "SecurityPage",
    "ShortcutsPage",
    "StoresPage",
    "TransactionPage",
]
