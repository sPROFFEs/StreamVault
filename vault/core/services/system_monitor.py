"""Bounded, read-only operational telemetry; no recursive disk scans."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time

import psutil

from config import settings
from version import VERSION
from services.storage_reservations import reservation_totals

SERVICE_NAMES = ("core", "downloader", "cloudflared", "nginx")
_service_cache = (0, [])
_lock = threading.Lock()
_playback_lock = threading.Lock()
_playback = {"direct_responses": 0, "conversion_starts": 0, "conversion_errors": 0,
             "conversion_first_byte_ms": None, "hardware_fallbacks": 0}


def playback_event(key, value=None):
    with _playback_lock:
        _playback[key] = _playback[key] + 1 if value is None else round(value)


def record_cleanup(outcome):
    path = Path(settings.DATA_ROOT) / "runtime" / "cleanup-latest.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump({"sampled_at": time.time(), "freed_bytes": outcome["freed_bytes"],
                           "deleted": len(outcome["deleted"]), "errors": len(outcome["errors"]),
                           "storage": outcome["remaining"]["storage"],
                           "recoverable_bytes": outcome["remaining"]["total_bytes"]}, file)
                file.flush()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError:
        pass  # Telemetry must not turn a successful cleanup into a failed request.


def _read_cleanup():
    try:
        return json.loads((Path(settings.DATA_ROOT) / "runtime" / "cleanup-latest.json").read_text())
    except (OSError, ValueError):
        return None


def _download_queue():
    from services.download_queue import ACTIVE, JobStore
    store = JobStore(settings.DOWNLOADS_ROOT)
    if not store.path.is_file():
        return {}
    try:
        from contextlib import closing
        with closing(sqlite3.connect(store.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
            return dict(connection.execute(f"SELECT status, count(*) FROM jobs "
                                           f"WHERE status IN ({','.join('?' for _ in ACTIVE)}) GROUP BY status", ACTIVE))
    except sqlite3.Error:
        return None


def _providers():
    import database
    from routers.resolver import orchestrator
    from sqlalchemy import func
    from services.tmdb_service import tmdb_service
    model = database.FederationPeer
    with database.SessionLocal() as db:
        federation = dict(db.query(model.status, func.count()).filter_by(enabled=True).group_by(model.status).all())
    return {"tmdb": tmdb_service.provider_status(), "torrentio": orchestrator.torrentio.status(),
            "federation": federation}


def service_status():
    global _service_cache
    with _lock:
        now = time.monotonic()
        if now - _service_cache[0] < 10:
            return _service_cache[1]
        try:
            prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
            format_string = '{{json .Name}}\t{{json .State.Status}}\t{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}\t{{json .RestartCount}}\t{{json .State.OOMKilled}}\t{{json .State.StartedAt}}'
            result = subprocess.run([*prefix, "docker", "inspect", "--format", format_string,
                                    *["streamvault_" + name for name in SERVICE_NAMES]],
                                    capture_output=True, text=True, timeout=4)
            available = {}
            for line in result.stdout.splitlines():
                name, status, health, restarts, oom, started = (json.loads(value) for value in line.split('\t'))
                name = name.removeprefix("/streamvault_")
                available[name] = {"name": name, "state": status, "health": health, "restarts": restarts,
                                   "oom_killed": oom, "started_at": started}
            items = [available.get(name, {"name": name, "state": "unavailable"}) for name in SERVICE_NAMES]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            items = [{"name": name, "state": "unavailable"} for name in SERVICE_NAMES]
        _service_cache = (now, items)
        return items


def snapshot():
    from routers import downloads

    root = Path(settings.DATA_ROOT)
    disk = shutil.disk_usage(root if root.exists() else root.parent)
    memory = psutil.virtual_memory()
    process = psutil.Process()
    with _playback_lock:
        playback = dict(_playback)
    ffmpeg = 0
    for child in process.children(recursive=True):
        try:
            ffmpeg += child.name() == "ffmpeg"
        except psutil.Error:
            pass
    return {"version": VERSION, "sampled_at": time.time(), "scope": "host-visible resources",
            "cpu_percent": psutil.cpu_percent(interval=None), "cpu_count": psutil.cpu_count(),
            "memory": {"total_bytes": memory.total, "available_bytes": memory.available, "percent": memory.percent},
            "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free,
                     "percent": round(100 * disk.used / disk.total, 1) if disk.total else 0},
            "process": {"rss_bytes": process.memory_info().rss, "uptime_seconds": max(0, time.time() - process.create_time())},
            "services": service_status(), "downloads": _download_queue(),
            "playback": {**playback, **downloads.playback_activity(), "ffmpeg_processes": ffmpeg},
            "storage": {"reservations": reservation_totals(settings.DOWNLOADS_ROOT), "last_cleanup": _read_cleanup()},
            "providers": _providers()}
