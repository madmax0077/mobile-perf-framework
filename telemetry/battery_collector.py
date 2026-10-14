"""Battery collector: ``dumpsys battery`` samples + ``batterystats`` attribution."""

from __future__ import annotations

import time

from models.telemetry_model import BatterySnapshot, BatteryUsage, MetricKind, TelemetrySample
from telemetry.base_collector import BaseCollector, CollectorUnavailableError, register_collector
from telemetry.executors import CommandError, CommandNotSupportedError
from telemetry.parsers.android import (
    ParseError,
    format_android_uid,
    parse_batterystats,
    parse_dumpsys_battery,
    parse_package_uid,
    perf_table_to_dict,
)


@register_collector("battery")
class BatteryCollector(BaseCollector):
    kind = MetricKind.BATTERY

    def on_start(self) -> None:
        self._use_perf_api = False
        self._unplugged = False
        self._uid: str | None = None
        self._start_level: float | None = None
        self._last_level: float | None = None
        self._started = time.monotonic()
        if not self.is_android:
            return
        executor = self.require_executor()
        settings = self.context.settings
        try:
            if settings is None or settings.battery_simulate_unplug:
                executor.shell("dumpsys battery unplug")
                self._unplugged = True
            executor.shell("dumpsys batterystats --reset")
            uid = parse_package_uid(executor.shell(f"dumpsys package {self.context.app_id}"))
            self._uid = format_android_uid(uid) if uid is not None else None
        except CommandError as exc:
            self.log.info("batterystats attribution unavailable: %s", exc)

    def _sample(self) -> BatterySnapshot | None:
        if not self.is_android:
            backend = self.require_ios_backend()
            level = backend.battery_level()
            return (
                BatterySnapshot(level_pct=level, **self.context.stamp(backend.name))
                if level is not None
                else None
            )

        executor = self.require_executor()
        if not self._use_perf_api:
            try:
                info = parse_dumpsys_battery(executor.shell("dumpsys battery"))
                return BatterySnapshot(
                    level_pct=info.level_pct,
                    temperature_c=info.temperature_c,
                    voltage_mv=info.voltage_mv,
                    charging=info.charging,
                    **self.context.stamp("dumpsys-battery"),
                )
            except CommandNotSupportedError:
                self._use_perf_api = True
            except ParseError:
                return None
        try:
            data = perf_table_to_dict(executor.performance_data(self.context.app_id, "batteryinfo"))
        except CommandNotSupportedError as exc:
            raise CollectorUnavailableError(str(exc)) from exc
        except ParseError:
            return None
        level = data.get("power")
        return (
            BatterySnapshot(level_pct=level, **self.context.stamp("appium-perf"))
            if level is not None
            else None
        )

    def collect(self) -> list[TelemetrySample]:
        sample = self._sample()
        if sample is None:
            return []
        if self._start_level is None:
            self._start_level = sample.level_pct
        self._last_level = sample.level_pct
        return [sample]

    def on_stop(self) -> list[TelemetrySample]:
        duration = time.monotonic() - self._started
        level_drop = (
            max(0.0, self._start_level - self._last_level)
            if self._start_level is not None and self._last_level is not None
            else None
        )
        usage = BatteryUsage(
            drain_pct=level_drop, duration_sec=round(duration, 2), **self.context.stamp("battery")
        )
        if self.is_android and self.context.executor is not None:
            executor = self.context.executor
            try:
                if self._uid:
                    stats = parse_batterystats(
                        executor.shell(f"dumpsys batterystats {self.context.app_id}", timeout=60), self._uid
                    )
                    usage.app_power_mah = stats.app_power_mah
                    usage.capacity_mah = stats.capacity_mah
                    if stats.app_power_mah is not None and stats.capacity_mah:
                        usage.drain_pct = round(stats.app_power_mah / stats.capacity_mah * 100.0, 4)
                    usage.source = "batterystats"
            except CommandError as exc:
                self.log.info("batterystats read failed: %s", exc)
            finally:
                if self._unplugged:
                    try:
                        executor.shell("dumpsys battery reset")
                    except CommandError:
                        self.log.warning(
                            "Could not reset battery state (run 'adb shell dumpsys battery reset')"
                        )
        return [usage]
