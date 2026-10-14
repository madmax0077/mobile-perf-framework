"""Command transports used by collectors (Strategy pattern).

Collectors never call adb directly. They talk to a ``CommandExecutor`` so the
same collector works against a USB device, an Appium server, or a cloud's
proprietary ADB bridge.
"""

from __future__ import annotations

import shlex
import threading
from abc import ABC, abstractmethod
from typing import Any

from utils.adb_utils import AdbClient, AdbError
from utils.logger import get_logger

_log = get_logger("executor")


class CommandError(RuntimeError):
    """The command ran (or was attempted) and failed."""


class CommandNotSupportedError(CommandError):
    """The transport cannot run this command (e.g. blocked by the cloud)."""


class CommandExecutor(ABC):
    name: str = "abstract"

    @abstractmethod
    def shell(self, command: str, timeout: float = 30.0) -> str:
        """Run an ``adb shell`` command and return stdout."""

    def performance_data(self, package: str, data_type: str) -> list[list[Any]] | None:
        """Appium ``getPerformanceData`` (cpuinfo / memoryinfo / batteryinfo / networkinfo)."""
        raise CommandNotSupportedError(f"{self.name} does not support performance_data")


class AdbExecutor(CommandExecutor):
    """Host adb binary (local devices and emulators)."""

    name = "adb"

    def __init__(self, client: AdbClient) -> None:
        self.client = client

    def shell(self, command: str, timeout: float = 30.0) -> str:
        try:
            return self.client.shell(command, timeout=timeout)
        except AdbError as exc:
            raise CommandError(str(exc)) from exc


class AppiumShellExecutor(CommandExecutor):
    """Appium UiAutomator2 ``mobile: shell`` + ``getPerformanceData``.

    ``mobile: shell`` needs the server started with ``--allow-insecure adb_shell``;
    most clouds block it, so ``allow_shell=False`` keeps only the perf-data API.
    Driver calls are serialised because collectors run on several threads.
    """

    name = "appium"

    def __init__(self, driver: Any, *, allow_shell: bool = True) -> None:
        self.driver = driver
        self.allow_shell = allow_shell
        self._lock = threading.Lock()

    def shell(self, command: str, timeout: float = 30.0) -> str:
        if not self.allow_shell:
            raise CommandNotSupportedError("mobile: shell disabled for this provider")
        parts = shlex.split(command)
        with self._lock:
            try:
                result = self.driver.execute_script(
                    "mobile: shell", {"command": parts[0], "args": parts[1:], "timeout": int(timeout * 1000)}
                )
            except Exception as exc:
                message = str(exc)
                if "insecure" in message.lower() or "adb_shell" in message:
                    self.allow_shell = False
                    raise CommandNotSupportedError(message) from exc
                raise CommandError(message) from exc
        if isinstance(result, dict):
            return str(result.get("stdout", ""))
        return str(result or "")

    def performance_data(self, package: str, data_type: str) -> list[list[Any]] | None:
        with self._lock:
            try:
                return self.driver.get_performance_data(package, data_type, 5)
            except Exception as exc:
                raise CommandError(f"getPerformanceData({data_type}) failed: {exc}") from exc


class TestMuAdbExecutor(CommandExecutor):
    """TestMu AI ``lambda-adb`` JavaScript executor with an optional fallback.

    TestMu allows a curated subset of shell commands (e.g. ``dumpsys``,
    ``getprop``, ``cat``). Commands that are rejected are remembered and routed
    to ``fallback`` from then on.
    """

    __test__ = False
    name = "testmu-lambda-adb"

    def __init__(self, driver: Any, fallback: CommandExecutor | None = None) -> None:
        self.driver = driver
        self.fallback = fallback
        self._blocked: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _key(command: str) -> str:
        parts = command.split()
        return " ".join(parts[:2]) if parts and parts[0] == "dumpsys" else (parts[0] if parts else "")

    def shell(self, command: str, timeout: float = 30.0) -> str:
        key = self._key(command)
        if key not in self._blocked:
            with self._lock:
                try:
                    result = self.driver.execute_script("lambda-adb", {"command": "shell", "text": command})
                except Exception as exc:
                    _log.info("lambda-adb rejected '%s' (%s); using fallback", key, exc)
                    self._blocked.add(key)
                else:
                    if isinstance(result, dict):
                        result = result.get("output") or result.get("stdout") or result.get("value") or ""
                    text = str(result or "")
                    if "not supported" in text.lower() or "not allowed" in text.lower():
                        self._blocked.add(key)
                    else:
                        return text
        if self.fallback is None:
            raise CommandNotSupportedError(f"'{key}' is not available via lambda-adb")
        return self.fallback.shell(command, timeout)

    def performance_data(self, package: str, data_type: str) -> list[list[Any]] | None:
        if self.fallback is None:
            return super().performance_data(package, data_type)
        return self.fallback.performance_data(package, data_type)
