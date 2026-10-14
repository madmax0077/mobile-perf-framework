from __future__ import annotations

from selenium.common.exceptions import TimeoutException

from pages.base_page import BasePage


class LoginPage(BasePage):
    """Phone number -> PIN -> OTP sign-in flow."""

    page_name = "login"

    def enter_phone(self, phone: str) -> None:
        self.type("phone_input", phone)

    def submit_phone(self) -> None:
        self.tap("phone_next")

    def enter_pin(self, pin: str) -> None:
        self.type("pin_input", pin, clear=False)

    def submit_pin(self) -> None:
        self.tap("pin_next")

    def verify_pin(self, pin: str) -> None:
        """Answer a "Verify your PIN" sheet: the step-up check before protected screens,
        also shown at random right after sign-in."""
        self.enter_pin(pin)
        self.submit_pin()

    def pin_requested(self, timeout: float = 3.0) -> bool:
        return self.is_visible("pin_input", timeout)

    def enter_otp(self, otp: str) -> bool:
        """Type the OTP; False when the app skipped it (trusted device) and went straight to
        the biometrics prompt or a second "Verify your PIN" check."""
        try:
            if self.first_visible("otp_input", "biometrics_later") != "otp_input":
                return False
        except TimeoutException:
            return False
        self.type("otp_input", otp, clear=False)
        return True

    def dismiss_biometrics_prompt(self, timeout: float = 8.0) -> bool:
        """Answer 'Maybe later' to the optional biometrics prompt shown after the first login."""
        if not self.is_visible("biometrics_later", timeout):
            return False
        try:
            self.tap("biometrics_later")
        except Exception:
            return False  # the prompt can close itself while we tap
        return True

    def login(self, phone: str, pin: str, otp: str) -> None:
        self.enter_phone(phone)
        self.submit_phone()
        self.enter_pin(pin)
        self.submit_pin()
        self.enter_otp(otp)
        self.dismiss_biometrics_prompt()
