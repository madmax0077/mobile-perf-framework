"""Stable names and relative links inside a run folder (``reports/runs/<run_id>/``).

Deep links: ``executions/<slug>.html#issue-<n>`` / ``#step-<n>`` / ``#why-step-<n>`` /
``#crash-<n>`` / ``#bench-<rule>`` / ``#operations``, and ``index.html#exec-<slug>`` /
``#issues`` on the run report.
"""

from __future__ import annotations

import os
from pathlib import Path

from models.execution_model import RunSummary, TestExecution
from models.report_model import Issue
from utils.fs import safe_name

EXECUTIONS_DIR = "executions"


def execution_slugs(run: RunSummary) -> list[str]:
    return [f"{i:02d}-{safe_name(e.name, 60)}" for i, e in enumerate(run.executions, 1)]


def execution_page(slug: str, anchor: str | None = None) -> str:
    """Link from the run folder root to an execution page (optionally to an anchor on it)."""
    return f"{EXECUTIONS_DIR}/{slug}.html" + (f"#{anchor}" if anchor else "")


def why_anchor(issue: Issue, execution: TestExecution) -> str | None:
    """Anchor of the root-cause evidence for an issue on its execution page."""
    if issue.target == "operations":
        return "operations"
    if issue.step_index and any(
        b.step_index == issue.step_index and b.primary is not None for b in execution.breakdowns
    ):
        return f"why-step-{issue.step_index}"
    return None


def relative_to(path: str | Path | None, base: Path) -> str | None:
    """POSIX path of ``path`` relative to ``base`` (absolute when on another drive)."""
    if not path:
        return None
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return Path(path).as_posix()
