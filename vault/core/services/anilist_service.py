"""AniList service — Python port of the AniList helpers in Streambert's
utils/api.js. Used to detect anime content, fetch a sequel chain, get the
MAL id (for AniSkip), and pull AniList cover/banner images."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"

ANIME_DETAIL_QUERY = """
query($search:String,$type:MediaType){
  Media(search:$search,type:$type,sort:SEARCH_MATCH){
    id idMal title{english romaji native}
    description coverImage{extraLarge large color}
    bannerImage episodes seasonYear startDate{year}
    format type genres
    relations{edges{relationType node{id idMal type format
      title{english romaji}episodes startDate{year}seasonYear coverImage{large}}}}
  }
}
"""


def clean_description(text: Optional[str]) -> str:
    """Strip `(Source: ...)` and `Note: ...` lines, decode some entities."""
    if not text:
        return ""
    text = re.sub(r"\(Source:[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\nNote:.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def is_anime_content(details: Dict[str, Any]) -> bool:
    """Streambert's heuristic: TMDB genre 16 (Animation) AND origin JP."""
    if not details:
        return False
    genres = details.get("genres") or []
    if isinstance(genres, list):
        if not any((g.get("id") if isinstance(g, dict) else g) == 16 for g in genres):
            return False
    if (details.get("original_language") or "") == "ja":
        return True
    origin = details.get("origin_country") or []
    if isinstance(origin, list) and "JP" in origin:
        return True
    return False


async def fetch_anime(client: httpx.AsyncClient, title: str, media_type: str = "ANIME") -> Optional[dict]:
    try:
        r = await client.post(
            ANILIST_URL,
            json={"query": ANIME_DETAIL_QUERY, "variables": {"search": title, "type": media_type}},
            timeout=8,
        )
        return r.json().get("data", {}).get("Media")
    except Exception as e:
        logger.debug("anilist fetch failed: %s", e)
        return None


def build_season_chain(media: dict) -> List[dict]:
    """Returns ordered season list from a Media query response (S1 + sequels)."""
    if not media:
        return []
    chain = [{
        "id": media.get("id"),
        "idMal": media.get("idMal"),
        "title": (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji"),
        "episodes": media.get("episodes"),
        "year": (media.get("startDate") or {}).get("year") or media.get("seasonYear"),
        "coverImage": (media.get("coverImage") or {}).get("large"),
    }]
    sequels = [
        e for e in ((media.get("relations") or {}).get("edges") or [])
        if e.get("relationType") == "SEQUEL"
        and (e.get("node") or {}).get("type") == "ANIME"
        and (e.get("node") or {}).get("format") in ("TV", "TV_SHORT")
    ]
    sequels.sort(key=lambda e: (e.get("node") or {}).get("startDate", {}).get("year")
                 or (e.get("node") or {}).get("seasonYear") or 9999)
    for e in sequels:
        n = e["node"]
        chain.append({
            "id": n.get("id"),
            "idMal": n.get("idMal"),
            "title": (n.get("title") or {}).get("english") or (n.get("title") or {}).get("romaji"),
            "episodes": n.get("episodes"),
            "year": (n.get("startDate") or {}).get("year") or n.get("seasonYear"),
            "coverImage": (n.get("coverImage") or {}).get("large"),
        })
    return chain


async def lookup(title: str) -> dict:
    """Public entry: returns {ok, title, idMal, description, cover, banner, chain}."""
    if not title:
        return {"ok": False}
    async with httpx.AsyncClient() as client:
        media = await fetch_anime(client, title)
        if not media:
            return {"ok": False}
        chain = build_season_chain(media)
        return {
            "ok": True,
            "id": media.get("id"),
            "idMal": media.get("idMal"),
            "title": (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji"),
            "description": clean_description(media.get("description")),
            "cover": (media.get("coverImage") or {}).get("extraLarge")
                     or (media.get("coverImage") or {}).get("large"),
            "banner": media.get("bannerImage"),
            "color": (media.get("coverImage") or {}).get("color"),
            "chain": chain,
        }
