"""CPU collector: ``top`` on Android (Appium perf-data fallback), backend on iOS."""

from __future__ import annotations

from models.telemetry_model import CpuSnapshot, MetricKind, TelemetrySample
from telemetry.android_helpers import resolve_pid
from telemetry.base_collector import BaseCollector, CollectorUnavailableError, register_collector
from telemetry.executors import CommandNotSupportedError
from telemetry.parsers.android import ParseError, parse_top, perf_table_to_dict


@register_collector("cpu")
class CPUCollector(BaseCollector):
    kind = MetricKind.CPU

    def on_start(self) -> None:
        self._pid: int | None = None
        self._use_perf_api = False

    def collect(self) -> list[TelemetrySample]:
        if not self.is_android:
            value = self.require_ios_backend().cpu_usage()
            if value is None:
                return []
            return [CpuSnapshot(app_cpu_pct=value, **self.context.stamp(self.context.ios_backend.name))]

        executor = self.require_executor()
        package = self.context.app_id
        if not self._use_perf_api:
            try:
                if self._pid is None:
                    self._pid = resolve_pid(executor, package)
                if self._pid is None:
                    return []
                out = executor.shell(f"top -b -n 1 -p {self._pid}")
                info = parse_top(out, pid=self._pid, package=package)
                return [
                    CpuSnapshot(
                        app_cpu_pct=info.app_cpu_pct,
                        app_cpu_raw_pct=info.app_cpu_raw_pct,
                        total_cpu_pct=info.total_cpu_pct,
                        cores=info.cores,
                        **self.context.stamp("top"),
                    )
                ]
            except CommandNotSupportedError:
                self.log.info("top unavailable; switching to Appium getPerformanceData(cpuinfo)")
                self._use_perf_api = True
            except ParseError as exc:
                self.log.debug("top parse failed (%s); re-resolving pid", exc)
                self._pid = None
                return []
        try:
            data = perf_table_to_dict(executor.performance_data(package, "cpuinfo"))
        except CommandNotSupportedError as exc:
            raise CollectorUnavailableError(str(exc)) from exc
        except ParseError:
            return []
        value = data.get("user", 0.0) + data.get("kernel", 0.0)
        return [CpuSnapshot(app_cpu_pct=round(value, 2), **self.context.stamp("appium-perf"))]
