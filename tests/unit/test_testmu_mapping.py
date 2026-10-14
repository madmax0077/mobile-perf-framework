from __future__ import annotations

from models.telemetry_model import CpuSnapshot, FpsSnapshot, MemorySnapshot
from providers.testmu_api import map_app_metrics
from telemetry.parsers.har import parse_har


def test_row_oriented_payload() -> None:
    payload = {
        "data": [
            {"timestamp": 1767225600000, "cpu": 12.5, "memory": 180.2, "fps": 58},
            {"timestamp": 1767225601000, "cpu": "14%", "memory": 182.0, "fps": 57},
        ]
    }
    samples = map_app_metrics(payload)
    assert sum(isinstance(s, CpuSnapshot) for s in samples) == 2
    assert sum(isinstance(s, MemorySnapshot) for s in samples) == 2
    assert sum(isinstance(s, FpsSnapshot) for s in samples) == 2
    assert next(s for s in samples if isinstance(s, CpuSnapshot) and s.app_cpu_pct == 14.0)


def test_series_oriented_payload_with_kb_memory() -> None:
    payload = {
        "memory_usage": [{"time": "2026-01-01T00:00:00Z", "value": 204800}],
        "cpu_usage": [["2026-01-01T00:00:00Z", 30]],
    }
    samples = map_app_metrics(payload)
    memory = next(s for s in samples if isinstance(s, MemorySnapshot))
    assert memory.pss_mb == 200.0
    assert next(s for s in samples if isinstance(s, CpuSnapshot)).app_cpu_pct == 30.0


def test_unknown_payload_is_ignored() -> None:
    assert map_app_metrics({"message": "processing"}) == []
    assert map_app_metrics("not json") == []


def test_parse_har() -> None:
    har = {
        "log": {
            "entries": [
                {
                    "startedDateTime": "2026-01-01T00:00:00.000Z",
                    "time": 210.5,
                    "request": {"method": "GET", "url": "https://api.example.com/v1/search"},
                    "response": {"status": 200, "bodySize": 1200},
                },
                {
                    "time": 900,
                    "request": {"method": "POST", "url": "https://api.example.com/v1/checkout"},
                    "response": {"status": 502, "bodySize": -1},
                },
            ]
        }
    }
    requests = parse_har(har)
    assert [r.failed for r in requests] == [False, True]
    assert requests[0].duration_ms == 210.5
    assert requests[1].size_bytes == 0
    assert parse_har(har, url_filter="checkout")[0].method == "POST"
