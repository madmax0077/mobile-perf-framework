"""Mini program first open vs reopen: is the H5 package / WebView cache doing its job?

Cards & accounts, Offers and Orders are H5 mini programs. The first open in a session
downloads / unpacks the package and starts a WebView; a reopen should come from cache and
be clearly faster. Recent runs show 3-9 s per open, so this is the biggest user-visible
latency in the app after login.

For each mini program: one timed first open, then ``MINI_PROGRAM_REOPENS`` (default 3)
timed reopens, returning home in between (not measured). Checks:

* the median reopen is not slower than the first open (cache is used);
* first open stays under ``FIRST_OPEN_BUDGET_MS``, every reopen (p90) under
  ``REOPEN_BUDGET_MS``.

Offers and Orders run without TestMu network capture / app profiling (they fail with
"Error: 10003" otherwise, see README).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from core.recorder import PerfRecorder
from tests.perf_helpers import median, p90, sign_in

REOPENS = int(os.environ.get("MINI_PROGRAM_REOPENS", "3"))
FIRST_OPEN_BUDGET_MS = 10_000
REOPEN_BUDGET_MS = 5_000

uninstrumented_app = pytest.mark.provider_options(
    testmu={"lt_options": {"network": False, "appProfiling": False}}
)


@dataclass(frozen=True)
class MiniProgram:
    name: str
    open: Callable[[object], None]


def _open_cards(pages) -> None:
    pages.home.open_cards_and_accounts()
    pages.cards.wait_loaded()


def _open_offers(pages) -> None:
    pages.home.open_offers()
    pages.offers.wait_loaded()


def _open_orders(pages) -> None:
    pages.home.open_orders()
    pages.orders.wait_loaded()


MINI_PROGRAMS = [
    pytest.param(MiniProgram("cards", _open_cards), id="cards"),
    pytest.param(MiniProgram("offers", _open_offers), id="offers", marks=uninstrumented_app),
    pytest.param(MiniProgram("orders", _open_orders), id="orders", marks=uninstrumented_app),
]


@pytest.mark.journey
@pytest.mark.parametrize("mini_program", MINI_PROGRAMS)
def test_mini_program_first_open_vs_reopen(
    mini_program: MiniProgram,
    login_credentials: tuple[str, str, str],
    pages,
    perf_recorder: PerfRecorder,
) -> None:
    sign_in(pages, perf_recorder, login_credentials)
    first_step = f"{mini_program.name}_first_open"
    reopen_step = f"{mini_program.name}_reopen"

    with perf_recorder.step(first_step):
        mini_program.open(pages)
    with perf_recorder.step("back_home", measure=False):
        pages.home.go_home()

    for i in range(1, REOPENS + 1):
        perf_recorder.mark_iteration(i)
        with perf_recorder.step(reopen_step, iteration=i):
            mini_program.open(pages)
        with perf_recorder.step("back_home", iteration=i, measure=False):
            pages.home.go_home()

    first = perf_recorder.durations(first_step)[0]
    reopens = perf_recorder.durations(reopen_step)
    assert len(reopens) == REOPENS

    assert median(reopens) <= first * 1.1, (
        f"{mini_program.name}: reopen (median {median(reopens):.0f} ms) is not faster than the "
        f"first open ({first:.0f} ms): the mini program package / WebView is not cached"
    )
    assert first < FIRST_OPEN_BUDGET_MS, (
        f"{mini_program.name}: first open took {first:.0f} ms (budget {FIRST_OPEN_BUDGET_MS} ms)"
    )
    assert p90(reopens) < REOPEN_BUDGET_MS, (
        f"{mini_program.name}: reopen p90 {p90(reopens):.0f} ms (budget {REOPEN_BUDGET_MS} ms): {reopens}"
    )
