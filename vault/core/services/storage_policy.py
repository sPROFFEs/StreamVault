import json
import os
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


def get_path_size_bytes(path: str) -> int:
    resolved = str(Path(path).resolve())
    now = time.monotonic()
    cached = _PATH_SIZE_CACHE.get(resolved)
    if cached and (now - cached[0]) < _PATH_SIZE_TTL:
        return cached[1]

    total = 0
    if not os.path.exists(path):
        return total

    for root, _, files in os.walk(path):
        for filename in files:
            file_path = os.path.join(root, filename)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                continue

    _PATH_SIZE_CACHE[resolved] = (now, total)
    return total


def quota_available_bytes(path: str) -> int:
    quota_bytes = get_global_download_quota_gb() * (1024**3)
    used = get_path_size_bytes(path)
    return max(0, quota_bytes - used)
