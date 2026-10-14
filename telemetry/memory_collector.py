"""Memory collector: ``dumpsys meminfo`` (Appium perf-data fallback) / iOS backend."""

from __future__ import annotations

from models.telemetry_model import MemorySnapshot, MetricKind, TelemetrySample
from telemetry.base_collector import BaseCollector, CollectorUnavailableError, register_collector
from telemetry.executors import CommandNotSupportedError
from telemetry.parsers.android import ParseError, parse_meminfo, perf_table_to_dict


def _mb(value: float | None) -> float | None:
    return round(value / 1024.0, 3) if value else None


@register_collector("memory")
class MemoryCollector(BaseCollector):
    kind = MetricKind.MEMORY

    def on_start(self) -> None:
        self._use_perf_api = False

    def collect(self) -> list[TelemetrySample]:
        if not self.is_android:
            backend = self.require_ios_backend()
            value = backend.memory_usage_mb()
            if value is None:
                return []
            return [MemorySnapshot(pss_mb=value, **self.context.stamp(backend.name))]

        executor = self.require_executor()
        package = self.context.app_id
        if not self._use_perf_api:
            try:
                info = parse_meminfo(executor.shell(f"dumpsys meminfo {package}"))
                return [
                    MemorySnapshot(
                        pss_mb=info.pss_mb,
                        rss_mb=info.rss_mb,
                        java_heap_mb=info.java_heap_mb,
                        native_heap_mb=info.native_heap_mb,
                        dalvik_heap_mb=info.dalvik_heap_mb,
                        heap_alloc_mb=info.heap_alloc_mb,
                        **self.context.stamp("dumpsys-meminfo"),
                    )
                ]
            except CommandNotSupportedError:
                self.log.info("dumpsys meminfo unavailable; switching to getPerformanceData(memoryinfo)")
                self._use_perf_api = True
            except ParseError:
                return []
        try:
            data = perf_table_to_dict(executor.performance_data(package, "memoryinfo"))
        except CommandNotSupportedError as exc:
            raise CollectorUnavailableError(str(exc)) from exc
        except ParseError:
            return []
        if not data.get("totalPss"):
            return []
        heap_alloc = (data.get("nativeHeapAllocatedSize") or 0) + (data.get("dalvikHeapAllocatedSize") or 0)
        return [
            MemorySnapshot(
                pss_mb=_mb(data.get("totalPss")) or 0.0,
                rss_mb=_mb(data.get("totalRss")),
                native_heap_mb=_mb(data.get("nativePss")),
                dalvik_heap_mb=_mb(data.get("dalvikPss")),
                heap_alloc_mb=_mb(heap_alloc),
                **self.context.stamp("appium-perf"),
            )
        ]
