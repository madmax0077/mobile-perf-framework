from __future__ import annotations

from pages.base_page import BasePage


class SearchPage(BasePage):
    page_name = "search"

    def search(self, term: str) -> None:
        self.tap("search_box")
        self.type("search_box", f"{term}\n")
        self.wait_visible("first_result")

    def open_first_result(self) -> None:
        self.tap("first_result")
