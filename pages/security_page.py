from __future__ import annotations

from pages.base_page import BasePage


class SecurityPage(BasePage):
    """Profile > Security: auto-lock, biometrics, Change PIN, freeze QR code."""

    page_name = "security"

    def wait_loaded(self, timeout: float = 20.0) -> None:
        self.wait_visible("change_pin", timeout)

    def open_change_pin(self) -> None:
        self.tap("change_pin")
