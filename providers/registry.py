"""Provider plugin registry.

Providers become available in three ways, none of which require core changes:

1. Built-in modules listed in ``BUILTIN_PROVIDER_MODULES`` (lazy-imported).
2. Modules listed in ``framework.plugins`` in ``config/framework.yaml``.
3. Installed packages exposing the ``mobile_perf.providers`` entry point group.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Iterable
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, TypeVar

from utils.logger import get_logger

if TYPE_CHECKING:
    from providers.base import DeviceProvider

ENTRY_POINT_GROUP = "mobile_perf.providers"
BUILTIN_PROVIDER_MODULES: tuple[str, ...] = (
    "providers.testmu_provider",
    "providers.local_provider",
    "providers.simulated_provider",
)

_log = get_logger("registry")
_registry: dict[str, type[DeviceProvider]] = {}
_lock = threading.RLock()
_builtins_loaded = False
_entry_points_loaded = False

P = TypeVar("P", bound="type[DeviceProvider]")


class UnknownProviderError(KeyError):
    pass


def register_provider(name: str, *aliases: str) -> Callable[[P], P]:
    """Class decorator registering a provider under ``name`` (and aliases)."""

    def decorator(cls: P) -> P:
        cls.name = name
        with _lock:
            for key in (name, *aliases):
                key = key.lower()
                existing = _registry.get(key)
                if existing is not None and existing is not cls:
                    _log.warning("Provider '%s' re-registered: %s -> %s", key, existing, cls)
                _registry[key] = cls
        return cls

    return decorator


def load_plugins(modules: Iterable[str]) -> None:
    for module in modules:
        importlib.import_module(module)


def _load_builtins() -> None:
    global _builtins_loaded
    if not _builtins_loaded:
        load_plugins(BUILTIN_PROVIDER_MODULES)
        _builtins_loaded = True


def _load_entry_points() -> None:
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    _entry_points_loaded = True
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            cls = ep.load()
            with _lock:
                _registry.setdefault(ep.name.lower(), cls)
        except Exception:
            _log.exception("Failed to load provider entry point %s", ep.name)


def get_provider_class(name: str) -> type[DeviceProvider]:
    key = name.lower()
    _load_builtins()
    if key not in _registry:
        _load_entry_points()
    try:
        return _registry[key]
    except KeyError:
        raise UnknownProviderError(
            f"Unknown provider '{name}'. Registered providers: {available_providers()}"
        ) from None


def available_providers() -> list[str]:
    _load_builtins()
    _load_entry_points()
    return sorted(_registry)
