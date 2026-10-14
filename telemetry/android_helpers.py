"""Small helpers shared by Android collectors."""

from __future__ import annotations

from telemetry.executors import CommandError, CommandExecutor


def resolve_pid(executor: CommandExecutor, package: str) -> int | None:
    """Return the app's main pid, or ``None`` when the app is not running."""
    try:
        out = executor.shell(f"pidof {package}").strip()
    except CommandError:
        out = ""
    if out:
        try:
            return int(out.split()[0])
        except ValueError:
            pass
    ps = executor.shell("ps -A -o PID,NAME")
    for line in ps.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == package and parts[0].isdigit():
            return int(parts[0])
    return None
