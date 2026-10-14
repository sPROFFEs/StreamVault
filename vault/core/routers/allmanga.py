"""AllManga / AllAnime episode resolver + stream proxy.

POST /api/allmanga/resolve   { title, season, episode, isMovie, translationType }
  → { ok, url, isDirectMp4, referer, sourceName, resolution }

GET  /api/allmanga/proxy?url=<final_url>&referer=<allmanga|youtube>
  → transparent media proxy that adds the right Referer header so the
    browser's HTML5 <video>/<source> can play the URL across origins.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from services.allmanga_service import resolve_episode
from services.anilist_service import lookup as anilist_lookup
from services.aniskip_service import get_skip_times
from services.block_list import should_block
from security import is_safe_external_url

router = APIRouter(prefix="/api/allmanga", tags=["AllManga"])
logger = logging.getLogger(__name__)


class ResolveRequest(BaseModel):
    title: str
    seasonNumber: int = 1
    episodeNumber: int = 1
    isMovie: bool = False
    translationType: str = "sub"  # "sub" | "dub"


@router.post("/resolve")
async def allmanga_resolve(payload: ResolveRequest):
    try:
        out = await resolve_episode(
            title=payload.title,
            season_number=payload.seasonNumber,
            episode_number=payload.episodeNumber,
            is_movie=payload.isMovie,
            translation_type=payload.translationType,
        )
        return out
    except Exception as e:
        logger.exception("allmanga resolve failed")
        return {"ok": False, "error": str(e)}


@router.get("/anilist")
async def anilist_for_title(title: str = Query(...)):
    return await anilist_lookup(title)


@router.get("/aniskip/{mal_id}/{episode}")
async def aniskip_for_ep(mal_id: int, episode: int, episode_length: float = 0):
    return await get_skip_times(mal_id, episode, episode_length)


# ── Stream proxy ─────────────────────────────────────────────────────────────

ALLOWED_REFERERS = {
    "allmanga": "https://allmanga.to",
    "youtube": "https://www.youtube.com",
}


@router.get("/proxy")
async def proxy_stream(
    request: Request,
    url: str = Query(...),
    referer: str = Query("allmanga"),
):
    """Stream a remote video URL through StreamVault, injecting the right
    Referer header so the upstream CDN serves the bytes. Used by AllManga
    direct-mp4 and m3u8 playback in the browser."""
    if not is_safe_external_url(url):
        raise HTTPException(400, "only safe external http/https URLs allowed")
    if should_block(url):
        return JSONResponse({"blocked": True}, status_code=204)
    ref = ALLOWED_REFERERS.get(referer, ALLOWED_REFERERS["allmanga"])
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) "
            "Gecko/20100101 Firefox/121.0"
        ),
        "Referer": ref,
        "Accept": "*/*",
    }
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]

    async def _stream():
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as upstream:
                async for chunk in upstream.aiter_bytes(64 * 1024):
                    yield chunk

    # We need response headers from upstream — open the connection first to
    # grab content-type and content-range, then stream the body.
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        upstream = await client.send(
            client.build_request("GET", url, headers=headers),
            stream=True,
        )
    pass_headers = {}
    for h in ("content-type", "content-length", "content-range", "accept-ranges",
              "last-modified", "etag"):
        if h in upstream.headers:
            pass_headers[h] = upstream.headers[h]
    pass_headers["Access-Control-Allow-Origin"] = "*"
    pass_headers["Cache-Control"] = "no-store"

    async def _iter():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _iter(),
        status_code=upstream.status_code,
        headers=pass_headers,
        media_type=upstream.headers.get("content-type"),
    )
