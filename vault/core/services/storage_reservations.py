"""Atomic per-volume admission for the core and downloader containers."""
import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
import fcntl
from functools import lru_cache
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from config import settings
from services import storage_policy as policy

_DEFAULT_DOWNLOAD_WINDOW = 64 * 1024**2
_TOKEN = re.compile(r"[a-f0-9]{32}")


def download_window(rate_bytes_per_second):
    # At least two progress-check intervals of space before an unknown transfer starts.
    return max(_DEFAULT_DOWNLOAD_WINDOW, 10 * rate_bytes_per_second)


def _device(path):
    existing = Path(path).resolve()
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return existing.stat().st_dev


def _directory():
    return Path(settings.DATA_ROOT) / "runtime" / "space-reservations"


@lru_cache(maxsize=16)
def _initialize(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "ledger.sqlite3", timeout=15)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                token TEXT PRIMARY KEY, device INTEGER NOT NULL, category TEXT NOT NULL, remaining INTEGER NOT NULL,
                job_id TEXT
            )
        """)


@contextmanager
def _transaction(directory):
    _initialize(directory)
    with closing(sqlite3.connect(directory / "ledger.sqlite3", timeout=15)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def _live_rows(db, directory, device, own_token=None):
    rows = db.execute("SELECT token,category,remaining,job_id FROM reservations WHERE device=?", (device,)).fetchall()
    for token, category, remaining, job_id in rows:
        if token == own_token:
            continue
        if not _TOKEN.fullmatch(token):
            raise ValueError("Invalid storage reservation")
        lock = directory / f"{token}.lock"
        try:
            fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            db.execute("DELETE FROM reservations WHERE token=?", (token,))
            continue
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield category, remaining, job_id
            else:
                db.execute("DELETE FROM reservations WHERE token=?", (token,))
                lock.unlink(missing_ok=True)
        finally:
            os.close(fd)


def reservation_totals(path):
    directory, device = _directory(), _device(path)
    totals = {"downloads": 0, "cache": 0}
    with _transaction(directory) as db:
        for category, remaining, _ in _live_rows(db, directory, device):
            totals[category] += remaining
    return totals


def active_download_job_ids(path):
    directory, device = _directory(), _device(path)
    with _transaction(directory) as db:
        return {job for category, _, job in _live_rows(db, directory, device) if category == "downloads" and job}


class DiskReservation:
    def __init__(self, path, category, job_id):
        if category not in {"downloads", "cache"}:
            raise ValueError("Unknown storage category")
        self.path = Path(path)
        self.category = category
        self.job_id = job_id
        self.device = _device(self.path)
        self.directory = _directory()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.token = uuid4().hex
        self.lock = self.directory / f"{self.token}.lock"
        self.fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        self.remaining = None

    def resize(self, remaining):
        remaining = max(0, int(remaining))
        with _transaction(self.directory) as db:
            others = list(_live_rows(db, self.directory, self.device, self.token))
            physical_reserve = int(settings.MIN_FREE_DISK_GB * 1024**3)
            growing = self.remaining is None or remaining > self.remaining
            usage = policy.get_path_size_bytes(str(self.path), max_age=0 if growing else 5)
            if policy.disk_free_bytes(self.path) - remaining - sum(size for _, size, _ in others) < physical_reserve:
                raise ValueError("Insufficient disk space. The server's free disk reserve must be preserved.")
            quota = (policy.get_global_download_quota_gb() * 1024**3 if self.category == "downloads"
                     else policy.cache_limit_bytes())
            promised = sum(size for category, size, _ in others if category == self.category)
            if usage + promised + remaining > quota:
                raise ValueError("Download quota exceeded." if self.category == "downloads" else "Playback cache quota exceeded.")
            db.execute("INSERT OR REPLACE INTO reservations(token,device,category,remaining,job_id) VALUES (?,?,?,?,?)",
                       (self.token, self.device, self.category, remaining, self.job_id))
        self.remaining = remaining

    def close(self):
        try:
            with _transaction(self.directory) as db:
                db.execute("DELETE FROM reservations WHERE token=?", (self.token,))
                self.lock.unlink(missing_ok=True)
        finally:
            os.close(self.fd)


@contextmanager
def reserve_space(path, category, remaining, *, job_id=None):
    lease = DiskReservation(path, category, job_id)
    try:
        lease.resize(remaining)
        yield lease
    finally:
        lease.close()


@asynccontextmanager
async def reserve_space_async(path, category, remaining, *, job_id=None):
    reservation = reserve_space(path, category, remaining, job_id=job_id)
    acquisition = asyncio.create_task(asyncio.to_thread(reservation.__enter__))
    try:
        lease = await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        # The thread can still proceed after cancellation. Release any lease
        # it acquired before letting cancellation finish.
        try:
            await acquisition
        except Exception:
            pass
        else:
            await asyncio.to_thread(reservation.__exit__, None, None, None)
        raise
    try:
        yield lease
    finally:
        await asyncio.to_thread(reservation.__exit__, None, None, None)
