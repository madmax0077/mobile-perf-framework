from __future__ import annotations

import pytest

from core.config import ConfigError, deep_merge, interpolate_env, load_config
from models.execution_model import Platform


def test_env_interpolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERF_X", "value")
    monkeypatch.delenv("PERF_MISSING", raising=False)
    data = {"a": "${PERF_X}", "b": ["${PERF_MISSING:-fallback}", "pre-${PERF_X}-post"], "c": 3}
    assert interpolate_env(data) == {"a": "value", "b": ["fallback", "pre-value-post"], "c": 3}


def test_env_values_cannot_inject_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERF_EVIL", "x\nprovider: {name: hacked}")
    assert interpolate_env({"k": "${PERF_EVIL}"}) == {"k": "x\nprovider: {name: hacked}"}


def test_deep_merge() -> None:
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}, "d": 4}) == {"a": {"b": 1, "c": 3}, "d": 4}


def test_load_shipped_config() -> None:
    cfg = load_config(overrides={"provider": {"name": "testmu", "device": "iphone_15"}})
    assert cfg.provider.name == "testmu"
    assert cfg.device_profile().platform is Platform.IOS
    assert cfg.device_profile(platform=Platform.ANDROID).key == "iphone_15"
    assert sum(cfg.scoring.weights.values()) == 100
    assert cfg.provider_options("testmu")["hub_url"].startswith("https://")


def test_default_device_for_platform() -> None:
    cfg = load_config(overrides={"provider": {"device": ""}})
    assert cfg.device_profile(platform=Platform.IOS).key == "iphone_15"
    assert cfg.device_profile().key == "pixel_8"


def test_unknown_device_raises() -> None:
    cfg = load_config()
    with pytest.raises(ConfigError):
        cfg.device_profile("nope")


@pytest.mark.parametrize(
    "scoring",
    [
        {"weights": {"startup": -5}},
        {"thresholds": {"cpu": {"metric": "avg_cpu_pct", "target": 50, "limit": 50}}},
    ],
)
def test_invalid_config_raises(scoring: dict) -> None:
    with pytest.raises(ConfigError):
        load_config(overrides={"scoring": scoring})
