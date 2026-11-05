import os
import uuid
import logging
import asyncio
import time
from pathlib import Path
try:
    import yt_dlp
except ImportError:
    yt_dlp = None
from typing import Dict, List, Optional
from datetime import datetime

from config import settings
from services.storage_policy import ensure_download_space
from services.download_limits import download_slots, download_rate_per_job
from services.media_library import _invalidate_scan_cache, INCOMPLETE_MARKER

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = settings.DOWNLOADS_ROOT
if not os.path.exists(DOWNLOADS_DIR):
    try:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    except Exception as e:
        logger.error(f"Could not create downloads directory {DOWNLOADS_DIR}: {e}")

HISTORY_LIMIT = 50

class DownloadJob:
    def __init__(self, url: str, title: str, quality: str = 'best',
                 extra_headers: Optional[Dict[str, str]] = None):
        self.id = str(uuid.uuid4())
        self.download_dir = os.path.join(DOWNLOADS_DIR, self.id)
        self.url = url
        self.title = title
        self.quality = quality
        self.status = "pending" # pending, downloading, completed, error
        self.progress = 0.0
        self.filepath = ""
        self.error_msg = ""
        self.created_at = datetime.now()
        self.extra_headers = extra_headers or {}

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "error": self.error_msg,
            "created_at": self.created_at.isoformat()
        }

class DownloadService:
    def __init__(self):
        self.active_downloads: Dict[str, DownloadJob] = {}
        self.history: List[DownloadJob] = []
        self._lock = asyncio.Lock()
        self._bg_tasks: Dict[str, asyncio.Task] = {}

    def start_download(self, url: str, title: str, quality: str = 'best',
                       extra_headers: Optional[Dict[str, str]] = None,
                       quota_checked: bool = False) -> str:
        if not quota_checked:
            ensure_download_space(DOWNLOADS_DIR)

        duplicate_id = self.find_active_duplicate(url=url, title=title)
        if duplicate_id:
            return duplicate_id

        job = DownloadJob(url, title, quality, extra_headers=extra_headers)
        self.active_downloads[job.id] = job

        # Store the task reference so it isn't garbage-collected mid-flight.
        task = asyncio.create_task(self._process_download(job))
        self._bg_tasks[job.id] = task
        task.add_done_callback(lambda t, jid=job.id: self._bg_tasks.pop(jid, None))
        return job.id

    def find_active_duplicate(self, url: Optional[str] = None, title: Optional[str] = None) -> Optional[str]:
        for job in self.active_downloads.values():
            if url and job.url == url:
                return job.id
            if title and job.title.casefold() == title.casefold() and job.status in ("pending", "downloading"):
                return job.id
        return None

    def get_job(self, job_id: str) -> Optional[dict]:
        job = self.active_downloads.get(job_id)
        if not job:
            # Check history
            for h in self.history:
                if h.id == job_id: return h.to_dict()
            return None
        return job.to_dict()

    def get_all_jobs(self):
        active = [j.to_dict() for j in self.active_downloads.values()]
        # Recent history
        hist = [j.to_dict() for j in self.history[-20:]]
        return active + hist

    def _archive(self, job: DownloadJob):
        """Move job from active to history, capping history size."""
        if job.id in self.active_downloads:
            del self.active_downloads[job.id]
        if job not in self.history:
            self.history.append(job)
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]

    async def _process_download(self, job: DownloadJob):
        async with download_slots:
            await self._download(job)

    async def _download(self, job: DownloadJob):
        job.status = "downloading"
        logger.info("Starting download: %s", job.id)
        marker = Path(job.download_dir) / INCOMPLETE_MARKER
        last_space_check = 0.0

        def progress_hook(d):
            nonlocal last_space_check
            if time.monotonic() - last_space_check >= 5:
                ensure_download_space(DOWNLOADS_DIR)
                last_space_check = time.monotonic()
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%','')
                    job.progress = float(p)
                except: pass
            elif d['status'] == 'finished':
                job.progress = 100.0
                job.filepath = d.get('filename')

        def _safe_format(q: str) -> str:
            if not q or str(q).lower() in ("best", "auto"):
                return "bestvideo+bestaudio/best"
            height = "".join(filter(str.isdigit, str(q)))
            if height:
                return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            return "bestvideo+bestaudio/best"

        ydl_opts = {
            'format': _safe_format(job.quality),
            'outtmpl': os.path.join(job.download_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
            # Sanitise filenames: restrict to ASCII-safe chars, no weird parentheses.
            'restrictfilenames': True,
            # Hardened options matching YouTubeService
            'nocheckcertificate': True,
            # Android client logic for bot bypass
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'socket_timeout': 30,
            'retries': 5,
            'fragment_retries': 5,
            'buffersize': 131072,
            'concurrent_fragment_downloads': int(os.environ.get("YTDLP_CONCURRENT_FRAGMENTS", "5")),
            'ratelimit': download_rate_per_job(),
            'noplaylist': True,
        }
        if job.extra_headers:
            ydl_opts['http_headers'] = dict(job.extra_headers)

        try:
            # Recheck after queueing: another transfer or conversion may have
            # consumed the available space while this job waited for its slot.
            await asyncio.to_thread(ensure_download_space, DOWNLOADS_DIR)
            await asyncio.to_thread(Path(job.download_dir).mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(marker.touch)
            # Offload blocking yt-dlp to thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._run_yt_dlp, ydl_opts, job.url)
            await asyncio.to_thread(marker.unlink, missing_ok=True)
            job.status = "completed"
            _invalidate_scan_cache(DOWNLOADS_DIR)
            self._archive(job)

        except Exception as e:
            logger.error(f"Download failed {job.id}: {e}")
            job.status = "error"
            job.error_msg = str(e)
            self._archive(job)

    def _run_yt_dlp(self, opts, url):
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is not installed")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
