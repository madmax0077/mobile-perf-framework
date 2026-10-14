# Mobile Performance Testing Framework

A provider-agnostic, pytest-based framework for measuring mobile app performance
(startup, memory, CPU, battery, FPS, network) on Android and iOS. Tests run on the
**TestMu AI** (formerly LambdaTest) real-device cloud, a local Appium server, or a
built-in **simulated** provider that needs no device at all.

Every run gets its own report folder (`reports/runs/<run_id>/`) with an interactive HTML
dashboard, a drilldown page per test (step-by-step screenshots, timeline, issues, crashes),
a production-readiness verdict, a versioned `performance_data.json` export with CSV files,
and raw JSONL telemetry. `reports/index.html` lists all runs.

- Architecture, UML and threading model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Adding a new device cloud (BrowserStack, Sauce Labs, ...): [docs/PROVIDER_EXTENSION_GUIDE.md](docs/PROVIDER_EXTENSION_GUIDE.md)

## Quick start

Requires Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env                # then fill in what you need

# No device needed: run everything against the simulated provider
pytest tests --provider simulated
start reports\index.html
```

## Providers

| Provider    | Use it for                                   | Required settings                                   |
|-------------|----------------------------------------------|-----------------------------------------------------|
| `simulated` | Demos, CI smoke runs, framework development  | none                                                |
| `local`     | Emulators / USB devices via local Appium 2.x | `APPIUM_URL`, `adb` on PATH, app package/path       |
| `testmu`    | TestMu AI real-device cloud                  | `TESTMU_USERNAME`, `TESTMU_ACCESS_KEY`, app location|

Select a provider with `--provider` or `PERF_PROVIDER`, and a device profile from
`config/devices.yaml` with `--device` or `PERF_DEVICE`:

```powershell
pytest tests/startup --provider local --device pixel_8
pytest tests/startup --provider testmu --device galaxy_s24 --platform android
```

For TestMu, `ANDROID_APP` / `IOS_APP` can be an `lt://APP...` id, an http(s) URL, or a
local `.apk`/`.ipa` path. Local files are uploaded once and cached by SHA-256 in
`.app_upload_cache.json`. TestMu blocks some shell commands (`top`, `am`) on public devices,
so the framework falls back to Appium `getPerformanceData`, driver-based launch timing, and
TestMu's app-profiling API (collected after the session ends).

Credentials are read only from environment variables or `.env`; never commit them.

To change provider options for a single test, use the `provider_options` marker. It is
keyed by provider name and deep-merged over `config/framework.yaml` for that test only:

```python
@pytest.mark.journey
@pytest.mark.provider_options(testmu={"lt_options": {"network": False, "appProfiling": False}})
def test_check_orders(...): ...
```

These Rebell UAT mini programs need this: Offers, Orders, AI Assistant, Stores, Loyalty
cards and the daily shortcuts (Top up phone, Pay bills, Save on utilities). Cards &
accounts, Send & request, transaction details and the notification centre work while
instrumented. When TestMu
instruments the app for network capture (`network`) or app profiling (`appProfiling`), the
mPaaS config gateway (`imgs-ac.alipay.com`) rejects the call with 7014 "rpc signature
check: sign content is null", and the mini program shows "Error: 10003". Those tests run
without a HAR or TestMu app metrics; Appium telemetry still runs. Excluding hosts with
`networkLogsOptions` would avoid this, but it is not included in the current TestMu plan.

Behind a TLS-inspecting corporate proxy, HTTPS calls fail with "self-signed certificate in
certificate chain". The framework verifies TLS against the OS certificate store (via
`truststore`) to handle this; set `PERF_SYSTEM_TRUST_STORE=false` to use certifi's bundle instead.

### Sample app placeholders

The sample tests target a generic app. Locators live in `config/app.yaml`
(`locators.<page>.<element>.<platform>: [strategy, value]`). Any test that uses a locator
you have not configured is **skipped**, not failed, so you can wire up your app incrementally.

## Running each test type

| Test type          | Command                                                   | Notes |
|--------------------|-----------------------------------------------------------|-------|
| App launch smoke   | `pytest tests/smoke/test_app_launch.py`                   | Session opens, app is in the foreground, one cold relaunch |
| Login smoke        | `pytest tests/smoke/test_login.py --provider testmu`      | Profile → Sign in → phone → PIN → OTP; needs `APP_TEST_PHONE/PIN/OTP`; skipped on simulated |
| Home features      | `pytest tests/smoke/test_home_features.py --provider testmu` | Signed in: add new card (Cards & accounts → Add new), check offers (Offers → My offers → Discover offers → Flash deals), check orders (Orders tab), discover (Discover tab); a mini program that fails to open fails the step with the app's error code |
| Engagement         | `pytest tests/smoke/test_engagement.py --provider testmu` | Signed in: notification centre (search, My World / Promotions & offers / Services tabs, preferences), AI Assistant (search bar → "Find best offers" → "All offers"), Stores (My stores → store page; store from `APP_TEST_STORE`) |
| Wallet             | `pytest tests/smoke/test_wallet.py --provider testmu`     | Signed in, nothing is sent or paid: Send & request (Send money, Request money screens), latest transaction details, loyalty cards, daily shortcuts (Top up phone, Pay bills, Save on utilities) |
| Profile            | `pytest tests/smoke/test_profile.py --provider testmu`    | Signed in: Personal details, Balance alert, Subscriptions & automatic payments |
| Business journeys  | `pytest tests/smoke/test_business_journeys.py --provider testmu` | Critical journeys from the regression suite: relaunch and logout, balance visibility, scan to pay and payment code, Change PIN, Top up and Back to bank; `docs/USER_JOURNEYS.md` maps every journey (UJ-xxx) to its test |
| Startup benchmark  | `pytest tests/startup`                                    | Cold/warm/hot launches; `--iterations N` or `STARTUP_ITERATIONS` |
| User journey       | `pytest tests/user_journey`                               | Login → Search → Checkout → Profile with per-step timings |
| Endurance          | `pytest tests/stress`                                     | `ENDURANCE_PROFILES=50,100,500` runs each iteration count |
| Build A vs Build B | `pytest tests/benchmark`                                  | Set `BUILD_A_APP` / `BUILD_B_APP` (simulated uses built-in profiles) |

Screens behind the PIN step-up (loyalty cards, subscriptions, ...) only ask for the PIN when
the last check has expired, and the app locks itself with the same sheet a few seconds after
sign-in. The `protected_step` fixture times opening such a screen; when the PIN sheet appears,
entering it is timed as a separate `<step>_after_pin` step. Sign-in also answers the "Verify
your PIN" sheet and biometrics prompt the app can show right after login.

Don't run signed-in tests in parallel with the same test account: the server can reject one of
the sign-ins ("Sorry, something went wrong"), and the test fails at sign-in with that message.

Useful options (all also configurable in `config/framework.yaml`):

```text
--provider NAME          simulated | local | testmu (or any registered plugin)
--device KEY             device profile from config/devices.yaml
--platform android|ios
--iterations N           override startup/endurance iterations
--sampling-interval SEC  telemetry sampling period (default 1.0)
--no-telemetry           disable background collectors
--perf-label LABEL       label for this run (shown in reports/trends)
--perf-baseline PATH     metrics.json of a baseline run to compare against
--perf-report-dir DIR    output directory (default reports/)
```

Endurance tests flag memory leaks (regression slope in MB/min plus first-vs-last window
growth), CPU growth, and response-time degradation. They only fail the test when
`PERF_FAIL_ON_LEAK=true`; otherwise the findings are reported.

### Crash and ANR tracking

Every test (`crash:` in `config/framework.yaml`) watches for Java crashes, native crashes,
ANRs and the app dying mid-step. Sources: the device logcat after each `perf_recorder.step`,
the app state after a failed step, and, on TestMu, the post-session device log, crash log and
app-profiling crash/ANR counters. Events are de-duplicated, attributed to the step they
happened in, and shown in the report's **Stability Dashboard** (KPIs, charts, crash log with
stack traces and session video/log links) and in `metrics.json`. A crash fails the test
unless `PERF_FAIL_ON_CRASH=false`.

### Step-by-step screenshots

The screen is captured as each `perf_recorder.step(...)` ends (after the step's timing is
taken, so it does not inflate durations) and once more at the end of the test.
Screenshots are downscaled to `screenshot_max_width` and stored as JPEG (about 5-60 KB each).
`PERF_SCREENSHOTS=every_step|on_failure|off` (default `every_step`). Steps that repeat across
endurance iterations are captured once, plus every time they fail.

Mark steps that mostly wait on purpose (optional prompts, fixed delays) with
`perf_recorder.step("name", measure=False)`: they are still shown, but excluded from the
latency benchmarks and slow-step issues.

### Production readiness

`benchmarks` in `config/framework.yaml` rates KPIs against production thresholds: Google Play
Android vitals bad-behaviour limits (crash rate 1.09%, ANR rate 0.47%, cold start 5 s,
warm start 2 s, hot start 1.5 s, slow sessions above 25% slow frames) plus common SLOs and team
budgets (slowest user step, API p95 latency and error rate, peak memory, battery drain).
Each rule has a `good` and a `limit` value: pass, warn, or fail. Any fail makes the run
**Not Ready**, any warn **At Risk**. Set `PERF_FAIL_ON_BENCHMARK=true` to fail tests that
breach a limit (default: report only). Tune the values to your app's production targets.

### Issues and drilldown links

Crashes, failed steps, slow steps, benchmark breaches, leak/CPU-growth findings and failed
API requests become ranked issues (critical, major, minor). Each one has a shareable deep
link that opens the test's drilldown page, highlights the issue, and from there jumps to the
step (row, screenshot, and the step's time window on the timeline chart), the crash stack trace,
or the benchmark row. Examples:

```text
reports/runs/<run_id>/executions/01-test_login_with_phone_pin_otp.html#issue-1
reports/runs/<run_id>/executions/01-test_login_with_phone_pin_otp.html#step-10
reports/runs/<run_id>/executions/01-test_login_with_phone_pin_otp.html#crash-1
```

Every issue, step and crash has a "Copy link" button. Clicking a step bar in the timeline
opens that step.

### Slow-step root cause

Every step is broken down into the code that spent its time, at three levels, and the
slowest part is named in the steps table, the issue ("Cause: ..."), the CI summary and the
export. The "Root cause" section of the drilldown page has one card per step (slow and
failed steps are expanded; link: `...html#why-step-3`):

| Level | Names | Evidence |
| --- | --- | --- |
| App code | Activity, Looper handler / callback class, app method from a StrictMode stack | live device log (`Displayed`, StrictMode, `Looper` slow dispatch, `Choreographer` skipped frames, `Davey`) |
| Backend API | Operation, e.g. mPaaS `operationType` `ap.mobile.payments.queryPayMethod`, GraphQL `operationName`, JSON-RPC `method`, `Operation-Type` header; else `METHOD host/path` with ids as `{id}` | HAR: per-call server time (`Server-Timing`, `x-envoy-upstream-service-time`, ...), connection setup, waiting time, trace id (`mgw-traceid`, `traceparent`, ...) |
| Test code | Page-object method chain and Appium command, e.g. `HomePage.wait_loaded > BasePage.wait_visible > BasePage.find`, 12 x findElement on `home.profile_icon`, `tests/smoke/test_login.py:50` | every driver command of the step, timed and attributed to the calling method |

The primary cause is: app code that blocked the main thread for at least 25% of the step;
else the slowest backend call when backend calls kept the app busy for at least 40% of the
step; else the test method that took longest (e.g. polling for an element the app had not
rendered yet). The "API operations" table groups calls by operation with avg / p95 / max,
server time and connection setup, slowest first.

Set `PERF_APP_PACKAGES` (comma-separated, e.g. `com.adw`) when your code lives in other
packages than the application id, so stacks resolve to your class rather than a library.
Cloud network logs capture the whole device, so analytics, RUM / log uploads, crash
reporting and OS / Chrome component updates are marked *background*: listed, but never
counted as busy time or named as the cause. Add your own with `PERF_BACKGROUND_HOSTS`.
StrictMode lines only appear in builds that enable StrictMode (typically debug / UAT). Only
operation names, trace ids and class names are kept: no request bodies or log messages.
Switch parts off under `attribution:` in `config/framework.yaml`.

### Performance data export

`performance_data.json` is a versioned (`schema_version`), self-describing export of the run:
run/app/device metadata, readiness and benchmark results, KPIs, distribution statistics
(count, min, mean, p50, p90, p95, p99, max with units) for every time series, steps with
screenshots, deep links and their root-cause breakdown, backend operation statistics,
crashes, and issues with their cause. Schema 1.1 only adds fields to 1.0. Its JSON Schema is published next to it
(`performance_data.schema.json`). The `csv/` folder holds the same data in long format
(`samples`, `steps`, `requests`, `kpis`, `benchmarks`, `issues`) for Grafana, ELK, BigQuery,
Excel or pandas.

## Comparing runs

Compare two `metrics.json` files from the command line:

```powershell
python -m core.comparison --baseline baseline/metrics.json --candidate reports/metrics.json `
    --output reports/comparison.json --fail-on-regression
```

The command exits with code 1 when any metric regresses beyond its `max_regression_pct`
(see `comparison` in `config/framework.yaml`). Passing `--perf-baseline` to pytest embeds the
same comparison into the HTML report.

## Scoring

Each category is scored 0-100 by linear interpolation between a `target` (100) and a
`limit` (0), then weighted: Startup 25, Memory 20, CPU 20, FPS 15, Battery 10, Network 10.
Weights are re-normalized over the categories that have data.

| Score   | Rating            |
|---------|-------------------|
| 90-100  | Excellent         |
| 80-89   | Good              |
| 70-79   | Acceptable        |
| < 70    | Needs Improvement |

## Output

```text
reports/
  index.html                 all runs (newest first) with status, issues, score, readiness
  latest.html                redirects to the newest run report
  metrics.json               copy of the latest run's metrics.json (stable path for CI)
  performance_data.json      copy of the latest run's export
  history/trend.json         last N runs for trend charts
  runs/<run_id>/             one self-contained folder per run (oldest pruned, keep_runs)
    index.html               run report: summary, readiness, issues, 7 dashboards, executions
    executions/<nn>-<test>.html   drilldown page per test
    metrics.json             run summary, score, KPIs, benchmarks, issues, comparisons
    performance_data.json    versioned export (+ performance_data.schema.json)
    csv/                     samples, steps, requests, kpis, benchmarks, issues
    artifacts/<test>/screenshots/   step-by-step JPEG screenshots
    raw/<test>.json          per-test execution (telemetry, summary, findings, issues)
    raw/<test>.jsonl         raw samples as they were collected
    framework.log
```

`PERF_KEEP_RUNS` (default 30, 0 = keep all) sets how many run folders are kept.

## CI/CD

| System          | File                                   |
|-----------------|----------------------------------------|
| GitHub Actions  | `.github/workflows/mobile-perf.yml`    |
| Azure DevOps    | `ci/azure-pipelines.yml`               |
| Jenkins         | `ci/Jenkinsfile`                       |

All three run unit tests, a simulated smoke run, and (when credentials are available) a
TestMu device-cloud run, then publish the HTML report, `metrics.json` and trend history.
`ci/publish_summary.py` renders `metrics.json` as Markdown for job summaries.

**Jenkins:** the HTML Publisher plugin's default Content Security Policy blocks the
Plotly and Bootstrap CDN scripts. Either relax
`hudson.model.DirectoryBrowserSupport.CSP`, or set `reporting.plotly_mode: inline` in
`config/framework.yaml` to embed Plotly in the report.

## Development

```powershell
pytest tests/unit          # parser, scoring, analysis, provider and pipeline tests
ruff check .
mypy core models providers telemetry reporting
```

## Project layout

```text
config/      framework.yaml, devices.yaml, app.yaml
core/        config loader, pytest plugin, sessions, analysis, scoring, comparison, trends
providers/   DeviceProvider ABC, registry, TestMu, Local, Simulated
telemetry/   executors, parsers, collectors, TelemetryManager, iOS backends
reporting/   Plotly charts, run/drilldown/portal pages, data export, Jinja2 templates
models/      Pydantic telemetry/execution/report models
pages/       page objects driven by YAML locators
tests/       startup, user_journey, stress, benchmark, unit
ci/          Azure DevOps, Jenkins, summary publisher
docs/        architecture and provider extension guide
```
