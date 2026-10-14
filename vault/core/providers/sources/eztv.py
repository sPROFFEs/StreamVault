import re
import logging
import httpx
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

EZTV_API = "https://eztv.re/api/get-torrents"


def _numeric_imdb(imdb_id: str) -> str:
    """tt0123456 → 123456"""
    return re.sub(r"^tt0*", "", imdb_id)


def _quality_from_name(name: str) -> str:
    n = name.upper()
    if "2160" in n or "4K" in n:
        return "4K"
    if "1080" in n:
        return "1080p"
    if "720" in n:
        return "720p"
    if "480" in n:
        return "480p"
    return "HD"


class EztvProvider:
    name = "EZTV"

    async def get_streams(
        self,
        tmdb_id: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Dict]:
        if not imdb_id or not imdb_id.startswith("tt"):
            return []

        numeric = _numeric_imdb(imdb_id)
        if not numeric:
            return []

        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    EZTV_API,
                    params={"imdb_id": numeric, "limit": 20},
                )
                logger.info(f"EZTV: HTTP {resp.status_code} for {imdb_id}")
                if resp.status_code != 200:
                    return []
                data = resp.json()
        except Exception as e:
            logger.error(f"EZTV: error for {imdb_id}: {e}")
            return []

        torrents = data.get("torrents") or []
        if not torrents:
            logger.info(f"EZTV: no torrents for {imdb_id}")
            return []

        results = []
        for t in torrents:
            # Filter by season/episode when specified
            if season is not None and episode is not None:
                ep_s = t.get("season")
                ep_e = t.get("episode")
                if ep_s and ep_e:
                    try:
                        if int(ep_s) != int(season) or int(ep_e) != int(episode):
                            continue
                    except (ValueError, TypeError):
                        pass

            hash_ = (t.get("hash") or "").strip()
            magnet = t.get("magnet_url") or ""
            if not hash_ and not magnet:
                continue

            seeds = int(t.get("seeds") or 0)
            size_bytes = t.get("size_bytes") or None
            filename = t.get("filename") or t.get("title") or ""
            quality = _quality_from_name(filename)

            if not magnet and hash_:
                dn = filename.replace(" ", "+")
                magnet = (
                    f"magnet:?xt=urn:btih:{hash_}&dn={dn}"
                    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337"
                )

            results.append({
                "server": f"EZTV · {quality}",
                "url": magnet,
                "quality": quality,
                "type": "torrent",
                "info_hash": hash_,
                "file_idx": None,
                "size_bytes": int(size_bytes) if size_bytes else None,
                "seeders": seeds,
                "title": filename,
            })

        results.sort(key=lambda x: x["seeders"], reverse=True)
        logger.info(f"EZTV: returning {min(len(results), 10)} streams for {imdb_id}")
        return results[:10]
