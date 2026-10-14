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
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from config import settings
from services.download_limits import download_slots, download_rate_per_job, stop_process
from services.media_library import VIDEO_EXTENSIONS, INCOMPLETE_MARKER, _invalidate_scan_cache

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = settings.DOWNLOADS_ROOT
HISTORY_LIMIT = 50


def _aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def build_aria2_command(
    magnet: str,
    downloads_dir: str,
    file_idx: Optional[int] = None,
) -> list[str]:
    """Build a resource-conscious aria2 command for a personal media server."""
    upload_limit = max(16384, min(settings.TORRENT_UPLOAD_RATE_LIMIT, 131072) // max(1, settings.CONCURRENT_DOWNLOADS))
    command = [
        "aria2c",
        "--no-conf=true",
        f"--dir={downloads_dir}",
        "--seed-time=0",
        "--file-allocation=falloc",
        "--summary-interval=3",
        "--console-log-level=warn",
        "--enable-color=false",
        "--truncate-console-readout=false",
        "--continue=true",
        "--allow-overwrite=false",
        "--auto-file-renaming=true",
        # Keep DHT and PEX with safety bounds, disable IPv6 DHT socket spam and LPD multicast flood
        "--enable-dht=true",
        "--enable-dht6=false",
        "--dht-entry-point=dht.transmissionbt.com:6881",
        "--dht-message-timeout=3",
        "--enable-peer-exchange=true",
        "--bt-enable-lpd=false",
        # Limit peer count, connections, and upload rate to prevent bufferbloat, link saturation, and NAT table exhaustion
        "--bt-max-peers=8",
        "--max-connection-per-server=2",
        "--split=4",
        "--min-split-size=4M",
        "--bt-request-peer-speed-limit=2M",
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


class TorrentJob:
    def __init__(
        self,
        title: str,
        magnet: str,
        info_hash: Optional[str] = None,
        file_idx: Optional[int] = None,
        size_bytes: Optional[int] = None,
    ):
        self.id = str(uuid.uuid4())
        self.title = title
        self.magnet = magnet
        self.info_hash = info_hash
        self.file_idx = file_idx
        self.size_bytes = size_bytes

        self.status = "pending"       # pending | connecting | downloading | completed | error | cancelled
        self.progress = 0.0           # 0-100
        self.speed_bytes = 0          # bytes/s
        self.eta_seconds: Optional[int] = None
        self.downloaded_bytes = 0
        self.seeders = 0
        self.peers = 0
        self.local_path: Optional[str] = None
        self.local_paths: List[str] = []
        self.download_dir = os.path.join(DOWNLOADS_DIR, self.id)
        self.error_msg = ""
        self.created_at = datetime.now()
        self._process: Optional[asyncio.subprocess.Process] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "info_hash": self.info_hash,
            "file_idx": self.file_idx,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed_bytes": self.speed_bytes,
            "eta_seconds": self.eta_seconds,
            "downloaded_bytes": self.downloaded_bytes,
            "size_bytes": self.size_bytes,
            "seeders": self.seeders,
            "peers": self.peers,
            "local_path": self.local_path,
            "local_paths": self.local_paths,
            "error": self.error_msg,
            "created_at": self.created_at.isoformat(),
        }

    def cancel(self):
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
            except Exception:
                pass
        self.status = "cancelled"


class TorrentService:
    def __init__(self):
        self._jobs: Dict[str, TorrentJob] = {}
        self._history: List[TorrentJob] = []
        self._bg_tasks: Dict[str, asyncio.Task] = {}
        # Callbacks registrados para notificaciones al completar
        self._completion_callbacks: List = []

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start_torrent_download(
        self,
        title: str,
        magnet: str,
        info_hash: Optional[str] = None,
        file_idx: Optional[int] = None,
        size_bytes: Optional[int] = None,
    ) -> str:
        if not _aria2c_available():
            raise RuntimeError("aria2c no está instalado en el sistema")

        duplicate_id = self.find_active_duplicate(magnet, info_hash, file_idx)
        if duplicate_id:
            return duplicate_id

        job = TorrentJob(title, magnet, info_hash, file_idx, size_bytes)
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        self._bg_tasks[job.id] = task
        task.add_done_callback(lambda t, jid=job.id: self._bg_tasks.pop(jid, None))
        return job.id

    def get_job(self, job_id: str) -> Optional[dict]:
        job = self._jobs.get(job_id)
        if job:
            return job.to_dict()
        for h in self._history:
            if h.id == job_id:
                return h.to_dict()
        return None

    def get_all_jobs(self) -> List[dict]:
        active = [j.to_dict() for j in self._jobs.values()]
        recent = [j.to_dict() for j in self._history[-30:] if j.status != "cancelled"]
        return active + recent

    def find_active_duplicate(self, magnet: str, info_hash: Optional[str] = None,
                              file_idx: Optional[int] = None) -> Optional[str]:
        normalized_hash = _source_hash(magnet, info_hash)
        for job in self._jobs.values():
            if job.status not in {"pending", "connecting", "downloading"}:
                continue
            same_hash = normalized_hash and _source_hash(job.magnet, job.info_hash) == normalized_hash
            if (same_hash or job.magnet == magnet) and job.file_idx == file_idx:
                return job.id
        return None

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancel()
        task = self._bg_tasks.get(job_id)
        if task:
            task.cancel()
        self._archive(job)
        return True

    def on_completion(self, callback) -> None:
        """Registra un callback async(job_dict) que se llama al completar una descarga."""
        if callback not in self._completion_callbacks:
            self._completion_callbacks.append(callback)

    # ------------------------------------------------------------------ #
    # Internal — aria2c subprocess                                        #
    # ------------------------------------------------------------------ #

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

    async def _run(self, job: TorrentJob):
        try:
            async with download_slots:
                try:
                    if job.status != "cancelled":
                        await self._download(job)
                finally:
                    # Keep the slot until the old process has really stopped.
                    await stop_process(job._process)
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:
            logger.exception("Torrent job %s failed", job.id)
            job.status = "error"
            job.error_msg = str(exc)
        finally:
            job.speed_bytes = 0
            job.eta_seconds = None
            self._archive(job)

    async def close(self):
        tasks = list(self._bg_tasks.values())
        for job_id in list(self._jobs):
            self.cancel_job(job_id)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _download(self, job: TorrentJob):
        await asyncio.to_thread(Path(job.download_dir).mkdir, parents=True, exist_ok=True)
        marker = Path(job.download_dir) / INCOMPLETE_MARKER
        await asyncio.to_thread(marker.touch)
        job.status = "connecting"

        # All output stays in this job's directory, including shared boundary
        # pieces for files aria2 hasn't selected.
        cmd = build_aria2_command(job.magnet, job.download_dir, job.file_idx)
        priority = ["nice", "-n", "19"] if shutil.which("nice") else []

        logger.info(f"[torrent] launching aria2c: {' '.join(cmd[:6])} … (magnet trimmed)")

        try:
            proc = await asyncio.create_subprocess_exec(
                *priority,
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            job._process = proc

            stdout_tail: list[str] = []
            stderr_tail: list[str] = []

            await asyncio.gather(
                self._parse_output(job, proc, stdout_tail),
                self._capture_stderr(proc, stderr_tail),
            )

            await proc.wait()
            if job.status == "cancelled":
                return

            if proc.returncode == 0:
                job.local_paths = await asyncio.to_thread(
                    self._register_completed_download, job, marker
                )
                job.local_path = job.local_paths[0] if job.local_paths else None
                job.status = "completed"
                job.progress = 100.0
                job.downloaded_bytes = job.size_bytes or job.downloaded_bytes
                logger.info(f"Torrent completado: {job.title} → {job.local_path}")
                await self._notify_completion(job)
            else:
                if job.status != "cancelled":
                    job.status = "error"
                    hint = self._EXIT_CODE_HINTS.get(proc.returncode or 0, "")
                    # Try to surface the actual line aria2c complained about.
                    last_err = next(
                        (l for l in reversed(stderr_tail + stdout_tail)
                         if l and not l.startswith("[#") and l != "."),
                        "",
                    )
                    pieces = [f"aria2c exited {proc.returncode}"]
                    if hint:
                        pieces.append(f"({hint})")
                    if last_err:
                        pieces.append(f"– {last_err[:160]}")
                    job.error_msg = " ".join(pieces)
                    logger.error(
                        "Torrent fallido %s: %s\n--- stderr tail ---\n%s",
                        job.title, job.error_msg, "\n".join(stderr_tail[-20:]),
                    )

        except Exception as e:
            logger.exception(f"Error inesperado en torrent job {job.id}: {e}")
            job.status = "error"
            job.error_msg = str(e)

    async def _parse_output(self, job: TorrentJob, proc: asyncio.subprocess.Process,
                            tail: list[str]):
        """Parsea la salida de aria2c para extraer progreso, velocidad, seeders y ETA.
        Mantiene un buffer pequeño de líneas no-progreso (errores, warnings,
        info) para que el manejador de error pueda reportarlas al UI."""
        pattern = re.compile(
            r"\[#[0-9a-f]+\s+"
            r"(?:([\d.]+\w+)/([\d.]+\w+))?"
            r"(?:\s*\((\d+)%\))?"
            r"(?:.*?CN:(\d+))?"
            r"(?:.*?SD:(\d+))?"
            r".*?DL:([\d.]+\w+)"
            r"(?:.*?ETA:([\d\w]+))?"
        )

        async for raw in _read_output_lines(proc.stdout):
            line = raw.decode(errors="ignore").strip()
            if not line:
                continue
            if "[METADATA]" in line:
                job.status = "connecting"
                continue

            m = pattern.search(line)
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
            else:
                # Keep last ~40 non-progress lines for error reporting.
                tail.append(line)
                if len(tail) > 40:
                    del tail[:-40]

    async def _capture_stderr(self, proc: asyncio.subprocess.Process,
                              tail: list[str]):
        if proc.stderr is None:
            return
        async for raw in _read_output_lines(proc.stderr):
            line = raw.decode(errors="ignore").strip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 40:
                del tail[:-40]

    def _find_downloaded_files(self, job: TorrentJob) -> List[str]:
        """Resolve this transfer's files, including complete season folders."""
        paths = []
        for path in Path(job.download_dir).rglob("*"):
            try:
                if (path.is_file() and not path.is_symlink()
                        and path.suffix.lower() in VIDEO_EXTENSIONS
                        and path.stat().st_size > 0):
                    paths.append(str(path))
            except OSError:
                continue
        return sorted(paths)

    def _register_completed_download(self, job: TorrentJob, marker: Path) -> List[str]:
        """Make an aria2 result visible only after playable media is present."""
        paths = self._find_downloaded_files(job)
        if not paths:
            raise RuntimeError(
                "Torrent finished but no playable video was downloaded; "
                "the selected Torrentio file index may be invalid"
            )
        marker.unlink(missing_ok=True)
        _invalidate_scan_cache(DOWNLOADS_DIR)
        return paths

    def _archive(self, job: TorrentJob):
        if job.id in self._jobs:
            del self._jobs[job.id]
        if job not in self._history:
            self._history.append(job)
        if len(self._history) > HISTORY_LIMIT:
            self._history = self._history[-HISTORY_LIMIT:]

    async def _notify_completion(self, job: TorrentJob):
        for cb in self._completion_callbacks:
            try:
                await cb(job.to_dict())
            except Exception as e:
                logger.error(f"Error en callback de completado: {e}")


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
