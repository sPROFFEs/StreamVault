"""Trailers via Invidious — server-side embed-URL builder with instance
fallback. We don't proxy video; we hand the renderer an iframe URL it can
load directly. Streambert kept a list of 9 instances and rotated on
Cloudflare interstitials; we do the same.

We don't probe instances here — that would add latency. Instead we expose
the full ordered list so the frontend can fail over client-side.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

INVIDIOUS_INSTANCES: List[str] = [
    "inv.nadeko.net",
    "yewtu.be",
    "invidious.private.coffee",
    "invidious.fdn.fr",
    "iv.melmac.space",
    "invidious.protokolla.fi",
    "invidious.materialio.us",
    "invidious.privacydev.net",
    "invidious.lunar.icu",
]


def embed_urls(youtube_key: str, preferred: Optional[str] = None) -> List[str]:
    """Return a prioritised list of `https://{instance}/embed/{key}` URLs."""
    if not youtube_key:
        return []
    instances = list(INVIDIOUS_INSTANCES)
    if preferred and preferred in instances:
        instances.remove(preferred)
        instances.insert(0, preferred)
    elif preferred:
        instances.insert(0, preferred)
    return [f"https://{i}/embed/{youtube_key}?autoplay=1&showinfo=0" for i in instances]


async def youtube_key_for_tmdb(tmdb_id: int, media_type: str = "movie") -> Optional[str]:
    """Pull the first YouTube trailer key from TMDB videos endpoint."""
    import os
    api_key = os.getenv("TMDB_API_KEY")
    if not api_key:
        return None
    media_type = "tv" if media_type == "tv" else "movie"
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/videos"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params={"api_key": api_key})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    candidates = data.get("results", []) or []
    for typ in ("Trailer", "Teaser", "Clip"):
        for v in candidates:
            if v.get("site") == "YouTube" and v.get("type") == typ and v.get("key"):
                return v["key"]
    return None
