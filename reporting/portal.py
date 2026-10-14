"""Run history portal (``reports/index.html``): one row per run folder under ``reports/runs``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from models.execution_model import RunSummary
from models.report_model import Severity
from utils.logger import get_logger

INDEX_FILE = "run_index.json"
_log = get_logger("report")


class RunIndexEntry(BaseModel):
    run_id: str
    label: str
    started_at: datetime
    duration_sec: float
    provider: str
    device: str | None = None
    app_version: str
    build_number: str
    tests: dict[str, int] = Field(default_factory=dict, description="Executions per status")
    crashes: int = 0
    issues: dict[str, int] = Field(default_factory=dict, description="Issues per severity")
    score: float | None = None
    rating: str
    readiness: str
    report: str = Field(description="Run report, relative to the reports root")

    @classmethod
    def from_run(cls, run: RunSummary) -> RunIndexEntry:
        tests: dict[str, int] = {}
        for execution in run.executions:
            tests[execution.status.value] = tests.get(execution.status.value, 0) + 1
        issues = [i for e in run.executions for i in e.issues]
        return cls(
            run_id=run.run_id,
            label=run.label,
            started_at=run.started_at,
            duration_sec=round(run.duration_sec, 1),
            provider=run.provider,
            device=f"{run.device.name} ({run.device.platform.value} {run.device.os_version})"
            if run.device
            else None,
            app_version=run.app.version,
            build_number=run.app.build_number,
            tests=tests,
            crashes=sum(len(e.telemetry.crashes) for e in run.executions),
            issues={s.value: sum(1 for i in issues if i.severity == s) for s in Severity},
            score=run.score.overall,
            rating=run.score.rating.value,
            readiness=run.readiness.value,
            report=f"runs/{run.run_id}/index.html",
        )


def write_entry(run: RunSummary, run_dir: Path) -> RunIndexEntry:
    entry = RunIndexEntry.from_run(run)
    (run_dir / INDEX_FILE).write_text(entry.model_dump_json(indent=2), encoding="utf-8")
    return entry


def load_entries(report_dir: Path) -> list[RunIndexEntry]:
    entries = []
    for path in (report_dir / "runs").glob(f"*/{INDEX_FILE}"):
        try:
            entries.append(RunIndexEntry.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError) as exc:
            _log.warning("Skipping unreadable run index %s: %s", path, exc)
    return sorted(entries, key=lambda e: e.started_at, reverse=True)
