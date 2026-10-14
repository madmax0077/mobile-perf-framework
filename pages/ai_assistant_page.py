from __future__ import annotations

from pages.mini_program_page import MiniProgramPage


class AiAssistantPage(MiniProgramPage):
    """AI Assistant mini program behind the home search bar."""

    page_name = "ai_assistant"

    def wait_loaded(self, timeout: float = 30.0) -> None:
        self.wait_opened("topic_best_offers", timeout)

    def ask_best_offers(self, timeout: float = 45.0) -> None:
        """Pick the "Find best offers" topic and wait for the assistant's follow-up question."""
        self.tap("topic_best_offers")
        self.wait_visible("follow_up", timeout)

    def answer_all_offers(self, timeout: float = 45.0) -> None:
        """Answer the follow-up with "All offers" and wait until the assistant starts replying."""
        self.tap("answer_all_offers")
        self.wait_visible("reply", timeout)
