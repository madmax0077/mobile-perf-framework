"""Thin, testable wrapper around the ``adb`` binary."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from utils.logger import get_logger

_log = get_logger("adb")


class AdbError(RuntimeError):
    """Raised when an adb invocation fails."""


@dataclass(slots=True)
class AdbClient:
    adb_path: str = "adb"
    serial: str | None = None
    default_timeout: float = 30.0

    def is_available(self) -> bool:
        return shutil.which(self.adb_path) is not None

    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, *args: str, timeout: float | None = None) -> str:
        cmd = [*self._base(), *args]
        _log.debug("adb: %s", " ".join(cmd))
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.default_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdbError(f"adb command failed: {' '.join(cmd)}: {exc}") from exc
        if completed.returncode != 0:
            raise AdbError(
                f"adb exited with {completed.returncode}: {' '.join(cmd)}: {completed.stderr.strip()}"
            )
        return completed.stdout

    def shell(self, command: str, timeout: float | None = None) -> str:
        return self.run("shell", command, timeout=timeout)

    def devices(self) -> list[str]:
        out = self.run("devices")
        return [
            line.split()[0] for line in out.splitlines()[1:] if line.strip() and line.split()[-1] == "device"
        ]

    def getprop(self, name: str) -> str:
        return self.shell(f"getprop {name}").strip()

    def pidof(self, package: str) -> int | None:
        out = self.shell(f"pidof {package}").strip()
        return int(out.split()[0]) if out else None

    def install(self, apk_path: str, timeout: float = 300) -> None:
        self.run("install", "-r", "-g", apk_path, timeout=timeout)
