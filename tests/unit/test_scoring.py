from __future__ import annotations

import pytest

from core.config import ScoringSettings, ThresholdSpec
from core.scoring import ScoringEngine, normalize
from models.report_model import Rating
from models.telemetry_model import TelemetrySummary


@pytest.mark.parametrize(
    ("value", "expected"),
    [(800, 100.0), (500, 100.0), (3000, 0.0), (5000, 0.0), (1900, 50.0)],
)
def test_normalize_lower_is_better(value: float, expected: float) -> None:
    assert normalize(value, ThresholdSpec(metric="m", target=800, limit=3000)) == expected


def test_normalize_higher_is_better() -> None:
    spec = ThresholdSpec(metric="avg_fps", target=58, limit=30)
    assert spec.higher_is_better
    assert normalize(60, spec) == 100.0
    assert normalize(44, spec) == 50.0
    assert normalize(20, spec) == 0.0


@pytest.mark.parametrize(
    ("score", "rating"),
    [
        (95, Rating.EXCELLENT),
        (90, Rating.EXCELLENT),
        (85, Rating.GOOD),
        (70, Rating.ACCEPTABLE),
        (69.9, Rating.NEEDS_IMPROVEMENT),
        (None, Rating.NOT_RATED),
    ],
)
def test_rating_bands(score: float | None, rating: Rating) -> None:
    assert Rating.from_score(score) is rating


def test_weighted_score_and_reweighting() -> None:
    engine = ScoringEngine(ScoringSettings())
    perfect = TelemetrySummary(
        cold_start_avg_ms=500,
        peak_pss_mb=100,
        avg_cpu_pct=10,
        battery_drain_pct_per_hour=1,
        avg_fps=60,
        p95_response_ms=100,
    )
    assert engine.score(perfect).overall == 100.0

    # Only startup (25) and fps (15) have data -> weights re-normalised over 40
    partial = TelemetrySummary(cold_start_avg_ms=1900, avg_fps=60)
    score = engine.score(partial)
    assert score.overall == pytest.approx((50 * 25 + 100 * 15) / 40, abs=0.1)
    assert score.category("startup").effective_weight == pytest.approx(62.5)
    assert score.category("memory").score is None


def test_no_data_is_not_rated() -> None:
    score = ScoringEngine(ScoringSettings()).score(TelemetrySummary())
    assert score.overall is None
    assert score.rating is Rating.NOT_RATED
