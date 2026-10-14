"""
API Routes para YouTube Service (estilo NewPipe)
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional, List
from pydantic import BaseModel

import auth
import database
from services.youtube_service import YouTubeService
from services.user_library_service import user_library_service

router = APIRouter(prefix="/api/youtube", tags=["YouTube"])

# Instancia global del servicio
yt_service = YouTubeService()


class VideoResult(BaseModel):
    id: str
    title: str
    author: str
    author_id: Optional[str]
    duration: int
    duration_text: str
    views: int
    views_text: str
    published: Optional[str]
    thumbnail: Optional[str]
    description: Optional[str]


class SubscriptionRequest(BaseModel):
    channel_id: str
    channel_name: Optional[str] = None
    channel_url: Optional[str] = None


class HistoryEntry(BaseModel):
    video_id: str
    title: str
    channel_name: Optional[str]
    thumbnail: Optional[str]
    watch_progress: int = 0


class YouTubeFavorite(BaseModel):
    video_id: str
    title: str
    author: Optional[str] = None
    author_id: Optional[str] = None
    thumbnail: Optional[str] = None
    duration_text: Optional[str] = None


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db=Depends(get_db)):
    return auth.get_current_user(request, db)


@router.get("/search")
async def search_videos(
    q: str = Query(..., description="Término de búsqueda"),
    page: int = Query(1, ge=1),
    sort: str = Query("relevance", pattern="^(relevance|rating|upload_date|view_count)$")
):
    """Busca videos en YouTube"""
    results = await yt_service.search(q, page=page, sort_by=sort)
    return [yt_service.format_video_result(v) for v in results]


@router.get("/video/{video_id}")
async def get_video_info(video_id: str):
    """Obtiene información de un video"""
    info = await yt_service.get_video(video_id)
    if not info:
        raise HTTPException(404, "Video no encontrado")
    return info


@router.get("/video/{video_id}/stream")
async def get_stream_url(
    video_id: str,
    quality: str = Query("720p", pattern="^(360p|480p|720p|1080p)$")
):
    """Obtiene URL de stream directo"""
    stream = await yt_service.get_stream_data(video_id, quality)
    if not stream:
        raise HTTPException(404, "Stream no disponible")
    return stream


@router.get("/video/{video_id}/qualities")
async def get_stream_qualities(video_id: str):
    info = await yt_service.get_video(video_id)
    if not info or "formats" not in info:
        raise HTTPException(404, "Video no encontrado")
    return {"qualities": yt_service._extract_available_qualities(info.get("formats", []))}


@router.get("/video/{video_id}/audio")
async def get_audio_url(video_id: str):
    """Obtiene URL de audio (para música)"""
    url = await yt_service.get_audio_url(video_id)
    if not url:
        raise HTTPException(404, "Audio no disponible")
    return {"url": url}


@router.get("/channel/{channel_id}")
async def get_channel_info(channel_id: str):
    """Obtiene información de un canal"""
    info = await yt_service.get_channel(channel_id)
    if not info:
        raise HTTPException(404, "Canal no encontrado")
    return info


@router.get("/channel/{channel_id}/videos")
async def get_channel_videos(channel_id: str, page: int = Query(1, ge=1)):
    """Obtiene videos de un canal"""
    results = await yt_service.get_channel_videos(channel_id, page=page, channel_name=channel_id)
    return [yt_service.format_video_result(v) for v in results]


@router.get("/trending")
async def get_trending(region: str = Query("ES", max_length=2)):
    """Obtiene videos trending"""
    results = await yt_service.get_trending(region)
    return [yt_service.format_video_result(v) for v in results]


# --- Watch history (auth required, persisted via user_library_service) -----


class HistoryUpsert(BaseModel):
    video_id: str
    title: str
    author: Optional[str] = None
    author_id: Optional[str] = None
    thumbnail: Optional[str] = None
    duration_text: Optional[str] = None
    watch_progress: float = 0
    duration_seconds: float = 0
    completed: bool = False


@router.post("/history")
async def add_to_history(
    payload: HistoryUpsert,
    user: database.User = Depends(get_current_user),
):
    user_library_service.upsert_youtube_history(user.username, payload.model_dump())
    return {"status": "ok"}


@router.get("/history")
async def get_history(
    limit: int = Query(60, le=200),
    user: database.User = Depends(get_current_user),
):
    items = user_library_service.list_youtube_history(user.username, limit=limit)
    return {"items": items, "total": len(items)}


@router.delete("/history/{video_id}")
async def remove_from_history(
    video_id: str,
    user: database.User = Depends(get_current_user),
):
    return {"success": user_library_service.remove_youtube_history(user.username, video_id)}


@router.delete("/history")
async def clear_history(user: database.User = Depends(get_current_user)):
    n = user_library_service.clear_youtube_history(user.username)
    return {"cleared": n}


@router.get("/favorites")
async def get_local_favorites(user: database.User = Depends(get_current_user)):
    return {"items": user_library_service.list_youtube_favorites(user.username)}


@router.post("/favorites")
async def add_local_favorite(
    payload: YouTubeFavorite,
    user: database.User = Depends(get_current_user),
):
    user_library_service.upsert_youtube_favorite(user.username, payload.model_dump())
    return {"success": True}


@router.delete("/favorites/{video_id}")
async def delete_local_favorite(
    video_id: str,
    user: database.User = Depends(get_current_user),
):
    return {"success": user_library_service.remove_youtube_favorite(user.username, video_id)}


@router.get("/subscriptions/local")
async def get_local_subscriptions(user: database.User = Depends(get_current_user)):
    return {"items": user_library_service.list_youtube_subscriptions(user.username)}


@router.post("/subscriptions/local")
async def add_local_subscription(
    payload: SubscriptionRequest,
    user: database.User = Depends(get_current_user),
):
    user_library_service.upsert_youtube_subscription(user.username, payload.model_dump())
    return {"success": True}


@router.delete("/subscriptions/local/{channel_id}")
async def delete_local_subscription(
    channel_id: str,
    user: database.User = Depends(get_current_user),
):
    return {"success": user_library_service.remove_youtube_subscription(user.username, channel_id)}


@router.get("/subscriptions/local/feed")
async def get_local_subscription_feed(
    user: database.User = Depends(get_current_user),
):
    subscriptions = user_library_service.list_youtube_subscriptions(user.username)
    items: list[dict] = []
    healthy_channels = 0

    async def fetch_subscription(subscription: dict):
        try:
            return subscription, await asyncio.wait_for(
                yt_service.get_channel_videos(
                    subscription["channel_id"],
                    page=1,
                    channel_url=subscription.get("channel_url"),
                    channel_name=subscription.get("channel_name"),
                ),
                timeout=10,
            )
        except Exception:
            return subscription, []

    # Was hard-capped at 8 — power users hit the wall. Process all, but
    # cap concurrency with a semaphore so we don't overwhelm yt-dlp.
    sem = asyncio.Semaphore(6)

    async def _bounded(sub):
        async with sem:
            return await fetch_subscription(sub)

    results = await asyncio.gather(*(_bounded(subscription) for subscription in subscriptions))

    for subscription, channel_videos in results:
        if channel_videos:
            healthy_channels += 1
        for video in channel_videos[:6]:
            formatted = yt_service.format_video_result(video)
            formatted["channel_id"] = subscription["channel_id"]
            if not formatted.get("author"):
                formatted["author"] = subscription.get("channel_name")
            items.append(formatted)

    items.sort(key=lambda item: item.get("published") or "", reverse=True)
    limited = items[:40]
    return {
        "items": limited,
        "total": len(limited),
        "subscriptions": len(subscriptions[:8]),
        "healthy_channels": healthy_channels,
    }
