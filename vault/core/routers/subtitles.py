import gzip
import io
import logging
import zipfile
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

import auth
import database
from config import settings
from services.subtitles_service import SubtitlesService
from services.media_library import srt_to_vtt

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

TMDB_BASE = "https://api.themoviedb.org/3"


async def _get_tmdb_info(tmdb_id: int, media_type: str) -> dict:
    """Obtiene título, año e IMDB ID desde TMDB."""
    if not settings.TMDB_API_KEY:
        return {}
    endpoint = "tv" if media_type == "tv" else "movie"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            detail = await client.get(
                f"{TMDB_BASE}/{endpoint}/{tmdb_id}",
                params={"api_key": settings.TMDB_API_KEY},
            )
            ext_ids = await client.get(
                f"{TMDB_BASE}/{endpoint}/{tmdb_id}/external_ids",
                params={"api_key": settings.TMDB_API_KEY},
            )
            title = ""
            year: Optional[int] = None
            if detail.status_code == 200:
                d = detail.json()
                title = d.get("title") or d.get("name") or ""
                date_str = d.get("release_date") or d.get("first_air_date") or ""
                if date_str and len(date_str) >= 4:
                    try:
                        year = int(date_str[:4])
                    except ValueError:
                        pass
            imdb_id = ""
            if ext_ids.status_code == 200:
                imdb_id = ext_ids.json().get("imdb_id", "")
            return {"title": title, "imdb_id": imdb_id, "year": year}
    except Exception as e:
        logger.warning(f"No se pudo obtener info TMDB para {tmdb_id}: {e}")
        return {}


@router.get("/search")
async def subtitles_search(
    request: Request,
    tmdb_id: int = Query(...),
    media_type: str = Query(..., pattern="^(movie|tv|series)$"),
    season: Optional[int] = Query(None, ge=1),
    episode: Optional[int] = Query(None, ge=1),
    language: str = Query("en", min_length=2, max_length=8),
    db: Session = Depends(get_db),
):
    if media_type == "series":
        media_type = "tv"
    info = await _get_tmdb_info(tmdb_id, media_type)
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


def _subtitle_bytes_to_vtt(content: bytes, content_type: str = "") -> str:
    if content.startswith(b"PK") or "zip" in content_type:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates = [name for name in archive.namelist() if name.lower().endswith((".srt", ".vtt"))]
            if not candidates:
                raise ValueError("Archive contains no supported subtitle")
            content = archive.read(candidates[0])
    elif content.startswith(b"\x1f\x8b") or "gzip" in content_type:
        content = gzip.decompress(content)
    text = content.decode("utf-8-sig", errors="replace")
    return text if text.lstrip().startswith("WEBVTT") else srt_to_vtt(text)


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
        response = await client.get(url)
        if not response.is_success:
            logger.warning("Subtitle download failed: %s %s (final_url=%s)",
                           response.status_code, response.text[:200], str(response.url)[:120])
            raise HTTPException(502, "Subtitle provider download failed")
        if len(response.content) > 8 * 1024 * 1024:
            raise HTTPException(413, "Subtitle file is too large")
    try:
        content = _subtitle_bytes_to_vtt(response.content, response.headers.get("content-type", ""))
    except (ValueError, zipfile.BadZipFile, gzip.BadGzipFile) as exc:
        raise HTTPException(422, str(exc)) from exc
    return PlainTextResponse(content, media_type="text/vtt")
