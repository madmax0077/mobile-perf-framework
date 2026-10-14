"""Network collector: throughput from ``/proc/<pid>/net/dev`` (or Appium
``networkinfo``) plus API timings / failures from the session HAR."""

from __future__ import annotations

import time

from models.telemetry_model import MetricKind, NetworkSnapshot, TelemetrySample
from telemetry.android_helpers import resolve_pid
from telemetry.base_collector import BaseCollector, CollectorUnavailableError, register_collector
from telemetry.executors import CommandNotSupportedError
from telemetry.parsers.android import ParseError, parse_proc_net_dev, perf_table_to_dict
from telemetry.parsers.har import parse_har


@register_collector("network")
class NetworkCollector(BaseCollector):
    kind = MetricKind.NETWORK

    def on_start(self) -> None:
        self._pid: int | None = None
        self._use_perf_api = False
        self._baseline: tuple[int, int] | None = None
        self._last: tuple[float, int, int] | None = None

    def _read_bytes(self) -> tuple[int, int] | None:
        if not self.is_android:
            return self.require_ios_backend().network_bytes()
        executor = self.require_executor()
        package = self.context.app_id
        if not self._use_perf_api:
            try:
                if self._pid is None:
                    self._pid = resolve_pid(executor, package)
                if self._pid is None:
                    return None
                return parse_proc_net_dev(executor.shell(f"cat /proc/{self._pid}/net/dev"))
            except CommandNotSupportedError:
                self._use_perf_api = True
            except ParseError:
                self._pid = None
                return None
        try:
            data = perf_table_to_dict(executor.performance_data(package, "networkinfo"))
        except CommandNotSupportedError as exc:
            raise CollectorUnavailableError(str(exc)) from exc
        except ParseError:
            return None
        if "rxBytes" not in data:
            return None
        return int(data["rxBytes"]), int(data.get("txBytes", 0))

    def collect(self) -> list[TelemetrySample]:
        reading = self._read_bytes()
        if reading is None:
            return []
        now = time.monotonic()
        if self._baseline is None:
            self._baseline = reading
        rx, tx = reading[0] - self._baseline[0], reading[1] - self._baseline[1]
        rx_kbps = tx_kbps = 0.0
        if self._last is not None:
            dt = max(now - self._last[0], 1e-3)
            rx_kbps = max(0.0, (rx - self._last[1]) * 8 / 1000 / dt)
            tx_kbps = max(0.0, (tx - self._last[2]) * 8 / 1000 / dt)
        self._last = (now, rx, tx)
        return [
            NetworkSnapshot(
                rx_bytes=max(rx, 0),
                tx_bytes=max(tx, 0),
                rx_kbps=round(rx_kbps, 2),
                tx_kbps=round(tx_kbps, 2),
                **self.context.stamp("net-dev" if not self._use_perf_api else "appium-perf"),
            )
        ]

    def on_stop(self) -> list[TelemetrySample]:
        if self.context.har_loader is None:
            return []
        try:
            har = self.context.har_loader()
        except Exception as exc:
            self.log.warning("Could not load network log: %s", exc)
            return []
        return list(parse_har(har)) if har else []
