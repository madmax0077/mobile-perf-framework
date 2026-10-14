"""Weighted performance scoring (0-100) with ratings."""

from __future__ import annotations

from core.config import ScoringSettings, ThresholdSpec
from models.report_model import CategoryScore, PerformanceScore, Rating
from models.telemetry_model import TelemetrySummary

DEFAULT_THRESHOLDS: dict[str, ThresholdSpec] = {
    "startup": ThresholdSpec(metric="cold_start_avg_ms", target=800, limit=3000),
    "memory": ThresholdSpec(metric="peak_pss_mb", target=150, limit=600),
    "cpu": ThresholdSpec(metric="avg_cpu_pct", target=15, limit=80),
    "battery": ThresholdSpec(metric="battery_drain_pct_per_hour", target=5, limit=30),
    "fps": ThresholdSpec(metric="avg_fps", target=58, limit=30),
    "network": ThresholdSpec(metric="p95_response_ms", target=300, limit=2000),
}


def normalize(value: float, spec: ThresholdSpec) -> float:
    """Linear score: 100 at ``target`` (or better), 0 at ``limit`` (or worse)."""
    ratio = (value - spec.limit) / (spec.target - spec.limit)
    return round(max(0.0, min(1.0, ratio)) * 100.0, 2)


class ScoringEngine:
    """Categories without data are excluded and the remaining weights re-normalised."""

    def __init__(self, settings: ScoringSettings) -> None:
        self.weights = settings.weights
        self.thresholds = {**DEFAULT_THRESHOLDS, **settings.thresholds}

    def score(self, summary: TelemetrySummary) -> PerformanceScore:
        categories: list[CategoryScore] = []
        for category, weight in self.weights.items():
            spec = self.thresholds.get(category)
            if spec is None:
                continue
            value = summary.metric(spec.metric)
            categories.append(
                CategoryScore(
                    category=category,
                    metric=spec.metric,
                    value=value,
                    score=normalize(value, spec) if value is not None else None,
                    weight=weight,
                )
            )
        scored = [c for c in categories if c.score is not None]
        total_weight = sum(c.weight for c in scored)
        if not scored or total_weight <= 0:
            return PerformanceScore(categories=categories)
        for c in scored:
            c.effective_weight = round(c.weight / total_weight * 100.0, 2)
        overall = round(sum(c.score * c.weight for c in scored) / total_weight, 1)  # type: ignore[operator]
        return PerformanceScore(overall=overall, rating=Rating.from_score(overall), categories=categories)
