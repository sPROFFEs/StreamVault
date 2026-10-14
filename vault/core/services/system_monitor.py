"""Bounded, read-only operational telemetry; no recursive disk scans."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import psutil

from config import settings
from version import VERSION

SERVICE_NAMES = ("core", "downloader", "cloudflared", "nginx")
_service_cache = (0, [])
_lock = threading.Lock()


def service_status():
    global _service_cache
    with _lock:
        now = time.monotonic()
        if now - _service_cache[0] < 10:
            return _service_cache[1]
        try:
            prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
            result = subprocess.run([*prefix, "docker", "inspect", *["streamvault_" + name for name in SERVICE_NAMES]],
                                    capture_output=True, text=True, check=True, timeout=4)
            items = []
            for service in json.loads(result.stdout):
                state = service.get("State", {})
                items.append({"name": service["Name"].removeprefix("/streamvault_"), "state": state.get("Status", "unknown"),
                              "health": state.get("Health", {}).get("Status"), "restarts": service.get("RestartCount", 0),
                              "oom_killed": state.get("OOMKilled", False), "started_at": state.get("StartedAt")})
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            items = [{"name": name, "state": "unavailable"} for name in SERVICE_NAMES]
        _service_cache = (now, items)
        return items


def snapshot():
    root = Path(settings.DATA_ROOT)
    disk = shutil.disk_usage(root if root.exists() else root.parent)
    memory = psutil.virtual_memory()
    process = psutil.Process()
    return {"version": VERSION, "sampled_at": time.time(), "scope": "host-visible resources",
            "cpu_percent": psutil.cpu_percent(interval=None), "cpu_count": psutil.cpu_count(),
            "memory": {"total_bytes": memory.total, "available_bytes": memory.available, "percent": memory.percent},
            "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free,
                     "percent": round(100 * disk.used / disk.total, 1) if disk.total else 0},
            "process": {"rss_bytes": process.memory_info().rss, "uptime_seconds": max(0, time.time() - process.create_time())},
            "services": service_status()}
