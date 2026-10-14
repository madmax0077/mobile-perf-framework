from __future__ import annotations

import time

from pages.base_page import BasePage


class ScanPage(BasePage):
    """Scan to pay (C-Scan-B) behind "Rebell QR", with the payment code (B-Scan-C) to show."""

    page_name = "scan"

    def wait_loaded(self, timeout: float = 20.0) -> None:
        """Scanner is ready and the default payment method is shown."""
        self.wait_visible("title", timeout)
        self.wait_visible("pay_method", timeout)

    def show_payment_code(self, timeout: float = 20.0) -> bool:
        """Open the full-screen payment code; True when the security notice covers it."""
        self.tap("show_code")
        self.wait_visible("payment_code", timeout)
        return self.is_visible("code_notice_ok", timeout=2)

    def dismiss_code_notice(self, timeout: float = 10.0) -> None:
        self.tap("code_notice_ok")
        self._wait_gone("code_notice_ok", timeout)

    def close_payment_code(self, timeout: float = 10.0) -> None:
        """ "Tap anywhere to close": back to the scanner."""
        self.tap("code_close")
        self._wait_gone("payment_code", timeout)

    def _wait_gone(self, element: str, timeout: float) -> None:
        by, value = self.locator(element)
        deadline = time.monotonic() + timeout
        while any(found.is_displayed() for found in self.driver.find_elements(by, value)):
            if time.monotonic() >= deadline:
                raise AssertionError(f"{self.page_name}.{element} still shown after {timeout:.0f}s")
            time.sleep(0.25)

    def close(self) -> None:
        self.tap("close")
