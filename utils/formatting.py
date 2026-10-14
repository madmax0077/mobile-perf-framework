"""Human-readable formatting of metric values."""

from __future__ import annotations


def fmt_value(value: float | None, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "ms" and value >= 1000:
        return f"{value / 1000:.2f} s"
    text = f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}" if unit in ("%", "%/h") else f"{text} {unit}".strip()
