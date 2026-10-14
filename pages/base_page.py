"""Page Object base class with YAML-driven, platform-aware locators."""

from __future__ import annotations

from typing import Any, ClassVar

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.support.wait import WebDriverWait

from models.execution_model import Platform


class LocatorNotConfiguredError(KeyError):
    pass


class BasePage:
    page_name: ClassVar[str]

    def __init__(
        self,
        driver: Any,
        locators: dict[str, dict[str, dict[str, list[str]]]],
        platform: Platform,
        *,
        timeout: float = 15.0,
    ) -> None:
        self.driver = driver
        self.platform = platform
        self.timeout = timeout
        self._locators = locators.get(self.page_name, {})

    def locator(self, element: str) -> tuple[str, str]:
        try:
            strategy, value = self._locators[element][self.platform.value]
        except (KeyError, ValueError) as exc:
            raise LocatorNotConfiguredError(
                f"Locator '{self.page_name}.{element}' for {self.platform.value} missing in config/app.yaml"
            ) from exc
        return strategy, value

    def find(self, element: str, timeout: float | None = None) -> Any:
        by, value = self.locator(element)
        wait = WebDriverWait(
            self.driver,
            timeout or self.timeout,
            poll_frequency=0.25,
            ignored_exceptions=(NoSuchElementException, StaleElementReferenceException),
        )
        return wait.until(lambda d: d.find_element(by, value), f"{self.page_name}.{element} not found")

    def tap(self, element: str) -> None:
        self.find(element).click()

    def type(self, element: str, text: str, *, clear: bool = True) -> None:
        field = self.find(element)
        if clear:
            field.clear()
        field.send_keys(text)

    def is_visible(self, element: str, timeout: float = 3.0) -> bool:
        try:
            return bool(self.find(element, timeout).is_displayed())
        except Exception:
            return False

    def first_visible(self, *elements: str, timeout: float | None = None) -> str:
        """Wait until one of ``elements`` is on screen and return its name."""
        locators = {element: self.locator(element) for element in elements}

        def shown(_driver: Any) -> str | None:
            for element, (by, value) in locators.items():
                if any(found.is_displayed() for found in self.driver.find_elements(by, value)):
                    return element
            return None

        wait = WebDriverWait(
            self.driver,
            timeout or self.timeout,
            poll_frequency=0.25,
            ignored_exceptions=(StaleElementReferenceException,),
        )
        names = " or ".join(f"{self.page_name}.{e}" for e in elements)
        return wait.until(shown, f"none of {names} appeared")

    def back(self) -> None:
        """System back: closes this screen (or the sheet on top of it)."""
        self.driver.back()

    def wait_visible(self, element: str, timeout: float | None = None) -> Any:
        found = self.find(element, timeout)
        if not found.is_displayed():
            raise AssertionError(f"{self.page_name}.{element} is not displayed")
        return found
