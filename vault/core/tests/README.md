# Regression and browser checks

Run commands from the repository root with Python 3.11 (the production Docker
version). Create a disposable environment; install test dependencies and Chromium:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r vault/core/requirements-test.lock
.venv/bin/python -m playwright install chromium
```

Node is required by the existing JavaScript contract tests and syntax checks.
On a Linux host lacking Chromium runtime libraries, install them with
`.venv/bin/python -m playwright install-deps chromium`.
FFmpeg/FFprobe and aria2 enable the existing media integration checks; nginx
enables the existing proxy integration checks. Missing optional tools are reported
as skips, while the deterministic baseline requires no media tools.
The HLS browser check additionally needs `STREAMVAULT_HLS_JS` pointing to a local
Hls.js bundle, as documented in `test_iptv_playback_browser.py`.

```sh
# Existing tests plus server regression baseline; browser checks remain opt-in.
.venv/bin/python vault/scripts/check.py unit -q
# Deterministic server and browser baseline at all three viewport sizes.
.venv/bin/python vault/scripts/check.py baseline -q
# All existing browser checks; media checks require the tools listed above.
.venv/bin/python vault/scripts/check.py browser -q
# Full suite, including opt-in browsers.
.venv/bin/python -m pytest --browser -q
# Server/template/JavaScript/entrypoint syntax validation.
.venv/bin/python vault/scripts/check.py validate
# Validate Compose and build production images; requires Docker/Compose/daemon.
.venv/bin/python vault/scripts/check.py build
```

The build check runs `docker compose --env-file vault/.env.example -f vault/docker-compose.yaml config --quiet`
and the corresponding `build core downloader cloudflared` command.
It supplies a test-only secret when none is set. There is no Node asset build:
the application serves Jinja templates and JavaScript/CSS assets directly.

## Isolated feature work

Before implementing a behavior change, add a focused regression that asserts
the desired result and run it against current behavior. Preserve its failing
output and command in the review evidence. Implement the change, rerun the
same command, then run the shared baseline and unit suite. Report both the
failure and passing results; skipped checks do not establish regression coverage.

```sh
# Backend or JavaScript unit regression (substitute the feature's test/node ID).
.venv/bin/python vault/scripts/check.py unit vault/core/tests/test_regression_baseline.py -q
# Focused browser regression; select mobile, desktop, TV, or all (default).
.venv/bin/python vault/scripts/check.py baseline vault/core/tests/test_regression_baseline_browser.py --viewport desktop -q
# Any feature browser test can run directly, with viewport parameterization.
.venv/bin/python -m pytest --browser vault/core/tests/test_regression_baseline_browser.py --viewport mobile -q
```

Mark new shared baseline tests `baseline` and browser tests `browser`. Request
`baseline_page` to reuse the fixture driver; it supplies `page`, `base_url`,
`login(username="baseline-viewer")`, mutable `payloads`, and recorded `requests`
as `(method, path, query)` tuples. `regression_client` supplies an HTTPS TestClient
with viewer/admin/disabled fixture users, password `Baseline123!`.

`regression_fixtures.py` owns deterministic movie/series, preferences, favorites,
playback and updater responses. Replace a response before navigation, for example
`baseline_page.payloads["/admin/system/update/git-pull"] = (500, {"detail": "failure"})`.
Add narrowly scoped endpoints there when a feature needs them. Unexpected
requests and page errors fail fixture teardown; remote requests are blocked.

The browser driver serves real login/browse/settings routes on an ephemeral
loopback HTTP server; auth cookies and redirects are real. It skips application
lifespan jobs and uses temporary users/database/storage. API auth tests cover
HTTPS cookie flags. Admin UI rendering and updater responses are fixtures;
server updater tests mock subprocesses/threads, so no update or restart executes.
Playback baseline checks control/resume/close logic with the decoder stubbed;
actual decoding/remux is covered by the existing optional media tests.

`browser_viewport` selects 390×844 mobile, 1440×900 desktop and 1920×1080 TV.
Baseline checks exercise keyboard login, card focus, navigation activation,
updater activation/confirmation and horizontal overflow at these sizes. TV
coverage uses desktop Chromium at TV dimensions; physical remote devices,
other browser engines, third-party streams and real updater deployments need
separate verification.

## Local evidence

Keep generated browser captures, performance samples, logs and machine-specific
review notes in ignored `output/`, `test-results/` or build directories. Missing
optional tools produce skips; a skipped check is not evidence of a passing
production integration. Run the Docker build and Android/device checks on hosts
with the required tooling before publishing a release.
