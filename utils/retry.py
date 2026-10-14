"""Retry helpers built on tenacity."""

from __future__ import annotations

import logging
from collections.abc import Callable

from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.logger import get_logger

_log = get_logger("retry")


def call_with_retry[T](
    func: Callable[[], T],
    *,
    attempts: int = 3,
    initial_wait: float = 2.0,
    max_wait: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Invoke ``func`` with exponential backoff; re-raises the last error."""
    retrying = Retrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_exponential(multiplier=initial_wait, max=max_wait),
        retry=retry_if_exception_type(retry_on),
        before_sleep=before_sleep_log(_log, logging.WARNING),
        reraise=True,
    )
    return retrying(func)
