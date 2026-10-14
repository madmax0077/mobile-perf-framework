# Provider extension guide

Adding a device cloud (BrowserStack, Sauce Labs, Perfecto, HeadSpin, ...) is one new
module plus configuration. No core changes are needed.

## 1. Decide what to inherit

| Your cloud offers | Inherit from | You implement |
| --- | --- | --- |
| Appium hub (almost every cloud) | `AppiumDeviceProvider` | `create_driver`, optionally `build_capabilities`, `get_command_executor`, hooks |
| Something other than Appium | `DeviceProvider` | the four abstract methods yourself |

`AppiumDeviceProvider` already implements `start_telemetry`, `stop_telemetry`,
`get_metrics`, `record`, startup launch strategies and iOS backends on top of
`TelemetryManager`.

## 2. Write the provider

`providers/browserstack_provider.py`:

```python
from __future__ import annotations

from typing import Any

from core.driver_manager import DriverManager
from models.execution_model import Platform
from providers.base import AppiumDeviceProvider, ProviderNotReadyError
from providers.registry import register_provider
from telemetry.executors import AppiumShellExecutor, CommandExecutor


@register_provider("browserstack", "bs")
class BrowserStackProvider(AppiumDeviceProvider):
    def validate(self) -> None:
        if not (self.options.get("username") and self.options.get("access_key")):
            raise ProviderNotReadyError("Set BROWSERSTACK_USERNAME / BROWSERSTACK_ACCESS_KEY")
        super().validate()

    def build_capabilities(self) -> dict[str, Any]:
        return {
            "platformName": self.platform.value,
            "appium:app": self.app_location,
            "bstack:options": {
                "userName": self.options["username"],
                "accessKey": self.options["access_key"],
                "deviceName": self.device.device_name,
                "osVersion": self.device.platform_version,
                "sessionName": self.test_name,
                "appProfiling": True,
                **self.device.providers.get(self.name, {}),
            },
        }

    def create_driver(self) -> Any:
        self.driver = DriverManager(retries=3).create(
            self.options["hub_url"], self.platform, self.build_capabilities()
        )
        return self.driver

    def get_command_executor(self) -> CommandExecutor | None:
        if self.platform != Platform.ANDROID:
            return None
        if not hasattr(self, "_executor"):
            # Shell is usually blocked on clouds; collectors fall back to getPerformanceData.
            self._executor = AppiumShellExecutor(self._require_driver(), allow_shell=False)
        return self._executor

    def mark_session_status(self, passed: bool, reason: str = "") -> None:
        import json

        status = "passed" if passed else "failed"
        payload = {"action": "setSessionStatus", "arguments": {"status": status, "reason": reason[:250]}}
        self.driver.execute_script(f"browserstack_executor: {json.dumps(payload)}")
```

Optional hooks you can override, all with safe defaults:

| Hook | Use it for |
| --- | --- |
| `get_command_executor()` | Cloud-specific shell bridge (see `TestMuAdbExecutor` for a fallback chain) |
| `get_ios_backend()` | Cloud profiler as an `IOSMetricsBackend` |
| `get_launch_strategy()` | Custom cold/warm/hot launch control |
| `load_network_log()` | HAR available during the session |
| `collect_post_session_metrics()` | Cloud profiling data published after the session ends (merged only for missing kinds) |
| `session_metadata()` | Video / dashboard links shown in the report |
| `device_info()` | Real model / UDID from the session capabilities |

## 3. Register it

Pick one of these:

1. **In-repo**: add the module to `BUILTIN_PROVIDER_MODULES` in `providers/registry.py`.
2. **Config-only**: list it in `config/framework.yaml`:
   ```yaml
   framework:
     plugins: [providers.browserstack_provider]
   ```
3. **Separate package**: expose an entry point:
   ```toml
   [project.entry-points."mobile_perf.providers"]
   browserstack = "acme_perf.browserstack:BrowserStackProvider"
   ```

## 4. Configure it

```yaml
# config/framework.yaml
provider:
  providers:
    browserstack:
      hub_url: https://hub-cloud.browserstack.com/wd/hub
      username: ${BROWSERSTACK_USERNAME:-}
      access_key: ${BROWSERSTACK_ACCESS_KEY:-}

# config/devices.yaml - provider-specific device naming
devices:
  pixel_8:
    providers:
      browserstack: {deviceName: Google Pixel 8, osVersion: "14.0"}
```

## 5. Run and verify

```bash
pytest tests/startup --provider browserstack --device pixel_8
pytest tests/unit   # add a unit test like tests/unit/test_providers.py::test_testmu_capabilities
```

Checklist:

- [ ] `validate()` raises `ProviderNotReadyError` (the test is skipped) rather than failing when config is missing.
- [ ] Credentials only come from env vars, never from YAML literals.
- [ ] `build_capabilities()` is unit tested without network access.
- [ ] Executors serialise driver calls (collectors run on several threads).
- [ ] `mark_session_status()` and `quit()` never raise.

## Extending other layers

* **New metric collector**: subclass `BaseCollector`, decorate with
  `@register_collector("thermal")`, import it via `framework.plugins`, then add it to
  `telemetry.collectors` in YAML.
* **New iOS backend**: subclass `IOSMetricsBackend` and decorate it with
  `@register_ios_backend("name")`. Select it with `telemetry.ios_backend`.
* **Event listeners**: `state.event_bus.subscribe(EventType.SAMPLE_COLLECTED, handler)`
  covers live threshold alerts, streaming to InfluxDB or Grafana, and similar uses.
