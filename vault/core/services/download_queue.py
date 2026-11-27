"""Durable transfer state and recovery shared by aria2 and yt-dlp."""
import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import datetime
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile
import time
from uuid import uuid4

from config import settings
from services.media_library import INCOMPLETE_MARKER, VIDEO_EXTENSIONS, sync_library_paths

logger = logging.getLogger(__name__)
MANAGED_MARKER = "streamvault-download-v2"
PARTIAL_GUARD = ".streamvault-managed.part"
MAX_ATTEMPTS = 3
ACTIVE = ("pending", "connecting", "downloading", "processing", "cancelling")
TERMINAL = ("completed", "error", "cancelled", "interrupted")
_PUBLIC_STATE = {"pending": "queued", "connecting": "downloading", "error": "failed"}
_JOB_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
    payload TEXT, view TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL,
    attempts INTEGER NOT NULL, verified TEXT, changed REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_active_source ON jobs(kind,fingerprint)
    WHERE status IN ('pending','connecting','downloading','processing','cancelling');
CREATE TABLE IF NOT EXISTS job_requests (
    kind TEXT NOT NULL, request_key TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    PRIMARY KEY (kind,request_key)
);
CREATE INDEX IF NOT EXISTS jobs_listing ON jobs(kind,status,changed);
"""


class TransferError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class TransferJob:
    def __init__(self, root, title, priority):
        self.id = str(uuid4())
        self.download_dir = str(Path(root).resolve() / self.id)
        self.title, self.priority = title, priority
        self.status = "pending"
        self.attempts = 0
        self.progress = 0.0
        self.speed_bytes = 0
        self.eta_seconds = None
        self.size_bytes = None
        self.downloaded_bytes = 0
        self.local_paths = []
        self.local_path = None
        self.error_msg = ""
        self.created_at = datetime.now()
        self._running = self._publishing = False
        self._stop_state = None
        self._last_checkpoint = 0
        self._has_source = True
        self._claim_fd = None

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "status": self.status, "priority": self.priority,
            "progress": round(self.progress, 1), "speed_bytes": self.speed_bytes,
            "eta_seconds": self.eta_seconds, "size_bytes": self.size_bytes,
            "downloaded_bytes": self.downloaded_bytes, "local_paths": self.local_paths,
            "local_path": self.local_path, "error": self.error_msg, "created_at": self.created_at.isoformat(),
        }


def source_fingerprint(*parts):
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class JobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        namespace = sha256(os.fsencode(self.root)).hexdigest()[:20]
        self.directory = Path(settings.DATA_ROOT) / "runtime" / f"jobs-{namespace}"
        self.path = self.directory / "queue.sqlite3"
        self.initialized = False

    @contextmanager
    def connect(self):
        if not self.initialized:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=4)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            if not self.initialized:
                db.executescript(_SCHEMA)
                self.initialized = True
            yield db

    @contextmanager
    def claim(self, job_id):
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid download ID")
        lock = self.directory / (job_id + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield None
            else:
                yield fd
        finally:
            os.close(fd)

    def add(self, kind, job, payload, fingerprint, request_key):
        if request_key:
            request_key = source_fingerprint(request_key)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_key:
                previous = db.execute("""SELECT j.id,j.fingerprint FROM job_requests r
                                      JOIN jobs j ON j.id=r.job_id WHERE r.kind=? AND r.request_key=?""",
                                      (kind, request_key)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ValueError("This request key already identifies another download")
                    return previous["id"]
            active = db.execute(f"SELECT id FROM jobs WHERE kind=? AND fingerprint=? AND status IN ({','.join('?' for _ in ACTIVE)})",
                                (kind, fingerprint, *ACTIVE)).fetchone()
            job_id = active["id"] if active else job.id
            if not active:
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    job.id, kind, fingerprint, json.dumps(payload) if payload is not None else None,
                    json.dumps(job.to_dict()), job.status, job.priority, 0, None, time.time(),
                ))
            if request_key:
                db.execute("INSERT INTO job_requests VALUES (?,?,?)", (kind, request_key, job_id))
            return job_id if active else None

    def get(self, job_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def save(self, job, *, verified=None, reset=False):
        with self.connect() as db:
            db.execute("""
                UPDATE jobs SET view=?,status=?,attempts=?,changed=?,
                    verified=CASE WHEN ? THEN NULL ELSE coalesce(?,verified) END,
                    payload=CASE WHEN ?='completed' THEN NULL ELSE payload END
                WHERE id=?
            """, (json.dumps(job.to_dict()), job.status, job.attempts, time.time(), reset,
                  json.dumps(verified) if verified is not None else None, job.status, job.id))

    def list(self, kind, *, recovering=False):
        with self.connect() as db:
            if recovering:
                statuses = (*ACTIVE, "interrupted")
                return db.execute(f"""
                    SELECT * FROM jobs WHERE kind=? AND status IN ({','.join('?' for _ in statuses)})
                    ORDER BY priority DESC, changed, id
                """, (kind, *statuses)).fetchall()
            return db.execute("""
                SELECT * FROM jobs WHERE kind=? AND status <> 'cancelled'
                ORDER BY CASE WHEN status IN ('pending','connecting','downloading','processing','cancelling')
                              THEN 0 ELSE 1 END, priority DESC, changed DESC
            """, (kind,)).fetchall()

    def clear_history(self, kind, keep=0):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            obsolete = db.execute(f"""
                SELECT id FROM jobs WHERE kind=? AND status IN ({','.join('?' for _ in TERMINAL)})
                ORDER BY changed DESC LIMIT -1 OFFSET ?
            """, (kind, *TERMINAL, keep)).fetchall()
            for row in obsolete:
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                (self.directory / (row["id"] + ".lock")).unlink(missing_ok=True)


def public_job(row):
    if row is None:
        return None
    view = json.loads(row["view"])
    view["state"] = _PUBLIC_STATE.get(row["status"], row["status"])
    view["attempts"] = row["attempts"]
    view["can_resume"] = bool(row["payload"] and row["status"] in {"interrupted", "error", "cancelled"})
    return view


def restore_job_fields(job, row, fields):
    view = public_job(row)
    job.id = row["id"]
    job.download_dir = str(Path(job.download_dir).parent / job.id)
    for name in (*fields, "status", "progress", "size_bytes", "downloaded_bytes", "local_paths", "local_path", "error"):
        if name in view:
            setattr(job, "error_msg" if name == "error" else name, view[name])
    job.priority, job.attempts = row["priority"], row["attempts"]
    job.created_at = datetime.fromisoformat(view["created_at"])
    return job


def prepare_directory(job):
    directory = Path(job.download_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise TransferError("Invalid download directory")
    # Earlier releases ignore any job with a .part file. Keep their startup
    # recovery from exposing an unverified transfer after a rollback.
    fd = os.open(directory / PARTIAL_GUARD, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with NamedTemporaryFile(mode="w", encoding="ascii", dir=directory, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(MANAGED_MARKER)
            file.flush()
            os.fsync(file.fileno())
            temporary.replace(directory / INCOMPLETE_MARKER)
        finally:
            temporary.unlink(missing_ok=True)


def downloaded_videos(job):
    directory = Path(job.download_dir)
    paths = []
    for root, folders, files in os.walk(directory, followlinks=False):
        parent = Path(root)
        for name in files:
            path = parent / name
            if (path.suffix.lower() in VIDEO_EXTENSIONS and not path.is_symlink()
                    and path.is_file() and path.stat().st_size > 0):
                paths.append(path)
    return sorted(paths)


def verified_paths(directory, paths):
    directory = Path(directory).resolve()
    verified = []
    for path in paths:
        stat = path.stat(follow_symlinks=False)
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or stat.st_size <= 0:
            raise TransferError("Download contains an invalid media file")
        verified.append([path.relative_to(directory).as_posix(), stat.st_size, stat.st_mtime_ns])
    if not verified:
        raise TransferError("Download finished but no playable video was downloaded")
    return verified


def publish(job, root, verified):
    directory = Path(job.download_dir)
    if directory.is_symlink():
        raise TransferError("Invalid download directory")
    paths = []
    for relative, size, modified in verified:
        path = directory / relative
        try:
            stat = path.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise TransferError("A verified file is missing") from exc
        if (Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve() != path.absolute()
                or not path.is_file() or stat.st_size != size or stat.st_mtime_ns != modified):
            raise TransferError("A verified file changed before publication")
        paths.append(str(path))
    (directory / PARTIAL_GUARD).unlink(missing_ok=True)
    (directory / INCOMPLETE_MARKER).unlink(missing_ok=True)
    sync_library_paths(root, paths)
    return paths


class PersistentTransfers:
    """Owns durable state. Subclasses supply the transfer engine and public view."""

    def __init__(self, root, kind):
        self.root = root
        self._store = None
        self.kind = kind
        self._jobs = {}
        self._bg_tasks = {}
        self._completion_callbacks = []

    @property
    def store(self):
        root = Path(self.root()).resolve()
        if self._store is None or self._store.root != root:
            self._store = JobStore(root)
        return self._store

    def on_completion(self, callback):
        if callback not in self._completion_callbacks:
            self._completion_callbacks.append(callback)

    def _schedule(self, job):
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        self._bg_tasks[job.id] = task
        task.add_done_callback(lambda completed: self._forget(job, completed))

    def _forget(self, job, task):
        if self._bg_tasks.get(job.id) is task:
            self._bg_tasks.pop(job.id, None)
            self._jobs.pop(job.id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Could not persist transfer state for %s", job.id)

    def _enqueue(self, job, payload, fingerprint, request_key=None):
        existing = self.store.add(self.kind, job, payload, fingerprint, request_key)
        if existing:
            return existing
        self._schedule(job)
        return job.id

    def get_job(self, job_id):
        row = self.store.get(job_id)
        return public_job(row) if row and row["kind"] == self.kind else None

    def get_all_jobs(self):
        return [public_job(row) for row in self.store.list(self.kind)]

    def clear_history(self):
        self.store.clear_history(self.kind)

    async def restore(self):
        rows = await asyncio.to_thread(self.store.list, self.kind, recovering=True)
        for row in rows:
            if row["id"] in self._jobs:
                continue
            if row["status"] == "interrupted" and (not row["payload"] or row["attempts"] >= MAX_ATTEMPTS) and not row["verified"]:
                continue
            self._schedule(self._restore_job(row))

    def resume(self, job_id):
        if job_id in self._jobs:
            job = self._jobs[job_id]
            return job.status in ACTIVE and job.status != "cancelling"
        row = self.store.get(job_id)
        if not row or row["kind"] != self.kind or not public_job(row)["can_resume"]:
            return False
        with self.store.claim(job_id) as claimed:
            if claimed is None:
                return False
            row = self.store.get(job_id)
            if not row or not public_job(row)["can_resume"]:
                return False
            job = self._restore_job(row)
            job.status, job.error_msg, job.attempts = "pending", "", 0
            try:
                self.store.save(job, reset=True)
            except sqlite3.IntegrityError:
                return False
            self._schedule(job)
        return True

    def cancel_job(self, job_id):
        job = self._jobs.get(job_id)
        if job is None:
            row = self.store.get(job_id)
            if not row or row["kind"] != self.kind:
                return False
            if row["status"] == "cancelled":
                return True
            if row["status"] not in {"pending", "interrupted"}:
                return False
            with self.store.claim(job_id) as claimed:
                if claimed is None:
                    return False
                current = self.store.get(job_id)
                if current is None or current["status"] not in {"pending", "interrupted"}:
                    return False
                job = self._restore_job(current)
                job.status = "cancelled"
                self.store.save(job)
                return True
        if job.status in TERMINAL or job._publishing:
            return job.status == "cancelled"
        if job._stop_state:
            return job._stop_state == "cancelled"
        if job._claim_fd is None:
            with self.store.claim(job_id) as claimed:
                if claimed is None:
                    return False
                current = self.store.get(job_id)
                if not current or current["status"] in TERMINAL:
                    return bool(current and current["status"] == "cancelled")
                job.status, job._stop_state = "cancelled", "cancelled"
                self.store.save(job)
                self._bg_tasks[job.id].cancel()
                return True
        previous_status = job.status
        job._stop_state = "cancelled"
        job.status = "cancelling" if job._running else "cancelled"
        try:
            self.store.save(job)
        except Exception:
            job._stop_state = None
            job.status = previous_status
            raise
        self._bg_tasks[job.id].cancel()
        return True

    def stop_all(self):
        return sum(self.cancel_job(job_id) for job_id in list(self._jobs))

    async def close(self):
        tasks = list(self._bg_tasks.values())
        for job in list(self._jobs.values()):
            if job._stop_state or job._publishing or job.status in TERMINAL:
                continue
            job._stop_state = "pending" if job.status == "pending" else "interrupted"
            self._bg_tasks[job.id].cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def checkpoint(self, job, *, force=False):
        now = time.monotonic()
        if force or now - job._last_checkpoint >= 2:
            self.store.save(job)
            job._last_checkpoint = now

    async def complete(self, job, verified):
        job._publishing = True
        try:
            self.store.save(job, verified=verified)
            paths = await asyncio.to_thread(publish, job, self.store.root, verified)
            job.local_paths = paths
            job.local_path = paths[0]
            job.progress = 100.0
            job.status = "completed"
            self.store.save(job)
        finally:
            job._publishing = False
        for callback in self._completion_callbacks:
            try:
                await callback(job.to_dict())
            except Exception:
                logger.warning("Download completion notification failed for %s", job.id)

    @asynccontextmanager
    async def _hold_claim(self, job):
        while True:
            with self.store.claim(job.id) as fd:
                if fd is not None:
                    job._claim_fd = fd
                    try:
                        yield
                    finally:
                        job._claim_fd = None
                    return
            await asyncio.sleep(2)

    async def _run(self, job):
        async with self._hold_claim(job):
            row = self.store.get(job.id)
            if not row or row["status"] in ("completed", "error", "cancelled"):
                return
            job._running = True
            job.attempts = row["attempts"]
            try:
                if row["status"] == "cancelling":
                    job.status = "cancelled"
                elif row["verified"]:
                    job.status = "processing"
                    await self.complete(job, json.loads(row["verified"]))
                elif not job._has_source or job.attempts >= MAX_ATTEMPTS:
                    job.status = "interrupted"
                    job.error_msg = "Download interrupted; the transfer needs a fresh request" if not row["payload"] else "Download interrupted; resume it to retry"
                else:
                    await self._with_retries(job)
            except asyncio.CancelledError:
                job.status = job._stop_state or "interrupted"
                raise
            except (TransferError, ValueError) as exc:
                job.status, job.error_msg = "error", str(exc)
            except Exception as exc:
                logger.warning("Transfer %s failed (%s)", job.id, type(exc).__name__)
                job.status, job.error_msg = "error", "Download could not be completed"
            finally:
                job._running = False
                job.speed_bytes = 0
                job.eta_seconds = None
                self.store.save(job)
                if job.status in TERMINAL:
                    self.store.clear_history(self.kind, keep=60)

    async def _with_retries(self, job):
        while True:
            job.status = "pending"
            self.checkpoint(job, force=True)
            try:
                async with self._slots().hold(job.priority):
                    job.attempts += 1
                    job.error_msg = ""
                    job.status = "downloading"
                    self.checkpoint(job, force=True)
                    await self._download(job)
                return
            except TransferError as exc:
                if not exc.retryable or job.attempts >= MAX_ATTEMPTS:
                    raise
                job.status = "pending"
                job.error_msg = str(exc)
                self.checkpoint(job, force=True)
                await asyncio.sleep(2 ** job.attempts)
