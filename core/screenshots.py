"""Step-by-step screenshots, stored with the session artifacts and shown in the drilldown report."""

from __future__ import annotations

from pathlib import Path

from core.config import ReportingSettings, ScreenshotMode
from providers.base import DeviceProvider
from utils.fs import safe_name
from utils.logger import get_logger

_log = get_logger("screenshots")


class ScreenshotRecorder:
    """Numbers screenshots in capture order and downsizes them to JPEG when Pillow is available.

    In ``every_step`` mode, a step that repeats across iterations is captured only the first
    time (plus whenever it fails), so endurance runs do not produce thousands of images.
    """

    def __init__(self, provider: DeviceProvider, directory: Path, settings: ReportingSettings) -> None:
        self.provider = provider
        self.directory = directory
        self.mode = settings.screenshots
        self.max_width = settings.screenshot_max_width
        self.quality = settings.screenshot_quality
        self._count = 0
        self._seen: set[str] = set()

    def wanted(self, name: str, *, failed: bool, repeating: bool = False) -> bool:
        if self.mode == ScreenshotMode.OFF:
            return False
        if failed:
            return True
        if self.mode != ScreenshotMode.EVERY_STEP:
            return False
        return not (repeating and name in self._seen)

    def capture(self, name: str, *, failed: bool = False, repeating: bool = False) -> Path | None:
        if not self.wanted(name, failed=failed, repeating=repeating):
            return None
        self._seen.add(name)
        self._count += 1
        shot = self.provider.take_screenshot(self.directory / f"{self._count:03d}_{safe_name(name, 60)}.png")
        return self._optimise(shot) if shot is not None else None

    def _optimise(self, png: Path) -> Path:
        if not self.max_width:
            return png
        try:
            from PIL import Image
        except ImportError:
            return png
        jpg = png.with_suffix(".jpg")
        try:
            with Image.open(png) as source:
                image = source.convert("RGB")
            if image.width > self.max_width:
                image.thumbnail((self.max_width, image.height))
            image.save(jpg, "JPEG", quality=self.quality, optimize=True)
        except (OSError, ValueError) as exc:
            _log.warning("Keeping original screenshot %s: %s", png.name, exc)
            return png
        png.unlink(missing_ok=True)
        return jpg
