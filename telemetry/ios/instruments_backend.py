"""Instruments backend via Appium XCUITest ``mobile: startPerfRecord``.

Requires the Appium server to run with ``--allow-insecure perf_record``. The
recorded ``.trace`` is saved as an artifact and parsed when ``xcrun`` is available.
"""

from __future__ import annotations

import base64
import io
import time
import zipfile
from datetime import UTC, datetime

from models.telemetry_model import TelemetrySample
from telemetry.ios.backend import register_ios_backend
from telemetry.ios.xctrace_backend import trace_to_samples
from telemetry.ios.xcuitest_backend import XCUITestMetricsBackend
from utils.ios_utils import xcrun_available

PROFILE = "Activity Monitor"


@register_ios_backend("instruments")
class InstrumentsBackend(XCUITestMetricsBackend):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._recording = False
        self._started_at = datetime.now(UTC)

    def start_session(self) -> None:
        try:
            self._exec(
                "mobile: startPerfRecord",
                {"profileName": PROFILE, "pid": "current", "timeout": 30 * 60 * 1000},
            )
            self._recording = True
            self._started_at = datetime.now(UTC)
        except Exception as exc:
            self.log.warning("startPerfRecord failed (is perf_record allowed?): %s", exc)

    def stop_session(self) -> list[TelemetrySample]:
        if not self._recording:
            return []
        self._recording = False
        try:
            payload = self._exec("mobile: stopPerfRecord", {"profileName": PROFILE})
        except Exception as exc:
            self.log.warning("stopPerfRecord failed: %s", exc)
            return []
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        target = self.artifacts_dir / f"instruments-{int(time.time())}"
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as archive:
            archive.extractall(target)
        traces = list(target.rglob("*.trace"))
        self.log.info("Instruments trace saved to %s", target)
        if not traces or not xcrun_available():
            return []
        try:
            process = str(self.options.get("process_name") or self.bundle_id.rsplit(".", 1)[-1])
            return trace_to_samples(traces[0], process, self._started_at, self.name)
        except Exception as exc:
            self.log.warning("Could not parse Instruments trace: %s", exc)
            return []
