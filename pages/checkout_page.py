from __future__ import annotations

from pages.base_page import BasePage


class CheckoutPage(BasePage):
    page_name = "checkout"

    def add_to_cart(self) -> None:
        self.tap("add_to_cart")

    def checkout(self) -> None:
        self.tap("cart")
        self.tap("checkout")
        self.wait_visible("confirmation", timeout=30)
