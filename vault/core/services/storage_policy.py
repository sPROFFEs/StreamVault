import json
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from config import settings

POLICY_DIR = Path(settings.DATA_ROOT) / "config"
POLICY_FILE = POLICY_DIR / "storage_policy.json"


def _default_quota_gb() -> int:
    return max(1, int(settings.MAX_CACHE_SIZE_GB))


def get_global_download_quota_gb() -> int:
    if POLICY_FILE.exists():
        try:
            payload = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
            value = int(payload.get("global_download_quota_gb", _default_quota_gb()))
            return max(1, value)
        except Exception:
            return _default_quota_gb()
    return _default_quota_gb()


def set_global_download_quota_gb(value: int) -> int:
    quota = max(1, int(value))
    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(
        json.dumps({"global_download_quota_gb": quota}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return quota


import time

_PATH_SIZE_CACHE: dict[str, tuple[float, int]] = {}
_PATH_SIZE_TTL = 5.0  # seconds
_SIZE_LOCK = threading.RLock()
_SIZE_SCAN_LOCK = threading.Lock()
_SIZE_GENERATION = 0


def get_path_size_bytes(path: str) -> int:
    resolved = str(Path(path).resolve())
    # Concurrent library requests share one bounded scan, rather than all
    # walking the same mounted disk when the five-second cache expires.
    with _SIZE_LOCK:
        now = time.monotonic()
        cached = _PATH_SIZE_CACHE.get(resolved)
        if cached and (now - cached[0]) < _PATH_SIZE_TTL:
            return cached[1]
    with _SIZE_SCAN_LOCK:
        with _SIZE_LOCK:
            cached = _PATH_SIZE_CACHE.get(resolved)
            if cached and time.monotonic() - cached[0] < _PATH_SIZE_TTL:
                return cached[1]
            generation = _SIZE_GENERATION
        total = 0
        for root, folders, files in os.walk(resolved, followlinks=False):
            folders[:] = [name for name in folders if not (Path(root) / name).is_symlink()]
            for filename in files:
                file_path = Path(root) / filename
                try:
                    if not file_path.is_symlink():
                        total += file_path.stat().st_size
                except OSError:
                    continue
        with _SIZE_LOCK:
            if generation == _SIZE_GENERATION:
                if len(_PATH_SIZE_CACHE) >= 128:
                    _PATH_SIZE_CACHE.clear()
                _PATH_SIZE_CACHE[resolved] = (time.monotonic(), total)
        return total


def invalidate_path_size(path: str | Path | None = None) -> None:
    global _SIZE_GENERATION
    with _SIZE_LOCK:
        # Invalidations also happen on the HTTP loop. They must never wait
        # behind filesystem traversal; a scan already running cannot republish
        # its stale result after this generation changes.
        _SIZE_GENERATION += 1
        if path is None:
            _PATH_SIZE_CACHE.clear()
        else:
            _PATH_SIZE_CACHE.pop(str(Path(path).resolve()), None)


def disk_free_bytes(path: str | Path) -> int:
    existing = Path(path)
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return shutil.disk_usage(existing).free


def ensure_download_space(path: str | Path, expected_bytes: int = 0) -> None:
    """A quota is not free disk: preserve space for SQLite, logs and playback."""
    expected_bytes = max(0, expected_bytes)
    if get_path_size_bytes(str(path)) + expected_bytes >= get_global_download_quota_gb() * 1024**3:
        raise ValueError("Download quota exceeded. Free space from shared downloads.")
    reserve = int(settings.MIN_FREE_DISK_GB * 1024**3)
    if disk_free_bytes(path) - expected_bytes < reserve:
        raise ValueError("Insufficient disk space. The server's free disk reserve must be preserved.")


def quota_available_bytes(path: str) -> int:
    quota_bytes = get_global_download_quota_gb() * (1024**3)
    used = get_path_size_bytes(path)
    return max(0, quota_bytes - used)
