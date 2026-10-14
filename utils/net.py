"""Network helpers."""

from __future__ import annotations

import os

from utils.logger import get_logger

_log = get_logger("net")
_injected = False


def use_system_trust_store() -> bool:
    """Verify TLS against the OS certificate store instead of certifi's bundle.

    Needed behind TLS-inspecting corporate proxies, whose root CA is trusted by
    the OS but not by Python. Disable with ``PERF_SYSTEM_TRUST_STORE=false``.
    """
    global _injected
    if _injected:
        return True
    if os.environ.get("PERF_SYSTEM_TRUST_STORE", "true").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        import truststore
    except ImportError:
        _log.debug("truststore not installed; using certifi CA bundle")
        return False
    truststore.inject_into_ssl()
    _injected = True
    return True
