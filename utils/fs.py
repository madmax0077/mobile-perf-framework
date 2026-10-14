"""Filesystem helpers shared by sessions, screenshots and report publishing."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from utils.logger import get_logger

_log = get_logger("fs")


def safe_name(value: str, max_len: int = 150) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned[-max_len:] or "test"


def prune_dirs(
    parent: Path, keep: int, *, protect: set[str] | None = None, marker: str | None = None
) -> list[Path]:
    """Delete the oldest sub-directories of ``parent`` (by name, which starts with a timestamp).

    With ``marker``, only directories containing that file are counted and removed.
    """
    if keep <= 0 or not parent.is_dir():
        return []
    protect = protect or set()
    dirs = sorted(
        (d for d in parent.iterdir() if d.is_dir() and (marker is None or (d / marker).exists())),
        key=lambda d: d.name,
    )
    removed: list[Path] = []
    for directory in dirs[: max(0, len(dirs) - keep)]:
        if directory.name in protect:
            continue
        try:
            shutil.rmtree(directory)
            removed.append(directory)
        except OSError as exc:
            _log.warning("Could not remove old run %s: %s", directory, exc)
    return removed
