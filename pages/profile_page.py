from __future__ import annotations

from pages.base_page import BasePage


class ProfilePage(BasePage):
    page_name = "profile"

    def start_sign_in(self) -> None:
        self.tap("sign_in")

    def phone_number(self, timeout: float = 20.0) -> str:
        return str(self.wait_visible("phone_number", timeout).text)

    def wait_menu(self, timeout: float = 20.0) -> None:
        """Signed-in profile menu is shown."""
        self.wait_visible("personal_details", timeout)

    def open_item(self, item: str) -> None:
        """Tap a menu entry: ``personal_details``, ``balance_alert``, ``subscriptions``,
        ``get_support`` or ``security``."""
        self.tap(item)

    def open_logout_dialog(self, timeout: float = 10.0) -> None:
        self.tap("logout")
        self.wait_visible("logout_dialog", timeout)

    def stay_signed_in(self, timeout: float = 10.0) -> None:
        """STAY in the "Log out?" dialog: the profile menu stays."""
        self.tap("logout_stay")
        self.wait_menu(timeout)

    def confirm_logout(self) -> None:
        self.tap("logout_confirm")
