"""
TorrentService — gestión de descargas de torrents vía aria2c.

Flujos soportados:
  1. Descargar → notificar cuando termine (download-first)
  2. Ver y Guardar → el usuario usa WebTorrent.js en el navegador mientras aria2c
     guarda en disco simultáneamente.

Progreso expuesto vía SSE en /api/downloads/progress/{job_id}.
Notificaciones globales (descarga completa) vía NotificationService.
"""

import asyncio
import json
import os
import re
import shutil
import time
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from config import settings
from services.download_limits import download_slots, download_rate_per_job, stop_process
from services.download_queue import (
    ACTIVE, PersistentTransfers, TransferError, TransferJob, downloaded_videos, prepare_directory,
    restore_job_fields, source_fingerprint, verified_paths,
)
from services.storage_policy import ensure_download_space
from services.storage_reservations import download_window, reserve_space_async

DOWNLOADS_DIR = settings.DOWNLOADS_ROOT


def _aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def build_aria2_command(
    magnet: str,
    downloads_dir: str,
    file_idx: Optional[int] = None,
) -> list[str]:
    """Build a resource-conscious aria2 command for a personal media server."""
    upload_limit = max(1, min(settings.TORRENT_UPLOAD_RATE_LIMIT, 131072) // settings.CONCURRENT_DOWNLOADS)
    command = [
        "aria2c",
        "--no-conf=true",
        f"--dir={downloads_dir}",
        "--seed-time=0",
        # Allocate as pieces arrive, rather than freezing disk I/O and
        # consuming the server reserve before torrent metadata is available.
        "--file-allocation=none",
        "--summary-interval=3",
        "--console-log-level=warn",
        "--enable-color=false",
        "--truncate-console-readout=false",
        "--continue=true",
        "--check-integrity=true",
        "--check-certificate=true",
        "--bt-save-metadata=true",
        "--bt-load-saved-metadata=true",
        "--max-tries=2",
        "--retry-wait=2",
        "--allow-overwrite=false",
        "--auto-file-renaming=false",
        # Keep DHT and PEX with safety bounds, disable IPv6 DHT socket spam and LPD multicast flood
        "--enable-dht=true",
        "--enable-dht6=false",
        "--dht-entry-point=dht.transmissionbt.com:6881",
        "--dht-message-timeout=3",
        "--enable-peer-exchange=true",
        "--bt-enable-lpd=false",
        # Limit peer count, connections, and upload rate to prevent bufferbloat, link saturation, and NAT table exhaustion
        f"--bt-max-peers={int(os.environ.get('TORRENT_MAX_PEERS', '8'))}",
        "--max-connection-per-server=4",
        "--split=8",
        "--min-split-size=4M",
        "--bt-request-peer-speed-limit=10M",
        f"--max-overall-download-limit={download_rate_per_job()}",
        f"--max-overall-upload-limit={upload_limit}",
        "--disk-cache=64M",
        "--bt-tracker-timeout=8",
        "--bt-tracker-connect-timeout=5",
        "--lowest-speed-limit=1K",
        "--bt-stop-timeout=300",
        # Each job owns its directory; boundary pieces in other episodes
        # must not leave partial videos in the completed library.
        "--bt-remove-unselected-file=true",
    ]
    if file_idx is not None and file_idx >= 0:
        command.append(f"--select-file={file_idx + 1}")
    command.append(magnet)
    return command


class TorrentJob(TransferJob):
    def __init__(self, title, magnet, info_hash=None, file_idx=None, size_bytes=None, priority=0):
        super().__init__(DOWNLOADS_DIR, title, priority)
        self.magnet, self.info_hash, self.file_idx = magnet, info_hash, file_idx
        self.size_bytes = size_bytes
        self.seeders = self.peers = 0
        self._reservation = None

    def to_dict(self):
        return {**super().to_dict(), "info_hash": self.info_hash, "file_idx": self.file_idx,
                "seeders": self.seeders, "peers": self.peers}


class TorrentService(PersistentTransfers):
    def __init__(self):
        super().__init__(lambda: DOWNLOADS_DIR, "torrent")

    def _slots(self):
        return download_slots

    @staticmethod
    def _fingerprint(magnet, info_hash, file_idx):
        return source_fingerprint(_source_hash(magnet, info_hash) or magnet, file_idx)

    def start_torrent_download(self, title, magnet, info_hash=None, file_idx=None, size_bytes=None,
                               priority=0, request_key=None):
        if not _aria2c_available():
            raise RuntimeError("aria2c no está instalado en el sistema")
        job = TorrentJob(title, magnet, info_hash, file_idx, size_bytes, priority)
        payload = {"magnet": magnet, "info_hash": info_hash, "file_idx": file_idx, "size_bytes": size_bytes}
        return self._enqueue(job, payload, self._fingerprint(magnet, info_hash, file_idx), request_key)

    def _restore_job(self, row):
        view = json.loads(row["view"])
        payload = json.loads(row["payload"]) if row["payload"] else {"magnet": ""}
        return restore_job_fields(TorrentJob(view["title"], **payload), row, ("seeders", "peers"))

    def find_active_duplicate(self, magnet, info_hash=None, file_idx=None):
        fingerprint = self._fingerprint(magnet, info_hash, file_idx)
        for job in self._jobs.values():
            if job.status in ACTIVE and self._fingerprint(job.magnet, job.info_hash, job.file_idx) == fingerprint:
                return job.id
        return None

    # Exit codes that point at the operator, not the network — surface them
    # in human-friendly form so the UI explains itself.
    _EXIT_CODE_HINTS = {
        1:  "unknown error",
        2:  "timeout",
        3:  "resource not found",
        5:  "download aborted (likely no peers)",
        6:  "network problem",
        9:  "no disk space",
        13: "i/o error",
        15: "DNS resolve error",
        22: "bad torrent metadata",
        23: "bad magnet URI",
        25: "removed by user",
        27: "bt registered timeout",
        28: "aria2c rejected a command-line option (try `aria2c --version`)",
    }

    async def _download(self, job):
        window = download_window(download_rate_per_job())
        remaining = max(0, job.size_bytes - job.downloaded_bytes) if job.size_bytes else window
        if job.size_bytes and job.downloaded_bytes:
            remaining = max(remaining, min(job.size_bytes, window))
        async with reserve_space_async(self.store.root, "downloads", remaining, job_id=job.id) as reservation:
            job._reservation = reservation
            try:
                await asyncio.to_thread(prepare_directory, job)
                job.status = "connecting"
                self.checkpoint(job, force=True)
                await self._transfer(job)
                job.status = "processing"
                self.checkpoint(job, force=True)
                paths = await asyncio.to_thread(downloaded_videos, job)
                verified = await asyncio.to_thread(verified_paths, job.download_dir, paths)
                await self.complete(job, verified)
            finally:
                job._reservation = None

    async def _transfer(self, job):
        command = build_aria2_command(job.magnet, job.download_dir, job.file_idx)
        priority = ["nice", "-n", "19"] if shutil.which("nice") else []
        process = await asyncio.create_subprocess_exec(
            *priority, *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(job._claim_fd, job._reservation.fd),
        )
        try:
            await self._parse_output(job, process)
            result = await process.wait()
            if result:
                detail = self._EXIT_CODE_HINTS.get(result, "transfer failed")
                raise TransferError(f"aria2c {result} ({detail})", retryable=result in {2, 6, 15, 27})
        finally:
            await stop_process(process)

    async def _parse_output(self, job: TorrentJob, proc: asyncio.subprocess.Process,
                            tail: list[str] | None = None):
        """Parse bounded aria2 progress without storing raw tracker output."""
        pattern = re.compile(
            r"\[#[0-9a-f]+\s+"
            r"(?:([\d.]+\w+)/([\d.]+\w+))?"
            r"(?:\s*\((\d+)%\))?"
            r"(?:.*?CN:(\d+))?"
            r"(?:.*?SD:(\d+))?"
            r".*?DL:([\d.]+\w+)"
            r"(?:.*?ETA:([\d\w]+))?"
        )
        last_space_check = time.monotonic()

        async for raw in _read_output_lines(proc.stdout):
            line = raw.decode(errors="ignore").strip()
            if not line:
                continue
            previous_size = job.size_bytes
            if "[METADATA]" in line:
                job.status = "connecting"
            m = pattern.search(line) if "[METADATA]" not in line else None
            if m:
                job.status = "downloading"
                try:
                    dl_str = m.group(1)
                    tot_str = m.group(2)
                    pct_str = m.group(3)
                    cn_str = m.group(4)
                    sd_str = m.group(5)
                    speed_str = m.group(6)
                    eta_str = m.group(7)

                    if dl_str:
                        job.downloaded_bytes = _parse_size_str(dl_str)
                    if tot_str:
                        tot_bytes = _parse_size_str(tot_str)
                        if tot_bytes > 0:
                            job.size_bytes = tot_bytes
                    if pct_str:
                        job.progress = float(pct_str)
                    elif job.size_bytes and job.downloaded_bytes:
                        job.progress = min(100.0, (job.downloaded_bytes / job.size_bytes) * 100)

                    if cn_str:
                        job.peers = int(cn_str)
                    if sd_str:
                        job.seeders = int(sd_str)
                    if speed_str:
                        job.speed_bytes = _parse_size_str(speed_str)
                    if eta_str:
                        job.eta_seconds = _parse_eta_str(eta_str)
                except Exception:
                    pass
            if job.size_bytes != previous_size or time.monotonic() - last_space_check >= 5:
                if job._reservation:
                    remaining = (max(0, job.size_bytes - job.downloaded_bytes)
                                 if job.size_bytes else download_window(download_rate_per_job()))
                    await asyncio.to_thread(job._reservation.resize, remaining)
                else:
                    await asyncio.to_thread(ensure_download_space, DOWNLOADS_DIR)
                last_space_check = time.monotonic()
            if job._running:
                self.checkpoint(job)



# ------------------------------------------------------------------ #
# Helpers de parseo                                                    #
# ------------------------------------------------------------------ #

_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
               "KB": 1000, "MB": 1000**2, "GB": 1000**3}


def _source_hash(magnet: str, info_hash: Optional[str]) -> str:
    if info_hash:
        return info_hash.casefold()
    return next((value[9:].casefold() for value in parse_qs(urlsplit(magnet).query).get("xt", [])
                 if value.lower().startswith("urn:btih:")), "")


async def _read_output_lines(reader):
    """aria2 uses CR updates as well as newlines, even with redirected stdout."""
    pending = b""
    while chunk := await reader.read(8192):
        lines = re.split(rb"[\r\n]", pending + chunk)
        pending = lines.pop()[-16384:]
        for line in lines:
            if line:
                yield line
    if pending:
        yield pending


def _parse_size_str(value: Optional[str]) -> int:
    if not value:
        return 0
    m = re.match(r"([\d.]+)([A-Za-z]+)", value)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2)
    return int(num * _SIZE_UNITS.get(unit, 1))


def _parse_eta_str(value: Optional[str]) -> Optional[int]:
    """Convierte '8m30s', '1h5m', '45s' a segundos."""
    if not value:
        return None
    total = 0
    for hours, minutes, seconds in re.findall(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value):
        total += int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
    return total or None


def _sanitize_filename(path: str) -> Optional[str]:
    """Clean a downloaded filename: strip nested/duplicate parentheses,
    normalize brackets, and keep only one year if present."""
    dirname = os.path.dirname(path)
    name, ext = os.path.splitext(os.path.basename(path))
    # Collapse duplicate parentheses like "Title (2024) (2024)" → "Title (2024)"
    name = re.sub(r"(\([^)]*\))\s*\1+", r"\1", name)
    # Replace brackets with parentheses for consistency
    name = name.replace("[", "(").replace("]", ")")
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return None
    return os.path.join(dirname, f"{name}{ext}")


# Singleton
torrent_service = TorrentService()
