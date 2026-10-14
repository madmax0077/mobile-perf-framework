from __future__ import annotations

import pytest

from telemetry.parsers.android import (
    ParseError,
    format_android_uid,
    parse_am_start,
    parse_batterystats,
    parse_displayed_time,
    parse_dumpsys_battery,
    parse_gfxinfo,
    parse_meminfo,
    parse_package_uid,
    parse_proc_net_dev,
    parse_top,
    perf_table_to_dict,
)


def test_meminfo_android13(fixture_text) -> None:
    info = parse_meminfo(fixture_text("meminfo_android13.txt"))
    assert info.pss_mb == pytest.approx(150.0)
    assert info.rss_mb == pytest.approx(213.098, rel=1e-3)
    assert info.java_heap_mb == pytest.approx(25.234, rel=1e-3)
    assert info.native_heap_mb == pytest.approx(50.0)
    assert info.dalvik_heap_mb == pytest.approx(20.0)
    assert info.heap_alloc_mb == pytest.approx(84.0)


def test_meminfo_legacy_total_row() -> None:
    text = "** MEMINFO in pid 1 [x] **\n  Native Heap  1024  1000\n        TOTAL   20480   18000\n"
    info = parse_meminfo(text)
    assert info.pss_mb == pytest.approx(20.0)
    assert info.rss_mb is None


def test_meminfo_process_missing() -> None:
    with pytest.raises(ParseError):
        parse_meminfo("No process found for: com.example.app")


def test_top_toybox(fixture_text) -> None:
    info = parse_top(fixture_text("top_toybox.txt"), pid=12345)
    assert info.app_cpu_raw_pct == 120.0
    assert info.cores == 8
    assert info.app_cpu_pct == pytest.approx(15.0)
    assert info.total_cpu_pct == pytest.approx(21.88, abs=0.01)


def test_top_legacy_cpu_percent_header() -> None:
    text = "  PID PR CPU% S  #THR     VSS     RSS PCY UID      Name\n 4321  1  12% S    40 1500000K 90000K  fg u0_a1 com.example.app\n"
    info = parse_top(text, package="com.example.app")
    assert info.app_cpu_pct == 12.0
    assert info.cores is None


def test_top_process_missing(fixture_text) -> None:
    with pytest.raises(ParseError):
        parse_top(fixture_text("top_toybox.txt"), pid=999)


def test_battery(fixture_text) -> None:
    info = parse_dumpsys_battery(fixture_text("battery.txt"))
    assert info.level_pct == 78.0
    assert info.temperature_c == pytest.approx(32.4)
    assert info.voltage_mv == 4012
    assert info.charging is False


def test_batterystats_uid_attribution(fixture_text) -> None:
    assert format_android_uid(10123) == "u0a123"
    assert format_android_uid(1010123) == "u10a123"
    assert parse_package_uid("    userId=10123\n") == 10123
    stats = parse_batterystats(fixture_text("batterystats.txt"), "u0a123")
    assert stats.app_power_mah == pytest.approx(2.48)
    assert stats.capacity_mah == 4614


def test_gfxinfo(fixture_text) -> None:
    info = parse_gfxinfo(fixture_text("gfxinfo.txt"))
    assert info.total_frames == 1200
    assert info.janky_frames == 60
    assert info.jank_pct == 5.0
    assert info.frame_drops == 48
    assert (info.p50_ms, info.p90_ms, info.p95_ms, info.p99_ms) == (8, 14, 19, 42)


def test_am_start(fixture_text) -> None:
    result = parse_am_start(fixture_text("am_start_cold.txt"))
    assert result.total_time_ms == 1234
    assert result.wait_time_ms == 1260
    assert result.launch_state == "COLD"


def test_am_start_error() -> None:
    with pytest.raises(ParseError):
        parse_am_start("Error: Activity class {com.x/.Main} does not exist.")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Displayed com.example.app/.MainActivity: +1s234ms", 1234.0),
        ("Displayed com.example.app/.Main: +456ms", 456.0),
    ],
)
def test_displayed_time(line: str, expected: float) -> None:
    assert parse_displayed_time(f"I ActivityTaskManager: {line}\n", "com.example.app") == expected


def test_proc_net_dev(fixture_text) -> None:
    rx, tx = parse_proc_net_dev(fixture_text("net_dev.txt"))
    assert rx == 5_250_000
    assert tx == 1_250_000


def test_perf_table_to_dict() -> None:
    rows = [["user", "kernel"], ["10.5", "3"]]
    assert perf_table_to_dict(rows) == {"user": 10.5, "kernel": 3.0}
    with pytest.raises(ParseError):
        perf_table_to_dict([])
