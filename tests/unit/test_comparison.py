from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.comparison import compare_metrics, headline, main
from models.execution_model import AppInfo, RunSummary


def test_compare_metrics_directions() -> None:
    baseline = {"cold_start_avg_ms": 1000, "peak_pss_mb": 200, "avg_cpu_pct": 20, "avg_fps": 60}
    candidate = {"cold_start_avg_ms": 900, "peak_pss_mb": 240, "avg_cpu_pct": 21, "avg_fps": 50}
    result = compare_metrics(
        baseline, candidate, baseline_label="A", candidate_label="B", thresholds={"avg_cpu_pct": 15}
    )

    startup, memory, cpu, fps = (
        result.delta(m) for m in ("cold_start_avg_ms", "peak_pss_mb", "avg_cpu_pct", "avg_fps")
    )
    assert startup.diff_pct == -10.0 and startup.improvement and not startup.regression
    assert memory.diff_pct == 20.0 and memory.regression
    assert cpu.diff_pct == 5.0 and not cpu.regression
    assert fps.diff_pct == -16.67 and fps.regression
    assert not result.passed
    assert headline(result) == {"Startup": -10.0, "Memory": 20.0, "CPU": 5.0, "FPS": -16.67}


def test_missing_metrics_are_skipped() -> None:
    result = compare_metrics({"avg_fps": 60}, {"avg_fps": 60}, baseline_label="A", candidate_label="B")
    assert [d.metric for d in result.deltas] == ["avg_fps"]
    assert result.passed


def _run(path: Path, cold: float) -> Path:
    run = RunSummary(
        run_id=f"r{cold}",
        label="ci",
        started_at=datetime.now(UTC),
        provider="simulated",
        app=AppInfo(name="App", version="1", build_number=str(cold)),
        metrics={"cold_start_avg_ms": cold},
    )
    return run.save(path)


def test_cli_fails_on_regression(tmp_path: Path, capsys) -> None:
    base = _run(tmp_path / "a.json", 1000)
    cand = _run(tmp_path / "b.json", 1300)
    out = tmp_path / "cmp.json"
    assert (
        main(
            ["--baseline", str(base), "--candidate", str(cand), "--output", str(out), "--fail-on-regression"]
        )
        == 1
    )
    assert "REGRESSION" in capsys.readouterr().out
    assert out.exists()
    assert main(["--baseline", str(base), "--candidate", str(base)]) == 0
