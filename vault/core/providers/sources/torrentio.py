import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import logging
import random
import time
import httpx
from typing import List, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

TORRENTIO_BASE = "https://torrentio.strem.fun"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"

TRACKERS = (
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.tracker.cl%3A1337%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
    "&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Ftracker.dler.org%3A6969%2Fannounce"
    "&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce"
    "&tr=wss%3A%2F%2Ftracker.openwebtorrent.com"
    "&tr=wss%3A%2F%2Ftracker.btorrent.xyz"
    "&tr=wss%3A%2F%2Ftracker.webtorrent.dev"
    "&tr=wss%3A%2F%2Ftracker.files.fm%3A7073%2Fannounce"
)


class TorrentioProvider:
    name = "Torrentio"

    def __init__(self, *, client=None, clock=time.monotonic):
        self._client = client
        self._owns_client = client is None
        self._clock = clock
        self._cache = OrderedDict()
        self._requests = {}
        self._failures = 0
        self._retry_at = 0

    def status(self):
        return {"retry_in_seconds": round(max(0, self._retry_at - self._clock())), "in_flight": len(self._requests)}

    async def close(self):
        tasks = list(self._requests.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _cooldown(self, header=None):
        self._failures = min(self._failures + 1, 5)
        delay = 2 ** self._failures + random.random()
        if header:
            try:
                supplied = int(header) if header.isdecimal() else (
                    parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
                delay = max(delay, supplied)
            except (ValueError, TypeError, OverflowError):
                pass
        self._retry_at = max(self._retry_at, self._clock() + delay)

    def _parse_quality(self, name: str) -> str:
        n = name.upper()
        if any(k in n for k in ("2160", "4K", "UHD")):
            return "4K"
        if "1080" in n:
            return "1080p"
        if "720" in n:
            return "720p"
        if "480" in n:
            return "480p"
        return "HD"

    def _parse_seeders(self, title: str) -> int:
        m = re.search(r"👤\s*(\d+)", title)
        return int(m.group(1)) if m else 0

    def _parse_size(self, title: str) -> Optional[int]:
        m = re.search(r"💾\s*([\d.]+)\s*(GB|MB)", title)
        if m:
            size = float(m.group(1))
            return int(size * (1024 ** 3 if m.group(2) == "GB" else 1024 ** 2))
        return None

    async def get_streams(
        self,
        tmdb_id: str,
        season: int = None,
        episode: int = None,
        imdb_id: str = None,
    ) -> List[Dict]:
        if not imdb_id or not re.fullmatch(r"tt[0-9]{5,15}", imdb_id):
            return []

        if season is not None and episode is not None:
            resource = f"series/{imdb_id}:{season}:{episode}"
        else:
            resource = f"movie/{imdb_id}"

        cached = self._cache.get(resource)
        if cached and cached[0] > self._clock():
            self._cache.move_to_end(resource)
            return [item.copy() for item in cached[1]]
        if self._clock() < self._retry_at:
            return []
        task = self._requests.get(resource)
        if task is None:
            if len(self._requests) >= 12:
                return []
            task = asyncio.create_task(self._query(resource))
            self._requests[resource] = task
            task.add_done_callback(lambda _: self._requests.pop(resource, None))
        return [item.copy() for item in await asyncio.shield(task)]

    async def _query(self, resource):
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3, pool=2), trust_env=False,
                    limits=httpx.Limits(max_connections=6, max_keepalive_connections=3, keepalive_expiry=30),
                    headers={"User-Agent": _UA, "Accept": "application/json"})
            async with asyncio.timeout(10):
                async with self._client.stream("GET", f"{TORRENTIO_BASE}/stream/{resource}.json", follow_redirects=False) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        self._cooldown(response.headers.get("Retry-After"))
                        return []
                    results = await self._read_results(response) if response.status_code == 200 else []
            if self._clock() >= self._retry_at:
                self._failures = 0
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, AttributeError) as exc:
            self._cooldown()
            logger.warning("Torrentio lookup failed (%s)", type(exc).__name__)
            return []
        self._cache[resource] = (self._clock() + (90 if results else 20), results)
        self._cache.move_to_end(resource)
        if len(self._cache) > 256:
            self._cache.popitem(last=False)
        return results

    async def _read_results(self, response):
        body = bytearray()
        async for chunk in response.aiter_bytes(64 * 1024):
            body.extend(chunk)
            if len(body) > 2 * 1024**2:
                raise ValueError("Torrentio response exceeds the size limit")
        records = json.loads(body).get("streams", [])
        return self._parse(records[:500])

    def _parse(self, raw_streams):
        results = []
        for stream in raw_streams:
            if not isinstance(stream, dict):
                continue
            info_hash = stream.get("infoHash")
            if not isinstance(info_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-zA-Z2-7]{32}", info_hash):
                continue

            name = str(stream.get("name") or "")[:256]
            title = str(stream.get("title") or "")[:1024]
            file_idx = stream.get("fileIdx")

            quality = self._parse_quality(name)
            seeders = self._parse_seeders(title)
            size_bytes = self._parse_size(title)
            file_name = title.split("\n")[0] if "\n" in title else name

            results.append({
                "server": f"Torrentio · {quality}",
                "url": f"magnet:?xt=urn:btih:{info_hash}&dn={quote(file_name, safe='')}{TRACKERS}",
                "quality": quality,
                "type": "torrent",
                "info_hash": info_hash,
                "file_idx": file_idx,
                "size_bytes": size_bytes,
                "seeders": seeders,
                "title": file_name,
            })

        results.sort(key=lambda x: x.get("seeders", 0), reverse=True)
        return results[:12]
