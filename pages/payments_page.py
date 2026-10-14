from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class PaymentsPage(MiniProgramPage):
    """Send & request: the "What do you want to do?" sheet and its amount screens.

    Only opens the screens; nothing is sent or requested.
    """

    page_name = "payments"

    def wait_sheet(self, timeout: float = 15.0) -> None:
        self.wait_visible("sheet", timeout)

    def open_send_money(self, timeout: float = 30.0) -> None:
        self.tap("send_money")
        self.wait_opened("send_button", timeout)

    def open_request_money(self, timeout: float = 30.0) -> None:
        self.tap("request_money")
        self.wait_opened("request_button", timeout)
