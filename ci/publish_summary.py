"""Render reports/metrics.json as Markdown (CI job summaries / PR comments).

python ci/publish_summary.py reports/metrics.json >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

KPIS = [
    ("Cold start (avg)", "cold_start_avg_ms", "ms"),
    ("Warm start (avg)", "warm_start_avg_ms", "ms"),
    ("Peak memory (PSS)", "peak_pss_mb", "MB"),
    ("Average CPU", "avg_cpu_pct", "%"),
    ("Average FPS", "avg_fps", "fps"),
    ("Jank rate", "jank_pct", "%"),
    ("API p95", "p95_response_ms", "ms"),
    ("Battery drain / h", "battery_drain_pct_per_hour", "%"),
]


def render(metrics: dict) -> str:
    score = metrics.get("score", {})
    overall = score.get("overall")
    lines = [
        f"## Mobile performance - {metrics.get('label')} ({metrics.get('run_id')})",
        "",
        f"**Overall score:** {overall if overall is not None else 'n/a'} / 100 - **{score.get('rating')}**",
        "",
        f"**Production readiness:** {metrics.get('readiness', 'Not Evaluated')}"
        + "".join(
            f" - {b['title']} {b['status'].upper()}"
            for b in metrics.get("benchmarks", [])
            if b.get("status") in ("fail", "warn")
        ),
        "",
        "| KPI | Value |",
        "| --- | ---: |",
    ]
    values = metrics.get("metrics", {})
    for label, key, unit in KPIS:
        value = values.get(key)
        lines.append(f"| {label} | {f'{value:,.1f} {unit}' if isinstance(value, int | float) else '-'} |")
    for comparison in metrics.get("comparisons", []):
        regressions = [d["metric"] for d in comparison.get("deltas", []) if d.get("regression")]
        verdict = "PASS" if not regressions else f"REGRESSION: {', '.join(regressions)}"
        lines += ["", f"**{comparison['candidate_label']} vs {comparison['baseline_label']}:** {verdict}"]
    lines += ["", "| Test | Status | Score | Issues |", "| --- | --- | ---: | --- |"]
    issues = []
    for execution in metrics.get("executions", []):
        found = execution.get("issues", [])
        counts = ", ".join(
            f"{n} {sev}"
            for sev in ("critical", "major", "minor")
            if (n := sum(i["severity"] == sev for i in found))
        )
        lines.append(
            f"| {execution['name']} | {execution['status']} | {execution['score'].get('overall') or '-'} "
            f"| {counts or '-'} |"
        )
        issues += [(execution["name"], i) for i in found if i["severity"] != "minor"]
    if issues:
        lines += ["", "**Critical / major issues** (details in the HTML report drilldown):", ""]
        lines += [
            f"- `{i['severity']}` {name}: {i['title']}"
            + (f" - cause: `{i['cause']}`" if i.get("cause") else "")
            for name, i in issues[:20]
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "reports/metrics.json")
    if not path.exists():
        print(f"_No performance metrics found at {path}_")
        return 0
    print(render(json.loads(path.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
