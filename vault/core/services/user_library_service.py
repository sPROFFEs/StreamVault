import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any


class UserLibraryService:
    def profile_metrics(self, username: str) -> dict:
        """Aggregate existing history; progress positions are NOT watch time."""
        with self._connection(username) as connection:
            counts = connection.execute("""SELECT
                COUNT(*) AS entries_started,
                COALESCE(SUM(CASE WHEN media_type='movie' AND completed=1 THEN 1 ELSE 0 END), 0) AS movies_completed,
                COALESCE(SUM(CASE WHEN media_type='tv' AND completed=1 THEN 1 ELSE 0 END), 0) AS episodes_completed
                FROM history""").fetchone()
            favorites = connection.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
        return {**dict(counts), "favorites": favorites}

    def __init__(self, data_root: str = "/saas-data/users") -> None:
        self.data_root = data_root

    def list_favorites(self, username: str) -> list[dict[str, Any]]:
        with self._connection(username) as connection:
            rows = connection.execute(
                """
                SELECT tmdb_id, media_type, title, poster_path, backdrop_path,
                       release_date, rating, added_at
                FROM favorites
                ORDER BY added_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_continue_watching(self, username: str, limit: int = 12) -> list[dict[str, Any]]:
        with self._connection(username) as connection:
            rows = connection.execute(
                """
                SELECT tmdb_id, media_type, title, poster_path, backdrop_path,
                       release_date, rating,
                       NULLIF(season_number, -1) AS season_number,
                       NULLIF(episode_number, -1) AS episode_number,
                       progress_seconds, duration_seconds, updated_at
                FROM history
                WHERE completed = 0 AND progress_seconds > 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_youtube_favorites(self, username: str) -> list[dict[str, Any]]:
        with self._connection(username) as connection:
            rows = connection.execute(
                """
                SELECT video_id, title, author, author_id, thumbnail, duration_text, added_at
                FROM youtube_favorites
                ORDER BY added_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_youtube_favorite(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as connection:
            connection.execute(
                """
                INSERT INTO youtube_favorites (
                    video_id, title, author, author_id, thumbnail, duration_text
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title=excluded.title,
                    author=excluded.author,
                    author_id=excluded.author_id,
                    thumbnail=excluded.thumbnail,
                    duration_text=excluded.duration_text
                """,
                (
                    payload["video_id"],
                    payload["title"],
                    payload.get("author"),
                    payload.get("author_id"),
                    payload.get("thumbnail"),
                    payload.get("duration_text"),
                ),
            )

    def remove_youtube_favorite(self, username: str, video_id: str) -> bool:
        with self._connection(username) as connection:
            cursor = connection.execute("DELETE FROM youtube_favorites WHERE video_id = ?", (video_id,))
            return cursor.rowcount > 0

    def list_youtube_subscriptions(self, username: str) -> list[dict[str, Any]]:
        with self._connection(username) as connection:
            rows = connection.execute(
                """
                SELECT channel_id, channel_name, channel_url, added_at
                FROM youtube_subscriptions
                ORDER BY channel_name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_youtube_subscription(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as connection:
            connection.execute(
                """
                INSERT INTO youtube_subscriptions (
                    channel_id, channel_name, channel_url
                ) VALUES (?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_name=excluded.channel_name,
                    channel_url=excluded.channel_url
                """,
                (
                    payload["channel_id"],
                    payload.get("channel_name") or payload["channel_id"],
                    payload.get("channel_url"),
                ),
            )

    def remove_youtube_subscription(self, username: str, channel_id: str) -> bool:
        with self._connection(username) as connection:
            cursor = connection.execute("DELETE FROM youtube_subscriptions WHERE channel_id = ?", (channel_id,))
            return cursor.rowcount > 0

    def get_progress(
        self,
        username: str,
        tmdb_id: int,
        media_type: str,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> dict[str, Any] | None:
        with self._connection(username) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM history
                WHERE tmdb_id = ?
                  AND media_type = ?
                  AND season_number IS ?
                  AND episode_number IS ?
                LIMIT 1
                """,
                (tmdb_id, media_type, season_number if season_number is not None else -1,
                 episode_number if episode_number is not None else -1),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["season_number"] = None if item["season_number"] == -1 else item["season_number"]
        item["episode_number"] = None if item["episode_number"] == -1 else item["episode_number"]
        return item

    def upsert_progress(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as connection:
            # A cached initialized connection may still receive legacy NULL
            # episode keys from an older writer. Replace only this entry's
            # legacy rows, leaving other shows and episodes untouched.
            connection.execute(
                """DELETE FROM history
                   WHERE tmdb_id = ? AND media_type = ?
                     AND COALESCE(season_number, -1) = ?
                     AND COALESCE(episode_number, -1) = ?
                     AND (season_number IS NULL OR episode_number IS NULL)""",
                (payload["tmdb_id"], payload["media_type"],
                 payload.get("season_number") if payload.get("season_number") is not None else -1,
                 payload.get("episode_number") if payload.get("episode_number") is not None else -1),
            )
            connection.execute(
                """
                INSERT INTO history (
                    tmdb_id, media_type, title, poster_path, backdrop_path, release_date, rating,
                    season_number, episode_number, progress_seconds, duration_seconds, completed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tmdb_id, media_type, season_number, episode_number) DO UPDATE SET
                    title=excluded.title,
                    poster_path=excluded.poster_path,
                    backdrop_path=excluded.backdrop_path,
                    release_date=excluded.release_date,
                    rating=excluded.rating,
                    progress_seconds=excluded.progress_seconds,
                    duration_seconds=excluded.duration_seconds,
                    completed=excluded.completed,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    payload["tmdb_id"],
                    payload["media_type"],
                    payload["title"],
                    payload.get("poster_path"),
                    payload.get("backdrop_path"),
                    payload.get("release_date"),
                    payload.get("rating"),
                    payload.get("season_number") if payload.get("season_number") is not None else -1,
                    payload.get("episode_number") if payload.get("episode_number") is not None else -1,
                    payload.get("progress_seconds", 0),
                    payload.get("duration_seconds", 0),
                    1 if payload.get("completed") else 0,
                ),
            )

    def is_favorite(self, username: str, tmdb_id: int, media_type: str) -> bool:
        with self._connection(username) as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM favorites
                WHERE tmdb_id = ? AND media_type = ?
                LIMIT 1
                """,
                (tmdb_id, media_type),
            ).fetchone()
        return row is not None

    def upsert_favorite(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as connection:
            connection.execute(
                """
                INSERT INTO favorites (
                    tmdb_id, media_type, title, poster_path, backdrop_path,
                    release_date, rating
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tmdb_id, media_type) DO UPDATE SET
                    title=excluded.title,
                    poster_path=excluded.poster_path,
                    backdrop_path=excluded.backdrop_path,
                    release_date=excluded.release_date,
                    rating=excluded.rating
                """,
                (
                    payload["tmdb_id"],
                    payload["media_type"],
                    payload["title"],
                    payload.get("poster_path"),
                    payload.get("backdrop_path"),
                    payload.get("release_date"),
                    payload.get("rating"),
                ),
            )

    def remove_favorite(self, username: str, tmdb_id: int, media_type: str) -> bool:
        with self._connection(username) as connection:
            cursor = connection.execute(
                "DELETE FROM favorites WHERE tmdb_id = ? AND media_type = ?",
                (tmdb_id, media_type),
            )
            return cursor.rowcount > 0

    # ── YouTube watch history ────────────────────────────────────────────

    def list_youtube_history(self, username: str, limit: int = 60) -> list[dict[str, Any]]:
        with self._connection(username) as conn:
            rows = conn.execute(
                """SELECT * FROM youtube_history ORDER BY watched_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_youtube_history(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as conn:
            conn.execute(
                """
                INSERT INTO youtube_history (
                    video_id, title, author, author_id, thumbnail, duration_text,
                    watch_progress, duration_seconds, completed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title=excluded.title,
                    author=excluded.author,
                    author_id=excluded.author_id,
                    thumbnail=excluded.thumbnail,
                    duration_text=excluded.duration_text,
                    watch_progress=excluded.watch_progress,
                    duration_seconds=excluded.duration_seconds,
                    completed=excluded.completed,
                    watched_at=CURRENT_TIMESTAMP
                """,
                (
                    payload["video_id"],
                    payload["title"],
                    payload.get("author"),
                    payload.get("author_id"),
                    payload.get("thumbnail"),
                    payload.get("duration_text"),
                    payload.get("watch_progress", 0),
                    payload.get("duration_seconds", 0),
                    1 if payload.get("completed") else 0,
                ),
            )

    def remove_youtube_history(self, username: str, video_id: str) -> bool:
        with self._connection(username) as conn:
            cur = conn.execute("DELETE FROM youtube_history WHERE video_id = ?", (video_id,))
            return cur.rowcount > 0

    def clear_youtube_history(self, username: str) -> int:
        with self._connection(username) as conn:
            cur = conn.execute("DELETE FROM youtube_history")
            return cur.rowcount

    # ── IPTV favorites + recents ─────────────────────────────────────────

    def list_iptv_favorites(self, username: str) -> list[dict[str, Any]]:
        with self._connection(username) as conn:
            rows = conn.execute(
                """SELECT * FROM iptv_favorites ORDER BY added_at DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def is_iptv_favorite(self, username: str, channel_id: str) -> bool:
        with self._connection(username) as conn:
            row = conn.execute(
                "SELECT 1 FROM iptv_favorites WHERE channel_id = ? LIMIT 1",
                (channel_id,),
            ).fetchone()
        return row is not None

    def upsert_iptv_favorite(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as conn:
            conn.execute(
                """
                INSERT INTO iptv_favorites (
                    channel_id, name, logo, "group", language, country, stream_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name=excluded.name,
                    logo=excluded.logo,
                    "group"=excluded."group",
                    language=excluded.language,
                    country=excluded.country,
                    stream_url=excluded.stream_url
                """,
                (
                    payload["channel_id"],
                    payload["name"],
                    payload.get("logo"),
                    payload.get("group"),
                    payload.get("language"),
                    payload.get("country"),
                    payload["stream_url"],
                ),
            )

    def remove_iptv_favorite(self, username: str, channel_id: str) -> bool:
        with self._connection(username) as conn:
            cur = conn.execute(
                "DELETE FROM iptv_favorites WHERE channel_id = ?", (channel_id,)
            )
            return cur.rowcount > 0

    def list_iptv_recents(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection(username) as conn:
            rows = conn.execute(
                """SELECT * FROM iptv_recents ORDER BY last_watched_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_iptv_recent(self, username: str, payload: dict[str, Any]) -> None:
        with self._connection(username) as conn:
            conn.execute(
                """
                INSERT INTO iptv_recents (channel_id, name, logo, "group", stream_url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name=excluded.name,
                    logo=excluded.logo,
                    "group"=excluded."group",
                    stream_url=excluded.stream_url,
                    last_watched_at=CURRENT_TIMESTAMP
                """,
                (
                    payload["channel_id"],
                    payload["name"],
                    payload.get("logo"),
                    payload.get("group"),
                    payload["stream_url"],
                ),
            )

    _initialized_dbs: set[str] = set()

    def _cleanup_legacy_history(self, connection: sqlite3.Connection) -> None:
        legacy_history = connection.execute(
            "SELECT 1 FROM history WHERE season_number IS NULL OR episode_number IS NULL LIMIT 1"
        ).fetchone()
        if legacy_history:
            connection.execute(
                """
                DELETE FROM history
                WHERE rowid NOT IN (
                    SELECT MAX(rowid) FROM history
                    GROUP BY tmdb_id, media_type,
                             COALESCE(season_number, -1), COALESCE(episode_number, -1)
                )
                """
            )
            connection.execute("UPDATE history SET season_number = -1 WHERE season_number IS NULL")
            connection.execute("UPDATE history SET episode_number = -1 WHERE episode_number IS NULL")

    def _init_db(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT NOT NULL,
                poster_path TEXT,
                backdrop_path TEXT,
                release_date TEXT,
                rating REAL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tmdb_id, media_type)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT NOT NULL,
                poster_path TEXT,
                backdrop_path TEXT,
                release_date TEXT,
                rating REAL,
                season_number INTEGER,
                episode_number INTEGER,
                progress_seconds REAL DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                completed INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tmdb_id, media_type, season_number, episode_number)
            )
            """
        )
        self._cleanup_legacy_history(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_favorites (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                author_id TEXT,
                thumbnail TEXT,
                duration_text TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_subscriptions (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                channel_url TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_history (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT,
                author_id TEXT,
                thumbnail TEXT,
                duration_text TEXT,
                watch_progress REAL DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                completed INTEGER DEFAULT 0,
                watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS iptv_favorites (
                channel_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                logo TEXT,
                "group" TEXT,
                language TEXT,
                country TEXT,
                stream_url TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS iptv_recents (
                channel_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                logo TEXT,
                "group" TEXT,
                stream_url TEXT NOT NULL,
                last_watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

    @contextmanager
    def _connection(self, username: str):
        db_path = self._db_path(username)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        connection = None
        last_err = None
        for attempt in range(3):
            try:
                connection = sqlite3.connect(db_path, timeout=30.0)
                connection.row_factory = sqlite3.Row
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA cache_size=-16000")
                if db_path not in self._initialized_dbs:
                    self._init_db(connection)
                    self._cleanup_legacy_history(connection)
                    self._initialized_dbs.add(db_path)
                break
            except sqlite3.OperationalError as err:
                last_err = err
                time.sleep(0.1 * (attempt + 1))
        else:
            raise last_err

        try:
            yield connection
            connection.commit()
        except Exception:
            try:
                if connection:
                    connection.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                if connection:
                    connection.close()
            except Exception:
                pass

    def _db_path(self, username: str) -> str:
        safe_username = "".join(char for char in username if char.isalnum() or char in ("-", "_")) or "user"
        return os.path.join(self.data_root, safe_username, "library.db")


user_library_service = UserLibraryService()
