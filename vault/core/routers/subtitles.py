import asyncio
import logging
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import auth
import database
from config import settings
from services.subtitles_service import SubtitlesService
from services.media_identity import release_year
from services.media_index import local_tmdb_metadata
from services.tmdb_service import tmdb_service
from services.subtitle_content import subtitle_bytes_to_vtt as _subtitle_bytes_to_vtt, download_subtitle, SubtitleTooLarge, SubtitleDownloadError

router = APIRouter(prefix="/api/subtitles", tags=["Subtitles"])
_service = SubtitlesService()
logger = logging.getLogger(__name__)


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

async def _get_tmdb_info(tmdb_id: int, media_type: str, language: str) -> dict:
    """Obtiene título, año e IMDB ID desde TMDB."""
    get_details = tmdb_service.get_tv_details if media_type == "tv" else tmdb_service.get_movie_details
    detail, ids = await asyncio.gather(
        run_in_threadpool(get_details, tmdb_id, language),
        run_in_threadpool(tmdb_service.get_external_ids, tmdb_id, media_type),
    )
    detail = detail or await run_in_threadpool(local_tmdb_metadata, tmdb_id, media_type, language) or {}
    title = detail.get("original_title") or detail.get("original_name") or detail.get("title") or detail.get("name")
    return {"title": title or str(tmdb_id), "imdb_id": ids.get("imdb_id") or detail.get("imdb_id"),
            "year": release_year(detail.get("release_date") or detail.get("first_air_date"))}


@router.get("/search")
async def subtitles_search(
    request: Request,
    tmdb_id: int = Query(...),
    media_type: str = Query(..., pattern="^(movie|tv|series)$"),
    season: Optional[int] = Query(None, ge=0),
    episode: Optional[int] = Query(None, ge=1),
    language: str = Query("en", min_length=2, max_length=8),
    db: Session = Depends(get_db),
):
    if media_type == "series":
        media_type = "tv"
    info = await _get_tmdb_info(tmdb_id, media_type, language)
    # Per-user keys override the env defaults when present.
    user = _maybe_user(request, db)
    subdl_key = None
    wyzie_key = None
    opensubtitles_key = None
    if user and user.preferences:
        subdl_key = user.preferences.subdl_api_key or None
        wyzie_key = user.preferences.wyzie_api_key or None
        opensubtitles_key = user.preferences.opensubtitles_api_key or None
    result = await _service.search(
        title=info.get("title", str(tmdb_id)),
        imdb_id=info.get("imdb_id"),
        media_type=media_type,
        season=season,
        episode=episode,
        language=language,
        year=info.get("year"),
        subdl_api_key=subdl_key,
        wyzie_api_key=wyzie_key,
        opensubtitles_api_key=opensubtitles_key or settings.OPENSUBTITLES_API_KEY,
    )
    for item in result["items"]:
        if item.get("provider") == "OpenSubtitles":
            item["play_url"] = f"/api/subtitles/fetch?provider=opensubtitles&file_id={item['file_id']}"
        elif item.get("stream_url"):
            item["play_url"] = f"/api/subtitles/fetch?provider=remote&url={quote(item['stream_url'], safe='')}"
    return result


_ALLOWED_SUBTITLE_HOSTS = {
    "dl.subdl.com", "sub.wyzie.ru", "sub.wyzie.io",
    "dl.opensubtitles.com", "dl.opensubtitles.org", "dl.opensubtitles.com.ru",
}


@router.get("/fetch")
async def fetch_subtitle(
    request: Request,
    provider: str,
    url: Optional[str] = None,
    file_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = _maybe_user(request, db)
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True,
        headers={"User-Agent": settings.OPENSUBTITLES_USER_AGENT},
    ) as client:
        trusted_link = False
        if provider == "opensubtitles":
            key = (
                user.preferences.opensubtitles_api_key
                if user and user.preferences and user.preferences.opensubtitles_api_key
                else settings.OPENSUBTITLES_API_KEY
            )
            if not key or not file_id:
                raise HTTPException(400, "OpenSubtitles key and file are required")
            response = await client.post(
                "https://api.opensubtitles.com/api/v1/download",
                headers={"Api-Key": key, "User-Agent": settings.OPENSUBTITLES_USER_AGENT},
                json={"file_id": file_id},
            )
            if not response.is_success:
                logger.warning("OpenSubtitles download auth failed: %s %s",
                               response.status_code, response.text[:200])
                raise HTTPException(response.status_code, "OpenSubtitles download authorization failed")
            url = response.json().get("link")
            # The link came from OpenSubtitles' authenticated API — trust it.
            trusted_link = True
        if not url:
            raise HTTPException(400, "No subtitle URL available")
        parsed = urlparse(url)
        # Only enforce the allowlist for untrusted (remote/search-result) URLs,
        # not for links returned by the authenticated OpenSubtitles API.
        if not trusted_link and parsed.hostname not in _ALLOWED_SUBTITLE_HOSTS:
            logger.warning("Subtitle host rejected: %s (url=%s)", parsed.hostname, url[:120])
            raise HTTPException(400, f"Subtitle host is not allowed: {parsed.hostname}")
        try:
            data, content_type = await download_subtitle(client, url)
        except SubtitleTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except (SubtitleDownloadError, httpx.HTTPError) as exc:
            raise HTTPException(502, 'Subtitle provider download failed') from exc
        except TimeoutError as exc:
            raise HTTPException(504, 'Subtitle download timed out') from exc
    try:
        content = await asyncio.to_thread(_subtitle_bytes_to_vtt, data, content_type)
    except SubtitleTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return PlainTextResponse(content, media_type="text/vtt")
