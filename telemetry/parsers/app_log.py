"""App-side slowness evidence from Android logcat (``threadtime`` format).

Each signal names the class or method involved, so a slow step can be traced to
app code rather than just "the app was slow":

* ``ActivityTaskManager: Displayed pkg/.HomeActivity: +1s234ms`` -> screen class and draw time
* ``StrictMode policy violation`` stacks -> the app method doing disk / network I/O
  (main-thread violations block the UI)
* ``Looper: Slow dispatch took 312ms main h=<Handler> c=<Callback>`` -> handler class
* ``Choreographer: Skipped 45 frames!`` -> main thread blocked for ~45 x 16.7 ms
* ``OpenGLRenderer: Davey! duration=812ms`` -> one very slow frame

Only processes of the app under test are considered; their pids are learned from
``Start proc`` lines, crash headers and ART's process-name log tag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from models.telemetry_model import AppSignal, AppSignalType

_LINE = re.compile(
    r"^(?P<time>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFA])\s+(?P<tag>[^:]*?)\s*:\s?(?P<msg>.*)$"
)
_DISPLAYED = re.compile(
    r"Displayed (?P<pkg>[\w.]+)/(?P<activity>[\w.$]+)(?: for user \d+)?: \+(?P<time>[\dsm]+)"
)
_START_PROC = re.compile(r"Start proc (?P<pid>\d+):(?P<process>[\w.:]+)")
_PROCESS = re.compile(r"Process: (?P<process>[\w.:]+), PID: (?P<pid>\d+)")
_SKIPPED = re.compile(r"Skipped (?P<frames>\d+) frames!")
_SLOW_DISPATCH = re.compile(
    r"Slow (?P<what>dispatch|delivery) took (?P<ms>\d+)ms (?P<thread>\S+) "
    r"h=(?P<handler>\S+) c=(?P<callback>\S+)"
)
_SLOW_LOOPER = re.compile(
    r"Slow Looper (?P<thread>\S+): .*?took (?P<ms>\d+)ms.*?h=(?P<handler>\S+) c=(?P<callback>\S+)"
)
_DAVEY = re.compile(r"Davey! duration=(?P<ms>\d+)ms")
_VIOLATION = re.compile(r"StrictMode policy violation(?:; ~duration=(?P<ms>\d+) ms)?: (?P<type>[\w.$]+)")
_FRAME = re.compile(r"^\s*at (?P<frame>[\w.$<>]+)\((?P<file>[^)]*)\)")
_OBFUSCATED = re.compile(r"^(?:[a-z]{1,2}\d{0,2}\.)+[A-Za-z]{1,2}\d{0,2}\.[\w$]+$")
PLATFORM_PREFIXES = (
    "android.",
    "androidx.",
    "java.",
    "javax.",
    "jdk.",
    "kotlin.",
    "kotlinx.",
    "dalvik.",
    "libcore.",
    "sun.",
    "com.android.",
    "com.google.android.",
    "org.apache.harmony.",
)
FRAME_MS = 1000.0 / 60.0
MAX_FRAMES = 8


def _duration(text: str) -> float | None:
    match = re.fullmatch(r"(?:(\d+)s)?(\d+)ms", text)
    if not match:
        return None
    return int(match.group(1) or 0) * 1000.0 + int(match.group(2))


def _short_class(name: str) -> str:
    return name.rsplit(".", 1)[-1] if name not in ("null", "") else ""


def app_frame(frames: Iterable[str], app_packages: Iterable[str] = ()) -> str | None:
    """The most specific app method in a stack: an ``app_packages`` frame, else the first
    non-platform, non-obfuscated frame."""
    frames = list(frames)
    packages = tuple(p.rstrip(".") + "." for p in app_packages if p)
    if packages:
        for frame in frames:
            if frame.startswith(packages):
                return frame
    for frame in frames:
        method = frame.split("(", 1)[0]
        if not method.startswith(PLATFORM_PREFIXES) and not _OBFUSCATED.match(method):
            return frame
    return None


class AppLogAnalyzer:
    """Stateful: keeps the app's pids between the log chunks read after each step."""

    def __init__(self, package: str, app_packages: Iterable[str] = ()) -> None:
        self.package = package
        self.app_packages = [p for p in app_packages if p] or ([package] if package else [])
        self.pids: set[str] = set()
        self.screen: str | None = None
        self._art_tag = package[-15:] if package else ""

    def _learn(self, pid: str, tag: str, msg: str) -> None:
        if not self.package:
            return
        for pattern in (_START_PROC, _PROCESS):
            match = pattern.search(msg)
            if match and match["process"].split(":")[0] == self.package:
                self.pids.add(match["pid"])
        if self._art_tag and tag == self._art_tag:
            self.pids.add(pid)

    def feed(self, text: str | None) -> list[AppSignal]:
        if not text:
            return []
        parsed = [m for m in (_LINE.match(line) for line in text.splitlines()) if m]
        for m in parsed:
            self._learn(m["pid"], m["tag"], m["msg"])
        signals: list[AppSignal] = []
        violation: _Violation | None = None
        violations: list[_Violation] = []
        for m in parsed:
            pid, tid, tag, msg = m["pid"], m["tid"], m["tag"], m["msg"]
            if tag == "StrictMode":
                start = _VIOLATION.search(msg)
                if start:
                    violation = _Violation(
                        type=start["type"].rsplit(".", 1)[-1],
                        ms=float(start["ms"]) if start["ms"] else None,
                        main=pid == tid,
                        time=m["time"],
                        pid=pid,
                    )
                    violations.append(violation)
                    continue
                frame = _FRAME.match(msg)
                if violation is not None and frame and violation.pid == pid:
                    violation.frames.append(f"{frame['frame']}({frame['file']})")
                continue
            violation = None
            if (displayed := _DISPLAYED.search(msg)) and displayed["pkg"] == self.package:
                activity = displayed["activity"]
                if activity.startswith("."):
                    activity = self.package + activity
                self.screen = activity
                signals.append(
                    AppSignal(
                        type=AppSignalType.SCREEN,
                        name=activity,
                        duration_ms=_duration(displayed["time"]),
                        detail=f"{_short_class(activity)} displayed in +{displayed['time']}",
                        device_time=m["time"],
                    )
                )
                continue
            if pid not in self.pids:
                continue
            shown = f" while {_short_class(self.screen)} was shown" if self.screen else ""
            if skipped := _SKIPPED.search(msg):
                frames = int(skipped["frames"])
                signals.append(
                    AppSignal(
                        type=AppSignalType.SKIPPED_FRAMES,
                        name=self.screen or "main thread",
                        duration_ms=round(frames * FRAME_MS, 1),
                        main_thread=True,
                        detail=f"Skipped {frames} frames - the main thread was blocked{shown}",
                        device_time=m["time"],
                    )
                )
            elif looper := (_SLOW_DISPATCH.search(msg) or _SLOW_LOOPER.search(msg)):
                handler, callback = looper["handler"], looper["callback"]
                name = callback if callback not in ("null", "") else handler
                what = looper.groupdict().get("what") or "dispatch"
                signals.append(
                    AppSignal(
                        type=AppSignalType.SLOW_MAIN_THREAD,
                        name=name,
                        duration_ms=float(looper["ms"]),
                        main_thread=looper["thread"] == "main",
                        detail=f"Slow {what} on {looper['thread']} thread (handler {handler})",
                        device_time=m["time"],
                    )
                )
            elif davey := _DAVEY.search(msg):
                signals.append(
                    AppSignal(
                        type=AppSignalType.SLOW_FRAME,
                        name=self.screen or "render thread",
                        duration_ms=float(davey["ms"]),
                        detail=f"One frame took longer than 700 ms to draw (Davey){shown}",
                        device_time=m["time"],
                    )
                )
        signals += self._violations(violations)
        return signals

    def _violations(self, violations: list[_Violation]) -> list[AppSignal]:
        grouped: dict[tuple[str, str, bool], AppSignal] = {}
        for v in violations:
            if self.pids and v.pid not in self.pids:
                continue
            culprit = app_frame(v.frames, self.app_packages) or (v.frames[0] if v.frames else "unknown")
            key = (v.type, culprit, v.main)
            signal = grouped.get(key)
            if signal is None:
                grouped[key] = AppSignal(
                    type=AppSignalType.STRICT_MODE,
                    name=culprit.split("(", 1)[0],
                    duration_ms=v.ms,
                    main_thread=v.main,
                    detail=f"{v.type} on {'the main' if v.main else 'a background'} thread at {culprit}",
                    frames=v.frames[:MAX_FRAMES],
                    device_time=v.time,
                )
            else:
                signal.count += 1
                if v.ms is not None:
                    signal.duration_ms = (signal.duration_ms or 0.0) + v.ms
        return list(grouped.values())


@dataclass
class _Violation:
    type: str
    ms: float | None
    main: bool
    time: str
    pid: str
    frames: list[str] = field(default_factory=list)
