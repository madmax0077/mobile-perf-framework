from __future__ import annotations

from pathlib import Path

import pytest

from core.config import FrameworkConfig, load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return read


@pytest.fixture
def unit_config(tmp_path: Path) -> FrameworkConfig:
    """Real YAML config with reports redirected to a temp dir."""
    return load_config(
        overrides={"framework": {"report_dir": str(tmp_path / "reports")}, "provider": {"name": "simulated"}}
    )
