"""Bounded maintenance of disposable playback copies, never original media."""
import asyncio
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import logging
import os
from pathlib import Path
import re
from stat import S_ISREG
import threading
import time

from fastapi.responses import FileResponse

from config import settings
from services.storage_policy import cache_limit_bytes, cache_retention_hours, disk_free_bytes, get_path_size_bytes, invalidate_path_size
from services.admin_download_library import _human_size

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_ACTIVE: dict[str, int] = {}
_IDLE_SECONDS = 600
_TEMP_SECONDS = 3600
_FINAL = re.compile(r"[a-f0-9]{64}\.(?:mp4|vtt)$")
_TEMP = re.compile(r"[a-f0-9]{64}(?:\.vtt)?\.[a-f0-9]{32}\.(?:mp4|tmp)$")


def _lease_bucket(path, root):
    relative = path.relative_to(root).as_posix()
    return hashlib.sha256(os.fsencode(relative)).hexdigest()[:2]


@contextmanager
def _cache_lock(root, bucket, flags):
    # A fixed number of lock files bounds disk usage. Never unlink them:
    # another process can already have the existing inode open.
    directory = root / ".leases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(bucket, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    try:
        fcntl.flock(fd, flags)
        yield
    finally:
        os.close(fd)


@contextmanager
def protect_cache(*paths):
    root = Path(settings.MEDIA_STORAGE_PATH).resolve()
    resolved = {Path(path).resolve() for path in paths}
    buckets = sorted({_lease_bucket(path, root) for path in resolved})
    keys = [str(path) for path in resolved]
    with ExitStack() as leases:
        for bucket in buckets:
            leases.enter_context(_cache_lock(root, bucket, fcntl.LOCK_SH))
        with _LOCK:
            for key in keys:
                _ACTIVE[key] = _ACTIVE.get(key, 0) + 1
        try:
            yield
        finally:
            with _LOCK:
                for key in keys:
                    if _ACTIVE[key] == 1:
                        del _ACTIVE[key]
                    else:
                        _ACTIVE[key] -= 1


class PlaybackFileResponse(FileResponse):
    """Larger bounded reads; idle cleanup cannot unlink a response still in use."""
    chunk_size = 512 * 1024

    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self._lease = protect_cache(path)
        self._lease.__enter__()
        touch_cache(path)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._lease.__exit__(None, None, None)


def touch_cache(path) -> bool:
    """Pin a descriptor-to-media handoff without changing the Range ETag."""
    path = Path(path)
    with _LOCK:
        try:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if not S_ISREG(stat.st_mode) or stat.st_size <= 0:
                    return False
                os.utime(path.name, ns=(time.time_ns(), stat.st_mtime_ns),
                         dir_fd=parent_fd, follow_symlinks=False)
                return True
            finally:
                os.close(parent_fd)
        except OSError:
            return False


def _candidate(path, stat, now):
    if path.is_symlink() or not path.is_file():
        return None
    key = str(path.resolve())
    with _LOCK:
        if key in _ACTIVE:
            return None
    last_use = max(stat.st_atime, stat.st_mtime)
    age = now - last_use
    temporary = bool(_TEMP.fullmatch(path.name))
    if not temporary and not _FINAL.fullmatch(path.name):
        return None
    if age < (_TEMP_SECONDS if temporary else _IDLE_SECONDS):
        return None
    return {"name": path.name, "kind": "cache_temporary" if temporary else "playback_cache",
            "is_directory": False, "size_bytes": stat.st_size, "size_label": _human_size(stat.st_size),
            "modified_at": last_use,
            "reason": "abandoned conversion" if temporary else "rebuildable playback copy"}


def cache_inventory() -> dict:
    root = Path(settings.MEDIA_STORAGE_PATH).resolve()
    items = []
    now = time.time()
    for folder in (root / 'remux', root / 'subtitles'):
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in folder.iterdir():
            try:
                if _candidate(path, path.lstat(), now):
                    with _cache_lock(root, _lease_bucket(path, root), fcntl.LOCK_EX | fcntl.LOCK_NB):
                        item = _candidate(path, path.lstat(), now)
                        if item:
                            item['relative_path'] = path.relative_to(root).as_posix()
                            items.append(item)
            except OSError:
                continue
    items.sort(key=lambda item: item['modified_at'])
    used = get_path_size_bytes(str(root))
    reclaimable = sum(item['size_bytes'] for item in items)
    return {"items": items, "used_bytes": used, "total_bytes": reclaimable,
            "total_bytes_label": _human_size(reclaimable)}


def cleanup_cache(paths=None) -> dict:
    root = Path(settings.MEDIA_STORAGE_PATH).resolve()
    allowed = {item['relative_path']: item for item in cache_inventory()['items']}
    selected = list(allowed) if paths is None else list(dict.fromkeys(paths))
    deleted, errors, freed = [], [], 0
    for relative in selected:
        path = root / relative
        try:
            if relative not in allowed or path.parent.is_symlink():
                raise ValueError("Not an idle playback cache candidate")
            with _cache_lock(root, _lease_bucket(path, root), fcntl.LOCK_EX | fcntl.LOCK_NB):
                stat = path.lstat()
                if not _candidate(path, stat, time.time()):
                    raise ValueError("File is now in use or has changed")
                path.unlink()
                freed += stat.st_size
                deleted.append(relative)
        except BlockingIOError:
            errors.append({"relative_path": relative, "error": "File is now in use"})
        except (ValueError, OSError) as error:
            errors.append({"relative_path": relative, "error": str(error)})
    if deleted:
        invalidate_path_size(root)
    return {"deleted": deleted, "errors": errors, "freed_bytes": freed}


def maintain_cache(required_bytes=0) -> dict:
    """Expire old copies, then reclaim LRU idle copies only under disk pressure."""
    root = Path(settings.MEDIA_STORAGE_PATH)
    inventory = cache_inventory()
    used, free = inventory['used_bytes'], disk_free_bytes(root)
    limit = cache_limit_bytes()
    reserve = int(settings.MIN_FREE_DISK_GB * 1024**3)
    estimated, selected = 0, []
    now = time.time()
    for item in inventory['items']:
        expired = (item['kind'] == 'cache_temporary' or
                   now - item['modified_at'] >= cache_retention_hours() * 3600)
        if not expired and used - estimated + required_bytes <= limit and free + estimated - required_bytes >= reserve:
            continue
        selected.append(item['relative_path'])
        estimated += item['size_bytes']
    return cleanup_cache(selected) if selected else {"deleted": [], "errors": [], "freed_bytes": 0}


def reserve_cache_space(required_bytes) -> bool:
    maintain_cache(required_bytes)
    root = settings.MEDIA_STORAGE_PATH
    return (get_path_size_bytes(root) + required_bytes <= cache_limit_bytes()
            and disk_free_bytes(root) - required_bytes >= int(settings.MIN_FREE_DISK_GB * 1024**3))


async def background_loop():
    # Do not delay readiness or compete with startup's library recovery scan.
    await asyncio.sleep(60)
    while True:
        try:
            result = await asyncio.to_thread(maintain_cache)
            if result['deleted']:
                logger.info("Reclaimed %d bytes of idle playback cache", result['freed_bytes'])
        except Exception:
            logger.exception("Playback cache maintenance failed")
        await asyncio.sleep(max(5, settings.CHECK_INTERVAL_MINUTES) * 60)
