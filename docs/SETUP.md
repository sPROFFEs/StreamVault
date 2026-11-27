# Setup and everyday use

StreamVault runs FastAPI/Jinja and SQLite on your server. Desktop, mobile and TV
browsers share the hosted interface; Android is a WebView client connecting to that
same server. Keep the server running while using any client. There is no offline
APK backend, Node package manager, SPA bundle or GitHub Pages deployment.

## Initial installation

Install Git, then clone your chosen repository's distribution **branch**, using its
real URL and branch name. Run the installer from the checked-out repository on
Debian/Ubuntu:

```sh
git clone --branch YOUR_RELEASE_BRANCH YOUR_REPOSITORY_URL StreamVault
cd StreamVault
sudo bash vault/setup.sh --non-interactive --domain localhost
```

The installer installs Docker/Compose if needed, generates a session secret,
prepares persistent storage, starts the services, waits for core health, and creates
the initial administrator. Save the generated password displayed at completion.
Use `bash vault/setup.sh --help` for custom domain, storage and integration options.
For unattended credentials, set `STREAMVAULT_ADMIN_USERNAME` and
`STREAMVAULT_ADMIN_PASSWORD` in the calling environment; do not commit passwords.

For an existing Docker host, use manual setup instead. From the repository root:

```sh
cp vault/.env.example vault/.env
python3 - <<'PY'
from pathlib import Path
import secrets
path = Path('vault/.env')
path.write_text(path.read_text().replace('SECRET_KEY=\n', 'SECRET_KEY=' + secrets.token_hex(32) + '\n', 1))
PY
# Edit DOMAIN/ALLOWED_HOSTS and optional service keys in vault/.env.
docker compose --env-file vault/.env -f vault/docker-compose.yaml config --quiet
docker compose --env-file vault/.env -f vault/docker-compose.yaml up -d --build
curl --fail http://127.0.0.1:8000/api/health
```

The example uses `HOST_DATA_ROOT=./data`, resolved by Compose under `vault/`;
choose another persistent directory if needed before first start. Keep that value
and `SECRET_KEY` across upgrades. To create an administrator manually, read a
strong password into the environment and pass it to the existing admin command:

```sh
read -r -s -p 'Admin password: ' STREAMVAULT_ADMIN_PASSWORD; echo
export STREAMVAULT_ADMIN_PASSWORD
docker compose --env-file vault/.env -f vault/docker-compose.yaml exec \
  -e STREAMVAULT_ADMIN_PASSWORD core python create_admin.py --username owner
unset STREAMVAULT_ADMIN_PASSWORD
```

The password needs at least eight characters, uppercase/lowercase letters, a number
and a symbol. Avoid `--reset` on an existing installation; it recreates the schema.
The admin command creates or updates that administrator. After signing in, manage
users, quotas and services in Administration.

## Services and remote access

| Service | Purpose | Requirement |
| --- | --- | --- |
| core + SQLite | Accounts, catalog, hosted interface and playback | Required; persistent data and session secret |
| downloader | Server-side downloads and media processing | Included in Compose; shares data with core |
| nginx | Front proxy and assets | Included; local browser entry point is `http://localhost` |
| cloudflared | Optional public HTTPS tunnel | Included; configure in Administration or with `TUNNEL_TOKEN`/`TUNNEL_HOSTNAME` |
| TMDB | Posters, descriptions and discovery | Optional API key/token; browse metadata needs a configured account |
| OpenSubtitles | External subtitles | Optional API key; configure the provider in settings |
| TV/YouTube/media providers | External channel/stream availability | Network/provider access; availability and codecs vary |

TMDB uses the API key saved in **Administration → Catalog** first, then
`TMDB_TOKEN` or `TMDB_API_KEY` from the deployment environment. Removing
the file key restores the environment credentials. Cached metadata survives
service restarts. Files with a registered TMDB ID keep a minimal title and
episode view during provider outages.

For other devices, configure a reachable HTTPS hostname with a trusted certificate,
`DOMAIN`, and matching comma-separated `ALLOWED_HOSTS`. The tunnel origin inside
Compose is `http://nginx:80`; `localhost` inside cloudflared refers to its own
container. Release APKs require HTTPS. Keep the application behind the HTTPS entry
point and restrict direct host ports to your local deployment network.

The Docker socket is used by administrator deployment operations; only trusted
administrators should control this server. Store integration keys in the local
`.env` or encrypted service configuration. No TMDB, tunnel or subtitle credential is
needed to build the application or run deterministic tests.

## Desktop, mobile and TV

Open the server origin and sign in. Use the portal/sidebar to browse movies and
series, search, open details and play available sources. Favorites, playback state,
appearance/language and user preferences stay with the account. Downloads initiated
in the interface are stored on the server. Settings manages account preferences;
Administration is visible to administrators.

**Remember me** keeps the login on a trusted device for 30 days by default
(`REMEMBER_SESSION_DAYS`). Without it, the default remains 24 hours
(`ACCESS_TOKEN_EXPIRE_MINUTES`). Valid sessions renew near expiry while there is
authorized activity, so ordinary playback progress/range requests keep the app
session alive. Expired sessions still require a login, disabled accounts remain
blocked, and changing the password invalidates newly issued sessions. Always sign
out on shared devices. Cloudflare Access has its own session policy, independent
of this app; see [Android/Access diagnostics and session settings](../android/README.md#server-and-connection).

Desktop supports mouse and keyboard with Tab/Enter/Escape and visible focus. Mobile
uses the bottom navigation and touch controls; portrait layouts adapt without a
separate site. TV layouts use larger spacing with arrow/D-pad navigation, Enter to
activate, and Back/Escape to dismiss overlays or exit fullscreen before navigating.
The TV browser must support the hosted web features. The Android client currently
has no dedicated Android TV launcher/Leanback integration; installing its APK on
every television model is not guaranteed. Use a supported TV browser and verify
your actual remote/device, including its available video codecs.

## Android installation and updates

Download `streamvault-VERSION-android.apk` and `SHA256SUMS` from the chosen GitHub
release. Verify the checksums against the downloaded assets, then permit installation
from your chosen browser/file manager in Android's per-app “Install unknown apps”
settings and open the APK. Android 8.0/API 26 or newer and a current Android System
WebView/Chrome provider (Chromium 108 or newer) are required.

Tap the native Server button and enter your HTTPS origin, such as
`https://watch.example.com`. Sign in on the hosted login screen. The server must
be reachable; Reload/Try again recover failed connections. Back dismisses the
keyboard/fullscreen/overlays before using page history. Switching server clears
the previous server's cookies and browser storage.

For later APK releases, install the new APK over the existing application using
the same signing key and a higher version code. A different key requires a separate
installation or uninstalling first, which clears client state. Debug APKs use a
separate application ID and may coexist. Administrator Update changes the hosted
server; it does not install Android updates. See the [Android guide](../android/README.md)
for emulator/USB debugging, native limitations and device smoke checks. In the
installed client, **More → About & updates** also checks the official GitHub APK
and offers a verified download and Android installation confirmation. Update in
place; do not uninstall or clear app data if you want to retain device videos.

## Build and test

Run from a clean checkout with Python 3.11, Node 22+, Docker/Compose for image builds,
and JDK 17/Android SDK for APKs. pip is the project's Python package manager.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r vault/core/requirements-test.lock
.venv/bin/python vault/scripts/release.py check
.venv/bin/python vault/scripts/check.py unit -q
.venv/bin/python vault/scripts/check.py validate
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python vault/scripts/check.py baseline -q
.venv/bin/python vault/scripts/check.py build
```

The image build check uses `.env.example` and a public build-only secret; it does
not start services or use local credentials. Browser checks use isolated fixture
data. FFmpeg/aria2/nginx enable optional integration checks; missing optional tools
are reported as skips. See [test instructions](../vault/core/tests/README.md).

For Android, install SDK command-line tools outside the checkout; set `JAVA_HOME`
to JDK 17 and `ANDROID_HOME` to your SDK directory. Accept SDK licenses, then:

```sh
sdkmanager --licenses
sdkmanager 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
cd android
./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug lintRelease assembleRelease
```

Outputs are `android/app/build/outputs/apk/debug/app-debug.apk` and unsigned
`release/app-release-unsigned.apk`. Only debug is installable without external
release signing. Signing configuration, artifact naming and the internal update
workflow are in [maintainer releases](RELEASES.md). GitHub build jobs reproduce
these checks; the release job signs only in the configured release environment.

## Backups and troubleshooting

Keep a protected copy of `.env`, your session secret, the APK signing key and
`HOST_DATA_ROOT`. Stop services before a filesystem backup to keep SQLite files
consistent; restarting with the same configuration resumes the existing library.
For connection problems, confirm `/api/health`, server origin, allowed hosts, TLS,
the WebView provider and tunnel status. Inspect local logs:

```sh
docker compose --env-file vault/.env -f vault/docker-compose.yaml ps
docker compose --env-file vault/.env -f vault/docker-compose.yaml logs --since 10m core downloader cloudflared nginx
```

Redact passwords/tokens before sharing logs. For update failures, follow
[update recovery](RELEASES.md#internal-update-operations-and-recovery).

## Playback performance and storage maintenance

The bundled nginx can send compatible completed downloads directly from a
read-only mount, after the server authorizes each request. Byte ranges and HEAD
requests are retained. Custom deployments without that mount should set
`MEDIA_ACCEL_REDIRECT=false`; direct core access also falls back to authenticated
file delivery. Existing installations must recreate nginx/core/downloader to
apply the new mount, environment and bounded Docker logs:

```sh
docker compose --env-file vault/.env -f vault/docker-compose.yaml up -d --build core downloader nginx
```

Configure these in `vault/.env`:

| Setting | Default | Purpose |
| --- | --- | --- |
| `MIN_FREE_DISK_GB` | `2` | Physical disk reserve for databases, logs and playback; separate from the download quota |
| `MAX_CACHE_SIZE_GB` | `100` | Maximum playback/cache storage, independent of the shared-download quota |
| `STORAGE_PROFILE` | `balanced` | `minimal` caps the cache at 10 GiB and 24 hours and disables automatic full copies. `performance` retains idle copies for at least 336 hours within the configured quota. |
| `ABANDONED_DOWNLOAD_TTL_HOURS` | `1` | Minimum age before a marked inactive transfer can appear in the storage cleaner |
| `BACKUP_RETENTION_DAYS` | `0` | Prune older StreamVault backups after a successful backup. A value of 0 preserves every archive. |
| `PLAYBACK_CACHE_RETENTION_HOURS` | `168` | Expire unused playback copies after seven days |
| `AUTO_CACHE_ON_WATCH` | `true` | Prepare a seekable copy after meaningful converted playback closes; disable to save disk/CPU |
| `MAX_LIVE_REMUXES` | `2` | Concurrent live conversions per core process; consider `1` on a small server |
| `MEDIA_ACCEL_REDIRECT` | `true` in Compose | Use the bundled nginx's private read-only file location |

Queued downloads recheck their space budget when starting; transfers and cache
writers also check while running. A job stops with a disk-space error when a
check detects that the reserve has been reached. Torrent files allocate as
pieces arrive rather than reserving a season's full disk space before transfer.
Completed media is never automatically deleted to create space.

The server keeps queued download IDs and partial files across restarts. Use
**Downloads → Resume** on a compatible failed or interrupted download to retry
in the same directory. A selected torrent season appears in the library once
the selected files have finished and passed verification. Update core and
downloader together and keep their shared volumes. See the
[download queue rollout notes](OPTIMIZATION_ROADMAP.md#migración-y-rollback-de-la-cola-de-descargas).

API clients can send `Idempotency-Key` when starting a direct or torrent
download to get the same job ID on repeat requests while the job is retained.
Requests can set `priority` to `-1`, `0` or `1`. Priority applies to jobs
waiting for a transfer slot.

**Administration → Files → Storage cleaner** previews abandoned partial jobs,
empty folders and rebuildable playback copies. It shows physical free space,
cache usage and the reserve, processes up to 500 candidates per batch, and
reports failed removals as well as bytes removed. Active jobs, open cached-video
responses and recently used copies are protected. A partial video's incomplete
marker remains until the rest of that abandoned job has been removed. Cleanup
leaves jobs containing media unselected by default: review them explicitly,
because a failed season can still contain complete episodes. Cleanup does not
traverse global `/tmp`, backups, databases or host Docker volumes.

Periodic maintenance starts after readiness, expires old playback copies and
abandoned conversion outputs, and evicts the oldest idle playback copies when
space is needed. Complete copies have a ten-minute idle grace period; partial
conversion files have a one-hour grace period. Source downloads, user metadata
and unrecognized cache files remain under administrator control. Core, nginx,
downloader and tunnel Docker logs are limited to three 10 MiB files each.

Core processes sharing a writable cache volume coordinate cleanup through the
hidden `.leases` directory in that volume. A new live conversion interrupts an
active background conversion and leaves queued cache work waiting for playback
to finish. Workers sharing the volume should run the same StreamVault version.

Core and downloader reserve capacity on the shared volume before starting a
download, cached conversion or subtitle extraction. The storage cleaner
displays job reservations separately from the configured free-disk reserve.

Use **Administration → Files → Identify** to review or correct a movie or
episode. A file can be assigned to several episodes, including in season 0.
The corrections live in the library's SQLite index and are included in
backups. External file changes are reconciled roughly once a minute. See the
[upgrade and rollback notes](OPTIMIZATION_ROADMAP.md#migración-y-rollback-del-segundo-tramo).

H.264/AAC files take the direct path when compatible. H.264 10-bit and other
unsupported video formats use the existing bounded conversion slots; these
still require enough CPU for real-time encoding. Seeking inside a native file
or an already buffered fragment no longer restarts conversion. Network stalls
have bounded reconnection attempts that retain the playback position. The
custom player includes buffered progress, playback speed and picture in picture
when the browser supports it. Android's installed frontend receives these UI
changes when a new APK is built and installed; hosted browsers receive the
versioned assets after updating the server.

## Deploying and monitoring the updated services

Recreate core, downloader, nginx and cloudflared together when applying this
update. Keep the existing `HOST_DATA_ROOT` and take a database and configuration
backup before updating. Core `/api/health` confirms that HTTP is live.
`/api/ready` checks SQLite and returns 503 when the database is unavailable.
The downloader has equivalent `/health` and `/ready` endpoints on its private
port. Compose waits for core readiness before starting nginx and for nginx's
HTTP listener before starting the tunnel.

| Setting | Default | Purpose |
| --- | --- | --- |
| `CORE_CPUS`, `CORE_MEMORY_LIMIT` | `0.0`, `0` | Optional core CPU and memory ceilings. Zero leaves resources uncapped. |
| `CORE_PIDS_LIMIT`, `CORE_NOFILE_LIMIT` | `384`, `16384` | Core process/thread and open-file limits. |
| `DOWNLOADER_PIDS_LIMIT`, `DOWNLOADER_NOFILE_LIMIT` | `128`, `8192` | Downloader process/thread and open-file limits. |
| `VIDEO_ENCODER` | `software` | Live H.264 encoding backend. Supported choices are `software`, `vaapi`, `qsv` and `nvenc`. |
| `VIDEO_RENDER_DEVICE` | `/dev/dri/renderD128` | Render device inside the core container for VAAPI and Quick Sync. |
| `UPDATE_DOCKER_SOCKET` | `/var/run/docker.sock` | Host socket used by the built-in deployment updater. Set `/dev/null` to remove Docker API access and apply updates from the host. |

The default playback settings run on CPU. To try hardware encoding, configure
the matching host driver and expose its device to the core container. VAAPI and
QSV need a render device accessible to `appuser`. NVENC needs NVIDIA container
runtime access. If an enabled encoder fails before the first fragment, StreamVault
tries software and waits a minute before trying that device again. Direct play,
video copying and background cache generation keep their existing paths.
For small systems start with `MAX_LIVE_REMUXES=1`, `CONCURRENT_DOWNLOADS=1`
and `AUTO_CACHE_ON_WATCH=false` if playback takes priority over cache warm-up.

**Administration → System monitor** shows live Core conversions, FFmpeg
children, direct delivery responses, queued downloads, provider state and disk
reservations. Direct delivery responses count requests, which can include seek
requests. Conversion first-byte timing measures Core and excludes browser
buffering. Storage categories and recoverable space are sampled during cleanup.
**Administration → Files → Storage cleaner** shows the candidates before deleting.
Automation can send `dry_run: true` with its selected `paths` to
`POST /admin/api/downloads/cleanup`; this returns the plan and estimated bytes.
