from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class OffersPage(MiniProgramPage):
    """Offers tab (mini program): My offers, Discover offers and Flash deals sections."""

    page_name = "offers"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("my_offers", timeout)

    def open_my_offers(self) -> None:
        self.tap("my_offers")

    def open_discover_offers(self) -> None:
        self.tap("discover_offers")

    def open_flash_deals(self) -> None:
        self.tap("flash_deals")
