from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class OrdersPage(MiniProgramPage):
    """Orders tab (mini program)."""

    page_name = "orders"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("content", timeout)
