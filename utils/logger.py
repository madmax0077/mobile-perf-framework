"""Central logging configuration for the framework."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT_LOGGER = "mobile_perf"
_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s - %(message)s"
_configured = False


def configure_logging(
    level: str | int = "INFO", log_file: Path | None = None, *, console: bool = True
) -> None:
    """Configure the framework logger once. Safe to call multiple times.

    Under pytest pass ``console=False``: records propagate to pytest's own
    logging capture / ``log_cli`` instead of being printed twice.
    """
    global _configured
    root = logging.getLogger(_ROOT_LOGGER)
    root.setLevel(level)
    if console and not _configured:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)
        root.propagate = True
        _configured = True
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not any(
            isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_file.resolve()
            for h in root.handlers
        ):
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            root.addHandler(file_handler)


def release_log_file(log_file: Path) -> None:
    """Close and detach the file handler for ``log_file`` (so the file can be deleted)."""
    root = logging.getLogger(_ROOT_LOGGER)
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_file.resolve():
            root.removeHandler(handler)
            handler.close()


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the framework root logger."""
    return logging.getLogger(f"{_ROOT_LOGGER}.{name}")
