"""Run-over-run trend history (``reports/history/trend.json``)."""

from __future__ import annotations

import json
from pathlib import Path

from models.execution_model import RunSummary
from models.report_model import TrendPoint

TREND_METRICS = (
    "cold_start_avg_ms",
    "warm_start_avg_ms",
    "peak_pss_mb",
    "avg_cpu_pct",
    "avg_fps",
    "jank_pct",
    "p95_response_ms",
)


def trend_point(run: RunSummary) -> TrendPoint:
    return TrendPoint(
        run_id=run.run_id,
        timestamp=run.started_at,
        label=run.label,
        app_version=run.app.version,
        build_number=run.app.build_number,
        device=run.device.name if run.device else None,
        score=run.score.overall,
        metrics={k: run.metrics.get(k) for k in TREND_METRICS},
    )


class TrendStore:
    def __init__(self, path: Path, max_points: int = 50) -> None:
        self.path = path
        self.max_points = max_points

    def load(self) -> list[TrendPoint]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            return []
        return [TrendPoint.model_validate(p) for p in raw]

    def append(self, point: TrendPoint) -> list[TrendPoint]:
        points = [p for p in self.load() if p.run_id != point.run_id]
        points.append(point)
        points = sorted(points, key=lambda p: p.timestamp)[-self.max_points :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([p.model_dump(mode="json") for p in points], indent=2), encoding="utf-8"
        )
        return points
