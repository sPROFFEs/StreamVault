"""Bounded SQLite storage for public TMDB responses."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
import os
import sqlite3
from threading import Lock


@dataclass(frozen=True)
class CachedMetadata:
    value: dict | None
    fresh_until: float


class MetadataCache:
    def __init__(self, path, max_entries, max_bytes):
        self.path = path
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._initialized = False
        self._lock = Lock()

    @contextmanager
    def _connect(self):
        with self._lock:
            if not self._initialized or not self.path.exists():
                self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                with closing(sqlite3.connect(self.path, timeout=.25)) as db, db:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS metadata (
                            key TEXT PRIMARY KEY, payload BLOB NOT NULL, size INTEGER NOT NULL,
                            fresh_until REAL NOT NULL, stale_until REAL NOT NULL, accessed REAL NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS metadata_lru ON metadata(accessed, key);
                    """)
                self._initialized = True
        with closing(sqlite3.connect(self.path, timeout=.25)) as db, db:
            yield db

    def get(self, key, now):
        entry = None
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload, fresh_until FROM metadata WHERE key=? AND stale_until>?",
                    (key, now),
                ).fetchone()
                if row is not None:
                    entry = CachedMetadata(json.loads(row[0]), row[1])
                    db.execute("UPDATE metadata SET accessed=? WHERE key=?", (now, key))
        except (OSError, sqlite3.Error, ValueError):
            # A read-only, full or unavailable cache must not disable the provider.
            pass
        return entry

    def store(self, key, value, now, ttl, stale):
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(payload) > self.max_bytes or self.max_entries <= 0:
            return
        try:
            with self._connect() as db:
                # Allow SQLite index overhead and reuse evicted pages while
                # keeping the database's page allocation bounded.
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
                pages = max(256, (2 * self.max_bytes + page_size - 1) // page_size)
                db.execute(f"PRAGMA max_page_count={pages}")
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM metadata WHERE stale_until<=? OR key=?", (now, key))
                entries, used = db.execute("SELECT count(*), coalesce(sum(size), 0) FROM metadata").fetchone()
                if entries >= self.max_entries or used + len(payload) > self.max_bytes:
                    oldest = db.execute("SELECT key,size FROM metadata ORDER BY accessed,key").fetchall()
                    evicted = []
                    for old_key, size in oldest:
                        evicted.append((old_key,))
                        entries -= 1
                        used -= size
                        if entries < self.max_entries and used + len(payload) <= self.max_bytes:
                            break
                    db.executemany("DELETE FROM metadata WHERE key=?", evicted)
                db.execute("INSERT INTO metadata VALUES (?,?,?,?,?,?)",
                           (key, payload, len(payload), now + ttl, now + ttl + stale, now))
        except (OSError, sqlite3.Error):
            pass
