"""
Downloads router — gestiona descargas yt-dlp y torrent, con SSE para progreso en tiempo real.

Endpoints:
  POST /api/downloads/start              → yt-dlp (YouTube, URLs HTTP)
  POST /api/downloads/start-torrent      → aria2c (magnet links / torrents)
  GET  /api/downloads/jobs               → lista todos los trabajos
  GET  /api/downloads/jobs/{id}          → estado de un trabajo
  DELETE /api/downloads/jobs/{id}        → cancela un torrent activo
  GET  /api/downloads/progress/{id}      → SSE: progreso en tiempo real
  GET  /api/downloads/notifications      → SSE: notificaciones globales (descarga completa)
  GET  /api/downloads/files              → lista archivos en disco
  DELETE /api/downloads/files            → elimina un archivo del disco
"""

import asyncio
from functools import lru_cache
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4
from urllib.parse import quote
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.download_service import DownloadService
from services.torrent_service import torrent_service
from services.download_client import downloader_client
from services.download_limits import stop_process
from services.streaming_response import ClosingStreamingResponse
from services.storage_policy import get_global_download_quota_gb, get_path_size_bytes
from services.media_library import (
    find_matching_media,
    resolve_library_path,
    scan_media_library,
    srt_to_vtt,
    _invalidate_scan_cache,
    is_incomplete_media,
    classify_video,
    _sidecar_subtitles,
)
from config import settings
from routers.admin import get_current_admin
from routers.preferences import get_current_user

router = APIRouter(
    prefix="/api/downloads",
    tags=["Downloads"],
    dependencies=[Depends(get_current_user)],
)
download_service = DownloadService()
logger = logging.getLogger(__name__)


def get_download_authorized(user=Depends(get_current_user)):
    """Only admins or users with can_download may start downloads."""
    if not (user.is_admin or getattr(user, "can_download", False)):
        raise HTTPException(403, "Download permission required.")
    return user


# Cola de notificaciones globales: lista de asyncio.Queue, una por cliente SSE conectado
_notification_queues: list[asyncio.Queue] = []
_embed_downloads_in_progress: set[str] = set()
_downloader_listener_task: Optional[asyncio.Task] = None


async def _listen_downloader_events():
    """Single persistent background listener to downloader notifications stream."""
    import httpx
    while True:
        try:
            if not downloader_client.is_remote:
                await asyncio.sleep(5)
                continue
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{downloader_client.url}/api/downloads/notifications") as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith("data: "):
                            raw = line[6:].strip()
                            try:
                                event = json.loads(raw)
                                if event.get("type") in ("download_complete", "library_updated"):
                                    _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
                                await _broadcast_notification(event)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("[listener] Downloader stream reconnecting: %s", exc)
            await asyncio.sleep(3)


def _ensure_downloader_listener():
    global _downloader_listener_task
    if downloader_client.is_remote and (_downloader_listener_task is None or _downloader_listener_task.done()):
        try:
            loop = asyncio.get_running_loop()
            _downloader_listener_task = loop.create_task(_listen_downloader_events())
        except RuntimeError:
            pass


async def _broadcast_notification(event: dict):
    """Envía un evento a todos los clientes SSE de notificaciones conectados."""
    dead = []
    for q in _notification_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        if q in _notification_queues:
            _notification_queues.remove(q)


async def _on_torrent_done(job_dict: dict):
    _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
    await _broadcast_notification({
        "type": "download_complete",
        "title": job_dict.get("title", "Descarga"),
        "job_id": job_dict["id"],
        "local_path": job_dict.get("local_path"),
    })
    # #4: Notify library views to refresh — file list changed.
    await _broadcast_notification({"type": "library_updated"})
    # #10: Auto-fetch subtitles for the downloaded file.
    local_path = job_dict.get("local_path")
    title = job_dict.get("title", "")
    if local_path and title:
        asyncio.create_task(_auto_fetch_subtitles(local_path, title))


async def _auto_fetch_subtitles(local_path: str, title: str):
    """Search OpenSubtitles/Subdl/Wyzie for the downloaded title and save
    the best subtitle as a sidecar .vtt next to the video file."""
    from services.subtitles_service import SubtitlesService
    from services.media_library import classify_video
    try:
        info = classify_video(os.path.basename(local_path))
        media_type = "tv" if info.get("media_type") == "series" else "movie"
        sub_service = SubtitlesService()
        result = await sub_service.search(
            title=title,
            media_type=media_type,
            season=info.get("season_number"),
            episode=info.get("episode_number"),
            language="en",
        )
        items = result.get("items", [])
        if not items:
            logger.info(f"[auto-subs] No subtitles found for '{title}'")
            return
        # Prefer OpenSubtitles (highest quality), then any provider with a URL.
        best = next((i for i in items if i.get("provider") == "OpenSubtitles"), None)
        if best:
            content = await _download_opensubtitles_subtitle(best.get("file_id"))
        else:
            best = items[0]
            content = await _download_remote_subtitle(best.get("stream_url"))
        if not content:
            logger.info(f"[auto-subs] No download URL for subtitle of '{title}'")
            return
        text = _subtitle_bytes_to_vtt(content)
        base_path = os.path.splitext(local_path)[0]
        sub_path = f"{base_path}.en.vtt"
        with open(sub_path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"[auto-subs] Saved subtitle for '{title}' → {sub_path}")
    except Exception as exc:
        logger.warning(f"[auto-subs] Failed for '{title}': {exc}")


async def _download_opensubtitles_subtitle(file_id: int) -> bytes | None:
    """Download an OpenSubtitles file via the /download endpoint and return
    raw bytes (may be zip/gzip)."""
    import httpx
    from config import settings
    # Use per-request env key; per-user keys are not available in this
    # background task context.
    key = settings.OPENSUBTITLES_API_KEY
    if not key or not file_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            auth_resp = await client.post(
                "https://api.opensubtitles.com/api/v1/download",
                headers={"Api-Key": key, "User-Agent": settings.OPENSUBTITLES_USER_AGENT},
                json={"file_id": file_id},
            )
            if not auth_resp.is_success:
                logger.warning(f"[auto-subs] OpenSubtitles download auth failed: {auth_resp.status_code}")
                return None
            dl_url = auth_resp.json().get("link")
            if not dl_url:
                return None
            resp = await client.get(dl_url)
            if resp.is_success:
                return resp.content
    except Exception as exc:
        logger.warning(f"[auto-subs] OpenSubtitles download error: {exc}")
    return None


async def _download_remote_subtitle(url: str) -> bytes | None:
    """Download a subtitle from a direct URL (Subdl/Wyzie)."""
    import httpx
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.is_success:
                return resp.content
    except Exception as exc:
        logger.warning(f"[auto-subs] Remote subtitle download error: {exc}")
    return None


def _subtitle_bytes_to_vtt(content: bytes, content_type: str = "") -> str:
    """Convert raw subtitle bytes (plain, zip, or gzip) to WebVTT text.
    Shared with the /api/subtitles/fetch endpoint for consistency."""
    import gzip
    import io
    import zipfile
    if content.startswith(b"PK") or "zip" in content_type:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            candidates = [name for name in archive.namelist()
                          if name.lower().endswith((".srt", ".vtt"))]
            if not candidates:
                raise ValueError("Archive contains no supported subtitle")
            content = archive.read(candidates[0])
    elif content.startswith(b"\x1f\x8b") or "gzip" in content_type:
        content = gzip.decompress(content)
    text = content.decode("utf-8-sig", errors="replace")
    return text if text.lstrip().startswith("WEBVTT") else srt_to_vtt(text)


torrent_service.on_completion(_on_torrent_done)


# ------------------------------------------------------------------ #
# Schemas                                                             #
# ------------------------------------------------------------------ #

class YtDlpRequest(BaseModel):
    url: str
    title: str
    quality: Optional[str] = "best"
    type: Optional[str] = "video"


class TorrentRequest(BaseModel):
    title: str
    magnet: str
    info_hash: Optional[str] = None
    file_idx: Optional[int] = Field(None, ge=0)
    size_bytes: Optional[int] = Field(None, ge=0)


# ------------------------------------------------------------------ #
# Endpoints                                                           #
# ------------------------------------------------------------------ #

@router.post("/start")
async def start_yt_dlp_download(req: YtDlpRequest, user=Depends(get_download_authorized)):
    """Inicia descarga con yt-dlp (YouTube, URLs HTTP directas)."""
    try:
        await _check_download_quota()
        return await downloader_client.start_yt_dlp(req.url, req.title, req.quality)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error iniciando descarga: {e}")


@router.post("/start-torrent")
async def start_torrent_download(req: TorrentRequest, user=Depends(get_download_authorized)):
    """Inicia descarga de torrent con aria2c (magnet link)."""
    await _check_download_quota(req.size_bytes or 0)

    try:
        return await downloader_client.start_torrent(
            title=req.title,
            magnet=req.magnet,
            info_hash=req.info_hash,
            file_idx=req.file_idx,
            size_bytes=req.size_bytes,
        )
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error iniciando descarga torrent: {e}")


async def _check_download_quota(expected_bytes: int = 0):
    used_bytes = await asyncio.to_thread(get_path_size_bytes, settings.DOWNLOADS_ROOT)
    quota_bytes = get_global_download_quota_gb() * (1024 ** 3)
    if used_bytes + expected_bytes >= quota_bytes:
        raise HTTPException(400, "Cuota de descarga superada. Libera espacio primero.")


@router.get("/jobs")
async def list_jobs():
    """Lista todas las descargas activas y recientes (yt-dlp + torrent)."""
    return await downloader_client.get_jobs()


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Estado de un trabajo concreto."""
    job = await downloader_client.get_job(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado")
    return job


@router.delete("/jobs/{job_id}")
@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    """Cancela un torrent activo."""
    if await downloader_client.cancel_job(job_id):
        return {"success": True}
    raise HTTPException(404, "Trabajo no encontrado o ya finalizado")


@router.post("/stop-all")
async def stop_all_jobs():
    """Cancela todos los trabajos activos."""
    return await downloader_client.stop_all()


@router.post("/clear-history")
async def clear_download_history():
    """Limpia el historial de descargas finalizadas."""
    return await downloader_client.clear_history()


@router.get("/progress/{job_id}")
async def stream_progress(job_id: str, request: Request):
    """
    SSE: emite el estado del trabajo cada 2 segundos hasta que termine.
    Úsalo con EventSource('/api/downloads/progress/{id}').
    """
    async def generator():
        for _ in range(600):  # máx 20 min
            if await request.is_disconnected():
                break
            job = await downloader_client.get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break
            yield f"data: {json.dumps(job)}\n\n"
            if job.get("status") in ("completed", "error", "cancelled"):
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/notifications")
async def global_notifications(request: Request):
    """
    SSE: stream de notificaciones globales.
    Recibe eventos cuando una descarga termina.
    Úsalo con EventSource('/api/downloads/notifications') al cargar la app.
    """
    _ensure_downloader_listener()
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _notification_queues.append(q)

    async def generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # Evita que el proxy cierre la conexión
        finally:
            if q in _notification_queues:
                _notification_queues.remove(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _filter_library_items(items: list[dict], query: str) -> list[dict]:
    """Return matching media files, including episodes with numeric filenames."""
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return items
    return [
        item for item in items
        if normalized_query in item.get("name", "").casefold()
        or normalized_query in item.get("title", "").casefold()
    ]


def _paginate_library_items(
    items: list[dict], page: int, page_size: int, sort: str = "recent",
) -> tuple[list[dict], int, int]:
    """Paginate movies and complete series as library entries."""
    grouped_items: dict[tuple[str, str], list[dict]] = {}
    entry_order: list[tuple[str, str]] = []
    for item in items:
        if item["media_type"] == "series":
            key = ("series", item.get("title", "").casefold())
        else:
            key = ("movie", item["relative_path"])
        if key not in grouped_items:
            grouped_items[key] = []
            entry_order.append(key)
        grouped_items[key].append(item)

    # Sort whole shows, not individual episodes: size is the show's total
    # and recent uses its newest episode. Python's stable sort keeps ties
    # in scan order and never mutates the cached library snapshot.
    if sort == "title":
        entry_order.sort(key=lambda key: grouped_items[key][0].get("title", "").casefold())
    elif sort == "size":
        entry_order.sort(key=lambda key: sum(item.get("size_bytes", 0) for item in grouped_items[key]), reverse=True)
    else:
        entry_order.sort(key=lambda key: max(item.get("modified_at", 0) for item in grouped_items[key]), reverse=True)

    from math import ceil
    pages = max(1, ceil(len(entry_order) / page_size))
    safe_page = min(pages, page)
    start = (safe_page - 1) * page_size
    paginated = [
        item
        for key in entry_order[start:start + page_size]
        for item in grouped_items[key]
    ]
    return paginated, safe_page, pages


# /library also works behind older nginx configs that redirect /files to /files/.
@router.get("/library")
@router.get("/files")
async def list_downloaded_files(
    q: str = Query("", description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    media_type: str = Query("all", pattern="^(all|movie|series)$"),
    sort: str = Query("recent", pattern="^(recent|title|size)$"),
    origin: str = Query("local", pattern=r"^(local|all|federated|peer:[1-9][0-9]*)$", max_length=32),
):
    """List downloaded movies and series, with server-side search and pagination (#3, #4)."""
    downloads_dir = settings.DOWNLOADS_ROOT
    library, used_bytes = await asyncio.gather(
        asyncio.to_thread(scan_media_library, downloads_dir),
        asyncio.to_thread(get_path_size_bytes, downloads_dir),
    )
    quota_bytes = get_global_download_quota_gb() * (1024 ** 3)

    # Pagination is by logical library entry, not individual file. A season
    # must remain together; otherwise its episodes disappear onto arbitrary
    # pages before the UI gets a chance to group them.
    items = library["files"] if origin in {"local", "all"} else []
    nodes = None
    if origin != "local":
        from services import federation_service
        remote_items = await asyncio.to_thread(federation_service.library_items)
        nodes = list({item["peer_id"]: {"id": item["peer_id"], "name": item["peer_name"]}
                      for item in remote_items}.values())
        if origin.startswith("peer:"):
            remote_items = [item for item in remote_items if item["peer_id"] == int(origin[5:])]
        items = [*items, *remote_items]
    matching_items = _filter_library_items(items, q)
    if media_type != "all":
        matching_items = [item for item in matching_items if item["media_type"] == media_type]
    total = len(matching_items)
    paginated, safe_page, pages = _paginate_library_items(matching_items, page, page_size, sort)

    paginated = ([item for item in paginated if item["media_type"] == "movie"] +
                 [item for item in paginated if item["media_type"] == "series"])
    return {
        "movies": [m for m in paginated if m.get("media_type") == "movie"],
        "series": [s for s in paginated if s.get("media_type") == "series"],
        "files": paginated,
        "page": safe_page,
        "pages": pages,
        "page_size": page_size,
        "total": total,
        "used_bytes": used_bytes,
        "quota_bytes": quota_bytes,
        "nodes": nodes,
    }


@router.get("/files/stream")
async def stream_downloaded_file(path: str):
    try:
        media_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not media_path.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    if await asyncio.to_thread(is_incomplete_media, media_path, settings.DOWNLOADS_ROOT):
        raise HTTPException(409, "Download is not complete yet")
    return FileResponse(media_path)


def _probe_media_tracks(media_path) -> dict:
    media_path = Path(media_path)
    stat = media_path.stat()
    return _cached_media_tracks(str(media_path.resolve()), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=256)
def _cached_media_tracks(media_path: str, size: int, mtime_ns: int) -> dict:
    if not shutil.which("ffprobe"):
        return {"audio": [], "subtitles": [], "duration_seconds": 0}
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=index,codec_type,codec_name:stream_tags=language,title",
                "-of", "json", str(media_path),
            ],
            check=True, capture_output=True, text=True, timeout=8,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"audio": [], "subtitles": [], "duration_seconds": 0}
    try:
        duration = float(payload.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    streams = payload.get("streams", [])
    tracks = {"audio": [], "subtitles": [], "duration_seconds": duration, "video_codec": ""}
    for stream in streams:
        if stream.get("index") is None:
            continue
        item = {
            "stream_index": stream["index"],
            "codec": stream.get("codec_name") or stream.get("codec_type") or "unknown",
            "language": (stream.get("tags") or {}).get("language") or "und",
            "title": (stream.get("tags") or {}).get("title"),
        }
        if stream.get("codec_type") == "audio":
            tracks["audio"].append(item)
        elif stream.get("codec_type") == "subtitle":
            tracks["subtitles"].append(item)
        elif stream.get("codec_type") == "video" and not tracks["video_codec"]:
            tracks["video_codec"] = item["codec"]
    return tracks


TEXT_SUBTITLE_CODECS = {"ass", "mov_text", "ssa", "srt", "subrip", "text", "webvtt"}
SUBTITLE_EXTRACTION_TIMEOUT = 180


def subtitle_cache_path(media_path: Path, stream: int) -> Path:
    """Return a stable cache path that changes whenever the source file changes."""
    stat = media_path.stat()
    fingerprint = f"{media_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{stream}"
    cache_dir = Path(settings.MEDIA_STORAGE_PATH) / "subtitles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{sha256(fingerprint.encode()).hexdigest()}.vtt"


@router.get("/files/match")
async def match_downloaded_media(
    title: str,
    media_type: str = "movie",
    season: Optional[int] = None,
    episode: Optional[int] = None,
    year: Optional[int] = None,
    path: Optional[str] = None,
):
    if path is not None:
        media_path = await _playable_path(path)
        match = {
            "relative_path": path, "name": media_path.name,
            # Preserve season/show context for numeric episode files nested in
            # TorrentService's per-job download directory.
            **classify_video(media_path.name, relative_path=path),
            "subtitles": await asyncio.to_thread(_sidecar_subtitles, media_path, Path(settings.DOWNLOADS_ROOT)),
        }
    else:
        match = await asyncio.to_thread(
            find_matching_media,
            settings.DOWNLOADS_ROOT, title, media_type, season=season, episode=episode, year=year,
        )
    if not match:
        return {"match": None}
    relative = match["relative_path"]
    media_path = resolve_library_path(settings.DOWNLOADS_ROOT, relative)
    tracks = await asyncio.to_thread(_probe_media_tracks, media_path)
    encoded = quote(relative)
    match["audio_tracks"] = tracks["audio"]
    match["duration_seconds"] = tracks["duration_seconds"]
    match["subtitles"].extend({
        "language": track["language"],
        "label": track["title"] or track["language"].upper(),
        "codec": track["codec"],
        "url": f"/api/downloads/files/embedded-subtitle?path={encoded}&stream={track['stream_index']}",
    } for track in tracks["subtitles"] if track["codec"] in TEXT_SUBTITLE_CODECS)
    match["remux_available"] = bool(shutil.which("ffmpeg"))
    match["url"] = f"/api/downloads/files/stream?path={encoded}"
    match["remux_url"] = f"/api/downloads/files/remux?path={encoded}"
    return {"match": match}


async def _playable_path(path: str) -> Path:
    try:
        media_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not media_path.is_file():
        raise HTTPException(404, "File not found")
    if await asyncio.to_thread(is_incomplete_media, media_path, settings.DOWNLOADS_ROOT):
        raise HTTPException(409, "Download is not complete yet")
    return media_path


def _remux_cache_path(media_path: Path, audio_map: str) -> Path:
    stat = media_path.stat()
    fingerprint = f"v2|{media_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{audio_map}"
    return Path(settings.MEDIA_STORAGE_PATH) / "remux" / f"{sha256(fingerprint.encode()).hexdigest()}.mp4"


def _audio_map(tracks: dict, audio: Optional[int]) -> str:
    if audio is None:
        return f"0:{tracks['audio'][0]['stream_index']}" if tracks["audio"] else "0:a:0?"
    if not any(track["stream_index"] == audio for track in tracks["audio"]):
        raise HTTPException(422, "Audio track not found")
    return f"0:{audio}"


@router.get("/files/playback")
async def prepare_local_playback(path: str, audio: Optional[int] = None,
                                 start: float = Query(0, ge=0, le=86400)):
    """Choose an explicit playback mode so clients never guess from duration."""
    media_path = await _playable_path(path)
    tracks = await asyncio.to_thread(_probe_media_tracks, media_path)
    audio_map = _audio_map(tracks, audio)
    primary_audio = tracks["audio"][0] if tracks["audio"] else None
    default_audio = audio is None or (primary_audio and audio == primary_audio["stream_index"])
    native_mp4 = media_path.suffix.lower() in {".mp4", ".m4v", ".mov"} and tracks.get("video_codec") == "h264"
    native_webm = media_path.suffix.lower() == ".webm" and tracks.get("video_codec") in {"vp8", "vp9", "av1"}
    audio_ok = not primary_audio or primary_audio["codec"] in ({"opus", "vorbis"} if native_webm else {"aac", "mp3"})
    url = f"/api/downloads/files/stream?path={quote(path)}"
    mode, offset = "file", 0
    if not (default_audio and audio_ok and (native_mp4 or native_webm)):
        if not shutil.which("ffmpeg"):
            raise HTTPException(503, "FFmpeg is required for this file")
        cached = _remux_cache_path(media_path, audio_map)
        url = f"/api/downloads/files/remux?path={quote(path)}"
        if audio is not None:
            url += f"&audio={audio}"
        if not cached.is_file() or not cached.stat().st_size:
            mode, offset = "stream", start
            # Pin the mode even if the cache finishes before the video GET.
            url += f"&start={start}&force_stream=true"
    return {"url": url, "mode": mode, "offset": offset, "duration_seconds": tracks["duration_seconds"]}


@router.get("/files/subtitle")
async def serve_downloaded_subtitle(path: str):
    try:
        subtitle_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not subtitle_path.is_file() or subtitle_path.suffix.lower() not in {".srt", ".vtt"}:
        raise HTTPException(404, "Subtitle not found")
    content = subtitle_path.read_text(encoding="utf-8", errors="replace")
    if subtitle_path.suffix.lower() == ".srt":
        content = srt_to_vtt(content)
    return PlainTextResponse(content, media_type="text/vtt")


@router.get("/files/preview")
async def preview_downloaded_file(path: str):
    """Serve a non-video file for inline preview (images, text, subtitles)."""
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    TEXT_EXTS = {".txt", ".srt", ".vtt", ".nfo", ".json", ".ass", ".ssa", ".sub"}
    try:
        file_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not file_path.is_file():
        raise HTTPException(404, "File not found")
    suffix = file_path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return FileResponse(file_path)
    if suffix in TEXT_EXTS:
        content = file_path.read_text(encoding="utf-8", errors="replace")[:200000]
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")
    raise HTTPException(404, "Preview not available for this file type")


@router.get("/files/embedded-subtitle")
async def serve_embedded_subtitle(path: str, stream: int):
    """Convert a text subtitle stream in a local file to browser-native WebVTT."""
    try:
        media_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not media_path.is_file() or not shutil.which("ffmpeg"):
        raise HTTPException(404, "Subtitle unavailable")

    cached = subtitle_cache_path(media_path, stream)
    if cached.is_file() and cached.stat().st_size > 0:
        return FileResponse(cached, media_type="text/vtt")
    temporary = cached.with_name(f"{cached.name}.{uuid4().hex}.tmp")

    def extract():
        return subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(media_path),
                "-map", f"0:{stream}", "-c:s", "webvtt", "-f", "webvtt", "-y", str(temporary),
            ],
            capture_output=True, timeout=SUBTITLE_EXTRACTION_TIMEOUT,
        )

    try:
        result = await asyncio.to_thread(extract)
    except subprocess.TimeoutExpired as exc:
        temporary.unlink(missing_ok=True)
        logger.warning("Subtitle extraction timed out for stream %s", stream)
        raise HTTPException(504, "Subtitle extraction timed out") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(422, "Subtitle conversion failed") from exc
    if result.returncode or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        error = result.stderr.decode("utf-8", errors="replace").strip()[-800:]
        logger.warning("Subtitle conversion failed for stream %s: %s", stream, error)
        raise HTTPException(422, "Subtitle conversion failed")
    os.replace(temporary, cached)
    return FileResponse(cached, media_type="text/vtt")


@router.get("/files/remux")
async def remux_downloaded_media(
    path: str,
    audio: Optional[int] = None,
    start: float = Query(0, ge=0, le=86400),
    force_stream: bool = False,
):
    """Remux local video while converting unsupported audio to browser-safe AAC.

    Two-mode strategy:
      1. If a cached faststart MP4 already exists → serve it via FileResponse
         so the browser can seek freely via HTTP Range requests.
      2. Otherwise stream fragmented MP4, postponing cache construction until
         playback closes. Never run two encoders for the same viewing session.

    This avoids the Cloudflare 524 timeout that a blocking pre-remux would
    cause on large files.
    """
    media_path = await _playable_path(path)
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "Local remux is unavailable")

    tracks = await asyncio.to_thread(_probe_media_tracks, media_path)
    audio_map = _audio_map(tracks, audio)
    cached = _remux_cache_path(media_path, audio_map)

    # Mode 1: cached file exists → serve it (full seeking via Range requests).
    if not force_stream and cached.is_file() and cached.stat().st_size > 0:
        file_response = FileResponse(cached, media_type="video/mp4")
        file_response.headers["Accept-Ranges"] = "bytes"
        file_response.headers["X-Accel-Buffering"] = "no"
        return file_response

    # Jump close to the target using the input index, then discard at most
    # fifteen seconds for frame-accurate output without transcoding the video.
    fast_seek = max(0, start - 15)
    accurate_seek = start - fast_seek
    input_seek_args = ["-ss", f"{fast_seek:.3f}"] if fast_seek > 0 else []
    output_seek_args = ["-ss", f"{accurate_seek:.3f}"] if accurate_seek > 0 else []
    try:
        await asyncio.wait_for(_live_remux_slots.acquire(), timeout=2)
    except asyncio.TimeoutError:
        raise HTTPException(503, "Playback is busy. Try again shortly.", headers={"Retry-After": "3"})

    global _active_live_remuxes
    _active_live_remuxes += 1
    process = None

    async def cleanup():
        global _active_live_remuxes
        try:
            await stop_process(process)
        finally:
            _active_live_remuxes -= 1
            _live_remux_slots.release()
        if process is not None and not cached.is_file():
            _ensure_background_remux(media_path, audio_map, cached)

    # A cache build started after a previous viewing must yield to this one.
    try:
        tasks = list(_background_remux_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except BaseException:
        await cleanup()
        raise

    async def output():
        nonlocal process
        process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-v", "error", "-threads", "2",
                *input_seek_args, "-i", str(media_path), *output_seek_args,
                "-map", "0:v:0", "-map", audio_map,
                *_encoding_args(tracks, audio_map),
                "-avoid_negative_ts", "make_zero",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-frag_duration", "2000000", "-f", "mp4", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        while chunk := await process.stdout.read(256 * 1024):
            yield chunk
        await process.wait()
        if process.returncode:
            # Terminate the HTTP body as an error, rather than marking a
            # truncated conversion as a complete device download.
            raise RuntimeError("Video conversion failed before completion")

    return ClosingStreamingResponse(
        output(),
        cleanup=cleanup,
        media_type="video/mp4",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


# Track in-flight background remux tasks so we don't start duplicates.
_background_remux_tasks: dict[str, asyncio.Task] = {}
_background_remux_slots = asyncio.Semaphore(1)
_live_remux_slots = asyncio.Semaphore(settings.MAX_LIVE_REMUXES)
_active_live_remuxes = 0


def _encoding_args(tracks: dict, audio_map: str) -> list[str]:
    video = ["-c:v", "copy"] if tracks.get("video_codec") == "h264" else [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-threads:v", "2",
    ]
    selected = next((track for track in tracks["audio"] if f"0:{track['stream_index']}" == audio_map), None)
    audio = ["-c:a", "copy"] if selected and selected["codec"] == "aac" else [
        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-threads:a", "1",
    ]
    return video + audio


def _ensure_background_remux(media_path: Path, audio_map: str, cached: Path) -> None:
    """Start a background pre-remux to a seekable faststart MP4 if not already running."""
    if _active_live_remuxes:
        return  # Foreground playback owns the CPU and disk budget.
    key = str(cached)
    existing = _background_remux_tasks.get(key)
    if existing is not None and not existing.done():
        return  # Already in progress.
    if len(_background_remux_tasks) >= 4:
        return  # Bound the pending queue as well as the active encoder.

    task = asyncio.create_task(_run_background_remux(media_path, audio_map, cached))
    _background_remux_tasks[key] = task
    # Clean up the reference when done.
    task.add_done_callback(lambda t: _background_remux_tasks.pop(key, None))


async def _run_background_remux(media_path: Path, audio_map: str, cached: Path) -> None:
    async with _background_remux_slots:
        await _build_remux_cache(media_path, audio_map, cached)


async def _build_remux_cache(media_path: Path, audio_map: str, cached: Path) -> None:
    """Pre-remux the full file to a seekable faststart MP4. Runs in the background."""
    temporary = cached.with_name(f"{cached.stem}.{uuid4().hex}.mp4")
    process = None
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        # A faststart copy temporarily needs another file's worth of space.
        required = media_path.stat().st_size * 2
        used = await asyncio.to_thread(get_path_size_bytes, settings.MEDIA_STORAGE_PATH)
        free = await asyncio.to_thread(shutil.disk_usage, cached.parent)
        if free.free < required or used + required > settings.MAX_CACHE_SIZE_GB * 1024**3:
            logger.info("Skipping remux cache: insufficient cache space")
            return
        tracks = await asyncio.to_thread(_probe_media_tracks, media_path)
        priority = ["nice", "-n", "10"] if shutil.which("nice") else []
        process = await asyncio.create_subprocess_exec(
                *priority, "ffmpeg", "-nostdin", "-y", "-v", "error", "-threads", "2",
                "-i", str(media_path),
                "-map", "0:v:0", "-map", audio_map,
                *_encoding_args(tracks, audio_map),
                "-movflags", "+faststart",
                str(temporary),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=6 * 3600)
        if process.returncode or not temporary.is_file() or temporary.stat().st_size == 0:
            logger.warning("Background remux failed for %s (exit %s)", media_path, process.returncode)
            return
        os.replace(temporary, cached)
        logger.info("Background remux complete: %s", cached.name)
    except Exception:
        logger.exception("Background remux crashed for %s", media_path)
    finally:
        await stop_process(process)
        temporary.unlink(missing_ok=True)


async def close_playback_tasks():
    global _downloader_listener_task
    if _downloader_listener_task and not _downloader_listener_task.done():
        _downloader_listener_task.cancel()
        _downloader_listener_task = None
    tasks = list(_background_remux_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@router.delete("/library")
@router.delete("/files")
async def delete_downloaded_file(
    path: str,
    admin=Depends(get_current_admin),
):
    """Delete a local media file. Admin only."""
    del admin
    try:
        media_path = resolve_library_path(settings.DOWNLOADS_ROOT, path)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    if not media_path.is_file():
        raise HTTPException(404, "Archivo no encontrado")
    try:
        media_path.unlink()
        _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
        return {"success": True}
    except OSError as e:
        raise HTTPException(500, f"Error eliminando archivo: {e}")
