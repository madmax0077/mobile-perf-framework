from __future__ import annotations

from typing import Any

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support.wait import WebDriverWait

from models.execution_model import Platform
from pages.base_page import BasePage


class MiniProgramLoadError(AssertionError):
    pass


class MiniProgramPage(BasePage):
    """An H5 mini program screen; shares the ``mini_program`` locators (content, title, error page)."""

    def __init__(
        self,
        driver: Any,
        locators: dict[str, dict[str, dict[str, list[str]]]],
        platform: Platform,
        *,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(driver, locators, platform, timeout=timeout)
        self._locators = {**locators.get("mini_program", {}), **self._locators}

    def wait_opened(self, element: str = "content", timeout: float = 30.0) -> None:
        """Wait for ``element``; fail fast with the app's error code when the mini program
        shows its "Failed to open Mini Program" page instead."""
        if self.first_visible(element, "load_error", timeout=timeout) == "load_error":
            self._raise_load_error()

    def wait_titled(self, title: str, timeout: float = 30.0) -> None:
        """Wait until the native title bar reads ``title`` and the page content is rendered."""

        def titled(_driver: Any) -> str | None:
            if any(e.is_displayed() for e in self.driver.find_elements(*self.locator("load_error"))):
                return "load_error"
            shown = self.driver.find_elements(*self.locator("title"))
            return "title" if shown and shown[0].text.strip().casefold() == title.casefold() else None

        wait = WebDriverWait(
            self.driver, timeout, poll_frequency=0.25, ignored_exceptions=(StaleElementReferenceException,)
        )
        if wait.until(titled, f"{self.page_name} title {title!r} not shown") == "load_error":
            self._raise_load_error()
        self.wait_opened("content", timeout)

    def _raise_load_error(self) -> None:
        code = self.find("error_code", 3).text if self.is_visible("error_code", 1) else "no error code"
        raise MiniProgramLoadError(f"{self.page_name} mini program failed to open ({code})")
