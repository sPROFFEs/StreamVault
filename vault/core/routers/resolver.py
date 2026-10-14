import asyncio
import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Query

from config import settings
from providers.base import StreamResult
from providers.resolvers import ResolverOrchestrator
from providers.sources.torrentio import TorrentioProvider
from providers.torrent_options import prepare_torrent_options
from routers.preferences import _load_prefs, get_current_user
import database

router = APIRouter(prefix="/api/stream", tags=["Stream Resolver"])
orchestrator = ResolverOrchestrator()
logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"


async def _fetch_imdb_id(tmdb_id: str, media_type: str) -> Optional[str]:
    if not settings.TMDB_API_KEY:
        logger.warning("TMDB_API_KEY not set — torrent providers need IMDB IDs")
        return None
    endpoint = "tv" if media_type == "tv" else "movie"
    url = f"{TMDB_BASE}/{endpoint}/{tmdb_id}/external_ids"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, params={"api_key": settings.TMDB_API_KEY})
            if resp.status_code == 200:
                imdb_id = resp.json().get("imdb_id")
                logger.info(f"TMDB external_ids: tmdb={tmdb_id} → imdb={imdb_id}")
                return imdb_id
            else:
                logger.warning(f"TMDB external_ids HTTP {resp.status_code} for {tmdb_id}")
    except Exception as e:
        logger.warning(f"Could not fetch IMDB ID for tmdb_id={tmdb_id}: {e}")
    return None


@router.get("/resolve/{tmdb_id}", response_model=List[StreamResult])
async def resolve_media_stream(
    tmdb_id: str,
    season: Optional[int] = Query(None),
    episode: Optional[int] = Query(None),
    s: Optional[int] = Query(None),
    e: Optional[int] = Query(None),
    type: Optional[str] = Query(None, alias="type"),
    title: Optional[str] = Query(None, max_length=255),
    original_title: Optional[str] = Query(None, max_length=255),
    year: Optional[int] = Query(None, ge=1800, le=2200),
    user: database.User = Depends(get_current_user),
):
    """Resolve sources in the signed-in user's preferred playback order."""
    media_type = type or "movie"
    if media_type == "series":
        media_type = "tv"
    resolved_season = season if season is not None else s
    resolved_episode = episode if episode is not None else e
    prefs = _load_prefs(user.preferences)
    from services.federation_service import match_sources
    federated = await asyncio.to_thread(match_sources, tmdb_id, media_type, resolved_season, resolved_episode,
                                        title=title, original_title=original_title, year=year)

    logger.info(f"Resolving {tmdb_id}, type={media_type}, embed playback")

    try:
        streams = await asyncio.wait_for(
            orchestrator.resolve_stream(
                tmdb_id=tmdb_id,
                imdb_id=None,
                media_type=media_type,
                season=resolved_season,
                episode=resolved_episode,
                preferred_source=prefs["default_source"],
                source_order=prefs["source_order"],
                include_torrents=False,
            ),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Resolution timed out for {tmdb_id}")
        streams = []

    logger.info(f"Resolve result: {len(streams)} streams for {tmdb_id}")
    return [*federated, *(streams or [])]


@router.get("/download-options/{tmdb_id}", response_model=List[StreamResult])
async def resolve_download_options(
    tmdb_id: str,
    season: Optional[int] = Query(None),
    episode: Optional[int] = Query(None),
    s: Optional[int] = Query(None),
    e: Optional[int] = Query(None),
    type: Optional[str] = Query(None, alias="type"),
    user: database.User = Depends(get_current_user),
):
    """Find Torrentio choices only when the user opens Download."""
    del user  # Authentication is the purpose of this dependency.
    media_type = type or "movie"
    if media_type == "series":
        media_type = "tv"
    resolved_season = season if season is not None else s
    resolved_episode = episode if episode is not None else e
    imdb_id = await _fetch_imdb_id(tmdb_id, media_type)
    if not imdb_id:
        return []

    try:
        raw = await asyncio.wait_for(
            TorrentioProvider().get_streams(
                tmdb_id,
                resolved_season,
                resolved_episode,
                imdb_id=imdb_id,
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Torrentio download lookup timed out for {tmdb_id}")
        return []

    return [
        StreamResult(
            url=item["url"],
            quality=item.get("quality", "HD"),
            source=item.get("server", "Torrentio"),
            type="torrent",
            info_hash=item.get("info_hash"),
            file_idx=item.get("file_idx"),
            size_bytes=item.get("size_bytes"),
            seeders=item.get("seeders"),
            title=item.get("title"),
            health=item.get("health"),
            download_mode="torrent",
        )
        for item in prepare_torrent_options(raw)
    ]


@router.get("/debug/{tmdb_id}")
async def debug_stream(tmdb_id: str, media_type: str = Query("movie", alias="type")):
    """
    Comprehensive debug endpoint — tests EVERY provider and returns
    individual results so you can see exactly what works and what doesn't.
    """
    result: dict = {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "tmdb_key_configured": bool(settings.TMDB_API_KEY),
        "providers": {},
    }

    # 1. IMDB ID lookup
    imdb_id = await _fetch_imdb_id(tmdb_id, media_type)
    result["imdb_id"] = imdb_id

    # 2. Test each torrent provider
    for provider in orchestrator.torrent_providers:
        name = provider.name
        try:
            streams = await asyncio.wait_for(
                provider.get_streams(tmdb_id, None, None, imdb_id=imdb_id),
                timeout=14.0,
            )
            result["providers"][name] = {
                "status": "ok" if streams else "empty",
                "count": len(streams),
                "sample": [
                    {"title": s.get("title", "")[:60], "quality": s.get("quality"), "seeders": s.get("seeders")}
                    for s in (streams or [])[:3]
                ],
            }
        except asyncio.TimeoutError:
            result["providers"][name] = {"status": "timeout"}
        except Exception as e:
            result["providers"][name] = {"status": "error", "detail": str(e)[:200]}

    # 3. Test each embed provider
    for provider in orchestrator.embed_providers:
        name = provider.name
        try:
            embeds = await asyncio.wait_for(
                provider.get_streams(tmdb_id, None, None),
                timeout=7.0,
            )
            # Quick HEAD check for each embed URL
            checks = []
            async with httpx.AsyncClient(timeout=5, follow_redirects=True, verify=False) as client:
                for embed in (embeds or []):
                    url = embed.get("url", "")
                    try:
                        resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0"})
                        xfo = (resp.headers.get("x-frame-options") or "").upper()
                        checks.append({
                            "url": url[:80],
                            "http": resp.status_code,
                            "x_frame_options": xfo or "none",
                            "frameable": xfo not in ("DENY", "SAMEORIGIN"),
                        })
                    except Exception as he:
                        checks.append({"url": url[:80], "http": "fail", "error": str(he)[:100]})
            result["providers"][name] = {
                "status": "ok" if checks else "empty",
                "count": len(embeds or []),
                "checks": checks,
            }
        except asyncio.TimeoutError:
            result["providers"][name] = {"status": "timeout"}
        except Exception as e:
            result["providers"][name] = {"status": "error", "detail": str(e)[:200]}

    # 4. Network reachability summary
    reachable = {}
    hosts = [
        ("yts.mx", "https://yts.mx/api/v2/list_movies.json?limit=1"),
        ("yts.torrentbay.st", "https://yts.torrentbay.st/api/v2/list_movies.json?limit=1"),
        ("eztv.re", "https://eztv.re/api/get-torrents?limit=1"),
        ("torrentio.strem.fun", "https://torrentio.strem.fun/manifest.json"),
        ("api.themoviedb.org", f"https://api.themoviedb.org/3/configuration?api_key={settings.TMDB_API_KEY or 'x'}"),
    ]
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, verify=False) as client:
        for label, test_url in hosts:
            try:
                resp = await client.get(test_url)
                reachable[label] = f"HTTP {resp.status_code}"
            except Exception as exc:
                reachable[label] = f"FAIL: {exc.__class__.__name__}"
    result["network"] = reachable

    return result
