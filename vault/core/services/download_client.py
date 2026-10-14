"""Client for communicating with the isolated StreamVault Downloader container."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
try:
    import httpx
except ImportError:
    httpx = None

from config import settings
from services.download_service import DownloadService
from services.media_library import _invalidate_scan_cache
from services.torrent_service import torrent_service, TorrentService

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

    async def start_yt_dlp(self, url: str, title: str, quality: str = "best") -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.url}/api/downloads/start",
                    json={"url": url, "title": title, "quality": quality},
                )
                if not resp.is_success:
                    err = resp.json().get("detail") if resp.headers.get("content-type") == "application/json" else resp.text
                    raise RuntimeError(err or f"Downloader returned {resp.status_code}")
                return resp.json()
        job_id = self.local_download_service.start_download(url, title, quality, quota_checked=True)
        return {"success": True, "job_id": job_id, "type": "yt-dlp"}

    async def start_torrent(
        self,
        title: str,
        magnet: str,
        info_hash: Optional[str] = None,
        file_idx: Optional[int] = None,
        size_bytes: Optional[int] = None,
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
                    },
                )
                if not resp.is_success:
                    err = resp.json().get("detail") if resp.headers.get("content-type") == "application/json" else resp.text
                    raise RuntimeError(err or f"Downloader returned {resp.status_code}")
                return resp.json()

        duplicate_id = torrent_service.find_active_duplicate(magnet, info_hash, file_idx)
        if duplicate_id:
            return {"success": True, "job_id": duplicate_id, "type": "torrent", "duplicate": True}
        job_id = torrent_service.start_torrent_download(
            title=title,
            magnet=magnet,
            info_hash=info_hash,
            file_idx=file_idx,
            size_bytes=size_bytes,
        )
        return {"success": True, "job_id": job_id, "type": "torrent", "duplicate": False}

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
                        # The downloader's in-memory cache is separate from
                        # core's. Invalidate before the UI reloads the library.
                        if completed - self._completed_remote_jobs:
                            _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
                        self._completed_remote_jobs = completed
                        return jobs
            except Exception as exc:
                logger.warning("Failed to fetch jobs from downloader service (%s): %s", self.url, exc)
        return {
            "yt_dlp": self.local_download_service.get_all_jobs(),
            "torrent": torrent_service.get_all_jobs(),
        }

    async def get_job(self, job_id: str) -> Optional[dict]:
        if self.is_remote:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{self.url}/api/downloads/jobs/{job_id}")
                    if resp.is_success:
                        return resp.json()
            except Exception as exc:
                logger.warning("Failed to fetch job %s from downloader service: %s", job_id, exc)
        return self.local_download_service.get_job(job_id) or torrent_service.get_job(job_id)

    async def cancel_job(self, job_id: str) -> bool:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.delete(f"{self.url}/api/downloads/jobs/{job_id}")
                return resp.is_success
        return torrent_service.cancel_job(job_id)

    async def stop_all(self) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.url}/api/downloads/stop-all")
                if resp.is_success:
                    return resp.json()
        cancelled = 0
        for job in list(torrent_service._jobs.values()):
            if job.status in ("pending", "connecting", "downloading"):
                job.cancel()
                cancelled += 1
        return {"success": True, "cancelled": cancelled}

    async def clear_history(self) -> dict:
        if self.is_remote:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.url}/api/downloads/clear-history")
                if resp.is_success:
                    return resp.json()
        torrent_service._history.clear()
        self.local_download_service.history.clear()
        return {"success": True}


downloader_client = DownloaderClient(settings.DOWNLOADER_URL)
