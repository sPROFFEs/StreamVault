"""Fast-forward a configured checkout; rebuild it outside the serving container.

This module uses only the standard library so a snapshot can run in the current
core image even while the application's bind-mounted source is being updated.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid
import zipfile


class UpdateError(RuntimeError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class UpdateManager:
    # Preserve deployment overrides supplied to core as well as vault/.env.
    # Runtime-only container variables (PATH, HOME, HOSTNAME) stay in the image.
    COMPOSE_ENV = {"SECRET_KEY", "ALGORITHM", "ACCESS_TOKEN_EXPIRE_MINUTES", "DOMAIN", "ALLOWED_HOSTS",
                   "CHECK_INTERVAL_MINUTES", "CONCURRENT_DOWNLOADS", "DOWNLOAD_RATE_LIMIT",
                   "TORRENT_UPLOAD_RATE_LIMIT", "MAX_LIVE_REMUXES", "TMDB_API_KEY", "TMDB_TOKEN",
                   "OPENSUBTITLES_API_KEY", "OPENSUBTITLES_USER_AGENT", "UPDATE_REMOTE", "UPDATE_BRANCH",
                   "UPDATE_MODE", "UPDATE_PROVIDER", "UPDATE_API_URL", "UPDATE_REPOSITORY", "UPDATE_CHECK_INTERVAL_HOURS"}

    def __init__(self, workspace, data_root, remote="", branch="", *, mode="branch", provider="auto",
                 api_url="", repository="", check_interval_hours=24):
        self.workspace = Path(workspace).resolve()
        self.data_root = Path(data_root).resolve()
        self.remote, self.branch = remote, branch
        self.directory = self.data_root / "config/updater"
        self.status_path = self.directory / "status.json"
        self.config_path = self.directory / "source.json"
        self.defaults = {"mode": mode, "provider": provider, "api_url": api_url, "repository": repository,
                         "check_interval_hours": check_interval_hours, "automatic_checks": True, "api_token": ""}

    def _configuration(self):
        try:
            return {**self.defaults, **json.loads(self.config_path.read_text())}
        except FileNotFoundError:
            return dict(self.defaults)
        except (ValueError, OSError) as exc:
            raise UpdateError("Update source configuration cannot be read.") from exc

    def configuration(self):
        config = self._configuration()
        return {**{key: value for key, value in config.items() if key != "api_token"},
                "has_token": bool(config.get("api_token"))}

    def configure(self, patch):
        with self.lock():
            self.recover()
            previous = self._configuration()
            config = {**previous, **patch}
            if config["mode"] not in {"branch", "release"} or config["provider"] not in {"auto", "gitea", "github"}:
                raise UpdateError("Choose branch/release mode and auto/gitea/github provider.")
            interval = config["check_interval_hours"]
            if type(interval) is not int or not 1 <= interval <= 168:
                raise UpdateError("Check interval must be between 1 and 168 hours.")
            if type(config["automatic_checks"]) is not bool:
                raise UpdateError("automatic_checks must be boolean.")
            api_url = config["api_url"].strip().rstrip("/")
            parsed = urlsplit(api_url)
            if api_url and (parsed.scheme not in {"https", "http"} or not parsed.hostname
                            or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise UpdateError("Use an HTTP(S) API base URL without credentials or query parameters.")
            repository = config["repository"].strip()
            if repository and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise UpdateError("Repository must be OWNER/REPOSITORY.")
            if any(part in {".", ".."} for part in repository.split("/")):
                raise UpdateError("Invalid repository.")
            if "api_token" not in patch and (api_url != previous["api_url"] or config["provider"] != previous["provider"]):
                config["api_token"] = ""  # Never send the old credential to a newly selected forge.
            token = config.get("api_token", "")
            if not isinstance(token, str) or len(token) > 4096 or any(ord(c) < 32 for c in token):
                raise UpdateError("Invalid API token.")
            if token and not api_url:
                raise UpdateError("Set an explicit API base URL when storing a private release credential.")
            config.update(api_url=api_url, repository=repository)
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix(".tmp")
            # Set permissions at creation, before writing the credential.
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(config, handle)
            temporary.replace(self.config_path)
            self.save({"state": "idle", "message": "Update source saved. Check before installing.",
                       "requires_rebuild": bool(self.read().get("requires_rebuild"))})
            return self.configuration()

    def release_json(self, url, token):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        headers = {"Accept": "application/json", "User-Agent": "StreamVault-updater"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            # Never forward a private forge credential to a redirect or proxy.
            with build_opener(ProxyHandler({}), NoRedirect()).open(Request(url, headers=headers), timeout=15) as response:
                payload = response.read(2 * 1024**2 + 1)
            if len(payload) > 2 * 1024**2:
                raise ValueError("Oversized response")
            data = json.loads(payload)
            if not isinstance(data, list):
                raise ValueError("Invalid release list")
            return data
        except Exception as exc:
            raise UpdateError("Release API unavailable or invalid. Check the provider, URL and credentials.", 502) from exc

    def latest_release(self, selection):
        config = self._configuration()
        parsed = urlsplit(selection["repository"])
        provider = config["provider"]
        if provider == "auto":
            provider = "github" if parsed.hostname == "github.com" else "gitea"
        repository = config["repository"] or parsed.path.strip("/").removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("Configure OWNER/REPOSITORY for the release API.")
        api = config["api_url"] or ("https://api.github.com" if provider == "github"
                                    else urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
        if not api or urlsplit(api).scheme not in {"http", "https"}:
            raise UpdateError("Configure the release API base URL for this Git remote.")
        endpoint = "/repos/" if provider == "github" else "/api/v1/repos/"
        releases = self.release_json(api + endpoint + repository + "/releases?per_page=50&limit=50", config["api_token"])
        stable = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            match = re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", str(release.get("tag_name", "")))
            if match:
                stable.append((tuple(map(int, match.groups())), release))
        if not stable:
            raise UpdateError("The configured repository has no published stable vMAJOR.MINOR.PATCH release.", 404)
        version, release = max(stable, key=lambda item: item[0])
        return {"release_tag": release["tag_name"], "available_version": ".".join(map(str, version)),
                "release_notes": str(release.get("body") or "")[:12000], "provider": provider}

    def version_at(self, commit):
        try:
            return json.loads(self.git("show", f"{commit}:vault/core/version.json"))["version"]
        except (UpdateError, ValueError, KeyError):
            return None

    def history(self):
        try:
            return json.loads((self.directory / "history.json").read_text())
        except (OSError, ValueError):
            return []

    def backups(self):
        folder = self.directory / "backups"
        return [{"name": path.name, "size_bytes": path.stat().st_size, "created_at": path.stat().st_mtime}
                for path in sorted(folder.glob("*.zip"), reverse=True) if path.is_file() and not path.is_symlink()]

    def backup(self):
        folder = self.directory / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".zip"
        destination = folder / name
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({"schema": 1, "created_at": time.time(),
                    "commit": self.git("rev-parse", "HEAD"), "includes_media": False}))
                env = self.workspace / "vault/.env"
                if env.is_file() and not env.is_symlink():
                    archive.write(env, "deployment/vault.env")
                databases = list(self.data_root.glob("*.db")) + list((self.data_root / "users").glob("*/*.db"))
                for path in databases:
                    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.data_root):
                        continue
                    with tempfile.TemporaryDirectory(prefix="streamvault-db-backup-") as temporary:
                        snapshot = Path(temporary) / "snapshot.db"
                        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10) as source:
                            with sqlite3.connect(snapshot) as target:
                                deadline = time.monotonic() + 60
                                def progress(status, remaining, total):
                                    if time.monotonic() > deadline:
                                        raise TimeoutError("Database snapshot deadline exceeded")
                                source.backup(target, pages=128, progress=progress, sleep=0.01)
                        archive.write(snapshot, "data/" + path.relative_to(self.data_root).as_posix())
                config = self.data_root / "config"
                for path in config.rglob("*") if config.exists() else []:
                    if (path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(config.resolve()) and self.directory not in path.parents
                            and path.stat().st_size <= 5 * 1024**2):
                        archive.write(path, "data/config/" + path.relative_to(config).as_posix())
                if self.config_path.is_file():
                    archive.write(self.config_path, "data/config/updater/source.json")
            return {"name": name, "size_bytes": destination.stat().st_size, "created_at": destination.stat().st_mtime}
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise UpdateError("Pre-update database/configuration backup failed; no update was applied.", 500) from exc

    @contextmanager
    def lock(self, blocking=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise UpdateError("Another update or check is already running.", 409) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self):
        try:
            return json.loads(self.status_path.read_text())
        except FileNotFoundError:
            return {"state": "idle", "message": "Check the configured Git branch for updates."}
        except (ValueError, OSError):
            return {"state": "failure", "message": "Update status could not be read. Check again."}

    def save(self, data):
        self.directory.mkdir(parents=True, exist_ok=True)
        data = {**data, "updated_at": time.time()}
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(self.status_path)
        if data.get("state") in {"success", "failure"}:
            entries = self.history()
            entries.append({key: data.get(key) for key in ("state", "message", "updated_at", "operation", "job_id",
                            "local_commit", "remote_commit", "release_tag", "backup") if data.get(key) is not None})
            history_path = self.directory / "history.json"
            temporary = history_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(entries[-50:]))
            temporary.replace(history_path)
        return data

    def failure(self, exc):
        return self.save({**self.read(), "state": "failure", "message": str(exc), "error_code": exc.status_code})

    def command(self, args, *, cwd=None, timeout=60, env=None, error="Update command failed"):
        try:
            result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                                    text=True, timeout=timeout, check=True)
            # Porcelain and diff -z include significant spaces in filenames.
            return result.stdout if "\0" in result.stdout else result.stdout.strip()
        except subprocess.TimeoutExpired as exc:
            raise UpdateError(f"{error}: timed out.", 504) from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            # Git URLs / Docker env output can contain credentials. Never echo
            # command lines or stderr into the administrator's status payload.
            raise UpdateError(f"{error}.", 502) from exc

    def git(self, *args, timeout=60, error="Git command failed"):
        if not self.workspace.is_dir():
            raise UpdateError("UPDATE_WORKSPACE must point to the mounted Git checkout.")
        owner = self.workspace.stat().st_uid
        prefix = (["sudo", "-n", "-u", f"#{owner}", "--", "env", "GIT_TERMINAL_PROMPT=0",
                   "GIT_SSH_COMMAND=ssh -oBatchMode=yes"] if owner != os.geteuid() else [])
        return self.command([*prefix, "git", "-c", f"safe.directory={self.workspace}", *args],
                            cwd=self.workspace, timeout=timeout,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes"},
                            error=error)

    def docker(self, *args, timeout=30):
        prefix = ["sudo", "-n"] if os.geteuid() != 0 else []
        return self.command([*prefix, "docker", *args], timeout=timeout, error="Docker operation failed")

    def worker_running(self, name):
        try:
            return self.docker("inspect", "--format", "{{.State.Running}}", name) == "true"
        except UpdateError:
            # Distinguish a missing helper from an unavailable Docker daemon.
            self.docker("info", "--format", "{{.ServerVersion}}")
            return False

    def recover(self):
        data = self.read()
        if data["state"] == "checking":
            return self.failure(UpdateError("The previous update check was interrupted. Check again."))
        if data["state"] == "updating":
            if not data.get("worker") or not self.worker_running(data["worker"]):
                return self.failure(UpdateError("The update worker was interrupted. Check again and retry; configuration and data were kept."))
            raise UpdateError("An update is already running.", 409)
        return data

    def status(self):
        try:
            with self.lock():
                return self.recover()
        except UpdateError:
            # A held lock belongs to the checking process or detached worker.
            # Do not rewrite its state, including when Docker is unavailable.
            return self.read()

    def selection(self):
        root = self.git("rev-parse", "--show-toplevel", error="UPDATE_WORKSPACE is not a Git checkout")
        if Path(root).resolve() != self.workspace:
            raise UpdateError("UPDATE_WORKSPACE must be the Git repository root.")
        local_branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD", error="Detached checkouts cannot be updated")
        if bool(self.remote) != bool(self.branch):
            raise UpdateError("Set both UPDATE_REMOTE and UPDATE_BRANCH, or configure the checkout's upstream.")
        if self.remote:
            remote, branch = self.remote, self.branch
        else:
            remote = self.git("config", "--get", f"branch.{local_branch}.remote", error="No upstream configured; set UPDATE_REMOTE and UPDATE_BRANCH")
            ref = self.git("config", "--get", f"branch.{local_branch}.merge", error="No upstream branch configured")
            if not ref.startswith("refs/heads/"):
                raise UpdateError("The upstream must select a branch.")
            branch = ref.removeprefix("refs/heads/")
        if remote == "." or remote not in self.git("remote").splitlines():
            raise UpdateError("UPDATE_REMOTE must name a configured Git remote.")
        self.git("check-ref-format", f"refs/heads/{branch}", error="Invalid update branch")
        url = self.git("remote", "get-url", remote)
        if "://" in url:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            url = urlunsplit((parsed.scheme, host + (f":{parsed.port}" if parsed.port else ""), parsed.path, "", ""))
        elif "@" in url:
            url = url.split("@", 1)[1]
        return {"remote": remote, "branch": branch, "repository": url, "local_branch": local_branch}

    def protected(self, path):
        relative = Path(path)
        absolute = self.workspace / relative
        return (relative.name == ".env" or relative.suffix in {".db", ".sqlite", ".sqlite3"}
                or relative == Path("vault/data") or Path("vault/data") in relative.parents
                or absolute == self.data_root or self.data_root in absolute.parents)

    def clean_checkout(self):
        # NUL separation preserves spaces, newlines and Unicode in filenames.
        entries = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            paths = [entry[3:]]
            if "R" in entry[:2] or "C" in entry[:2]:
                paths.append(entries[index])
                index += 1
            # Only modifications/untracked persistent files are allowed.
            # Staged additions/deletions/renames change Git's merge behavior.
            if entry[:2] not in {" M", "??"} or not all(self.protected(p) for p in paths):
                raise UpdateError("The checkout has uncommitted code changes. Commit or move them before updating.", 409)

    def validate_target(self, target):
        self.clean_checkout()
        self.git("merge-base", "--is-ancestor", "HEAD", target, error="The selected branch cannot fast-forward this checkout; local commits or rewritten history require manual resolution")
        changes = self.git("diff", "--no-renames", "--name-only", "-z", "HEAD", target).split("\0")
        if any(path and self.protected(path) for path in changes):
            raise UpdateError("The update changes persistent configuration or data paths. Review and migrate these files manually.", 409)

    def checked(self):
        selection = self.selection()
        self.clean_checkout()
        config = self._configuration()
        release = self.latest_release(selection) if config["mode"] == "release" else {}
        shallow = self.git("rev-parse", "--is-shallow-repository") == "true"
        ref = f"refs/tags/{release['release_tag']}" if release else f"refs/heads/{selection['branch']}"
        self.git("fetch", "--no-tags", "--no-recurse-submodules", *(["--unshallow"] if shallow else []),
                 "--", selection["remote"], ref,
                 error="Could not fetch the configured remote branch/release; check network access, credentials and name")
        local = self.git("rev-parse", "HEAD")
        target = self.git("rev-parse", "FETCH_HEAD^{commit}")
        if release:
            if self.version_at(target) != release["available_version"]:
                raise UpdateError("Release tag does not match its committed version metadata.", 409)
            try:
                self.git("merge-base", "--is-ancestor", target, local)
                target = local  # Already installed/ahead: never downgrade.
            except UpdateError:
                pass
        self.validate_target(target)
        pending = bool(self.read().get("requires_rebuild"))
        return {**selection, **release, "mode": config["mode"], "installed_version": self.version_at(local),
                "checked_at": time.time(), "local_commit": local, "remote_commit": target,
                "requires_rebuild": pending, "state": "current" if local == target else "available",
                "message": ("Code is current; a rebuild is still required." if pending else "The configured branch is current.")
                if local == target else "An update is available from the configured " + ("stable release." if release else "branch.")}

    def check(self):
        with self.lock():
            self.recover()
            self.save({**self.read(), "state": "checking", "checked_at": time.time(), "message": "Checking the configured update source…"})
            try:
                return self.save(self.checked())
            except UpdateError as exc:
                self.failure(exc)
                raise

    def deployment(self):
        container_id = os.environ.get("HOSTNAME", "")
        if not container_id:
            raise UpdateError("The updater requires the Docker Compose core deployment.")
        info = json.loads(self.docker("inspect", container_id))[0]
        labels = info["Config"].get("Labels") or {}
        if labels.get("com.docker.compose.service") != "core" or not labels.get("com.docker.compose.project"):
            raise UpdateError("The updater requires the Docker Compose core deployment.")
        mounts = {mount["Destination"]: mount for mount in info.get("Mounts", []) if mount["Type"] == "bind"}
        workspace, data = mounts.get(str(self.workspace)), mounts.get(str(self.data_root))
        if not workspace or not data:
            raise UpdateError("The updater requires bind mounts for UPDATE_WORKSPACE and DATA_ROOT.")
        host_workspace = workspace["Source"]
        expected_file = f"{host_workspace}/vault/docker-compose.yaml"
        if labels.get("com.docker.compose.project.config_files") != expected_file:
            raise UpdateError("The updater supports the repository's single vault/docker-compose.yaml deployment.")
        if not (self.workspace / "vault/.env").is_file():
            raise UpdateError("The updater requires the persistent vault/.env deployment configuration.")
        try:
            self.docker("compose", "version")
        except UpdateError as exc:
            raise UpdateError("The core image needs the Docker Compose plugin. Rebuild core with the installer before using the updater.") from exc
        return {"workspace": host_workspace, "data": data["Source"],
                "project": labels["com.docker.compose.project"], "image": info["Image"],
                "environment": info["Config"].get("Env", [])}

    def start(self, operation="update"):
        if operation not in {"update", "restart", "rebuild"}:
            raise UpdateError("Unknown updater operation.")
        with self.lock():
            self.recover()
            try:
                if operation == "update":
                    self.save({**self.read(), "state": "checking", "checked_at": time.time(), "message": "Verifying the configured source before updating…"})
                    result = self.checked()
                else:
                    # Restart/rebuild operate on installed code and remain
                    # usable while GitHub is unavailable.
                    result = {**self.read(), "local_commit": self.git("rev-parse", "HEAD")}
                if operation == "update" and result["state"] == "current" and not result["requires_rebuild"]:
                    return self.save(result)
                deployment = self.deployment()
                job_id = uuid.uuid4().hex
                name = "streamvault-update-" + hashlib.sha256(str(self.workspace).encode()).hexdigest()[:12] + "-" + job_id[:8]
                # Run a fixed snapshot so updating bind-mounted Python cannot
                # alter the worker halfway through the operation.
                snapshot = self.directory / "worker.py"
                snapshot.write_bytes(Path(__file__).read_bytes())
                environment = [arg for value in deployment.get("environment", [])
                               if value.partition("=")[0] in self.COMPOSE_ENV for arg in ("--env", value)]
                result = self.save({**result, "state": "updating", "operation": operation,
                                    "job_id": job_id, "worker": name,
                                    "message": "Update worker queued. Waiting for deployment completion…"})
                self.docker("run", "--detach", "--rm", "--name", name, "--user", "0", "--entrypoint", "python",
                            "--volume", "/var/run/docker.sock:/var/run/docker.sock",
                            "--volume", f"{deployment['workspace']}:{deployment['workspace']}",
                            "--volume", f"{deployment['data']}:/saas-data",
                            *environment,
                            deployment["image"], "/saas-data/config/updater/worker.py",
                            "--workspace", deployment["workspace"], "--data-root", "/saas-data",
                            "--remote", result.get("remote", ""), "--branch", result.get("branch", ""),
                            "--target", result.get("remote_commit", result["local_commit"]), "--job-id", job_id,
                            "--operation", operation, "--project", deployment["project"], "--host-data", deployment["data"])
                return result
            except UpdateError as exc:
                queued = self.read()
                if queued["state"] == "updating":
                    try:
                        running = self.worker_running(queued["worker"])
                    except UpdateError:
                        # A lost Docker response may still have started a
                        # helper. Keep its reservation until status is known.
                        running = True
                    if running:
                        return self.save({**queued, "message": "Worker launch response was lost. Waiting for deployment status…"})
                self.failure(exc)
                raise

    def apply_code(self, target):
        self.validate_target(target)
        self.git("merge", "--ff-only", "--no-edit", "--no-overwrite-ignore", target,
                 error="Fast-forward update failed; the checkout was preserved")

    def compose(self, *args, timeout=1800):
        directory = self.workspace / "vault"
        env = {**os.environ, "HOST_DATA_ROOT": self.host_data}
        return self.command(["docker", "compose", "--project-name", self.project,
                             "--project-directory", str(directory), "--env-file", str(directory / ".env"),
                             "-f", str(directory / "docker-compose.yaml"), *args], cwd=directory,
                            env=env, timeout=timeout, error=f"Compose {args[0]} failed")

    def wait_ready(self):
        for _ in range(60):
            try:
                self.compose("exec", "-T", "core", "curl", "--fail", "--silent", "--max-time", "2",
                             "http://127.0.0.1:8000/api/health", timeout=5)
                return
            except UpdateError:
                time.sleep(2)
        raise UpdateError("Core did not become healthy after deployment. Check container logs and retry the rebuild.", 504)

    def run_worker(self, job_id, target, operation, project, host_data):
        self.project, self.host_data = project, host_data
        with self.lock(blocking=True):
            if self.read().get("job_id") != job_id or self.read()["state"] != "updating":
                return
            try:
                if operation != "restart":
                    self.save({**self.read(), "phase": "backup", "message": "Backing up databases and configuration before deployment…"})
                    self.save({**self.read(), "backup": self.backup()["name"]})
                if operation == "update":
                    selected = self.selection()
                    queued = self.read()
                    if (selected["local_branch"] != queued.get("local_branch")
                            or self.git("rev-parse", "HEAD") not in {queued.get("local_commit"), target}):
                        raise UpdateError("The checkout changed after the update was queued. Check again before updating.", 409)
                    # Persist before merging: an interrupted merge/rebuild must
                    # leave a visible retry action even if HEAD is now current.
                    self.save({**self.read(), "requires_rebuild": True,
                               "message": "Fast-forwarding code; configuration and data are preserved…"})
                    self.apply_code(target)
                if operation != "restart":
                    self.save({**self.read(), "requires_rebuild": True, "message": "Building deployment images…"})
                    self.compose("build")
                    self.save({**self.read(), "message": "Recreating services and waiting for core health…"})
                    self.compose("up", "-d", "--force-recreate")
                else:
                    self.compose("restart", "core", timeout=120)
                self.wait_ready()
                self.save({**self.read(), "state": "success", "local_commit": self.git("rev-parse", "HEAD"),
                           "installed_version": self.version_at("HEAD"),
                           "requires_rebuild": False if operation != "restart" else bool(self.read().get("requires_rebuild")),
                           "message": "Deployment completed and core is healthy."})
            except UpdateError as exc:
                self.failure(exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for field in ("workspace", "data-root", "remote", "branch", "target", "job-id", "operation", "project", "host-data"):
        parser.add_argument(f"--{field}", required=True)
    args = parser.parse_args()
    manager = UpdateManager(args.workspace, args.data_root, args.remote, args.branch)
    manager.run_worker(args.job_id, args.target, args.operation, args.project, args.host_data)
