from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class StoresPage(MiniProgramPage):
    """Stores tab (mini program): followed stores, the A-Z list and a store's page."""

    page_name = "stores"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("my_stores", timeout)

    def open_my_stores(self, timeout: float = 20.0) -> None:
        self.tap("my_stores")
        self.wait_visible("store", timeout)

    def open_all_stores(self, timeout: float = 20.0) -> None:
        self.tap("all_stores")
        self.wait_visible("store", timeout)

    def open_store(self, timeout: float = 30.0) -> None:
        """Open the configured store (``APP_TEST_STORE``) and wait for its page."""
        self.tap("store")
        self.wait_opened("store_directions", timeout)
