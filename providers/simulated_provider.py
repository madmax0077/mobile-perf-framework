"""Simulated device provider for CI smoke tests, demos and framework development.

Instead of fabricating metric objects, ``SimulatedExecutor`` emits realistic
*raw command output* (``dumpsys meminfo``, ``top``, ``gfxinfo``, ``am start -W``...).
That keeps the real collectors and parsers on the hot path, so a simulated run
exercises the same code as a device run.
"""

from __future__ import annotations

import base64
import random
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from models.execution_model import DeviceInfo, Platform
from models.telemetry_model import LaunchMode
from providers.base import AppiumDeviceProvider
from providers.registry import register_provider
from telemetry.executors import CommandExecutor
from telemetry.ios.backend import IOSMetricsBackend
from telemetry.startup_collector import AdbLaunchStrategy, IOSBackendLaunchStrategy, LaunchStrategy

DEFAULT_APP_ID = "com.example.perfdemo"
DEFAULT_ACTIVITY = ".MainActivity"
_PID = 23456
_SYSTEM_PID = 1596
_UID = 10234
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class SimulatedDevice:
    """Shared, thread-safe state of the fake device and app."""

    def __init__(self, options: dict[str, Any], package: str, profile: dict[str, float]) -> None:
        self.options = options
        self.package = package
        self.profile = profile
        self.rng = random.Random(int(options.get("seed", 42)))
        self.lock = threading.RLock()
        self.t0 = time.monotonic()
        self.running = False
        self.next_launch = LaunchMode.COLD
        self.last_launch_ms = 0.0
        self.actions = 0
        self.last_action = 0.0
        self.rx = 1_000_000
        self.tx = 200_000
        self.requests: list[dict[str, Any]] = []
        self.logcat: list[str] = []
        self.crash_after = int(options.get("crash_after_actions", 0) or 0)

    def minutes(self) -> float:
        return (time.monotonic() - self.t0) / 60.0

    def noise(self, scale: float) -> float:
        return self.rng.uniform(-scale, scale)

    def on_action(self, name: str) -> None:
        with self.lock:
            self.actions += 1
            self.last_action = time.monotonic()
            self.rx += self.rng.randint(5_000, 60_000)
            self.tx += self.rng.randint(1_000, 8_000)
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[-30:] or "action"
            area = re.sub(r"[^a-z]+", "", re.split(r"[/:.]", name)[-1].lower())[:20] or "app"
            failed = self.rng.random() < 0.03
            self.requests.append(
                {
                    "startedDateTime": datetime.now(UTC).isoformat(),
                    "time": round(max(40.0, self.rng.gauss(260, 90)), 1),
                    "request": {
                        "method": "POST" if self.actions % 3 == 0 else "GET",
                        "url": f"https://api.example.com/v1/{slug}",
                    },
                    "response": {"status": 503 if failed else 200, "bodySize": self.rng.randint(400, 25_000)},
                }
            )
            self._gateway_calls(area)
            self._app_log(area)
            if self.crash_after and self.actions == self.crash_after and self.running:
                self._crash(slug)

    def _gateway_calls(self, area: str) -> None:
        """mPaaS-style RPCs: one gateway URL, the backend method in the form body."""
        now = datetime.now(UTC)
        for k, operation in enumerate(
            [f"com.example.{area}.query", "com.example.user.queryProfile", "com.example.promo.queryBanner"][
                : self.rng.randint(1, 3)
            ]
        ):
            total = round(max(60.0, self.rng.gauss(320, 120)), 1)
            connect, ssl = round(self.rng.uniform(20, 60), 1), round(self.rng.uniform(30, 80), 1)
            server = round(max(10.0, (total - connect - ssl) * self.rng.uniform(0.4, 0.8)), 1)
            self.requests.append(
                {
                    "startedDateTime": (
                        now + timedelta(milliseconds=k * self.rng.randint(15, 90))
                    ).isoformat(),
                    "time": total,
                    "request": {
                        "method": "POST",
                        "url": "https://gw.example.com/mgw.htm",
                        "postData": {
                            "mimeType": "application/x-www-form-urlencoded",
                            "text": f"operationType={operation}&requestData=%5B%5D&ts=1",
                        },
                    },
                    "response": {
                        "status": 200,
                        "bodySize": self.rng.randint(300, 9_000),
                        "headers": [
                            {"name": "x-envoy-upstream-service-time", "value": str(int(server))},
                            {"name": "mgw-traceid", "value": f"{self.rng.getrandbits(64):016x}"},
                        ],
                    },
                    "timings": {
                        "dns": -1,
                        "connect": connect,
                        "ssl": ssl,
                        "send": 1,
                        "wait": round(total - connect - ssl - 3, 1),
                        "receive": 2,
                    },
                }
            )

    def _app_log(self, area: str) -> None:
        stamp = f"{datetime.now(UTC):%m-%d %H:%M:%S.%f}"[:-3]
        area = area.capitalize()
        app, system = f"{_PID} {_PID}", f"{_SYSTEM_PID} {_SYSTEM_PID + 150}"
        if self.rng.random() < 0.35:
            ms = self.rng.randint(120, 900)
            self.logcat.append(
                f"{stamp} {system} I ActivityTaskManager: Displayed {self.package}/.ui.{area}Activity: +{ms}ms"
            )
        if self.rng.random() < 0.15:
            ms = self.rng.randint(40, 400)
            self.logcat += [
                f"{stamp} {app} D StrictMode: StrictMode policy violation; ~duration={ms} ms: "
                "android.os.strictmode.DiskReadViolation",
                f"{stamp} {app} D StrictMode: \tat android.os.StrictMode$AndroidBlockGuardPolicy.onReadFromDisk"
                "(StrictMode.java:1666)",
                f"{stamp} {app} D StrictMode: \tat java.io.FileInputStream.<init>(FileInputStream.java:160)",
                f"{stamp} {app} D StrictMode: \tat {self.package}.data.LocalCache.read(LocalCache.kt:57)",
                f"{stamp} {app} D StrictMode: \tat {self.package}.ui.{area}ViewModel.load({area}ViewModel.kt:31)",
            ]
        if self.rng.random() < 0.1:
            frames = self.rng.randint(31, 90)
            self.logcat.append(
                f"{stamp} {app} I Choreographer: Skipped {frames} frames!  "
                "The application may be doing too much work on its main thread."
            )

    def _crash(self, where: str) -> None:
        stamp = f"{datetime.now(UTC):%m-%d %H:%M:%S.%f}"[:-3]
        lines = [
            "FATAL EXCEPTION: main",
            f"Process: {self.package}, PID: {_PID}",
            f"java.lang.IllegalStateException: simulated crash after '{where}'",
            f"\tat {self.package}.ui.MainActivity.onClick(MainActivity.kt:42)",
            "\tat android.view.View.performClick(View.java:7659)",
        ]
        self.logcat.extend(f"{stamp} {_PID} {_PID} E AndroidRuntime: {line}" for line in lines)
        self.running = False

    # ---- metric generators ------------------------------------------------
    def pss_kb(self) -> int:
        leak = (
            float(self.options.get("leak_mb_per_min", 0.0)) * self.minutes()
            + float(self.options.get("leak_mb_per_action", 0.0)) * self.actions
        )
        mb = float(self.options.get("base_pss_mb", 180)) * self.profile.get("memory", 1.0) + leak
        return int(max(50.0, mb + self.noise(1.5)) * 1024)

    def cpu_raw(self) -> float:
        busy = 18.0 if time.monotonic() - self.last_action < 1.5 else 0.0
        pct = (
            float(self.options.get("base_cpu_pct", 22)) * self.profile.get("cpu", 1.0) + busy + self.noise(4)
        )
        return round(max(0.5, pct) * 8, 1)  # 8 cores, per-core sum like toybox top

    def fps(self) -> float:
        return max(
            20.0,
            min(
                60.0, float(self.options.get("base_fps", 57)) * self.profile.get("fps", 1.0) + self.noise(2.5)
            ),
        )

    def battery_level(self) -> int:
        return max(5, round(92 - self.minutes() * 0.6))


class SimulatedExecutor(CommandExecutor):
    name = "simulated-adb"

    def __init__(self, device: SimulatedDevice) -> None:
        self.device = device
        self._gfx_window = time.monotonic()

    def shell(self, command: str, timeout: float = 30.0) -> str:
        d = self.device
        with d.lock:
            if command.startswith("pidof"):
                return f"{_PID}\n" if d.running else ""
            if command.startswith("top"):
                return self._top() if d.running else ""
            if command.startswith("dumpsys meminfo"):
                return self._meminfo() if d.running else f"No process found for: {d.package}\n"
            if command == "dumpsys battery":
                return self._battery()
            if command.startswith("dumpsys package"):
                return f"Packages:\n  Package [{d.package}] (abc123):\n    userId={_UID}\n"
            if command.startswith("dumpsys batterystats") and "--reset" not in command:
                mah = round(d.minutes() * 4.5 + d.actions * 0.01, 3)
                return (
                    f"Estimated power use (mAh):\n    Capacity: 4500, Computed drain: {mah * 3:.2f}\n"
                    f"    UID u0a{_UID - 10000}: {mah} fg: {mah} ( cpu={mah * 0.7:.3f} )\n"
                )
            if command.startswith("dumpsys gfxinfo"):
                return self._gfxinfo() if d.running else f"No process found for: {d.package}\n"
            if command.startswith("cat /proc/") and command.endswith("/net/dev"):
                d.rx += d.rng.randint(500, 4_000)
                d.tx += d.rng.randint(100, 800)
                return (
                    "Inter-|   Receive                            |  Transmit\n"
                    " face |bytes    packets errs drop fifo frame compressed multicast|bytes packets\n"
                    "    lo:   5000  50 0 0 0 0 0 0   5000  50 0 0 0 0 0 0\n"
                    f" wlan0: {d.rx} 900 0 0 0 0 0 0 {d.tx} 400 0 0 0 0 0 0\n"
                )
            if command.startswith("am force-stop"):
                d.running = False
                d.next_launch = LaunchMode.COLD
                return ""
            if command.startswith("am start"):
                return self._am_start()
            if command == "input keyevent 3":
                d.next_launch = LaunchMode.HOT
                return ""
            if command == "input keyevent 4":
                d.next_launch = LaunchMode.WARM
                return ""
            if command.startswith("logcat -d"):
                ms = int(d.last_launch_ms) + 40
                return f"I ActivityTaskManager: Displayed {d.package}/{DEFAULT_ACTIVITY}: +{ms // 1000}s{ms % 1000:03d}ms\n"
            return ""

    def _top(self) -> str:
        raw = self.device.cpu_raw()
        idle = max(0, int(800 - raw - 120))
        return (
            "Tasks: 1 total,   0 running,   1 sleeping,   0 stopped,   0 zombie\n"
            f"800%cpu  {int(raw)}%user   0%nice  60%sys {idle}%idle   0%iow  10%irq   0%sirq   0%host\n"
            "  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS\n"
            f"{_PID} u0_a{_UID - 10000}      10 -10  15G 210M 110M S {raw:.1f}   2.6   0:12.34 {self.device.package}\n"
        )

    def _meminfo(self) -> str:
        pss = self.device.pss_kb()
        native, dalvik = int(pss * 0.32), int(pss * 0.18)
        return (
            f"Applications Memory Usage (in Kilobytes):\n\n** MEMINFO in pid {_PID} [{self.device.package}] **\n"
            "                   Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap\n"
            "                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free\n"
            "                ------   ------   ------   ------   ------   ------   ------   ------\n"
            f"  Native Heap   {native}   {native - 200}        0        0   {native + 900}   {native + 8000}   {native - 500}     4000\n"
            f"  Dalvik Heap   {dalvik}   {dalvik - 100}        0        0   {dalvik + 500}   {dalvik + 6000}   {dalvik - 300}     3000\n"
            f"        TOTAL   {pss}   {pss - 9000}     3000        0   {pss + 40000}   {native + dalvik}   {native + dalvik - 800}     7000\n\n"
            " App Summary\n                       Pss(KB)                        Rss(KB)\n"
            f"           Java Heap:    {int(dalvik * 1.1)}                          {int(dalvik * 1.3)}\n"
            f"         Native Heap:    {native}                          {native + 900}\n"
            f"           TOTAL PSS:   {pss}            TOTAL RSS:   {pss + 40000}       TOTAL SWAP PSS:      0\n"
        )

    def _battery(self) -> str:
        return (
            "Current Battery Service state:\n  AC powered: false\n  USB powered: false\n"
            f"  Wireless powered: false\n  status: 3\n  level: {self.device.battery_level()}\n  scale: 100\n"
            f"  voltage: {4100 + int(self.device.noise(30))}\n  temperature: {315 + int(self.device.minutes() * 3)}\n"
        )

    def _gfxinfo(self) -> str:
        now = time.monotonic()
        window = max(0.2, now - self._gfx_window)
        self._gfx_window = now
        fps = self.device.fps()
        frames = int(fps * window)
        jank_pct = max(0.5, (60 - fps) * 0.9 + self.device.noise(1.0))
        janky = int(frames * jank_pct / 100)
        return (
            f"Graphics info for pid {_PID} [{self.device.package}]\n"
            f"Total frames rendered: {frames}\nJanky frames: {janky} ({jank_pct:.2f}%)\n"
            f"50th percentile: {int(1000 / max(fps, 1)) - 4}ms\n90th percentile: 14ms\n95th percentile: 19ms\n"
            f"99th percentile: {28 + int(jank_pct)}ms\nNumber Missed Vsync: {janky // 3}\n"
            f"Number Frame deadline missed: {janky}\n"
        )

    def _am_start(self) -> str:
        d = self.device
        base = float(d.options.get("base_cold_start_ms", 1150)) * d.profile.get("startup", 1.0)
        mode = d.next_launch if d.running else LaunchMode.COLD
        factor = {LaunchMode.COLD: 1.0, LaunchMode.WARM: 0.55, LaunchMode.HOT: 0.22}[mode]
        total = max(80, int(base * factor + d.rng.gauss(0, base * factor * 0.06)))
        if mode == LaunchMode.COLD:
            stamp = f"{datetime.now(UTC):%m-%d %H:%M:%S.%f}"[:-3]
            d.logcat.append(
                f"{stamp} {_SYSTEM_PID} {_SYSTEM_PID + 150} I ActivityManager: "
                f"Start proc {_PID}:{d.package}/u0a{_UID - 10000} for top-activity"
            )
        shown = f"{total // 1000}s{total % 1000:03d}ms" if total >= 1000 else f"{total}ms"
        d.logcat.append(
            f"{datetime.now(UTC):%m-%d %H:%M:%S.%f}"[:-3]
            + f" {_SYSTEM_PID} {_SYSTEM_PID + 150} I ActivityTaskManager: "
            f"Displayed {d.package}/{DEFAULT_ACTIVITY}: +{shown}"
        )
        d.running = True
        d.last_launch_ms = total
        d.next_launch = LaunchMode.HOT
        time.sleep(min(total / 1000.0, 0.15))
        return (
            f"Starting: Intent {{ cmp={d.package}/{DEFAULT_ACTIVITY} }}\nStatus: ok\nLaunchState: {mode.value.upper()}\n"
            f"Activity: {d.package}/{DEFAULT_ACTIVITY}\nTotalTime: {total}\nWaitTime: {total + 25}\nComplete\n"
        )


class SimulatedElement:
    """Like Selenium's ``WebElement``: every action is a command sent through the driver."""

    def __init__(self, parent: SimulatedDriver, locator: str) -> None:
        self._parent = parent
        self._locator = locator
        self.text = locator

    def _execute(self, command: str, **params: Any) -> Any:
        return self._parent.execute(command, {"id": self._locator, **params})["value"]

    def click(self) -> None:
        self._execute("clickElement")

    def send_keys(self, *value: Any) -> None:
        self._execute("sendKeysToElement", text="".join(map(str, value)))

    def clear(self) -> None:
        self._execute("clearElement")

    def is_displayed(self) -> bool:
        return bool(self._execute("isElementDisplayed"))

    def is_enabled(self) -> bool:
        return bool(self._execute("isElementEnabled"))


class SimulatedDriver:
    """Minimal stand-in for an Appium WebDriver; commands go through ``execute`` like Selenium's."""

    def __init__(
        self, device: SimulatedDevice, executor: SimulatedExecutor, platform: Platform, delay: float
    ) -> None:
        self._device = device
        self._executor = executor
        self._delay = delay
        self.session_id = f"sim-{int(time.time() * 1000)}"
        self.capabilities = {"platformName": platform.value, "deviceName": "Simulated Device"}

    def execute(self, driver_command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if driver_command in ("findElement", "findElements"):
            time.sleep(self._delay / 5)
            element = SimulatedElement(self, str(params.get("value")))
            return {"value": element if driver_command == "findElement" else [element]}
        if driver_command == "clickElement":
            time.sleep(self._delay)
            self._device.on_action(str(params.get("id")))
        elif driver_command == "sendKeysToElement":
            time.sleep(self._delay / 2)
        elif driver_command in ("isElementDisplayed", "isElementEnabled"):
            return {"value": True}
        return {"value": None}

    def find_element(self, by: str = "id", value: str | None = None) -> SimulatedElement:
        return self.execute("findElement", {"using": by, "value": value})["value"]

    def find_elements(self, by: str = "id", value: str | None = None) -> list[SimulatedElement]:
        return self.execute("findElements", {"using": by, "value": value})["value"]

    def implicitly_wait(self, seconds: float) -> None:
        return None

    def activate_app(self, app_id: str) -> None:
        self._executor.shell(f"am start -W -n {app_id}/{DEFAULT_ACTIVITY}")

    def terminate_app(self, app_id: str) -> bool:
        self._executor.shell(f"am force-stop {app_id}")
        return True

    def get_log(self, log_type: str) -> list[dict[str, Any]]:
        with self._device.lock:
            lines, self._device.logcat = self._device.logcat, []
        return [{"timestamp": int(time.time() * 1000), "level": "ALL", "message": line} for line in lines]

    def query_app_state(self, app_id: str) -> int:
        with self._device.lock:
            return 4 if self._device.running else 1  # Appium ApplicationState values

    def background_app(self, seconds: int) -> None:
        self._executor.shell("input keyevent 3")

    def back(self) -> None:
        self._executor.shell("input keyevent 4")

    def execute_script(self, script: str, *args: Any) -> Any:
        return {}

    def get_screenshot_as_file(self, filename: str) -> bool:
        Path(filename).write_bytes(_PNG_1PX)
        return True

    def quit(self) -> None:
        with self._device.lock:
            self._device.running = False


class SimulatedIOSBackend(IOSMetricsBackend):
    name = "simulated-ios"

    def __init__(self, device: SimulatedDevice) -> None:
        super().__init__(device.package)
        self.device = device

    def cpu_usage(self) -> float | None:
        return round(self.device.cpu_raw() / 8, 2) if self.device.running else None

    def memory_usage_mb(self) -> float | None:
        return round(self.device.pss_kb() / 1024, 2) if self.device.running else None

    def battery_level(self) -> float | None:
        return float(self.device.battery_level())

    def fps(self) -> float | None:
        return round(self.device.fps(), 2) if self.device.running else None

    def prepare_launch(self, mode: LaunchMode) -> None:
        with self.device.lock:
            if mode == LaunchMode.COLD:
                self.device.running = False
            self.device.next_launch = mode

    def measure_launch(self, mode: LaunchMode) -> float | None:
        result = SimulatedExecutor(self.device)._am_start()
        match = re.search(r"TotalTime: (\d+)", result)
        return float(match.group(1)) if match else None


@register_provider("simulated", "sim", "mock")
class SimulatedProvider(AppiumDeviceProvider):
    """Always-available provider producing realistic, deterministic-ish telemetry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        build = (self.app_override or "").removeprefix("simulated://")
        profile = dict(self.options.get("build_profiles", {}).get(build, {}))
        self.sim_device = SimulatedDevice(self.options, self.app_id or DEFAULT_APP_ID, profile)
        self._executor = SimulatedExecutor(self.sim_device)

    @property
    def app_id(self) -> str | None:
        return self.config.app.identifier(self.platform) or DEFAULT_APP_ID

    @property
    def launch_settle_sec(self) -> float:
        return 0.05

    def validate(self) -> None:
        return None

    def create_driver(self) -> Any:
        self.driver = SimulatedDriver(
            self.sim_device, self._executor, self.platform, float(self.options.get("step_delay_sec", 0.05))
        )
        self.driver.activate_app(self.app_id or DEFAULT_APP_ID)
        return self.driver

    def get_command_executor(self) -> CommandExecutor | None:
        return self._executor if self.platform == Platform.ANDROID else None

    def get_ios_backend(self) -> IOSMetricsBackend | None:
        if self.platform != Platform.IOS:
            return None
        if self._ios_backend is None:
            self._ios_backend = SimulatedIOSBackend(self.sim_device)
        return self._ios_backend

    def get_launch_strategy(self) -> LaunchStrategy:
        if self.platform == Platform.IOS:
            return IOSBackendLaunchStrategy(self.get_ios_backend(), self.app_id or DEFAULT_APP_ID)  # type: ignore[arg-type]
        activity = self.config.app.android.activity or DEFAULT_ACTIVITY
        return AdbLaunchStrategy(self._executor, self.app_id or DEFAULT_APP_ID, activity, pause_sec=0.05)

    def load_network_log(self) -> dict[str, Any] | None:
        with self.sim_device.lock:
            entries = list(self.sim_device.requests)
        return {"log": {"version": "1.2", "entries": entries}}

    def device_info(self) -> DeviceInfo:
        info = super().device_info()
        info.udid = "SIMULATED-0001"
        info.model = f"Simulated {self.device.device_name}"
        info.is_real_device = False
        return info
