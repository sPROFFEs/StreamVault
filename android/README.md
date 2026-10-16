# StreamVault for Android

For GitHub APK distribution, shared version metadata, signing secrets and signed
release automation, see [maintainer releases](../docs/RELEASES.md). Installation
and desktop/mobile/TV use are covered in [setup](../docs/SETUP.md).

The application remains FastAPI + Jinja + SQLite on a server. Its templates need
authenticated routes and APIs, so packaging HTML alone would not work. This small
Java WebView client opens a configurable server origin and preserves the existing
login cookies and frontend. No Node build, Capacitor bridge, bundled Python,
storage permission or runtime library is added. Application ID is
`com.streamvault.app` (`com.streamvault.app.debug` in debug), minimum Android 8.0
(API 26), compile/target API 35. Java 17, AGP 8.9.2 and Gradle 8.11.1 are pinned.
See [AGP compatibility](https://developer.android.com/build/releases/agp-8-9-0-release-notes).

## Server and connection

Deploy the existing server following [the deployment guide](../vault/README.md):
copy `vault/.env.example` to `vault/.env`, configure the secret, domain and data
directory, then run `docker compose -f vault/docker-compose.yaml up -d --build`.
Use its HTTPS hostname (for example the configured tunnel), with a valid system
trusted certificate. Create the initial administrator on the server. On first launch,
SetupActivity prompts for the server address; paths,
embedded credentials, queries and fragments are rejected. Accounts and passwords
are entered only in the existing web login. An unobtrusive settings button on
login and error screens allows changing servers at any time; switching servers clears WebView
cookies, cache and web storage; backup/transfer of app data is disabled.

Debug additionally permits HTTP only for `10.0.2.2`, `127.0.0.1` and `localhost`.
For an emulator, a backend listening on the host's port 8000 is available at
`http://10.0.2.2:8000`. For USB devices run `adb reverse tcp:8000 tcp:8000` and
enter `http://127.0.0.1:8000`. LAN IP addresses require HTTPS. Release rejects HTTP.
Loading progress, certificate/network error feedback, Cloudflare Access auth flows,
and Try again are native, so they also work when the server is unavailable.
They retain the current same-origin route, including catalog filters. A new default
network retries a failed connection automatically; server restarts on an unchanged
network can be recovered with Try again. Cookies are flushed after page loads and
when the app pauses. The server session expiry and logout still apply. Normal
browsing needs a reachable server and an up-to-date Android System WebView
(Chromium 108 or newer); the wrapper does not
bundle a backend. With an updated server, explicit device downloads and a public
offline-library shell are available through IndexedDB/service workers, including
without Internet. See [offline use, limits and privacy](../docs/OFFLINE.md).

Main-frame navigation remains on the selected origin. User-activated external
HTTP(S) links open the browser. Web media iframes remain supported without a native
bridge; mixed content, file/content access, third-party cookies and unsafe TLS
overrides are disabled. User-activated `target="_blank"` links use the same policy.
Android Back dismisses the keyboard, then native fullscreen or the web shell's top
overlay/CSS fullscreen, then page history, then the activity. Native fullscreen hides
system bars and uses sensor landscape; exit restores the previous orientation and
controls. Rotation keeps the WebView alive. API 30+ system/keyboard insets are
consumed by the native root; earlier Android versions use window resize. Native
alert/confirmation dialogs preserve the web updater's Continue/Cancel behavior.
Providers requiring third-party cookies, browser OAuth redirects, file uploads or
unsupported Android codecs need separate integration.
Torrentio/server downloads stay on the server. **Save to device** makes a separate
foreground copy in WebView's origin-private storage, without native bridges or
storage permissions. Keep the screen open until completion. The connection-error
screen's **On this device · Offline** button opens previously saved content;
the first offline setup must complete while connected. The administrator updater
rebuilds/recreates
the hosted Docker Compose deployment; it does not install or sign a new APK.
Install APK updates separately and reload after the server is healthy.
See [WebView](https://developer.android.com/develop/ui/views/layout/webapps/webview)
and [network policies](https://developer.android.com/privacy-and-security/security-config).

## Build and checks

Install JDK 17 and Android SDK command-line tools outside the repository. Set
`JAVA_HOME` and `ANDROID_HOME` (or use an ignored `android/local.properties`).
Accept the SDK licenses, then:

```sh
sdkmanager 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
# Optional preconfigured origin; leave empty for on-device setup.
./gradlew assembleDebug -PSTREAMVAULT_SERVER_URL=https://watch.example.com
./gradlew lintRelease assembleRelease
```

Debug APK: `app/build/outputs/apk/debug/app-debug.apk`. Test and lint reports:
`app/build/reports/tests/testDebugUnitTest/`, `app/build/reports/lint-results-debug.html`.
The wrapper distribution has a pinned SHA-256 checksum. Debug uses Android's local
debug signing key under `~/.android/`; it is never committed.

For the device smoke check, start the server and boot an emulator/device, then
run from the repository root:

```sh
adb devices
python3 android/scripts/smoke_device.py --server-url http://10.0.2.2:8000
# USB device (use --serial if multiple targets are connected):
adb reverse tcp:8000 tcp:8000
python3 android/scripts/smoke_device.py --server-url http://127.0.0.1:8000
```

This installs the debug APK, clears its test data, checks unavailable-server
feedback, changes the server through the native dialog, and verifies real
StreamVault content inside the WebView. It writes a screenshot, hierarchy and
JSON result under ignored `app/build/reports/device-smoke/`. The basic smoke run
does not supply credentials or start deployment services; optional authenticated
checks are described below. API 35 Google APIs x86_64 is a suitable
emulator image; hardware acceleration or an attached device is required for a
practical run in constrained CI environments.

## Release signing and assets

Release enables R8/resource shrinking and disables WebView debugging. Without
signing configuration it produces `app/build/outputs/apk/release/app-release-unsigned.apk`.
For an installable signed release, set all four environment variables:
`STREAMVAULT_KEYSTORE` (path to an external keystore),
`STREAMVAULT_STORE_PASSWORD`, `STREAMVAULT_KEY_ALIAS`, `STREAMVAULT_KEY_PASSWORD`,
then run `./gradlew assembleRelease`. Signed output is `app-release.apk`.
Keep these values in a local environment or CI secrets store. No release key or
password is included; incomplete signing configuration fails the build.

Launcher icons reproduce the [editable mark](../vault/assets/brand/mark.svg).
The adaptive foreground carries its shield/play paths inside the safe mask area on
the charcoal background, with a monochrome variant for themed launchers. All
supported Android versions use that adaptive icon. Branding source and
public asset names stay unchanged.

## Integration checks

The optional authenticated smoke runner clears only debug-app data, logs in with
an existing test account, checks browsing/search, keyboard/overlay/history Back,
Reload, confirmation cancellation/acceptance, and cookie persistence after a
process restart. It never invokes deployment updates. Install Playwright in the
Python environment (`vault/core/requirements-test.txt`) for these optional checks.
For playback pass a movie ID with a playable source on your test server. For
connection recovery use adb reverse and the 127.0.0.1 server origin:

```sh
adb reverse tcp:8000 tcp:8000
# Set STREAMVAULT_SMOKE_PASSWORD in the environment; it is not written to reports.
python3 android/scripts/smoke_device.py --server-url http://127.0.0.1:8000 \
  --username android-smoke --search Movie --media-id 7 --disconnect-port 8000
```

`--disconnect-port` temporarily removes and restores that adb forwarding rule,
checks native offline feedback and retries the same route. Use an isolated test
account/server because playback writes normal history/preferences. Reports record
only flows that completed. Keep reports local: they are generated under ignored
build directories, not published as application source or release assets.
