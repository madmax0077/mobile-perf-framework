"""Mini programs reached from the home screen and the profile menu."""

from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class TransactionPage(MiniProgramPage):
    page_name = "transaction"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("details", timeout)
        self.wait_visible("status", timeout)


class LoyaltyCardsPage(MiniProgramPage):
    page_name = "loyalty"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("your_cards", timeout)


class ShortcutsPage(MiniProgramPage):
    """Daily shortcut mini programs: Top up phone, Pay bills (PagoPA), Save on utilities."""

    page_name = "shortcuts"

    def wait_phone_top_up(self, timeout: float = 30.0) -> None:
        self.wait_opened("phone_top_up", timeout)
        self.wait_visible("choose_operator", timeout)

    def wait_pay_bills(self, timeout: float = 30.0) -> None:
        self.wait_opened("pago_pa", timeout)

    def wait_save_on_utilities(self, timeout: float = 30.0) -> None:
        self.wait_opened("utilities", timeout)


class AccountPage(MiniProgramPage):
    """Profile menu mini programs (Personal details, Balance alert, Subscriptions, ...),
    told apart by their title bar."""

    page_name = "account"
