from __future__ import annotations

from pages.base_page import BasePage


class NotificationsPage(BasePage):
    """Native notification centre: category tabs, search and notification preferences."""

    page_name = "notifications"

    def wait_loaded(self, timeout: float = 20.0) -> None:
        self.wait_visible("tab_all", timeout)
        self.wait_tab_content(timeout)

    def wait_tab_content(self, timeout: float = 20.0) -> None:
        """A tab has loaded once it lists messages or shows "No notifications yet"."""
        self.first_visible("message", "empty", timeout=timeout)

    def open_tab(self, tab: str, timeout: float = 20.0) -> None:
        """Switch category: ``tab_all``, ``tab_my_world``, ``tab_promotions`` or ``tab_services``."""
        self.tap(tab)
        self.wait_tab_content(timeout)

    def open_search(self) -> None:
        self.tap("search")
        self.wait_visible("search_input")

    def cancel_search(self) -> None:
        self.tap("search_cancel")
        self.wait_visible("tab_all")

    def open_settings(self, timeout: float = 20.0) -> None:
        self.tap("settings")
        self.wait_visible("preferences", timeout)
