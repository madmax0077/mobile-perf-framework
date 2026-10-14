"""Parser for ``xctrace export`` table XML (handles ``id`` / ``ref`` de-duplication)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

CPU_MNEMONICS = ("cpu-percent", "cpu-total", "cpu-usage")
MEMORY_MNEMONICS = (
    "memory-real-footprint",
    "physical-footprint",
    "resident-size",
    "memory-physical-footprint",
)
PROCESS_MNEMONICS = ("process",)
TIME_MNEMONICS = ("start", "start-time", "time")


@dataclass(slots=True)
class ProcessSample:
    offset_sec: float
    cpu_pct: float | None
    memory_mb: float | None


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return None


def parse_activity_monitor(xml_text: str, process_filter: str) -> list[ProcessSample]:
    root = ET.fromstring(xml_text)
    mnemonics = [m.text or "" for m in root.iter("mnemonic")]
    if not mnemonics:
        return []
    by_id: dict[str, ET.Element] = {}
    for element in root.iter():
        if "id" in element.attrib:
            by_id[element.attrib["id"]] = element

    def resolve(element: ET.Element) -> ET.Element:
        ref = element.attrib.get("ref")
        return by_id.get(ref, element) if ref else element

    def col(names: tuple[str, ...]) -> int | None:
        return next((mnemonics.index(n) for n in names if n in mnemonics), None)

    cpu_i, mem_i, proc_i, time_i = (
        col(CPU_MNEMONICS),
        col(MEMORY_MNEMONICS),
        col(PROCESS_MNEMONICS),
        col(TIME_MNEMONICS),
    )
    samples: list[ProcessSample] = []
    for row in root.iter("row"):
        cells = [resolve(c) for c in row]
        if proc_i is not None and proc_i < len(cells):
            label = cells[proc_i].attrib.get("fmt", "") + (cells[proc_i].text or "")
            if process_filter.lower() not in label.lower():
                continue
        offset = _num(cells[time_i].text) if time_i is not None and time_i < len(cells) else None
        cpu = _num(cells[cpu_i].text) if cpu_i is not None and cpu_i < len(cells) else None
        mem = _num(cells[mem_i].text) if mem_i is not None and mem_i < len(cells) else None
        samples.append(
            ProcessSample(
                offset_sec=(offset or 0.0) / 1e9,
                cpu_pct=cpu,
                memory_mb=round(mem / (1024 * 1024), 3) if mem is not None else None,
            )
        )
    return samples
