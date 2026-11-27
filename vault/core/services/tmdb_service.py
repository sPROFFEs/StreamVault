"""TMDB metadata, with shared connections and persistent offline fallback."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
import time

import httpx

from config import settings
from services.media_identity import choose_tmdb_candidate, release_year
from services.tmdb_cache import MetadataCache

logger = logging.getLogger(__name__)
TMDB_ORIGIN = "https://api.themoviedb.org"
_DAY = 24 * 3600
_RESPONSE_LIMIT = 4 * 1024**2
_WORKERS = 4
_PENDING = 12
_MAX_WAIT = 8


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # HTTPX includes the entire URL at INFO, even for a failed request.
        message = record.getMessage()
        return TMDB_ORIGIN not in message or "api_key=" not in message


logging.getLogger("httpx").addFilter(_PrivateRequestFilter())


class TMDBService:
    IMAGE_BASE = "https://image.tmdb.org/t/p/"
    POSTER_SIZE = "w500"
    BACKDROP_SIZE = "w1280"
    _CACHE_MAX = 500
    _CACHE_BYTES = 32 * 1024**2

    def __init__(self, api_key=None, *, cache_path=None, transport=None, clock=None,
                 max_entries=_CACHE_MAX, max_bytes=_CACHE_BYTES):
        self._explicit_key = api_key
        self._cache_path = cache_path
        self._limits = (max_entries, max_bytes)
        self._cache = None
        self._transport = transport
        self._clock = clock or time.time
        self._lock = Lock()
        self._client = None
        self._executor = None
        self._inflight = {}
        self._closing = False
        self._credential_snapshot = None
        self._retry_at = 0
        self._failures = 0

    @property
    def key_path(self):
        return Path(settings.DATA_ROOT) / "tmdb_key.txt"

    def provider_status(self):
        configured = bool(self._credentials()[2])
        with self._lock:
            cooldown = max(0, self._retry_at - self._clock())
        return {"configured": configured, "retry_in_seconds": round(cooldown)}

    def _credentials(self):
        if self._explicit_key is not None:
            return self._explicit_key.strip(), "", "environment"
        try:
            with self.key_path.open(encoding="utf-8") as file:
                key = file.read(257).strip()
            if key and len(key) <= 256:
                return key, "", "file"
        except (OSError, UnicodeError):
            pass
        key, token = settings.TMDB_API_KEY.strip(), settings.TMDB_TOKEN.strip()
        return key, token, "environment" if key or token else None

    @property
    def enabled(self):
        key, token, _ = self._credentials()
        return bool(key or token)

    def key_status(self):
        key, token, source = self._credentials()
        return {"configured": bool(key or token), "source": source}

    def set_key(self, key):
        path = self.key_path
        if key is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as file:
                temporary = Path(file.name)
                try:
                    file.write(key)
                    file.flush()
                    os.fsync(file.fileno())
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
        return self.key_status()

    @staticmethod
    def _language(language):
        return "es-ES" if (language or "").strip().lower().startswith("es") else "en-US"

    @staticmethod
    def _media_path(media_type, tmdb_id):
        try:
            identity = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        if not 0 < identity < 2**63:
            return None
        return f"/{'tv' if media_type == 'tv' else 'movie'}/{identity}"

    def _forget(self, key, future):
        with self._lock:
            if self._inflight.get(key) is future:
                del self._inflight[key]

    def _metadata(self, path, params=None, *, ttl=_DAY, stale=30 * _DAY):
        if path is None:
            return None
        key, token, _ = self._credentials()
        if not key and not token:
            return None
        credentials = (key, token)
        params = dict(params or {})
        cache_key = json.dumps([path, params], sort_keys=True, separators=(",", ":"))
        now = self._clock()
        with self._lock:
            cache_path = Path(self._cache_path or Path(settings.DATA_ROOT) / "runtime/tmdb.sqlite3")
            if self._cache is None or self._cache.path != cache_path:
                self._cache = MetadataCache(cache_path, *self._limits)
            cache = self._cache
            entry = cache.get(cache_key, now)
            if entry is not None and now < entry.fresh_until:
                return entry.value
            previous = entry.value if entry is not None else None
            if credentials != self._credential_snapshot:
                self._credential_snapshot = credentials
                self._retry_at = self._failures = 0
            if self._closing or now < self._retry_at:
                return previous
            worker_key = (credentials, cache_key)
            future = self._inflight.get(worker_key)
            started = False
            if future is None:
                # Reserve some capacity for cold reads instead of letting
                # stale refreshes occupy the entire queue.
                capacity = _WORKERS - 1 if entry is not None else _PENDING
                if len(self._inflight) >= capacity:
                    return previous
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="tmdb")
                future = self._executor.submit(
                    self._refresh, cache, cache_key, path, params, credentials, ttl, stale
                )
                self._inflight[worker_key] = future
                started = True
        if started:
            future.add_done_callback(lambda completed: self._forget(worker_key, completed))
        if entry is not None:
            return previous
        try:
            return deepcopy(future.result(timeout=_MAX_WAIT))
        except TimeoutError:
            return None

    def _get_client(self, credentials):
        with self._lock:
            if self._closing or credentials != self._credential_snapshot or self._clock() < self._retry_at:
                return None
            if self._client is None:
                self._client = httpx.Client(
                    base_url=f"{TMDB_ORIGIN}/3/", transport=self._transport,
                    timeout=httpx.Timeout(5, connect=3, pool=1),
                    limits=httpx.Limits(max_connections=_WORKERS, max_keepalive_connections=_WORKERS),
                    follow_redirects=False, headers={"Accept": "application/json"},
                )
            return self._client

    def _back_off(self, credentials, status=None, retry_after=None):
        with self._lock:
            if credentials != self._credential_snapshot:
                return
            now = self._clock()
            if status == 429:
                delay = 30
                if retry_after:
                    try:
                        delay = float(int(retry_after))
                    except (ValueError, OverflowError):
                        try:
                            date = parsedate_to_datetime(retry_after)
                            if date.tzinfo is None:
                                date = date.replace(tzinfo=timezone.utc)
                            delay = date.timestamp() - now
                        except (TypeError, ValueError, OverflowError):
                            pass
                delay = max(1, delay)
            elif status in (401, 403):
                delay = 60
            else:
                self._failures = min(self._failures + 1, 5)
                delay = 2 ** self._failures
            self._retry_at = max(self._retry_at, now + delay)
        logger.warning("TMDB temporarily unavailable (status %s)", status or "transport")

    def _refresh(self, cache, cache_key, path, params, credentials, ttl, stale):
        client = self._get_client(credentials)
        if client is None:
            return None
        key, token = credentials
        query = dict(params)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            query["api_key"] = key
        deadline = time.monotonic() + _MAX_WAIT
        try:
            with client.stream("GET", path.lstrip("/"), params=query, headers=headers) as response:
                status = response.status_code
                if status == 404:
                    cache.store(cache_key, None, self._clock(), 300, 0)
                    return None
                if status in (401, 403, 429) or status >= 500:
                    self._back_off(credentials, status, response.headers.get("Retry-After"))
                    return None
                if not response.is_success:
                    return None
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > _RESPONSE_LIMIT or time.monotonic() > deadline:
                        raise ValueError("TMDB response limit")
                    payload.extend(chunk)
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError("Unexpected TMDB response")
        except (httpx.HTTPError, ValueError):
            self._back_off(credentials)
            return None
        with self._lock:
            if credentials == self._credential_snapshot and self._clock() >= self._retry_at:
                self._failures = 0
        cache.store(cache_key, value, self._clock(), ttl, stale)
        return value

    def close(self):
        with self._lock:
            if self._closing:
                return
            self._closing = True
            executor, client = self._executor, self._client
        if executor is not None:
            # A queued foreground caller can still be awaiting its future.
            # Pending workers observe _closing and return safely.
            executor.shutdown(wait=True)
        if client is not None:
            client.close()
        with self._lock:
            self._client = self._executor = None
            self._closing = False

    def _search(self, media_type, query, year, language):
        params = {"query": query, "language": self._language(language)}
        date_key = "first_air_date" if media_type == "tv" else "release_date"
        year_key = "first_air_date_year" if media_type == "tv" else "primary_release_year"
        if year:
            params[year_key] = year
        path = f"/search/{media_type}"
        results = (self._metadata(path, params, ttl=600) or {}).get("results", [])
        if year and not results:
            del params[year_key]
            results = (self._metadata(path, params, ttl=600) or {}).get("results", [])
        if year:
            def distance(item):
                candidate = release_year(item.get(date_key))
                return abs(candidate - year) if candidate else 999
            results = sorted(results, key=distance)
        return results

    def search_movie(self, query, year=None, language="en-US"):
        return self._search("movie", query, year, language)

    def search_tv(self, query, year=None, language="en-US"):
        return self._search("tv", query, year, language)

    def get_movie_details(self, tmdb_id, language="en-US"):
        return self._metadata(self._media_path("movie", tmdb_id),
                              {"language": self._language(language), "append_to_response": "credits,videos"})

    def get_tv_details(self, tmdb_id, language="en-US"):
        return self._metadata(self._media_path("tv", tmdb_id),
                              {"language": self._language(language), "append_to_response": "credits"})

    def get_season_details(self, tmdb_id, season_number, language="en-US"):
        path = self._media_path("tv", tmdb_id)
        return self._metadata(f"{path}/season/{int(season_number)}" if path else None,
                              {"language": self._language(language)}, ttl=3600)

    def get_poster_url(self, poster_path, size=None):
        return f"{self.IMAGE_BASE}{size or self.POSTER_SIZE}{poster_path}" if poster_path else None

    def get_backdrop_url(self, backdrop_path):
        return f"{self.IMAGE_BASE}{self.BACKDROP_SIZE}{backdrop_path}" if backdrop_path else None

    def enrich_media(self, title, year=None, media_type="movie", language="en-US", duration_minutes=None):
        movie = media_type == "movie"
        search = self.search_movie if movie else self.search_tv
        best = choose_tmdb_candidate(search(title, year, language=language), title, year, duration_minutes)
        if not best:
            return None
        get_details = self.get_movie_details if movie else self.get_tv_details
        details = get_details(best["id"], language=language) or best
        original_title = details.get("original_title") or details.get("original_name")
        return {
            "tmdb_id": best["id"],
            "title": details.get("title") or details.get("name") or original_title,
            "original_title": original_title,
            "overview": details.get("overview"),
            "poster_url": self.get_poster_url(details.get("poster_path")),
            "backdrop_url": self.get_backdrop_url(details.get("backdrop_path")),
            "rating": details.get("vote_average"),
            "popularity": details.get("popularity"),
            "release_date": details.get("release_date") or details.get("first_air_date"),
            "genres": [genre["name"] for genre in details.get("genres", [])],
            "runtime": details.get("runtime"),
            "status": details.get("status"),
        }

    def get_trending(self, media_type="movie", time_window="week", language="en-US"):
        if media_type not in {"movie", "tv", "person", "all"} or time_window not in {"day", "week"}:
            return []
        return (self._metadata(f"/trending/{media_type}/{time_window}",
                               {"language": self._language(language)}, ttl=300) or {}).get("results", [])

    def get_genres(self, media_type="movie", language="en-US"):
        kind = "movie" if media_type == "movie" else "tv"
        return (self._metadata(f"/genre/{kind}/list", {"language": self._language(language)},
                               ttl=7 * _DAY) or {}).get("genres", [])

    def discover_media(self, media_type="movie", with_genres=None, language="en-US", sort_by=None,
                       vote_count_gte=None, page=1):
        kind = "movie" if media_type == "movie" else "tv"
        params = {"language": self._language(language), "page": page}
        if with_genres:
            params["with_genres"] = with_genres
        if sort_by:
            params["sort_by"] = sort_by
        if vote_count_gte is not None:
            params["vote_count.gte"] = vote_count_gte
        return (self._metadata(f"/discover/{kind}", params, ttl=900) or {}).get("results", [])

    def _media_resource(self, tmdb_id, media_type, suffix, params=None, *, ttl=_DAY):
        path = self._media_path(media_type, tmdb_id)
        return self._metadata(f"{path}/{suffix}" if path else None, params, ttl=ttl)

    def get_similar(self, tmdb_id, media_type="movie", language="en-US"):
        data = self._media_resource(tmdb_id, media_type, "similar", {"language": self._language(language)})
        return (data or {}).get("results", [])

    def get_recommendations(self, tmdb_id, media_type="movie", language="en-US"):
        data = self._media_resource(tmdb_id, media_type, "recommendations", {"language": self._language(language)})
        return (data or {}).get("results", [])

    def get_external_ids(self, tmdb_id, media_type):
        return self._media_resource(tmdb_id, media_type, "external_ids", ttl=7 * _DAY) or {}

    def get_videos(self, tmdb_id, media_type, language=None):
        params = {"language": self._language(language)} if language else None
        return (self._media_resource(tmdb_id, media_type, "videos", params) or {}).get("results", [])

    def get_certifications(self, tmdb_id, media_type):
        suffix = "content_ratings" if media_type == "tv" else "release_dates"
        return self._media_resource(tmdb_id, media_type, suffix, ttl=7 * _DAY) or {}

    def configuration(self):
        return self._metadata("/configuration", ttl=7 * _DAY)


tmdb_service = TMDBService()
