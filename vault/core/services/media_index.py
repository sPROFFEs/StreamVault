"""Persistent, incremental index of the downloads volume."""
from contextlib import closing, contextmanager
from functools import lru_cache
from hashlib import sha256
import asyncio
import fcntl
import json
import logging
from math import ceil
import os
from pathlib import Path
import sqlite3
from stat import S_ISREG
import time

from config import settings
from services.media_identity import normalized_title

logger = logging.getLogger(__name__)
RECONCILE_SECONDS = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_state (id INTEGER PRIMARY KEY CHECK (id = 1), scanned_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS identification_overrides (path TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
    path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime INTEGER NOT NULL, ctime INTEGER NOT NULL,
    title_key TEXT NOT NULL, original_key TEXT NOT NULL, localized_key TEXT NOT NULL, search_name TEXT NOT NULL,
    sort_title TEXT NOT NULL, group_key TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    tmdb_id INTEGER, data TEXT NOT NULL, tracks TEXT, probe_version INTEGER, accessed_at REAL
);
CREATE INDEX IF NOT EXISTS media_by_title ON entries(kind, title_key);
CREATE INDEX IF NOT EXISTS media_by_original ON entries(kind, original_key);
CREATE INDEX IF NOT EXISTS media_by_localized ON entries(kind, localized_key);
CREATE INDEX IF NOT EXISTS media_by_tmdb ON entries(kind, tmdb_id);
CREATE INDEX IF NOT EXISTS media_by_group ON entries(group_key, mtime DESC);
"""
_VISIBLE = "status NOT IN ('extra', 'incomplete')"


class LibraryIndex:
    def __init__(self, root, path):
        self.root = Path(root).resolve()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            yield db

    @contextmanager
    def writer(self):
        # Serialize directory snapshots and scoped updates across core/downloader.
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _snapshot_time(self, db):
        row = db.execute("SELECT scanned_at FROM index_state WHERE id=1").fetchone()
        return row[0] if row else None

    def ensure_initialized(self):
        with self.connect() as db:
            if self._snapshot_time(db) is not None:
                return
        self.reconcile(force=False)

    def _file(self, relative):
        from services.media_library import VIDEO_EXTENSIONS, is_incomplete_media

        path = Path(relative)
        if path.is_absolute():
            path = path.relative_to(self.root)
        if ".." in path.parts:
            raise ValueError("Path is outside the media library")
        target = self.root / path
        if target.resolve() != target or target.suffix.lower() not in VIDEO_EXTENSIONS:
            return path.as_posix(), None, None
        try:
            info = target.stat(follow_symlinks=False)
        except FileNotFoundError:
            return path.as_posix(), None, None
        if not S_ISREG(info.st_mode) or not info.st_size:
            return path.as_posix(), None, None
        status = "incomplete" if is_incomplete_media(target, self.root) else None
        return path.as_posix(), info, status

    @staticmethod
    def _entry(relative, stat, status, override):
        from services.media_library import identify_video

        name = Path(relative).name
        result = identify_video(name, relative)
        if override:
            result.update(override, identification_status="manual", identification_confidence=1.0)
            episodes = valid_episodes(result.get("episode_numbers"), result["media_type"])
            result.update(episode_number=episodes[0] if episodes else None, episode_numbers=episodes)
        state = status or result["identification_status"]
        result.update(name=name, relative_path=relative, size_bytes=stat.st_size, modified_at=stat.st_mtime,
                      file_status=state if state in {"incomplete", "extra"} else "ready")
        return result, state

    def _upsert(self, db, relative, stat, status, override):
        item, state = self._entry(relative, stat, status, override)
        name, title, kind = item["name"], item["title"], item["media_type"]
        group = ("series:" + title.casefold()) if kind == "series" else ("movie:" + relative)
        db.execute("""
            INSERT INTO entries(path, size, mtime, ctime, title_key, original_key, localized_key,
                                search_name, sort_title, group_key, kind, status, tmdb_id, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, ctime=excluded.ctime,
                title_key=excluded.title_key, original_key=excluded.original_key, localized_key=excluded.localized_key,
                search_name=excluded.search_name, sort_title=excluded.sort_title, group_key=excluded.group_key,
                kind=excluded.kind, status=excluded.status, tmdb_id=excluded.tmdb_id, data=excluded.data,
                tracks=CASE WHEN entries.ctime=excluded.ctime AND entries.mtime=excluded.mtime
                            AND entries.size=excluded.size THEN entries.tracks END
        """, (relative, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, normalized_title(title),
              normalized_title(item.get("original_title")), normalized_title(item.get("localized_title")),
              name.casefold(), title.casefold(), group, kind, state, item.get("tmdb_id"), json.dumps(item)))
        return item

    def reconcile(self, *, force=True):
        from services.media_library import iter_library_entries

        with self.writer(), self.connect() as db:
            last = self._snapshot_time(db)
            if last is not None and not force and time.time() - last < RECONCILE_SECONDS:
                return
            known = {row[0]: row[1:] for row in db.execute("SELECT path,size,mtime,ctime,status FROM entries")}
            overrides = {path: json.loads(data) for path, data in db.execute("SELECT path,data FROM identification_overrides")}
            changed = []
            for relative, stat, status in iter_library_entries(self.root):
                old = known.pop(relative, None)
                if old and old[:3] == (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) and (
                    (old[3] == "incomplete") == (status == "incomplete")
                ):
                    continue
                changed.append((relative, stat, status, overrides.get(relative)))
            for args in changed:
                self._upsert(db, *args)
            db.executemany("DELETE FROM entries WHERE path=?", ((path,) for path in known))
            db.execute("INSERT OR REPLACE INTO index_state(id,scanned_at) VALUES (1,?)", (time.time(),))

    def sync_paths(self, paths):
        with self.writer(), self.connect() as db:
            for path in dict.fromkeys(paths):
                relative, stat, status = self._file(path)
                if stat is None:
                    db.execute("DELETE FROM entries WHERE path=?", (relative,))
                    continue
                row = db.execute("SELECT data FROM identification_overrides WHERE path=?", (relative,)).fetchone()
                self._upsert(db, relative, stat, status, json.loads(row[0]) if row else None)

    def get_file(self, relative):
        with self.connect() as db:
            row = db.execute("SELECT data FROM entries WHERE path=?", (relative,)).fetchone()
            return json.loads(row[0]) if row else None

    def set_override(self, relative, data):
        with self.writer(), self.connect() as db:
            relative, stat, status = self._file(relative)
            if stat is None or status == "incomplete":
                raise FileNotFoundError("Completed video not found")
            if data is None:
                db.execute("DELETE FROM identification_overrides WHERE path=?", (relative,))
            else:
                db.execute("INSERT OR REPLACE INTO identification_overrides(path,data) VALUES (?,?)",
                           (relative, json.dumps(data)))
            return self._upsert(db, relative, stat, status, data)

    def describe(self, relative):
        relative, stat, status = self._file(relative)
        if stat is None or status == "incomplete":
            raise FileNotFoundError("Completed video not found")
        with self.connect() as db:
            row = db.execute("SELECT data FROM identification_overrides WHERE path=?", (relative,)).fetchone()
            return self._entry(relative, stat, status, json.loads(row[0]) if row else None)[0]

    def all(self):
        self.ensure_initialized()
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                f"SELECT data FROM entries WHERE {_VISIBLE} ORDER BY mtime DESC, path DESC")]

    def candidates(self, titles, kind, tmdb_id):
        self.ensure_initialized()
        keys = sorted({normalized_title(title) for title in titles} - {""})
        queries, values = [], []
        if keys:
            placeholders = ",".join("?" for _ in keys)
            for column in ("title_key", "original_key", "localized_key"):
                queries.append(f"SELECT path FROM entries WHERE kind=? AND {column} IN ({placeholders})")
                values.extend([kind, *keys])
        if tmdb_id:
            queries.append("SELECT path FROM entries WHERE kind=? AND tmdb_id=?")
            values.extend([kind, tmdb_id])
        if not queries:
            return []
        # A UNION allows each branch to use its full (kind, key) index,
        # including libraries where essentially every entry is a movie.
        matching_paths = " UNION ".join(queries)
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                f"SELECT data FROM entries WHERE path IN ({matching_paths}) AND status IN ('parsed','manual')"
                " ORDER BY mtime DESC,path DESC", values)]

    def page(self, query, kind, sort, page, page_size):
        self.ensure_initialized()
        filters, args = [_VISIBLE], []
        if kind != "all":
            filters.append("kind=?")
            args.append(kind)
        if query.strip():
            term = query.strip().casefold()
            filters.append("(instr(search_name,?)>0 OR instr(sort_title,?)>0 OR "
                           "(?<>'' AND (instr(original_key,?)>0 OR instr(localized_key,?)>0 OR instr(title_key,?)>0)))")
            key = normalized_title(term)
            args.extend([term, term, key, key, key, key])
        where = " AND ".join(filters)
        order = {"title": "MIN(sort_title)", "size": "SUM(size) DESC", "recent": "MAX(mtime) DESC"}[sort]
        with self.connect() as db:
            db.execute("BEGIN")
            total, groups = db.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT group_key) FROM entries WHERE {where}", args
            ).fetchone()
            pages = max(1, ceil(groups / page_size))
            page = min(page, pages)
            rows = db.execute(f"""
                WITH selected AS (
                    SELECT group_key, ROW_NUMBER() OVER (ORDER BY {order},MAX(mtime) DESC,MAX(path) DESC) AS position
                    FROM entries WHERE {where} GROUP BY group_key ORDER BY position LIMIT ? OFFSET ?
                )
                SELECT e.data FROM selected JOIN entries e USING (group_key)
                WHERE {where} ORDER BY selected.position, e.mtime DESC, e.path DESC
            """, [*args, page_size, (page - 1) * page_size, *args])
            return [json.loads(row[0]) for row in rows], page, pages, total

    def get_tracks(self, relative, size, mtime, ctime, version):
        with self.connect() as db:
            row = db.execute("SELECT tracks FROM entries WHERE path=? AND size=? AND mtime=? AND ctime=? "
                             "AND probe_version=?", (relative, size, mtime, ctime, version)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def save_tracks(self, relative, size, mtime, ctime, version, tracks):
        with self.connect() as db:
            db.execute("UPDATE entries SET tracks=?,probe_version=?,accessed_at=? WHERE path=? AND size=? "
                       "AND mtime=? AND ctime=?",
                       (json.dumps(tracks), version, time.time(), relative, size, mtime, ctime))

    def touch(self, relative):
        with self.connect() as db:
            now = time.time()
            db.execute("UPDATE entries SET accessed_at=? WHERE path=? AND "
                       "(accessed_at IS NULL OR accessed_at<?)", (now, relative, now - 60))


def valid_episodes(episodes, kind):
    return sorted(set(episodes or [])) if kind == "series" else []


@lru_cache(maxsize=32)
def _get_index(root, location):
    return LibraryIndex(root, location)


def get_index(root):
    root = str(Path(root).resolve())
    suffix = sha256(os.fsencode(root)).hexdigest()[:20]
    location = Path(settings.DATA_ROOT) / f"library-{suffix}.db"
    return _get_index(root, location)


def local_tmdb_metadata(tmdb_id, media_type, language, season=None):
    """Build a minimal playable detail view from an indexed TMDB identity."""
    if not 0 < tmdb_id < 2**63:
        return None
    kind = "series" if media_type == "tv" else "movie"
    try:
        entries = get_index(settings.DOWNLOADS_ROOT).candidates([], kind, tmdb_id)
    except (OSError, sqlite3.Error):
        logger.warning("Local metadata index is unavailable")
        return None
    if not entries:
        return None
    item = entries[0]
    spanish = (language or "").startswith("es")
    title = (item.get("localized_title") if spanish else None) or item["title"]
    original = item.get("original_title") or item["title"]
    date = str(item["year"]) if item.get("year") else None
    if kind == "movie":
        return {"id": tmdb_id, "title": title, "original_title": original, "release_date": date,
                "imdb_id": item.get("imdb_id")}
    seasons = {}
    for entry in entries:
        number = entry.get("season_number")
        if number is not None:
            seasons.setdefault(number, set()).update(entry.get("episode_numbers", []))
    if season is not None:
        if season not in seasons:
            return None
        label = "Episodio" if spanish else "Episode"
        return {"season_number": season, "episodes": [
            {"episode_number": number, "name": f"{label} {number}"} for number in sorted(seasons[season])
        ]}
    return {"id": tmdb_id, "name": title, "original_name": original, "first_air_date": date,
            "imdb_id": item.get("imdb_id"), "seasons": [
                {"season_number": number, "episode_count": len(episodes)}
                for number, episodes in sorted(seasons.items())
            ]}


async def reconcile_loop():
    while True:
        try:
            await asyncio.to_thread(lambda: get_index(settings.DOWNLOADS_ROOT).reconcile(force=False))
        except (OSError, sqlite3.Error):
            logger.warning("Library index reconciliation failed")
        await asyncio.sleep(RECONCILE_SECONDS)
