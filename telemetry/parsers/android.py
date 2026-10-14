"""Pure parsers for Android shell output (no I/O; fully unit-testable)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class ParseError(ValueError):
    pass


def _kb_to_mb(value: float | None) -> float | None:
    return round(value / 1024.0, 3) if value is not None else None


def _search_int(pattern: str, text: str, flags: int = 0) -> int | None:
    match = re.search(pattern, text, flags)
    return int(match.group(1)) if match else None


# ----------------------------------------------------------------- meminfo
@dataclass(slots=True)
class MemInfo:
    pss_mb: float
    rss_mb: float | None = None
    java_heap_mb: float | None = None
    native_heap_mb: float | None = None
    dalvik_heap_mb: float | None = None
    heap_alloc_mb: float | None = None


def _table_row(name: str, text: str) -> list[int]:
    match = re.search(rf"^\s*{re.escape(name)}\s+((?:\d+\s*)+)$", text, re.MULTILINE)
    return [int(n) for n in match.group(1).split()] if match else []


def parse_meminfo(text: str) -> MemInfo:
    """Parse ``dumpsys meminfo <package>`` (Android 8 - 15 layouts)."""
    if "No process found" in text or "MEMINFO" not in text:
        raise ParseError("meminfo: process not running or unexpected output")
    pss = _search_int(r"TOTAL PSS:\s+(\d+)", text)
    if pss is None:
        pss = _search_int(r"^\s*TOTAL\s+(\d+)", text, re.MULTILINE)
    if pss is None:
        pss = _search_int(r"TOTAL:\s+(\d+)", text)
    if pss is None:
        raise ParseError("meminfo: TOTAL PSS not found")

    rss = _search_int(r"TOTAL RSS:\s+(\d+)", text)
    java_heap = _search_int(r"Java Heap:\s+(\d+)", text)
    native_row = _table_row("Native Heap", text)
    dalvik_row = _table_row("Dalvik Heap", text)
    native = native_row[0] if native_row else _search_int(r"Native Heap:\s+(\d+)", text)
    dalvik = dalvik_row[0] if dalvik_row else None

    heap_alloc: int | None = None
    if "Alloc" in text and len(native_row) >= 3:
        heap_alloc = native_row[-2] + (dalvik_row[-2] if len(dalvik_row) >= 3 else 0)

    return MemInfo(
        pss_mb=_kb_to_mb(pss) or 0.0,
        rss_mb=_kb_to_mb(rss),
        java_heap_mb=_kb_to_mb(java_heap),
        native_heap_mb=_kb_to_mb(native),
        dalvik_heap_mb=_kb_to_mb(dalvik),
        heap_alloc_mb=_kb_to_mb(heap_alloc),
    )


# --------------------------------------------------------------------- top
@dataclass(slots=True)
class CpuInfo:
    app_cpu_raw_pct: float
    app_cpu_pct: float
    cores: int | None = None
    total_cpu_pct: float | None = None


def parse_top(text: str, *, pid: int | None = None, package: str | None = None) -> CpuInfo:
    """Parse toybox ``top -b -n 1`` output for one process."""
    lines = text.splitlines()
    cores: int | None = None
    total: float | None = None
    capacity_match = re.search(r"(\d+)%cpu", text)
    if capacity_match:
        capacity = int(capacity_match.group(1))
        cores = max(1, capacity // 100)
        idle = _search_int(r"(\d+)%idle", text)
        if idle is not None and capacity:
            total = round((capacity - idle) / capacity * 100.0, 2)

    header_idx = next(
        (i for i, line in enumerate(lines) if "PID" in line and "CPU" in line.upper() and "%" in line), None
    )
    if header_idx is None:
        raise ParseError("top: header row not found")
    tokens = lines[header_idx].split()
    cpu_col = next((i for i, t in enumerate(tokens) if "%CPU" in t.upper() or "CPU%" in t.upper()), None)
    if cpu_col is None:
        raise ParseError("top: %CPU column not found")
    if "[" in tokens[cpu_col]:  # toybox prints "S[%CPU]" for two data columns
        cpu_col += 1

    for line in lines[header_idx + 1 :]:
        parts = line.split()
        if len(parts) <= cpu_col:
            continue
        if (pid is not None and parts[0] == str(pid)) or (package and parts[-1].startswith(package)):
            try:
                raw = float(parts[cpu_col].rstrip("%"))
            except ValueError as exc:
                raise ParseError(f"top: bad CPU value {parts[cpu_col]!r}") from exc
            normalized = raw / cores if cores else raw
            return CpuInfo(
                app_cpu_raw_pct=raw,
                app_cpu_pct=round(min(normalized, 100.0), 2),
                cores=cores,
                total_cpu_pct=total,
            )
    raise ParseError("top: process row not found")


# ----------------------------------------------------------------- battery
@dataclass(slots=True)
class BatteryInfo:
    level_pct: float
    temperature_c: float | None
    voltage_mv: float | None
    charging: bool


def parse_dumpsys_battery(text: str) -> BatteryInfo:
    level = _search_int(r"^\s*level:\s*(\d+)", text, re.MULTILINE)
    if level is None:
        raise ParseError("battery: level not found")
    scale = _search_int(r"^\s*scale:\s*(\d+)", text, re.MULTILINE) or 100
    temp = _search_int(r"^\s*temperature:\s*(-?\d+)", text, re.MULTILINE)
    voltage = _search_int(r"^\s*voltage:\s*(\d+)", text, re.MULTILINE)
    powered = re.findall(r"(?:AC|USB|Wireless|Dock) powered:\s*(true|false)", text)
    status = _search_int(r"^\s*status:\s*(\d+)", text, re.MULTILINE)
    return BatteryInfo(
        level_pct=round(level / scale * 100.0, 2),
        temperature_c=temp / 10.0 if temp is not None else None,
        voltage_mv=float(voltage) if voltage is not None else None,
        charging="true" in powered or status == 2,
    )


def format_android_uid(uid: int) -> str:
    """10123 -> 'u0a123' (batterystats notation)."""
    user, app_id = divmod(uid, 100_000)
    return f"u{user}a{app_id - 10_000}" if app_id >= 10_000 else str(uid)


def parse_package_uid(text: str) -> int | None:
    return _search_int(r"userId=(\d+)", text)


@dataclass(slots=True)
class BatteryStats:
    app_power_mah: float | None
    capacity_mah: float | None


def parse_batterystats(text: str, uid: str) -> BatteryStats:
    capacity = _search_int(r"Capacity:\s*(\d+)", text)
    match = re.search(rf"uid\s+{re.escape(uid)}:\s+([\d.]+)", text, re.IGNORECASE)
    return BatteryStats(
        app_power_mah=float(match.group(1)) if match else None,
        capacity_mah=float(capacity) if capacity else None,
    )


# ----------------------------------------------------------------- gfxinfo
@dataclass(slots=True)
class GfxInfo:
    total_frames: int
    janky_frames: int
    jank_pct: float
    frame_drops: int
    p50_ms: float | None
    p90_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


def parse_gfxinfo(text: str) -> GfxInfo:
    total = _search_int(r"Total frames rendered:\s*(\d+)", text)
    if total is None:
        raise ParseError("gfxinfo: 'Total frames rendered' not found")
    jank = re.search(r"^\s*Janky frames:\s*(\d+)\s*\(([\d.]+)%\)", text, re.MULTILINE)
    janky = int(jank.group(1)) if jank else 0
    jank_pct = float(jank.group(2)) if jank else 0.0

    def pct(n: int) -> float | None:
        value = _search_int(rf"{n}th percentile:\s*(\d+)ms", text)
        return float(value) if value is not None else None

    deadline = _search_int(r"Number Frame deadline missed:\s*(\d+)", text)
    vsync = _search_int(r"Number Missed Vsync:\s*(\d+)", text)
    return GfxInfo(
        total_frames=total,
        janky_frames=janky,
        jank_pct=jank_pct,
        frame_drops=deadline if deadline is not None else (vsync or 0),
        p50_ms=pct(50),
        p90_ms=pct(90),
        p95_ms=pct(95),
        p99_ms=pct(99),
    )


# ------------------------------------------------------------- am start -W
@dataclass(slots=True)
class AmStartResult:
    total_time_ms: float
    wait_time_ms: float | None
    this_time_ms: float | None
    launch_state: str | None


def parse_am_start(text: str) -> AmStartResult:
    if re.search(r"^Error", text, re.MULTILINE) or "Status: timeout" in text:
        raise ParseError(f"am start failed: {text.strip()[:200]}")
    total = _search_int(r"TotalTime:\s*(\d+)", text)
    if total is None:
        raise ParseError("am start: TotalTime missing (is the activity exported / already on top?)")
    wait = _search_int(r"WaitTime:\s*(\d+)", text)
    this = _search_int(r"ThisTime:\s*(\d+)", text)
    state = re.search(r"LaunchState:\s*(\w+)", text)
    return AmStartResult(
        total_time_ms=float(total),
        wait_time_ms=float(wait) if wait is not None else None,
        this_time_ms=float(this) if this is not None else None,
        launch_state=state.group(1).upper() if state else None,
    )


def parse_displayed_time(text: str, package: str) -> float | None:
    """Parse ``Displayed pkg/.Activity: +1s234ms`` from logcat (last occurrence)."""
    matches = re.findall(rf"Displayed {re.escape(package)}/\S+:\s+\+(?:(\d+)s)?(\d+)ms", text)
    if not matches:
        return None
    seconds, millis = matches[-1]
    return float(int(seconds or 0) * 1000 + int(millis))


# ----------------------------------------------------------------- network
def parse_proc_net_dev(text: str, exclude: tuple[str, ...] = ("lo",)) -> tuple[int, int]:
    """Sum rx/tx bytes over interfaces in ``/proc/<pid>/net/dev``."""
    rx_total = tx_total = 0
    found = False
    for line in text.splitlines():
        if ":" not in line or "|" in line:
            continue
        iface, data = line.split(":", 1)
        if iface.strip() in exclude:
            continue
        fields = data.split()
        if len(fields) < 9:
            continue
        rx_total += int(fields[0])
        tx_total += int(fields[8])
        found = True
    if not found:
        raise ParseError("net/dev: no interfaces parsed")
    return rx_total, tx_total


# ------------------------------------------------ Appium getPerformanceData
def perf_table_to_dict(rows: list[list[Any]] | None) -> dict[str, float]:
    """Convert Appium's ``[[headers...], [values...], ...]`` into the last row as a dict."""
    if not rows or len(rows) < 2:
        raise ParseError("performance data: empty table")
    header, values = rows[0], rows[-1]
    result: dict[str, float] = {}
    for key, value in zip(header, values, strict=False):
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result
