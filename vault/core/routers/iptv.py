"""
API routes para IPTV.
"""
from typing import List, Optional
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import auth
import database
from services.iptv_service import IPTVService, PUBLIC_M3U_SOURCES, load_sources_concurrently
from services.user_library_service import user_library_service
from routers.admin import get_current_admin

router = APIRouter(prefix="/api/iptv", tags=["IPTV"])

iptv_service = IPTVService()
channels_cache: List[dict] = []
DEFAULT_SOURCE_KEYS = ["iptv-org", "tdtchannels", "free-tv"]
_bootstrap_lock = asyncio.Lock()


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _maybe_user(request: Request, db: Session) -> Optional[database.User]:
    try:
        return auth.get_current_user(request, db)
    except Exception:
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> database.User:
    return auth.get_current_user(request, db)


class ImportRequest(BaseModel):
    source: str
    source_name: Optional[str] = "custom"


def _merge_channels(new_channels: List[dict]) -> int:
    global channels_cache

    existing_ids = {channel.get("id") for channel in channels_cache}
    by_stream = {
        ((channel.get("name") or "").strip().casefold(), channel.get("stream_url")): channel
        for channel in channels_cache
    }
    imported = 0
    for channel in new_channels:
        channel_id = channel.get("id")
        if channel_id in existing_ids:
            continue
        key = ((channel.get("name") or "").strip().casefold(), channel.get("stream_url"))
        if key in by_stream:
            existing = by_stream[key]
            aliases = existing.setdefault("sources", [existing.get("source")])
            if channel.get("source") not in aliases:
                aliases.append(channel.get("source"))
            continue
        channels_cache.append(channel)
        by_stream[key] = channel
        existing_ids.add(channel_id)
        imported += 1

    return imported


async def _load_public_source(source_key: str) -> dict:
    source = PUBLIC_M3U_SOURCES.get(source_key)
    if not source:
        raise HTTPException(404, f"Fuente no encontrada: {source_key}")

    result = await iptv_service.import_m3u(source["url"], source_key)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Error importando M3U"))

    imported = _merge_channels(result.get("channels", []))
    return {
        "success": True,
        "source": source_key,
        "imported": imported,
        "total_in_cache": len(channels_cache),
    }


@router.get("/sources")
async def list_public_sources():
    return [{"key": key, **value} for key, value in PUBLIC_M3U_SOURCES.items()]


@router.post("/bootstrap")
async def bootstrap_sources():
    # Simultaneous page loads must not all fetch the same large public lists.
    async with _bootstrap_lock:
        loaded_sources = {
            source for channel in channels_cache
            for source in (channel.get("sources") or [channel.get("source")])
        }
        missing_sources = [key for key in DEFAULT_SOURCE_KEYS if key not in loaded_sources]
        if not missing_sources:
            return {"success": True, "imported": 0, "sources_loaded": [], "total_in_cache": len(channels_cache)}
        result = await load_sources_concurrently(missing_sources, _load_public_source, timeout_s=30)
        result["total_in_cache"] = len(channels_cache)
        result["success"] = bool(channels_cache)
        return result


@router.post("/import")
async def import_m3u(req: ImportRequest, admin=Depends(get_current_admin)):
    del admin
    result = await iptv_service.import_m3u(req.source, req.source_name)
    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Error importando M3U"))

    imported = _merge_channels(result.get("channels", []))
    return {
        "success": True,
        "imported": imported,
        "total_in_cache": len(channels_cache),
    }


@router.post("/import/{source_key}")
async def import_public_source(source_key: str, admin=Depends(get_current_admin)):
    del admin
    return await _load_public_source(source_key)


@router.get("/channels")
async def list_channels(
    group: Optional[str] = None,
    language: Optional[str] = None,
    country: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(100, le=5000),
    offset: int = Query(0, ge=0),
):
    filtered = iptv_service.filter_channels(
        channels_cache,
        group=group,
        language=language,
        country=country,
        search=search,
    )

    filtered.sort(key=lambda channel: ((channel.get("group") or ""), (channel.get("name") or "")))
    total = len(filtered)
    channels = filtered[offset : offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "channels": channels,
    }


@router.get("/channels/groups")
async def list_groups():
    groups = iptv_service.group_channels(channels_cache)
    return {group: len(channels) for group, channels in groups.items()}


@router.get("/channel/{channel_id}")
async def get_channel(channel_id: str):
    channel = next((item for item in channels_cache if item.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")
    return channel


@router.get("/channel/{channel_id}/play")
async def get_channel_stream(channel_id: str):
    channel = next((item for item in channels_cache if item.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")

    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "stream_url": channel.get("stream_url"),
        "logo_url": channel.get("logo_url"),
    }


@router.get("/channel/{channel_id}/health")
async def check_channel_health(channel_id: str):
    channel = next((item for item in channels_cache if item.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(404, "Canal no encontrado")

    health = await iptv_service.check_channel_health(channel.get("stream_url", ""))
    return {"name": channel.get("name"), **health}


@router.delete("/channels")
async def clear_channels(admin=Depends(get_current_admin)):
    del admin
    global channels_cache
    count = len(channels_cache)
    channels_cache = []
    return {"cleared": count}


@router.get("/stats")
async def get_stats():
    groups = iptv_service.group_channels(channels_cache)
    return {
        "total_channels": len(channels_cache),
        "total_groups": len(groups),
        "groups": {group: len(channels) for group, channels in groups.items()},
    }


@router.get("/countries")
async def list_countries():
    """Distinct country codes across the loaded channels."""
    seen: dict[str, int] = {}
    for ch in channels_cache:
        countries = ch.get("countries") or ([ch.get("country")] if ch.get("country") else [])
        for country in countries:
            code = country.upper()
            if code:
                seen[code] = seen.get(code, 0) + 1
    return [{"code": k, "count": v} for k, v in sorted(seen.items())]


@router.get("/languages")
async def list_languages():
    seen: dict[str, int] = {}
    for ch in channels_cache:
        languages = ch.get("languages") or ([ch.get("language")] if ch.get("language") else [])
        for l in languages:
            if not l:
                continue
            seen[l] = seen.get(l, 0) + 1
    return [{"code": k, "count": v} for k, v in sorted(seen.items(), key=lambda x: -x[1])]


# ── Favorites + recents (auth required) ──────────────────────────────────────

class IptvFavoritePayload(BaseModel):
    channel_id: str


@router.get("/favorites")
async def list_iptv_favorites(user: database.User = Depends(get_current_user)):
    items = user_library_service.list_iptv_favorites(user.username)
    return {"items": items, "total": len(items)}


@router.get("/favorites/{channel_id}")
async def iptv_favorite_status(
    channel_id: str,
    user: database.User = Depends(get_current_user),
):
    return {"is_favorite": user_library_service.is_iptv_favorite(user.username, channel_id)}


@router.post("/favorites")
async def add_iptv_favorite(
    payload: IptvFavoritePayload,
    user: database.User = Depends(get_current_user),
):
    channel = next((c for c in channels_cache if c.get("id") == payload.channel_id), None)
    if not channel:
        raise HTTPException(404, "Channel not found in current cache")
    user_library_service.upsert_iptv_favorite(user.username, {
        "channel_id": channel["id"],
        "name": channel.get("name") or channel["id"],
        "logo": channel.get("logo_url"),
        "group": channel.get("group"),
        "language": (channel.get("languages") or [channel.get("language")])[0],
        "country": channel.get("country"),
        "stream_url": channel.get("stream_url"),
    })
    return {"ok": True}


@router.delete("/favorites/{channel_id}")
async def remove_iptv_favorite(
    channel_id: str,
    user: database.User = Depends(get_current_user),
):
    removed = user_library_service.remove_iptv_favorite(user.username, channel_id)
    return {"ok": removed}


@router.get("/recents")
async def list_iptv_recents(
    limit: int = Query(20, ge=1, le=100),
    user: database.User = Depends(get_current_user),
):
    items = user_library_service.list_iptv_recents(user.username, limit=limit)
    return {"items": items, "total": len(items)}


@router.post("/recents/{channel_id}")
async def push_iptv_recent(
    channel_id: str,
    user: database.User = Depends(get_current_user),
):
    channel = next((c for c in channels_cache if c.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(404, "Channel not found in current cache")
    user_library_service.record_iptv_recent(user.username, {
        "channel_id": channel["id"],
        "name": channel.get("name") or channel["id"],
        "logo": channel.get("logo_url"),
        "group": channel.get("group"),
        "stream_url": channel.get("stream_url"),
    })
    return {"ok": True}
