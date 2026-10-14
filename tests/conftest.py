"""Fixtures shared by every device-backed test package."""

from __future__ import annotations

import pytest

from core.config import FrameworkConfig
from providers.base import DeviceProvider


@pytest.fixture
def login_credentials(provider: DeviceProvider, perf_config: FrameworkConfig) -> tuple[str, str, str]:
    """(phone, PIN, OTP) for tests that need a signed-in user; skips where they can't run."""
    if provider.name == "simulated":
        pytest.skip("Drives the real app UI; run with --provider testmu or local")
    data = perf_config.app.test_data
    phone, pin, otp = (str(data.get(key) or "") for key in ("phone", "pin", "otp"))
    if not (phone and pin and otp):
        pytest.skip("Set APP_TEST_PHONE, APP_TEST_PIN and APP_TEST_OTP to sign in")
    return phone, pin, otp
