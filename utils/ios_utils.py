"""Helpers for iOS tooling (xcrun, xctrace, devicectl). macOS only."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from utils.logger import get_logger

_log = get_logger("ios")


class IOSToolError(RuntimeError):
    """Raised when an Xcode command-line tool fails or is unavailable."""


def is_macos() -> bool:
    return platform.system() == "Darwin"


def xcrun_available() -> bool:
    return is_macos() and shutil.which("xcrun") is not None


def run_xcrun(*args: str, timeout: float = 120) -> str:
    if not xcrun_available():
        raise IOSToolError("xcrun is only available on macOS with Xcode installed")
    cmd = ["xcrun", *args]
    _log.debug("xcrun: %s", " ".join(cmd))
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IOSToolError(f"xcrun failed: {exc}") from exc
    if completed.returncode != 0:
        raise IOSToolError(
            f"xcrun {' '.join(args)} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def start_xctrace_record(
    template: str,
    output: Path,
    *,
    device: str | None,
    attach: str | None = None,
    time_limit_sec: int | None = None,
) -> subprocess.Popen[str]:
    """Start ``xctrace record`` in the background; stop it with ``stop_process``."""
    if not xcrun_available():
        raise IOSToolError("xctrace requires macOS with Xcode installed")
    cmd = ["xcrun", "xctrace", "record", "--template", template, "--output", str(output)]
    if device:
        cmd += ["--device", device]
    cmd += ["--attach", attach] if attach else ["--all-processes"]
    if time_limit_sec:
        cmd += ["--time-limit", f"{time_limit_sec}s"]
    _log.info("Starting xctrace: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop_process(proc: subprocess.Popen[str], timeout: float = 60) -> None:
    import signal

    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()


def export_xctrace_table(trace: Path, schema: str) -> str:
    xpath = f'/trace-toc/run[@number="1"]/data/table[@schema="{schema}"]'
    return run_xcrun("xctrace", "export", "--input", str(trace), "--xpath", xpath, timeout=300)
