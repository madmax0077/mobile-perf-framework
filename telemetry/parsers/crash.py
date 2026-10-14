"""Crash / ANR extraction from Android logcat (``-v threadtime``) output.

Recognised signatures, filtered to the app's own processes:

* Java crash  - ``E AndroidRuntime: FATAL EXCEPTION`` + ``Process: <pkg>, PID: n``
* ANR         - ``E ActivityManager: ANR in <pkg>`` (+ PID / Reason lines)
* Native crash - ``F libc: Fatal signal ... pid n (<pkg>)`` + ``DEBUG`` tombstone lines
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from models.telemetry_model import CrashEvent, CrashType

_LINE = re.compile(
    r"^(?P<time>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFA])\s+(?P<tag>[^:]*?)\s*:\s?(?P<msg>.*)$"
)
_PROCESS = re.compile(r"Process: (?P<process>[\w.:]+), PID: (?P<pid>\d+)")
_ANR = re.compile(r"ANR in (?P<process>[\w.:]+)")
_NATIVE = re.compile(r"Fatal signal \d+ \((?P<signal>\w+)\).*?pid (?P<pid>\d+) \((?P<process>[^)]*)\)")
_COMM_LEN = 15  # Linux truncates process names in some native crash lines


@dataclass(slots=True)
class LogLine:
    time: str
    pid: int
    level: str
    tag: str
    msg: str
    raw: str


@dataclass
class _Block:
    crash_type: CrashType
    start: LogLine
    lines: list[str] = field(default_factory=list)
    process: str | None = None
    pid: int | None = None
    exception: str | None = None


def parse_logcat_line(line: str) -> LogLine | None:
    match = _LINE.match(line.rstrip("\r\n"))
    if not match:
        return None
    return LogLine(
        time=match["time"],
        pid=int(match["pid"]),
        level=match["level"],
        tag=match["tag"].strip(),
        msg=match["msg"],
        raw=line.rstrip("\r\n"),
    )


def device_time_to_utc(value: str, year: int | None = None) -> datetime | None:
    """Logcat timestamps carry no year/zone; TestMu devices log in UTC."""
    try:
        parsed = datetime.strptime(f"{year or datetime.now(UTC).year}-{value}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def _belongs_to(process: str | None, package: str) -> bool:
    if not package:
        return True
    if not process:
        return False
    if process == package or process.startswith(f"{package}:"):
        return True
    return len(process) == _COMM_LEN and package.startswith(process)


def parse_logcat_crashes(text: str, package: str, *, max_lines: int = 60) -> list[CrashEvent]:
    """Return crash events for ``package`` (all processes when ``package`` is empty)."""
    blocks: list[_Block] = []
    java_open: dict[int, _Block] = {}
    anr_open: _Block | None = None
    native_open: _Block | None = None

    for raw in text.splitlines():
        line = parse_logcat_line(raw)
        if line is None:
            continue
        msg = line.msg

        if line.tag == "AndroidRuntime":
            if msg.startswith("FATAL EXCEPTION"):
                block = _Block(CrashType.JAVA_CRASH, line, [line.raw])
                java_open[line.pid] = block
                blocks.append(block)
                continue
            block = java_open.get(line.pid)
            if block is not None and len(block.lines) < max_lines:
                block.lines.append(line.raw)
                if (m := _PROCESS.search(msg)) and block.process is None:
                    block.process, block.pid = m["process"], int(m["pid"])
                elif block.exception is None and block.process is not None and msg.strip():
                    block.exception = msg.strip()
            continue
        java_open.pop(line.pid, None)

        if line.tag == "ActivityManager":
            if m := _ANR.search(msg):
                anr_open = _Block(CrashType.ANR, line, [line.raw], process=m["process"])
                blocks.append(anr_open)
                continue
            # The ANR report is a single multi-line log call: its lines share one timestamp.
            same_report = anr_open is not None and (line.pid, line.time) == (
                anr_open.start.pid,
                anr_open.start.time,
            )
            if anr_open is not None and same_report and len(anr_open.lines) < max_lines:
                anr_open.lines.append(line.raw)
                stripped = msg.strip()
                if stripped.startswith("PID:") and anr_open.pid is None:
                    anr_open.pid = int(re.sub(r"\D", "", stripped) or 0) or None
                elif stripped.startswith("Reason:") and anr_open.exception is None:
                    anr_open.exception = f"ANR - {stripped.removeprefix('Reason:').strip()}"
                continue

        if line.tag == "libc" and (m := _NATIVE.search(msg)):
            native_open = _Block(
                CrashType.NATIVE_CRASH,
                line,
                [line.raw],
                process=m["process"],
                pid=int(m["pid"]),
                exception=msg.strip(),
            )
            blocks.append(native_open)
            continue
        if native_open is not None and line.tag == "DEBUG" and len(native_open.lines) < max_lines:
            native_open.lines.append(line.raw)
            continue

    events: list[CrashEvent] = []
    for block in blocks:
        if not _belongs_to(block.process, package):
            continue
        if block.crash_type == CrashType.ANR and block.exception is None:
            block.exception = f"ANR in {block.process}"
        timestamp = device_time_to_utc(block.start.time)
        events.append(
            CrashEvent(
                crash_type=block.crash_type,
                process=block.process,
                pid=block.pid,
                exception=block.exception,
                stacktrace="\n".join(block.lines),
                device_time=block.start.time,
                source="logcat",
                **({"timestamp": timestamp} if timestamp else {}),
            )
        )
    return events


def parse_crash_report(text: str, package: str, *, source: str, max_lines: int = 60) -> list[CrashEvent]:
    """Parse a provider crash report: logcat-formatted, or a raw Java stack trace."""
    events = parse_logcat_crashes(text, package, max_lines=max_lines)
    if events or not text.strip():
        return events
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    exception = next((ln.strip() for ln in lines if "Exception" in ln or "Error" in ln), lines[0].strip())
    return [
        CrashEvent(
            crash_type=CrashType.JAVA_CRASH,
            process=package or None,
            exception=exception[:500],
            stacktrace="\n".join(lines[:max_lines]),
            source=source,
        )
    ]
