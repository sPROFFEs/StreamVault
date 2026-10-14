import logging
import httpx
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Multiple YTS mirrors — try each in order until one works
YTS_MIRRORS = [
    "https://yts.mx/api/v2/list_movies.json",
    "https://yts.torrentbay.st/api/v2/list_movies.json",
    "https://yts.rs/api/v2/list_movies.json",
]

TRACKERS = (
    "tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
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

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"


class YtsProvider:
    name = "YTS"

    def _quality_order(self, q: str) -> int:
        return {"2160p": 4, "1080p": 3, "720p": 2, "480p": 1}.get(q, 0)

    async def get_streams(
        self,
        tmdb_id: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Dict]:
        # YTS is movies-only
        if season is not None and episode is not None:
            return []

        query = imdb_id or tmdb_id
        if not query:
            return []

        data = None
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": _UA},
        ) as client:
            for mirror in YTS_MIRRORS:
                try:
                    resp = await client.get(
                        mirror,
                        params={"query_term": query, "limit": 5, "sort_by": "seeds"},
                    )
                    logger.info(f"YTS ({mirror.split('/')[2]}): HTTP {resp.status_code} for {query}")
                    if resp.status_code == 200:
                        data = resp.json()
                        break
                except Exception as e:
                    logger.warning(f"YTS ({mirror.split('/')[2]}): {e.__class__.__name__}")
                    continue

        if not data:
            logger.warning(f"YTS: all mirrors failed for {query}")
            return []

        movies = data.get("data", {}).get("movies") or []
        if not movies:
            logger.info(f"YTS: no movies found for {query}")
            return []

        movie = movies[0]
        title = movie.get("title_long") or movie.get("title", "Unknown")

        results = []
        for torrent in movie.get("torrents", []):
            hash_ = (torrent.get("hash") or "").strip()
            if not hash_:
                continue
            quality = torrent.get("quality", "HD")
            seeds = int(torrent.get("seeds") or 0)
            size_bytes = torrent.get("size_bytes") or None
            codec = torrent.get("video_codec", "")
            dn = title.replace(" ", "+")
            magnet = f"magnet:?xt=urn:btih:{hash_}&dn={dn}&{TRACKERS}"

            label = f"YTS · {quality}"
            if codec:
                label += f" · {codec}"

            results.append({
                "server": label,
                "url": magnet,
                "quality": quality,
                "type": "torrent",
                "info_hash": hash_,
                "file_idx": None,
                "size_bytes": size_bytes,
                "seeders": seeds,
                "title": title,
            })

        results.sort(key=lambda x: (self._quality_order(x["quality"]), x["seeders"]), reverse=True)
        logger.info(f"YTS: returning {len(results)} streams for {query}")
        return results
