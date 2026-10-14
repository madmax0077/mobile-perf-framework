from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class CardsPage(MiniProgramPage):
    """ "Cards & bank accounts" mini program."""

    page_name = "cards"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("add_new_card", timeout)

    def add_new_card(self, timeout: float = 30.0) -> None:
        """Open the add-card form."""
        self.tap("add_new_card")
        self.wait_visible("add_card_form", timeout)
