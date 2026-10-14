"""Factory that resolves and instantiates device providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import FrameworkConfig
from core.events import EventBus
from models.execution_model import Platform
from providers.base import DeviceProvider
from providers.registry import available_providers, get_provider_class, load_plugins


class ProviderFactory:
    """Creates ``DeviceProvider`` instances by name (Factory pattern)."""

    def __init__(self, config: FrameworkConfig, event_bus: EventBus | None = None) -> None:
        self.config = config
        self.event_bus = event_bus or EventBus()
        load_plugins(config.framework.plugins)

    def create(
        self,
        name: str | None = None,
        *,
        device: str | None = None,
        platform: Platform | None = None,
        test_name: str = "perf-test",
        app_override: str | None = None,
        artifacts_dir: Path | None = None,
        option_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> DeviceProvider:
        """``option_overrides`` maps provider name to options deep-merged over its config."""
        provider_cls = get_provider_class(name or self.config.provider.name)
        profile = self.config.device_profile(device, platform)
        return provider_cls(
            self.config,
            profile,
            event_bus=self.event_bus,
            test_name=test_name,
            app_override=app_override,
            artifacts_dir=artifacts_dir,
            option_overrides=(option_overrides or {}).get(provider_cls.name),
        )

    @staticmethod
    def available() -> list[str]:
        return available_providers()
