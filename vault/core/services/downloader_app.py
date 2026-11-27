"""Dedicated downloader service application for StreamVault.

Runs in an isolated container to offload heavy yt-dlp downloading and aria2c
torrent processes, preventing I/O, CPU, and socket saturation in the core container.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from services.download_service import DownloadService
from services.download_queue import TERMINAL
from services.torrent_service import torrent_service
from services.media_library import identify_video

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app):
    try:
        await asyncio.gather(torrent_service.restore(), download_service.restore())
        yield
    finally:
        await asyncio.gather(torrent_service.close(), download_service.close(), return_exceptions=True)


app = FastAPI(title="StreamVault Downloader Service", lifespan=lifespan)
download_service = DownloadService()

_notification_queues: list[asyncio.Queue] = []


async def _broadcast_notification(event: dict):
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
    await _broadcast_notification({
        "type": "download_complete",
        "title": job_dict.get("title", "Descarga"),
        "job_id": job_dict["id"],
        "local_path": job_dict.get("local_path"),
    })
    await _broadcast_notification({"type": "library_updated"})
    local_path = job_dict.get("local_path")
    title = job_dict.get("title", "")
    if local_path and title:
        asyncio.create_task(_auto_fetch_subtitles(local_path, title))


async def _auto_fetch_subtitles(local_path: str, title: str):
    from services.subtitles_service import SubtitlesService
    try:
        relative = os.path.relpath(local_path, settings.DOWNLOADS_ROOT)
        info = identify_video(os.path.basename(local_path), relative)
        if info["identification_status"] != "parsed":
            return
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
            return
        best = next((i for i in items if i.get("provider") == "OpenSubtitles"), items[0])
        # Download subtitle content
        import httpx
        content = None
        if best.get("provider") == "OpenSubtitles" and best.get("file_id"):
            key = settings.OPENSUBTITLES_API_KEY
            if key:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    auth_resp = await client.post(
                        "https://api.opensubtitles.com/api/v1/download",
                        headers={"Api-Key": key, "User-Agent": settings.OPENSUBTITLES_USER_AGENT},
                        json={"file_id": best["file_id"]},
                    )
                    if auth_resp.is_success:
                        dl_url = auth_resp.json().get("link")
                        if dl_url:
                            from services.subtitle_content import download_subtitle
                            content, _ = await download_subtitle(client, dl_url)
        elif best.get("stream_url"):
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                from services.subtitle_content import download_subtitle
                content, _ = await download_subtitle(client, best["stream_url"])
        if content:
            from services.subtitle_content import subtitle_bytes_to_vtt
            text = await asyncio.to_thread(subtitle_bytes_to_vtt, content)
            base_path = os.path.splitext(local_path)[0]
            sub_path = f"{base_path}.en.vtt"
            with open(sub_path, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("[auto-subs] Saved subtitle for '%s' -> %s", title, sub_path)
    except Exception as exc:
        logger.warning("[auto-subs] Subtitle fetch failed for '%s': %s", title, exc)


torrent_service.on_completion(_on_torrent_done)
download_service.on_completion(_on_torrent_done)


class YtDlpPayload(BaseModel):
    url: str
    title: str
    quality: Optional[str] = "best"
    type: Optional[str] = "video"
    extra_headers: Optional[Dict[str, str]] = None
    priority: int = Field(0, ge=-1, le=1)
    request_key: str | None = Field(None, max_length=128)


class TorrentPayload(BaseModel):
    title: str
    magnet: str
    info_hash: Optional[str] = None
    file_idx: Optional[int] = Field(None, ge=0)
    size_bytes: Optional[int] = Field(None, ge=0)
    priority: int = Field(0, ge=-1, le=1)
    request_key: str | None = Field(None, max_length=128)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "streamvault-downloader"}


@app.get("/ready")
def ready():
    try:
        with download_service.store.connect() as connection:
            connection.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.post("/api/downloads/start")
async def start_yt_dlp(payload: YtDlpPayload):
    try:
        job_id = download_service.start_download(
            payload.url,
            payload.title,
            payload.quality,
            extra_headers=payload.extra_headers,
            quota_checked=True,
            priority=payload.priority,
            request_key=payload.request_key,
        )
        return {"success": True, "job_id": job_id, "type": "yt-dlp"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error starting download: {e}")


@app.post("/api/downloads/start-torrent")
async def start_torrent(payload: TorrentPayload):
    try:
        duplicate_id = torrent_service.find_active_duplicate(payload.magnet, payload.info_hash, payload.file_idx)
        job_id = torrent_service.start_torrent_download(
            title=payload.title,
            magnet=payload.magnet,
            info_hash=payload.info_hash,
            file_idx=payload.file_idx,
            size_bytes=payload.size_bytes,
            priority=payload.priority,
            request_key=payload.request_key,
        )
        return {"success": True, "job_id": job_id, "type": "torrent", "duplicate": duplicate_id == job_id}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error starting torrent: {e}")


@app.get("/api/downloads/jobs")
async def list_jobs():
    yt_jobs, torrent_jobs = await asyncio.gather(
        asyncio.to_thread(download_service.get_all_jobs), asyncio.to_thread(torrent_service.get_all_jobs),
    )
    return {"yt_dlp": yt_jobs, "torrent": torrent_jobs}


@app.get("/api/downloads/jobs/{job_id}")
async def get_job(job_id: str):
    job = await asyncio.to_thread(lambda: download_service.get_job(job_id) or torrent_service.get_job(job_id))
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.delete("/api/downloads/jobs/{job_id}")
@app.post("/api/downloads/cancel/{job_id}")
async def cancel_job(job_id: str):
    if torrent_service.cancel_job(job_id) or download_service.cancel_job(job_id):
        return {"success": True}
    raise HTTPException(404, "Job not found or already completed")


@app.post("/api/downloads/stop-all")
async def stop_all_downloads():
    return {"success": True, "cancelled": torrent_service.stop_all() + download_service.stop_all()}


@app.post("/api/downloads/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    if torrent_service.resume(job_id) or download_service.resume(job_id):
        return {"success": True, "job_id": job_id}
    raise HTTPException(409, "This download cannot be resumed")


@app.post("/api/downloads/clear-history")
async def clear_history():
    await asyncio.gather(
        asyncio.to_thread(torrent_service.clear_history), asyncio.to_thread(download_service.clear_history),
    )
    return {"success": True}


@app.get("/api/downloads/progress/{job_id}")
async def stream_progress(job_id: str, request: Request):
    async def generator():
        for _ in range(600):
            if await request.is_disconnected():
                break
            job = await asyncio.to_thread(lambda: download_service.get_job(job_id) or torrent_service.get_job(job_id))
            if not job:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break
            yield f"data: {json.dumps(job)}\n\n"
            if job.get("status") in TERMINAL:
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
@app.get("/api/downloads/notifications")
async def global_notifications(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _notification_queues.append(q)

    async def generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in _notification_queues:
                _notification_queues.remove(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
