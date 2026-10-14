"""Attributes step time to the test / page-object methods and driver commands behind it.

Every Selenium / Appium command (find, click, send keys...) goes through
``driver.execute``. ``DriverTracer`` wraps that one method on the driver instance and,
while a step is open on the calling thread, records each command with:

* the call chain of project code that issued it, e.g. ``HomePage.wait_loaded > BasePage.find``
* the test source line (``tests/smoke/test_login.py:50``)
* the page element (``home.profile_icon``) or locator it targeted

Commands from other threads (telemetry collectors) are ignored. Time spent in test
code between two commands (e.g. ``WebDriverWait`` polling sleeps) is attributed to
the command that follows it, so per-method wall times add up to the step duration.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import FrameType
from typing import Any

from models.telemetry_model import DriverCall

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Framework packages; frames in them are plumbing, not the code under analysis
FRAMEWORK_DIRS = frozenset({"core", "providers", "telemetry", "utils", "reporting", "models", "ci"})
CHAIN_SEPARATOR = " > "
_TAIL_MIN_MS = 100.0


@lru_cache(maxsize=512)
def _project_relative(filename: str) -> str | None:
    """Path relative to the project for test / page-object code, else None."""
    try:
        relative = Path(filename).resolve().relative_to(PROJECT_ROOT)
    except (ValueError, OSError):
        return None
    parts = relative.parts
    if len(parts) < 2 or parts[0] in FRAMEWORK_DIRS or parts[0].startswith("."):
        return None
    return relative.as_posix()


@dataclass(frozen=True)
class CallSite:
    chain: tuple[str, ...]
    location: str | None
    target: str | None


def call_site(frame: FrameType | None) -> CallSite:
    """Project frames from the test function inwards, plus the page element being used."""
    chain: list[str] = []
    location: str | None = None
    target: str | None = None
    while frame is not None:
        relative = _project_relative(frame.f_code.co_filename)
        if relative is not None:
            name = frame.f_code.co_qualname
            if "<" not in name:
                chain.append(name)
                location = f"{relative}:{frame.f_lineno}"
            if target is None:
                element = frame.f_locals.get("element")
                page = getattr(frame.f_locals.get("self"), "page_name", None)
                if isinstance(element, str) and isinstance(page, str):
                    target = f"{page}.{element}"
        frame = frame.f_back
    chain.reverse()
    return CallSite(tuple(chain), location, target)


def _locator(params: Any) -> str | None:
    if isinstance(params, dict) and "using" in params and "value" in params:
        return f"{params['using']}={params['value']}"
    return None


@dataclass
class _Raw:
    command: str
    site: CallSite
    target: str | None
    started: float
    ended: float
    failed: bool


class DriverTracer:
    def __init__(self) -> None:
        self._local = threading.local()

    def install(self, driver: Any) -> bool:
        """Wrap ``driver.execute``; returns False when the driver has none (or is wrapped)."""
        execute = getattr(driver, "execute", None)
        if not callable(execute) or getattr(execute, "_perf_tracer", None) is self:
            return False
        driver.execute = self._wrap(execute)
        return True

    def _wrap(self, execute: Callable[..., Any]) -> Callable[..., Any]:
        local = self._local

        def traced(*args: Any, **kwargs: Any) -> Any:
            calls: list[_Raw] | None = getattr(local, "calls", None)
            if calls is None or getattr(local, "depth", 0):
                return execute(*args, **kwargs)
            command = str(args[0] if args else kwargs.get("driver_command", "?"))
            params = args[1] if len(args) > 1 else kwargs.get("params")
            site = call_site(sys._getframe(1))
            local.depth = 1
            started, failed = time.perf_counter(), False
            try:
                return execute(*args, **kwargs)
            except BaseException:
                failed = True
                raise
            finally:
                local.depth = 0
                calls.append(
                    _Raw(command, site, site.target or _locator(params), started, time.perf_counter(), failed)
                )

        traced._perf_tracer = self  # type: ignore[attr-defined]
        return traced

    def start(self) -> None:
        self._local.calls = []
        self._local.depth = 0
        self._local.started = time.perf_counter()

    def stop(self) -> list[DriverCall]:
        calls: list[_Raw] | None = getattr(self._local, "calls", None)
        started: float = getattr(self._local, "started", time.perf_counter())
        self._local.calls = None
        return aggregate(calls or [], started, time.perf_counter())


def _code(chain: tuple[str, ...]) -> str:
    """Chain below the test function (the test itself is in ``location``)."""
    inner = chain[1:] if len(chain) > 1 else chain
    return CHAIN_SEPARATOR.join(inner) or "(driver called outside test code)"


def aggregate(calls: list[_Raw], started: float, ended: float) -> list[DriverCall]:
    groups: dict[tuple[str, str, str | None], DriverCall] = {}
    previous_end = started
    for call in sorted(calls, key=lambda c: c.started):
        gap = max(0.0, call.started - previous_end)
        command_ms = (call.ended - call.started) * 1000.0
        key = (_code(call.site.chain), call.command, call.target)
        group = groups.get(key)
        if group is None:
            group = groups[key] = DriverCall(
                code=key[0],
                location=call.site.location,
                command=call.command,
                target=call.target,
                count=0,
                command_ms=0.0,
                wall_ms=0.0,
            )
        group.count += 1
        group.failed += int(call.failed)
        group.command_ms += command_ms
        group.wall_ms += gap * 1000.0 + command_ms
        previous_end = max(previous_end, call.ended)
    tail_ms = (ended - previous_end) * 1000.0
    if calls and tail_ms >= _TAIL_MIN_MS:
        groups[("tail", "", None)] = DriverCall(
            code="(test code after the last driver command)", command="-", command_ms=0.0, wall_ms=tail_ms
        )
    result = sorted(groups.values(), key=lambda g: g.wall_ms, reverse=True)
    for group in result:
        group.command_ms = round(group.command_ms, 1)
        group.wall_ms = round(group.wall_ms, 1)
    return result
