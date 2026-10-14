"""Login smoke test: Profile -> Sign in -> phone -> PIN -> OTP.

Runs as a journey test, so every step is timed, live telemetry is collected, and
the crash monitor checks the app after each step. TestMu app profiling stays on
(``lt_options.appProfiling``), so cloud CPU/memory/FPS and crash counters are
merged into the report after the session.
"""

from __future__ import annotations

import re

import pytest

from core.config import FrameworkConfig
from core.recorder import PerfRecorder
from providers.base import DeviceProvider
from utils.logger import get_logger

_log = get_logger("login")


@pytest.mark.journey
def test_login_with_phone_pin_otp(
    pages, provider: DeviceProvider, perf_recorder: PerfRecorder, perf_config: FrameworkConfig
) -> None:
    if provider.name == "simulated":
        pytest.skip("The login flow drives the real app UI; run with --provider testmu or local")
    data = perf_config.app.test_data
    phone, pin, otp = (str(data.get(key) or "") for key in ("phone", "pin", "otp"))
    if not (phone and pin and otp):
        pytest.skip("Set APP_TEST_PHONE, APP_TEST_PIN and APP_TEST_OTP to run the login smoke test")

    with perf_recorder.step("launch_app"):
        pages.home.wait_loaded()
    with perf_recorder.step("open_profile"):
        pages.home.open_profile()
    with perf_recorder.step("open_sign_in"):
        pages.profile.start_sign_in()
    with perf_recorder.step("enter_phone"):
        pages.login.enter_phone(phone)
    with perf_recorder.step("submit_phone"):
        pages.login.submit_phone()
    with perf_recorder.step("enter_pin"):
        pages.login.enter_pin(pin)
    with perf_recorder.step("submit_pin"):
        pages.login.submit_pin()
    with perf_recorder.step("enter_otp"):
        if not pages.login.enter_otp(otp):
            _log.info("OTP not requested: the app trusts this device and went to the biometrics prompt")
    with perf_recorder.step("biometrics_prompt", measure=False):
        pages.login.dismiss_biometrics_prompt()
    with perf_recorder.step("home_after_login"):
        pages.wait_signed_in(pin, settle=0)
    with perf_recorder.step("pin_recheck", measure=False):
        pages.wait_signed_in(pin)
    with perf_recorder.step("verify_profile"):
        pages.home.open_profile()
        shown = pages.profile.phone_number()

    assert re.sub(r"\D", "", shown).endswith(phone), f"profile shows {shown!r}, expected {phone}"
