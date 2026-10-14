from __future__ import annotations

from collections.abc import Callable

import pytest

from core.recorder import PerfRecorder
from pages import BasePage, Pages


@pytest.fixture
def sign_in(
    pages: Pages, perf_recorder: PerfRecorder, login_credentials: tuple[str, str, str]
) -> Callable[[], None]:
    """Call first in a test: signs in as one unmeasured ``sign_in`` step."""

    def run() -> None:
        with perf_recorder.step("sign_in", measure=False):
            pages.sign_in(*login_credentials)

    return run


ProtectedStep = Callable[[str, Callable[[], None], BasePage, str, Callable[[], None]], None]


@pytest.fixture
def protected_step(
    pages: Pages, perf_recorder: PerfRecorder, login_credentials: tuple[str, str, str]
) -> ProtectedStep:
    """Time opening a screen the "Verify your PIN" sheet can interrupt.

    That is a PIN-protected screen (the app asks when the last check has expired), or the
    first screen opened after sign-in: the app locks itself with the same sheet a few
    seconds after login and drops the tap that was in flight.

    ``protected_step(name, open_entry, page, ready, wait_loaded)``: step ``name`` covers the
    tap until the screen (``page.ready``) or the PIN sheet shows. When the app asks for the
    PIN, a second step ``<name>_after_pin`` covers entering it (and tapping the entry again
    if the app went back home) until the screen has loaded, so PIN typing never inflates
    the screen's own timing.
    """
    pin = login_credentials[1]

    def run(
        name: str, open_entry: Callable[[], None], page: BasePage, ready: str, wait_loaded: Callable[[], None]
    ) -> None:
        with perf_recorder.step(name):
            open_entry()
            pin_asked = pages.pin_requested_before(page, ready)
            if not pin_asked:
                wait_loaded()
        if pin_asked:
            with perf_recorder.step(f"{name}_after_pin"):
                if pages.answer_pin(pin, page, ready):
                    open_entry()
                wait_loaded()

    return run
