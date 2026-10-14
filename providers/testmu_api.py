"""REST client for TestMu AI (formerly LambdaTest) app-automation APIs.

All base URLs come from ``config/framework.yaml`` so the client works against
either the legacy ``lambdatest.com`` or the ``testmu.ai`` domains.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from models.telemetry_model import (
    BatterySnapshot,
    CpuSnapshot,
    FpsSnapshot,
    LaunchMode,
    MemorySnapshot,
    StartupMetrics,
    TelemetrySample,
)
from utils.logger import get_logger

_log = get_logger("testmu.api")


class TestMuApiError(RuntimeError):
    __test__ = False


class TestMuApiClient:
    __test__ = False

    def __init__(
        self,
        username: str,
        access_key: str,
        *,
        api_base_url: str,
        app_upload_url: str,
        timeout: float = 60.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.app_upload_url = app_upload_url
        self.timeout = timeout
        self._session = requests.Session()
        self._session.auth = (username, access_key)

    def _request(self, path: str) -> requests.Response:
        url = f"{self.api_base_url}/{path.lstrip('/')}"
        response = self._session.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            raise TestMuApiError(f"GET {path} -> {response.status_code}: {response.text[:300]}")
        return response

    def _get(self, path: str) -> Any:
        response = self._request(path)
        try:
            return response.json()
        except ValueError:
            return response.text

    def session_details(self, session_id: str) -> dict[str, Any]:
        data = self._get(f"sessions/{session_id}")
        return data.get("data", data) if isinstance(data, dict) else {}

    def app_metrics(self, session_id: str) -> Any:
        """App profiling metrics (requires ``appProfiling: true``)."""
        return self._get(f"sessions/{session_id}/log/appmetrics")

    def network_log(self, session_id: str) -> dict[str, Any] | None:
        """Network log (HAR) of the session (requires ``network: true``).

        TestMu returns a bare list of HAR entries; it is wrapped into a HAR document.
        """
        data = self._get(f"sessions/{session_id}/log/network")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        if isinstance(data, list):
            return {"log": {"entries": data}}
        if isinstance(data, dict) and "log" not in data and isinstance(data.get("data"), dict):
            data = data["data"]
        return data if isinstance(data, dict) and "log" in data else None

    def device_log(self, session_id: str) -> str:
        """Device log (Android logcat ``threadtime``) of the session (requires ``devicelog: true``)."""
        return self._request(f"sessions/{session_id}/log/devicelog").text

    def crash_log(self, session_id: str) -> str:
        """Crash log of the session; empty when the app did not crash. Served gzip/tar-compressed."""
        return _decompress_text(self._request(f"sessions/{session_id}/log/crashlog").content)

    def upload_app(self, app_path: Path, *, name: str | None = None, cache_file: Path | None = None) -> str:
        """Upload an .apk/.ipa and return its ``lt://`` URL. Cached by file hash."""
        digest = hashlib.sha256(app_path.read_bytes()).hexdigest()
        cache: dict[str, str] = {}
        if cache_file and cache_file.exists():
            cache = json.loads(cache_file.read_text(encoding="utf-8") or "{}")
            if digest in cache:
                _log.info("Reusing uploaded app %s -> %s", app_path.name, cache[digest])
                return cache[digest]
        _log.info("Uploading %s to TestMu AI", app_path)
        with app_path.open("rb") as handle:
            response = self._session.post(
                self.app_upload_url,
                files={"appFile": (app_path.name, handle)},
                data={"name": name or app_path.stem},
                timeout=max(self.timeout, 600),
            )
        if response.status_code >= 400:
            raise TestMuApiError(f"App upload failed {response.status_code}: {response.text[:300]}")
        app_url = response.json().get("app_url")
        if not app_url:
            raise TestMuApiError(f"App upload response missing app_url: {response.text[:300]}")
        if cache_file:
            cache[digest] = app_url
            cache_file.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        return app_url

    def poll(self, func: Any, *, retries: int, delay_sec: float) -> Any:
        """Retry a fetch that only succeeds once TestMu has post-processed the session."""
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                result = func()
                if result:
                    return result
            except (TestMuApiError, requests.RequestException) as exc:
                last_exc = exc
            _log.debug("TestMu data not ready (attempt %d/%d)", attempt, retries)
            time.sleep(delay_sec)
        if last_exc:
            _log.warning("TestMu fetch failed after %d attempts: %s", retries, last_exc)
        return None


# --------------------------------------------------------------------------
# App profiling payload mapping
# --------------------------------------------------------------------------
_TS_KEYS = ("timestamp", "time", "ts", "created_at", "datetime")


def _match(key: str) -> str | None:
    k = key.lower()
    if "cpu" in k:
        return "cpu"
    if "fps" in k or "frame" in k:
        return "fps"
    if "temp" in k:
        return "temperature"
    if "battery" in k:
        return "battery"
    if "mem" in k or "pss" in k:
        return "memory"
    return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%").split()[0])
        except (ValueError, IndexError):
            return None
    if isinstance(value, dict):
        for key in ("value", "usage", "avg", "total"):
            if key in value:
                return _to_float(value[key])
    return None


def _timestamp(row: dict[str, Any]) -> Any:
    for key in _TS_KEYS:
        if key in row:
            value = row[key]
            if isinstance(value, int | float) and value > 1e12:
                return value / 1000.0
            return value
    return None


def _make_sample(metric: str, value: float, ts: Any) -> TelemetrySample | None:
    extra: dict[str, Any] = {"source": "testmu_app_profiling"}
    if ts is not None:
        extra["timestamp"] = ts
    try:
        if metric == "cpu":
            return CpuSnapshot(app_cpu_pct=value, **extra)
        if metric == "memory":
            mb = value / 1024.0 if value > 20_000 else value  # KB payloads -> MB
            return MemorySnapshot(pss_mb=mb, **extra)
        if metric == "fps":
            return FpsSnapshot(fps=value, **extra)
        if metric == "battery":
            return BatterySnapshot(level_pct=value, **extra)
    except ValueError:
        return None
    return None


def _decompress_text(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if len(raw) > 262 and raw[257:262] == b"ustar":
        parts: list[str] = []
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                handle = archive.extractfile(member) if member.isfile() else None
                if handle is not None:
                    parts.append(handle.read().decode("utf-8", errors="replace"))
        return "\n".join(parts)
    return raw.decode("utf-8", errors="replace")


def _is_testmu_series(payload: Any) -> bool:
    return isinstance(payload, dict) and any(
        isinstance(payload.get(k), list)
        and payload[k]
        and isinstance(payload[k][0], dict)
        and "time" in payload[k][0]
        for k in ("cpu", "mem")
    )


def _map_testmu_series(payload: dict[str, Any], start: datetime | None) -> list[TelemetrySample]:
    """TestMu app profiling: ``{cpu: [{app, sys, time}], mem: [...], fps: [{count, time}], meta: {...}}``.

    ``time`` is seconds since session start; ``cpu.app`` is %, ``mem.app`` is MB.
    """
    origin = start or datetime.fromtimestamp(0, tz=UTC)

    def stamp(point: dict[str, Any]) -> dict[str, Any]:
        offset = float(point.get("time") or 0)
        return {
            "timestamp": origin + timedelta(seconds=offset),
            "elapsed_sec": offset,
            "source": "testmu_app_profiling",
        }

    samples: list[TelemetrySample] = []
    for point in payload.get("cpu") or []:
        if (app := _to_float(point.get("app"))) is not None:
            samples.append(
                CpuSnapshot(app_cpu_pct=app, total_cpu_pct=_to_float(point.get("sys")), **stamp(point))
            )
    for point in payload.get("mem") or []:
        if (app := _to_float(point.get("app"))) is not None:
            samples.append(MemorySnapshot(pss_mb=app, **stamp(point)))
    janky = {p.get("time"): p for p in payload.get("frames") or []}
    for point in payload.get("fps") or []:
        count = _to_float(point.get("count"))
        if count:  # 0 = nothing rendered (static screen), not a frame drop
            frames = janky.get(point.get("time"), {})
            janky_frames = int(frames.get("janky") or 0)
            samples.append(
                FpsSnapshot(
                    fps=min(count, 120.0),
                    total_frames=int(count),
                    janky_frames=janky_frames,
                    jank_pct=round(janky_frames / count * 100.0, 2) if count else 0.0,
                    frame_drops=int(frames.get("frozen") or 0),
                    **stamp(point),
                )
            )
    meta = payload.get("meta") or {}
    for key, mode in (("coldStartup", LaunchMode.COLD), ("hotStartup", LaunchMode.HOT)):
        if (value := _to_float(meta.get(key))) and value > 0:
            samples.append(
                StartupMetrics(
                    mode=mode,
                    iteration=1,
                    total_time_ms=value,
                    method="testmu_app_profiling",
                    source="testmu_app_profiling",
                    **({"timestamp": origin} if start else {}),
                )
            )
    return samples


def app_metrics_crash_info(payload: Any) -> tuple[int, int, str]:
    """``(crashCount, anrCount, anr_logs)`` from the TestMu app-profiling ``meta`` block."""
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return 0, 0, ""
    return int(meta.get("crashCount") or 0), int(meta.get("anrCount") or 0), str(meta.get("anr_logs") or "")


def map_app_metrics(payload: Any, start: datetime | None = None) -> list[TelemetrySample]:
    """Map the TestMu app-profiling payload into samples.

    Handles TestMu's series format (see ``_map_testmu_series``), plus generic
    row-oriented (``[{timestamp, cpu, memory, fps}]``) and series-oriented
    (``{cpu: [{timestamp, value}], ...}``) shapes, optionally wrapped in ``{"data": ...}``.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if _is_testmu_series(payload):
        return _map_testmu_series(payload, start)
    samples: list[TelemetrySample] = []
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            ts = _timestamp(row)
            for key, raw in row.items():
                metric = _match(key)
                value = _to_float(raw)
                if metric and value is not None and (sample := _make_sample(metric, value, ts)):
                    samples.append(sample)
    elif isinstance(payload, dict):
        for key, series in payload.items():
            metric = _match(key)
            if not metric or not isinstance(series, list):
                continue
            for point in series:
                if isinstance(point, dict):
                    value, ts = _to_float(point), _timestamp(point)
                elif isinstance(point, list | tuple) and len(point) >= 2:
                    ts, value = point[0], _to_float(point[1])
                else:
                    continue
                if value is not None and (sample := _make_sample(metric, value, ts)):
                    samples.append(sample)
    return samples
