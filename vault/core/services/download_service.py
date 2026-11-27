"""Resumable HTTP media downloads using the shared durable transfer queue."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time

from config import settings
from services.download_limits import download_slots, download_rate_per_job, stop_process, stop_process_group
from services.download_queue import (
    PARTIAL_GUARD, PersistentTransfers, TransferError, TransferJob, downloaded_videos, prepare_directory,
    restore_job_fields, source_fingerprint, verified_paths,
)
from services.storage_policy import ensure_download_space, get_path_size_bytes
from services.storage_reservations import download_window, reserve_space_async

DOWNLOADS_DIR = settings.DOWNLOADS_ROOT
_WORKER = Path(__file__).with_name("ytdlp_worker.py")


class DownloadJob(TransferJob):
    def __init__(self, url, title, quality="best", extra_headers=None, priority=0):
        super().__init__(DOWNLOADS_DIR, title, priority)
        self.url = url
        self.quality = quality
        self.extra_headers = extra_headers or {}


class DownloadService(PersistentTransfers):
    def __init__(self):
        super().__init__(lambda: DOWNLOADS_DIR, "yt_dlp")

    def _slots(self):
        return download_slots

    def start_download(self, url, title, quality="best", extra_headers=None, quota_checked=False,
                       priority=0, request_key=None):
        if not quota_checked:
            ensure_download_space(DOWNLOADS_DIR)
        job = DownloadJob(url, title, quality, extra_headers, priority)
        # Header-bearing embed URLs can depend on expiring cookies. Keep those
        # request secrets in the live worker only.
        payload = None if extra_headers else {"url": url, "quality": quality}
        fingerprint = source_fingerprint(url, quality, extra_headers or {})
        return self._enqueue(job, payload, fingerprint, request_key)

    def _restore_job(self, row):
        view = json.loads(row["view"])
        options = json.loads(row["payload"]) if row["payload"] else {}
        job = DownloadJob(options.get("url"), view["title"], options.get("quality", "best"))
        job._has_source = bool(row["payload"])
        return restore_job_fields(job, row, ())

    async def _download(self, job):
        window = download_window(download_rate_per_job())
        async with reserve_space_async(self.store.root, "downloads", window, job_id=job.id) as reservation:
            await asyncio.to_thread(prepare_directory, job)
            await self._transfer(job, reservation)
            job.status = "processing"
            self.checkpoint(job, force=True)
            verified = await self._verify(job)
            await self.complete(job, verified)

    def _options(self, job):
        quality = "".join(filter(str.isdigit, str(job.quality or "")))
        formats = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best" if quality else "bestvideo+bestaudio/best"
        return {
            "format": formats, "outtmpl": os.path.join(job.download_dir, "%(title)s.%(ext)s"),
            "quiet": True, "no_warnings": True, "restrictfilenames": True,
            "nocheckcertificate": False, "continuedl": True, "overwrites": False,
            "skip_unavailable_fragments": False,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "socket_timeout": 20, "retries": 3, "fragment_retries": 3, "buffersize": 131072,
            "concurrent_fragment_downloads": max(1, int(os.environ.get("YTDLP_CONCURRENT_FRAGMENTS", "5"))),
            "ratelimit": download_rate_per_job(), "noplaylist": True,
            "http_headers": job.extra_headers,
        }

    async def _transfer(self, job, reservation):
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(_WORKER), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(job._claim_fd, reservation.fd),
        )
        expected = 0
        last_space_check = 0.0
        window = download_window(download_rate_per_job())
        try:
            request = json.dumps({"url": job.url, "options": self._options(job)}).encode()
            process.stdin.write(request)
            await process.stdin.drain()
            process.stdin.close()
            while line := await asyncio.wait_for(process.stdout.readline(), timeout=180):
                data = json.loads(line)
                downloaded = data.get("downloaded_bytes") or 0
                size = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                job.downloaded_bytes = downloaded or job.downloaded_bytes
                job.size_bytes = size or job.size_bytes
                job.speed_bytes, job.eta_seconds = data.get("speed") or 0, data.get("eta")
                if size and downloaded:
                    job.progress = min(99.0, 100 * downloaded / size)
                phase = data.get("status")
                if phase in {"finished", "processing"}:
                    job.status = "processing"
                elif phase == "downloading":
                    job.status = "downloading"
                now = time.monotonic()
                if size * 2 > expected or now - last_space_check >= 5 or phase == "finished":
                    written = await asyncio.to_thread(get_path_size_bytes, job.download_dir, max_age=0)
                    expected = max(expected, 2 * (max(0, written - downloaded) + size))
                    await asyncio.to_thread(reservation.resize, max(window, expected - written, written))
                    last_space_check = now
                self.checkpoint(job)
            if await process.wait() != 0:
                raise TransferError("The media provider failed to complete the transfer")
        except asyncio.TimeoutError as exc:
            raise TransferError("The media provider stopped reporting progress", retryable=True) from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise TransferError("The media worker could not start") from exc
        finally:
            await stop_process_group(process)

    async def _verify(self, job):
        directory = Path(job.download_dir)
        files = await asyncio.to_thread(downloaded_videos, job)
        verified = await asyncio.to_thread(verified_paths, directory, files)
        for suffix in (".part", ".ytdl"):
            if await asyncio.to_thread(lambda: any(path != directory / PARTIAL_GUARD
                                                   for path in directory.rglob(f"*{suffix}"))):
                raise TransferError("The download still contains unfinished data")
        if not shutil.which("ffprobe"):
            raise TransferError("FFprobe is required to verify the downloaded video")
        for path in files:
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
                if process.returncode or b"video" not in output.splitlines():
                    raise TransferError("The downloaded file does not contain a readable video")
            except asyncio.TimeoutError as exc:
                raise TransferError("Video verification timed out") from exc
            finally:
                if process.returncode is None:
                    await stop_process(process)
        return verified
