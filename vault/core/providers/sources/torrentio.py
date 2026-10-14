import re
import logging
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
        if not imdb_id or not imdb_id.startswith("tt"):
            logger.info(f"Torrentio: no IMDB ID for tmdb_id={tmdb_id}, skipping")
            return []

        if season is not None and episode is not None:
            resource = f"series/{imdb_id}:{season}:{episode}"
        else:
            resource = f"movie/{imdb_id}"

        url = f"{TORRENTIO_BASE}/stream/{resource}.json"
        logger.info(f"Torrentio: querying {url}")

        try:
            async with httpx.AsyncClient(
                timeout=12,
                follow_redirects=True,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://web.stremio.com/",
                    "Origin": "https://web.stremio.com",
                },
            ) as client:
                resp = await client.get(url)
                logger.info(f"Torrentio: HTTP {resp.status_code} for {imdb_id}")
                if resp.status_code != 200:
                    logger.warning(f"Torrentio: {resp.status_code}: {resp.text[:200]}")
                    return []
                data = resp.json()
        except httpx.TimeoutException:
            logger.error(f"Torrentio: timeout for {imdb_id}")
            return []
        except Exception as e:
            logger.error(f"Torrentio: error for {imdb_id}: {e}")
            return []

        raw_streams = data.get("streams", [])
        logger.info(f"Torrentio: {len(raw_streams)} raw streams for {imdb_id}")

        results = []
        for stream in raw_streams:
            info_hash = stream.get("infoHash")
            if not info_hash:
                continue

            name = stream.get("name", "")
            title = stream.get("title", "")
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
        logger.info(f"Torrentio: returning {len(results[:12])} streams")
        return results[:12]
