"""Sniffer router — POST a URL, get back the m3u8 it sources.

POST /api/sniffer/sniff   { url, referer?, timeout_s? }
POST /api/sniffer/from-tmdb  { tmdb_id, media_type, season?, episode?, source? }

The /from-tmdb form builds the embed URL via the existing provider classes
(VidSrc, Videasy, 2Embed, MoviesAPI…) and sniffs that.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.m3u8_sniffer_service import sniffer
from providers.sources.vidsrc import VidSrcProvider
from providers.sources.vidsrc_xyz import VideoasyProvider
from providers.sources.twoembed import MoviesApiProvider
from providers.sources.autoembed import NovafixProvider

router = APIRouter(prefix="/api/sniffer", tags=["Sniffer"])


SOURCE_MAP = {
    "vidsrc": VidSrcProvider,
    "videasy": VideoasyProvider,
    "2embed": MoviesApiProvider,
    "moviesapi": MoviesApiProvider,
    "autoembed": NovafixProvider,
}


class SniffRequest(BaseModel):
    url: str
    referer: Optional[str] = None
    timeout_s: float = 25


@router.post("/sniff")
async def sniff_url(payload: SniffRequest):
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    return await sniffer.sniff(
        payload.url,
        referer=payload.referer,
        timeout_s=min(max(payload.timeout_s, 5), 60),
    )


class TmdbSniffRequest(BaseModel):
    tmdb_id: int
    media_type: str = "movie"
    season: Optional[int] = None
    episode: Optional[int] = None
    source: str = "vidsrc"
    timeout_s: float = 25


@router.post("/from-tmdb")
async def sniff_from_tmdb(payload: TmdbSniffRequest):
    if payload.media_type not in ("movie", "tv"):
        raise HTTPException(400, "media_type must be movie|tv")
    cls = SOURCE_MAP.get(payload.source.lower())
    if not cls:
        raise HTTPException(400, f"unknown source: {payload.source}")
    provider = cls()
    streams = await provider.get_streams(
        str(payload.tmdb_id), payload.season, payload.episode,
    )
    if not streams:
        return {"ok": False, "reason": f"provider {payload.source} returned no embeds"}
    # Pick the first embed URL the provider produced.
    embed_url = streams[0].get("url")
    if not embed_url:
        return {"ok": False, "reason": "provider returned an embed without a url"}
    return await sniffer.sniff(
        embed_url,
        referer=None,
        timeout_s=min(max(payload.timeout_s, 5), 60),
    )
