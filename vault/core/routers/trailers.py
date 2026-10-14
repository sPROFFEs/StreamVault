"""Trailers router — TMDB → YouTube key → Invidious embed URL list."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.trailers_service import embed_urls, youtube_key_for_tmdb

router = APIRouter(prefix="/api/trailers", tags=["Trailers"])


@router.get("/{media_type}/{tmdb_id}")
async def trailer_urls(
    media_type: str,
    tmdb_id: int,
    preferred: Optional[str] = Query(None),
):
    if media_type not in ("movie", "tv"):
        raise HTTPException(400, "media_type must be movie|tv")
    key = await youtube_key_for_tmdb(tmdb_id, media_type)
    if not key:
        return {"youtube_key": None, "embeds": []}
    return {"youtube_key": key, "embeds": embed_urls(key, preferred=preferred)}
