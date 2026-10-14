"""Age rating router."""
from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.age_rating_service import get_rating

router = APIRouter(prefix="/api/ratings", tags=["Ratings"])


@router.get("/{media_type}/{tmdb_id}")
async def single_rating(media_type: str, tmdb_id: int, country: str = Query("US")):
    if media_type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="media_type must be movie|tv")
    return await get_rating(tmdb_id, media_type, country)


class BatchRequest(BaseModel):
    items: list[dict]  # [{tmdb_id, media_type}, ...]
    country: str = "US"


@router.post("/batch")
async def batch_ratings(payload: BatchRequest):
    items = payload.items[:80]  # cap
    tasks = [
        get_rating(it["tmdb_id"], it.get("media_type", "movie"), payload.country)
        for it in items
        if isinstance(it, dict) and "tmdb_id" in it
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out = []
    for it, res in zip(items, results):
        if isinstance(res, Exception):
            out.append({**it, "cert": None, "min_age": None})
        else:
            out.append({**it, **res})
    return {"items": out}
