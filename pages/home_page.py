from __future__ import annotations

import contextlib
import time

from models.execution_model import Platform
from pages.base_page import BasePage


class HomePage(BasePage):
    page_name = "home"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_visible("profile_icon", timeout)

    def wait_balance_loaded(self, timeout: float = 30.0) -> None:
        """Signed-in home is usable: the wallet balance has been fetched and rendered."""
        self.wait_visible("balance", timeout)

    def open_profile(self) -> None:
        self.tap("profile_icon")

    def open_cards_and_accounts(self) -> None:
        self.tap("cards_and_accounts")

    def open_offers(self) -> None:
        self.tap("offers_tab")

    def open_orders(self) -> None:
        self.tap("orders_tab")

    def open_stores(self) -> None:
        self.tap("stores_tab")

    def open_search(self) -> None:
        self.tap("search_entry")

    def open_notifications(self) -> None:
        self.tap("notifications")

    def open_send_request(self) -> None:
        self.tap("send_request")

    def open_latest_transaction(self) -> None:
        self.tap("latest_transaction")

    def open_rebell_qr(self) -> None:
        self.tap("rebell_qr")

    def open_top_up(self) -> None:
        self.tap("top_up")

    def open_back_to_bank(self) -> None:
        self.tap("back_to_bank")

    def balance_text(self, timeout: float = 20.0) -> str:
        return str(self.wait_visible("balance_amount", timeout).text)

    def toggle_balance(self, timeout: float = 5.0) -> None:
        """Tap "Show or hide Rebell Balance" and wait until the amount looks different.

        Hiding only blurs the amount: its text stays the same in the accessibility tree, so
        the check compares screenshots of the amount instead.
        """
        before = self.wait_visible("balance_amount").screenshot_as_png
        self.tap("balance_visibility")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if self.find("balance_amount").screenshot_as_png != before:
                return
        raise AssertionError(
            f"balance did not change on screen within {timeout:.0f}s of tapping the visibility icon"
        )

    def open_loyalty_cards(self) -> None:
        self.tap("loyalty_cards")

    def open_top_up_phone(self) -> None:
        self.tap("top_up_phone")

    def open_pay_bills(self) -> None:
        self.tap("pay_bills")

    def open_save_on_utilities(self) -> None:
        self.tap("save_on_utilities")

    def open_discover(self) -> None:
        """Switch the home feed to Discover."""
        self.tap("discover_tab")

    def wait_discover_loaded(self, timeout: float = 20.0) -> None:
        """Discover shows its offers and stores."""
        self.wait_visible("discover_offers", timeout)
        self.wait_visible("discover_stores", timeout)

    def go_home(self, timeout: float = 30.0) -> None:
        """Return to the home screen from a tab or a pushed mini program.

        Uses the bottom navigation when it is on screen (tab mini programs), otherwise
        the system back button (mini programs opened as their own screen).
        """
        if self.is_visible("home_tab", 2):
            self.tap("home_tab")
        else:
            self.driver.back()
        self.wait_loaded(timeout)

    def swipe_feed(self, direction: str, percent: float = 0.75) -> None:
        """Fling the home feed: ``up`` scrolls the content down, ``down`` scrolls it back."""
        if self.platform == Platform.ANDROID:
            size = self.driver.get_window_size()
            width, height = int(size["width"]), int(size["height"])
            self.driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": int(width * 0.1),
                    "top": int(height * 0.3),
                    "width": int(width * 0.8),
                    "height": int(height * 0.5),
                    "direction": direction,
                    "percent": percent,
                },
            )
        else:
            with contextlib.suppress(Exception):
                self.driver.execute_script("mobile: swipe", {"direction": direction})
