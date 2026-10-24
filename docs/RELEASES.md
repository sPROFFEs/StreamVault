# Releases and maintenance

The server, Android and release script share `vault/core/version.json`. Keep
`version` as stable `MAJOR.MINOR.PATCH`, tag the same commit `vMAJOR.MINOR.PATCH`,
and increase `android_version_code` for every shipped APK. Gradle reads this file;
FastAPI's version and `/api/health` read it through `vault/core/version.py`.

## Distribution contract

| Item | Contract |
| --- | --- |
| Repository | The configured Gitea/GitHub `OWNER/REPOSITORY`; no hardcoded owner or fork |
| Branch | Repository variable `STREAMVAULT_RELEASE_BRANCH`, explicitly configured by the maintainer |
| Tag | `v` followed by the version in `vault/core/version.json`; points to a commit reachable from the configured branch |
| Web asset | `streamvault-VERSION-web.tar.gz`: server, assets, Android source and docs; no Git directory, configuration secrets, databases or signing files |
| Android asset | `streamvault-VERSION-android.apk`: release APK signed with the persistent external key |
| Metadata | `release.json`: schema 1, version/code, tag, repository, full commit SHA, `update.strategy=git-fast-forward`, `update.branch`, asset names/sizes/SHA-256 |
| Checksums | `SHA256SUMS`: web/APK and manifest; validate with `sha256sum --check SHA256SUMS` in the asset directory |

The internal updater supports two channels. **Branch** retains the existing
fast-forward behavior. **Release** discovers published, non-prerelease stable
`vMAJOR.MINOR.PATCH` releases through the configured Gitea/GitHub API, fetches that
exact tag from the checkout's Git remote and checks its committed version metadata.
It installs the tag's full commit SHA, never the unreviewed branch tip, and will
not downgrade a checkout already ahead of that release. Both channels reject
diverged history, code edits and changes to persistent data/configuration paths.
An extracted archive lacks the Git checkout required by the updater: install from
a branch clone, not a detached tag. APK installation remains separate.

Admins configure channel/provider/API root/repository and an optional private
repository API token in **Administration → Overview → Update source and schedule**.
Blank API root/repository are inferred from HTTP Git remotes for public releases.
Private API credentials require an explicit API root; changing root/provider clears
the old credential unless a new one is explicitly supplied, preventing a forge's
token from being forwarded to a different destination. SSH remotes need
explicit API settings. Gitea uses the forge root (for example
`https://forge.example.com`), GitHub uses `https://api.github.com`. Git credentials
still come from the checkout; an API token does not authenticate `git fetch`.
Changing forge later requires updating the checkout's configured Git remote as
well as the API settings. API discovery redirects are rejected to avoid forwarding
credentials to another host; configure the canonical forge URL.

Automatic checks default to every 24 hours and can be disabled or scheduled at
1–168 hour intervals. **Nothing is automatically installed.** Existing installs
keep branch mode unless the administrator selects release mode. Environment
`UPDATE_MODE`, `UPDATE_PROVIDER`, `UPDATE_API_URL`, `UPDATE_REPOSITORY` and
`UPDATE_CHECK_INTERVAL_HOURS` set initial defaults; admin choices live in
`DATA_ROOT/config/updater/source.json` (0600, token never returned by read APIs).

Before an update/rebuild, the detached worker takes a consistent SQLite backup
of root/user databases and configuration, including the deployment `.env`, without
copying videos. A backup failure prevents applying code. **System monitor** shows
host-visible CPU/RAM/disk, core uptime, service state/restarts/OOM, bounded operation
history and downloadable private backups. Sampling stops while hidden. These
backups contain credentials, use permissions 0600 and must be stored privately.
They are retained under `DATA_ROOT/config/updater/backups`; review/remove old
copies deliberately when managing disk space. Restoration is manual: stop
services and preserve the current data before replacing any database. There is
no destructive automatic rollback or restore button.

## Gitea release workflow

Enable Actions on the original repository and configure an isolated Linux runner
with the `ubuntu-latest` label, Python/Node setup support, and permission to install
Chromium dependencies. Set repository variable `STREAMVAULT_RELEASE_BRANCH` and
secret `STREAMVAULT_RELEASE_TOKEN` with release-write access to this repository.
The token is not needed in a normal server deployment. Use HTTPS for your forge;
HTTP is only appropriate on a trusted private development network.

`.gitea/workflows/release.yml` validates a pushed stable tag (or a manually selected
existing tag), checks branch ancestry, installs hash-locked dependencies, runs
web regressions, creates deterministic web assets/checksums, and creates a **draft**
release with verified attachments. Review and publish it manually: drafts are
ignored by deployed instances. It never deploys a server or replaces/deletes an
existing release. Re-running an already-created draft fails safely; manage partial
draft attachments manually. The Gitea job ships the web server; the existing GitHub
workflow retains signed Android building. A separately signed APK can be supplied
to `release.py package --apk ...` for manual Gitea packaging.

To publish an already-tested, committed package manually, set
`GITEA_RELEASE_TOKEN` in your shell without putting the value in command arguments,
then run:

```sh
python3 vault/scripts/publish_gitea_release.py \
  --server https://YOUR_FORGE --repository YOUR_OWNER/YOUR_REPOSITORY --assets dist
```

This operation writes a draft and its assets, so only run it when ready to create
that release. The API/workflow has unit coverage, but needs a configured real
Gitea runner/token for end-to-end release publishing verification.

`test_release_contract.py` builds local Git fixtures, tags a new version, generates
the real assets, checks deterministic archive bytes, and fast-forwards a deployment
through the actual updater while preserving its local `.env`. This needs no GitHub
account, credentials, Docker daemon or live release.

## Repository configuration

1. Enable GitHub Actions. The workflows use pinned action commits, Python 3.11,
   Node 22 for JS validation only, JDK 17, Android API/build tools 35/35.0.0 and
   the repository's checked-in, checksum-pinned Gradle wrapper.
2. Set the repository variable `STREAMVAULT_RELEASE_BRANCH` to the branch you
   maintain for distribution. Protect it against force pushes and require the
   `security`, `web` and `android` checks before merges. No `main`/`master`/owner is guessed.
3. Create a GitHub environment named `release`. Set these environment secrets:
   `STREAMVAULT_KEYSTORE_BASE64`, `STREAMVAULT_STORE_PASSWORD`,
   `STREAMVAULT_KEY_ALIAS`, `STREAMVAULT_KEY_PASSWORD`. The first is the base64
   encoding of your externally stored keystore (`base64 -w0` on Linux). Keep an
   offline backup of the original key; APK updates need the same signing identity.
   Optional environment reviewers control access to signing.
4. The build workflow has read-only repository permission and needs no signing
   secrets. Only the publication job gets `contents: write`; it uses the built-in
   `GITHUB_TOKEN`, with no PAT. Fork pull requests run unsigned checks.

After tests and manual product checks pass, merge version metadata into the
configured branch and create/push its matching tag through your normal maintainer
process. The tag workflow validates the contract, reruns the web/Android checks,
builds and verifies the signed APK, packages/checksums the assets, uploads an Actions
artifact, then **publishes a GitHub release** containing the signed, installable
`streamvault-VERSION-android.apk`, the web archive, `release.json` and `SHA256SUMS`.
Pushing a matching stable tag is the publication action; ordinary branch pushes
only run build/test checks and do not create releases.

For a preview, run **Release server and Android APK** manually, select an existing
tag and leave **publish** unchecked. This creates a draft with the same signed
assets. Check **publish** to publish directly instead. The selected tag must match
the version metadata and belong to the configured distribution branch. No tag is
created implicitly, and no unsigned/debug APK is distributed as the release APK.

Example for the first release, after committing the intended release to `main`:

```sh
python3 vault/scripts/release.py check --tag v1.0.0
git tag -a v1.0.0 -m "StreamVault 1.0.0"
git push origin v1.0.0
```

Use a new version and increment `android_version_code` for subsequent APK updates.
A rerun with an existing release fails safely instead of overwriting published
assets. Review or remove incomplete drafts manually before retrying. The signing
key must remain the same for future updates; keep an offline backup outside this
repository. Generating new keys on every workflow run would prevent APK updates.

## Local clean-checkout builds

Use the commands in the [setup guide](SETUP.md#build-and-test). Python uses pip
and checked-in hash locks; there is no npm project. Android compiles its installed
frontend from the canonical templates using Python/Jinja at build time. The
Android and signed-release jobs install `android/requirements.lock` with hash
verification before Gradle runs the compiler. Generated assets stay in `app/build/`.
Docker builds run without starting production services. Android release builds
are unsigned until all four `STREAMVAULT_*` signing environment variables described
in [the Android guide](../android/README.md#release-signing-and-assets) are set.

On a committed release checkout, prepare local web assets without signing:

```sh
python3 vault/scripts/release.py check --tag v1.0.0
python3 vault/scripts/release.py package --tag v1.0.0 \
  --repository YOUR_OWNER/YOUR_REPOSITORY --branch YOUR_RELEASE_BRANCH
cd dist
sha256sum --check SHA256SUMS
```

Replace the example tag, repository and branch with your release values. Packaging
reads committed files with `git archive HEAD`; commit the reviewed version first.
Add `--apk android/app/build/outputs/apk/release/app-release.apk` only after building
and verifying a signed release. Archives have fixed gzip headers and Git commit
timestamps, so the same commit generates the same web archive. APKs and Docker
images remain subject to upstream tool/image behavior; these jobs provide repeatable
commands and locked Python resolution, not a claim of bit-identical images/APKs.

## Updating dependencies

`requirements.txt`, `requirements-test.txt` and `requirements-tunnel.txt` are
dependency inputs. Docker and CI consume their respective `.lock` files with pip's
hash checking. Refresh all locks together with the same resolver and Python target,
then review pins/hashes and run the clean-checkout checks before merging:

```sh
python3 -m venv .lock-env
.lock-env/bin/python -m pip install uv==0.13.0
.lock-env/bin/uv pip compile vault/core/requirements.txt --python-version 3.11 \
  --universal --generate-hashes --output-file vault/core/requirements.lock
.lock-env/bin/uv pip compile vault/core/requirements-test.txt --python-version 3.11 \
  --universal --generate-hashes --output-file vault/core/requirements-test.lock
.lock-env/bin/uv pip compile vault/core/requirements-tunnel.txt --python-version 3.11 \
  --universal --generate-hashes --output-file vault/core/requirements-tunnel.lock
```

Use one resolver invocation window to keep shared packages aligned. Remove
`.lock-env` after use. Dependabot proposes Actions, Gradle and Python changes;
regenerate the locks when reviewing Python proposals. Gradle/AGP changes must also
retain compatible JDK, SDK and wrapper checksum settings. Docker base images and
Debian package repositories still move; freeze their digests/repositories if your
deployment requires long-term bit-for-bit rebuilds.

## Internal update operations and recovery

The deployment must use the repository's single `vault/docker-compose.yaml`, its
Git root bind mount, persistent `vault/.env`, persistent data bind mount and Docker
socket. The updater discovers host paths and project identity from Docker inspect;
there are no developer-machine paths to configure. Keep these mounts and the
Compose file identity when using this feature. Multiple Compose override files and
detached/source-only checkouts require manual maintenance.

Leave `UPDATE_REMOTE` and `UPDATE_BRANCH` empty to use the checkout's tracking
upstream, or set **both** to an existing Git remote name and branch. Do not put a
repository URL in `UPDATE_REMOTE`. Use a public HTTPS remote for unattended updates;
private repositories require separate Git authentication configured for the
checkout's owner and available in the container. The updater never prompts or
guesses a repository. Fetching needs network access; restart/rebuild use installed
code and do not require GitHub.

In Administration, check the displayed remote/branch and commits, then confirm
Update. The detached worker serializes jobs, rechecks the checkout and target,
fast-forwards code, builds/recreates services and waits for `/api/health`. Schedule
this while playback/downloads are idle. `.env`, databases and persistent data are
protected; uncommitted code, diverged history or upstream changes to protected
paths stop the update. Existing legacy tracked `vault/.env` must not be edited or
removed in release commits: the updater intentionally rejects such changes. The
packaged web asset excludes it; fresh deployments copy `.env.example`. GitHub's
automatic source snapshots still contain tracked files. Resolve legacy configuration
tracking through a separate reviewed migration before public source distribution.

Restart restarts core using installed images. Rebuild builds/recreates the current
checkout. An interrupted build or failed health check retains `requires_rebuild`;
fix the error in Compose logs and retry Rebuild. The updater does not automatically
roll back database changes or install an APK. Preserve `SECRET_KEY`, `.env` and the
data directory, and take a backup before applying a version with migrations.

### Updating an existing deployment

You do not need to reinstall or recreate accounts to update the hosted frontend.
Back up `.env` and persistent data, pause downloads/playback, and check that there
are no uncommitted source changes before updating. Keep `SECRET_KEY`, the existing
data directory and the Compose project identity; do not run the installer again
or use `docker compose down -v` as an update procedure.

For testing deployments that already track Gitea, keep that upstream: its history
is preserved and updates can fast-forward normally. For deployments cloned from
the public GitHub branch, the same commands update from GitHub. Check the remote
on the deployed server, not the developer's workstation.

For manual updates on the server, with the checkout's upstream configured:

```sh
git status --short
git remote -v
git pull --ff-only
docker compose --env-file vault/.env -f vault/docker-compose.yaml up -d --build --force-recreate
docker compose --env-file vault/.env -f vault/docker-compose.yaml ps
curl --fail http://127.0.0.1:8000/api/health
```

If Git reports local changes or divergent/unrelated histories, stop; do not force
the pull or reset the deployment. Moving a legacy Gitea installation to the new
GitHub history requires a fresh branch clone. Copy its private `.env`, preserve
`SECRET_KEY`, set `HOST_DATA_ROOT` to the absolute path of the **existing** data
directory (relative paths resolve against the new Compose directory), and keep
the original Compose project name before recreating services from the new clone.
Do not copy the legacy `.git` directory or delete the old data directory.

After the first manual update, Administration → Overview → Update source and
schedule → **Configured branch** checks the checkout's Git upstream for testing
updates. Check for updates, review the remote/branch, then confirm Install update.
**Published stable releases** only checks published matching version tags, not
every pushed commit. Updates are never installed automatically. The hosted login
also updates inside the Android WebView: reload it after deployment; a native
APK change is not required for a hosted-browser-only correction. The installed
Android frontend is versioned with its APK and needs an APK update for bundled
HTML/JS/CSS changes; its About screen checks the official release manifest and
offers a signature-verified, user-confirmed installation.

See [updater implementation](../vault/core/services/updater_service.py),
[release tooling](../vault/scripts/release.py) and
[workflow files](../.github/workflows) for the authoritative contracts.

## Public repository hygiene

The public repository starts from a sanitized initial snapshot; legacy private
history is not part of its branch. Never push legacy backup branches or tags with
`--mirror`, `--all` or `--tags`. Existing deployments based on a different history
must migrate using a fresh clone and their existing external `.env`/data directory;
the fast-forward updater intentionally refuses to merge unrelated histories.

`.gitignore` excludes runtime databases (including WAL/SHM), media, backups, VPN
profiles, local environments, signing material, session jars, generated APKs and
internal review reports. Ignoring does not untrack files or remove old commits.
Examples, dependency locks, the Gradle wrapper and source tests stay versioned.
The Docker build context is allowlisted separately in `vault/core/.dockerignore`.
Source packaging independently rejects private paths, even if force-added, and
requires an empty output directory so uploading `dist/` cannot leak stale files.

The `security` check scans the selected commit's complete ancestor history with
checksum-pinned Gitleaks, redacting findings and receiving no signing credentials.
Checks and signing use the contract job's validated commit SHA, not a mutable tag.
The APK has no preconfigured server origin; users choose their HTTPS server on
device. APK inspection rejects signing material, databases, environment files and
private checkout metadata before uploading artifacts or packaging a release.
The signing key exists only in the runner's temporary directory, is removed
when its build step exits, and is never an uploaded asset. Drafts require manual
review/publication. See [GitHub's workflow security guidance](https://docs.github.com/en/actions/reference/security/secure-use).

Before distribution, revoke/rotate any previously exposed credentials at their
providers. A clean snapshot and secret scan cannot invalidate leaked keys, inspect
every binary payload, or guarantee the absence of all sensitive information.
