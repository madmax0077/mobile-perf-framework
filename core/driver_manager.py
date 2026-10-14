"""Appium driver construction with platform-specific options and retries."""

from __future__ import annotations

from typing import Any

from models.execution_model import Platform
from utils.logger import get_logger
from utils.retry import call_with_retry

_log = get_logger("driver")


class DriverCreationError(RuntimeError):
    pass


class DriverManager:
    """Builds Appium 2.x sessions (UiAutomator2 / XCUITest)."""

    def __init__(self, implicit_wait_sec: float = 0.0, retries: int = 3) -> None:
        self.implicit_wait_sec = implicit_wait_sec
        self.retries = retries

    @staticmethod
    def build_options(platform: Platform, capabilities: dict[str, Any]) -> Any:
        from appium.options.android import UiAutomator2Options
        from appium.options.ios import XCUITestOptions

        options = UiAutomator2Options() if platform == Platform.ANDROID else XCUITestOptions()
        return options.load_capabilities(capabilities)

    def create(
        self,
        server_url: str,
        platform: Platform,
        capabilities: dict[str, Any],
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> Any:
        """Open a session. Pass credentials separately rather than in the URL:
        Selenium does not percent-decode URL userinfo, so e-mail usernames fail."""
        from appium import webdriver
        from appium.webdriver.client_config import AppiumClientConfig

        options = self.build_options(platform, capabilities)
        _log.info("Creating %s session on %s", platform.value, _redact(server_url))

        def _connect() -> Any:
            client_config = AppiumClientConfig(
                remote_server_addr=server_url, username=username, password=password, keep_alive=True
            )
            return webdriver.Remote(server_url, options=options, client_config=client_config)

        try:
            driver = call_with_retry(_connect, attempts=self.retries)
        except Exception as exc:
            raise DriverCreationError(f"Could not create Appium session: {exc}") from exc
        if self.implicit_wait_sec:
            driver.implicitly_wait(self.implicit_wait_sec)
        _log.info("Session created: %s", driver.session_id)
        return driver

    @staticmethod
    def quit_safely(driver: Any) -> None:
        if driver is None:
            return
        try:
            driver.quit()
        except Exception as exc:
            _log.warning("Error while quitting driver: %s", exc)


def _redact(url: str) -> str:
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1) if "://" in url else ("", url)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"
