"""Client for communicating with the isolated StreamVault Downloader container."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional
from urllib.parse import quote
try:
    import httpx
except ImportError:
    httpx = None

from config import settings
from services.download_service import DownloadService
from services.media_library import sync_completed_job
from services.torrent_service import torrent_service

logger = logging.getLogger(__name__)


class DownloaderClient:
    def __init__(self, downloader_url: str = ""):
        self.url = downloader_url.rstrip("/")
        self._local_download_service: Optional[DownloadService] = None
        self._completed_remote_jobs: set[tuple[str, str]] = set()

    @property
    def local_download_service(self) -> DownloadService:
        if self._local_download_service is None:
            self._local_download_service = DownloadService()
        return self._local_download_service

    @property
    def is_remote(self) -> bool:
        return bool(self.url)

    async def start_yt_dlp(
        self,
        url: str,
        title: str,
        quality: str = "best",
        extra_headers: Optional[Dict[str, str]] = None,
        priority: int = 0,
        request_key: str | None = None,
    ) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.url}/api/downloads/start",
                    json={
                        "url": url,
                        "title": title,
                        "quality": quality,
                        "extra_headers": extra_headers,
                        "priority": priority,
                        "request_key": request_key,
                    },
                )
                if not resp.is_success:
                    err = resp.json().get("detail") if resp.headers.get("content-type") == "application/json" else resp.text
                    raise RuntimeError(err or f"Downloader returned {resp.status_code}")
                return resp.json()
        service = self.local_download_service
        job_id = service.start_download(
            url, title, quality, extra_headers=extra_headers, quota_checked=True,
            priority=priority, request_key=request_key,
        )
        return {"success": True, "job_id": job_id, "type": "yt-dlp"}

    async def start_torrent(
        self,
        title: str,
        magnet: str,
        info_hash: Optional[str] = None,
        file_idx: Optional[int] = None,
        size_bytes: Optional[int] = None,
        priority: int = 0,
        request_key: str | None = None,
    ) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.url}/api/downloads/start-torrent",
                    json={
                        "title": title,
                        "magnet": magnet,
                        "info_hash": info_hash,
                        "file_idx": file_idx,
                        "size_bytes": size_bytes,
                        "priority": priority,
                        "request_key": request_key,
                    },
                )
                if not resp.is_success:
                    err = resp.json().get("detail") if resp.headers.get("content-type") == "application/json" else resp.text
                    raise RuntimeError(err or f"Downloader returned {resp.status_code}")
                return resp.json()

        duplicate_id = torrent_service.find_active_duplicate(magnet, info_hash, file_idx)
        job_id = torrent_service.start_torrent_download(
            title=title,
            magnet=magnet,
            info_hash=info_hash,
            file_idx=file_idx,
            size_bytes=size_bytes,
            priority=priority,
            request_key=request_key,
        )
        return {"success": True, "job_id": job_id, "type": "torrent", "duplicate": duplicate_id == job_id}

    async def get_jobs(self) -> dict:
        if self.is_remote:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{self.url}/api/downloads/jobs")
                    if resp.is_success:
                        jobs = resp.json()
                        completed = {
                            (kind, job["id"])
                            for kind in ("torrent", "yt_dlp")
                            for job in jobs.get(kind, [])
                            if job.get("status") == "completed"
                        }
                        for _, job_id in completed - self._completed_remote_jobs:
                            await asyncio.to_thread(sync_completed_job, settings.DOWNLOADS_ROOT, job_id)
                        self._completed_remote_jobs = completed
                        return jobs
            except Exception as exc:
                logger.warning("Failed to fetch jobs from downloader service (%s): %s", self.url, exc)
        yt_jobs, torrent_jobs = await asyncio.gather(
            asyncio.to_thread(self.local_download_service.get_all_jobs),
            asyncio.to_thread(torrent_service.get_all_jobs),
        )
        return {"yt_dlp": yt_jobs, "torrent": torrent_jobs}

    async def get_job(self, job_id: str) -> Optional[dict]:
        if self.is_remote:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{self.url}/api/downloads/jobs/{quote(job_id, safe='')}")
                    if resp.is_success:
                        return resp.json()
            except Exception as exc:
                logger.warning("Failed to fetch job %s from downloader service: %s", job_id, exc)
        return await asyncio.to_thread(lambda: self.local_download_service.get_job(job_id) or torrent_service.get_job(job_id))

    async def cancel_job(self, job_id: str) -> bool:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.delete(f"{self.url}/api/downloads/jobs/{quote(job_id, safe='')}")
                return resp.is_success
        return torrent_service.cancel_job(job_id) or self.local_download_service.cancel_job(job_id)

    async def resume_job(self, job_id: str) -> bool:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.url}/api/downloads/jobs/{quote(job_id, safe='')}/resume")
                return resp.is_success
        return torrent_service.resume(job_id) or self.local_download_service.resume(job_id)

    async def stop_all(self) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.url}/api/downloads/stop-all")
                if resp.is_success:
                    return resp.json()
        return {"success": True, "cancelled": torrent_service.stop_all() + self.local_download_service.stop_all()}

    async def clear_history(self) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.url}/api/downloads/clear-history")
                if resp.is_success:
                    return resp.json()
        await asyncio.gather(
            asyncio.to_thread(torrent_service.clear_history),
            asyncio.to_thread(self.local_download_service.clear_history),
        )
        return {"success": True}

    async def restore(self):
        if not self.is_remote:
            await asyncio.gather(torrent_service.restore(), self.local_download_service.restore())

    async def close(self):
        if not self.is_remote:
            await asyncio.gather(torrent_service.close(), self.local_download_service.close())


downloader_client = DownloaderClient(settings.DOWNLOADER_URL)
