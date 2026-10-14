"""Build a compact, searchable inventory of the shared download pool."""

from datetime import datetime
from math import ceil
from pathlib import Path
import os
import re
import time

from services.media_library import INCOMPLETE_MARKER, resolve_library_path


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm"}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa", ".sub", ".vtt"}
METADATA_EXTENSIONS = {".jpg", ".jpeg", ".json", ".nfo", ".png", ".txt", ".webp"}
VALID_KINDS = {"all", "video", "subtitle", "metadata", "other"}
_ORPHAN_SUFFIXES = {".aria2", ".part", ".partial", ".tmp", ".temp", ".crdownload", ".ytdl", ".downloading"}
_STALE_MARKER_SECONDS = 3600  # 1 hour (was 7 days)


def _kind_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in SUBTITLE_EXTENSIONS:
        return "subtitle"
    if suffix in METADATA_EXTENSIONS:
        return "metadata"
    return "other"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            decimals = 0 if unit == "B" else 1
            return f"{value:.{decimals}f} {unit}"
        value /= 1024
    return f"{size} B"


def scan_download_library(
    root: str | Path = "/saas-data/downloads",
    *,
    query: str = "",
    kind: str = "all",
    page: int = 1,
    page_size: int = 50,
    directory: str | None = None,
    sort: str = "modified",
    descending: bool = True,
) -> dict:
    if directory is not None:
        return _browse_directory(root, directory, query, kind, page, page_size, sort, descending)
    base = Path(root)
    normalized_query = query.strip().casefold()[:160]
    selected_kind = kind if kind in VALID_KINDS else "all"
    safe_page_size = min(100, max(1, int(page_size)))
    records = []

    if base.exists():
        for path in base.rglob("*"):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                relative = path.relative_to(base)
            except (OSError, ValueError):
                continue
            item_kind = _kind_for(path)
            records.append({
                "name": path.name,
                "relative_path": relative.as_posix(),
                "folder": relative.parent.as_posix() if relative.parent != Path(".") else "Downloads",
                "kind": item_kind,
                "size_bytes": stat.st_size,
                "size_label": _human_size(stat.st_size),
                "modified_at": stat.st_mtime,
                "modified_label": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })

    records.sort(key=lambda item: item["modified_at"], reverse=True)
    total_bytes = sum(item["size_bytes"] for item in records)
    type_counts = {item_kind: 0 for item_kind in VALID_KINDS if item_kind != "all"}
    for item in records:
        type_counts[item["kind"]] += 1

    filtered = [
        item for item in records
        if (selected_kind == "all" or item["kind"] == selected_kind)
        and (not normalized_query or normalized_query in item["relative_path"].casefold())
    ]
    pages = max(1, ceil(len(filtered) / safe_page_size))
    safe_page = min(pages, max(1, int(page)))
    start = (safe_page - 1) * safe_page_size

    return {
        "items": filtered[start:start + safe_page_size],
        "query": query.strip()[:160],
        "kind": selected_kind,
        "page": safe_page,
        "pages": pages,
        "page_size": safe_page_size,
        "total_files": len(records),
        "total_filtered": len(filtered),
        "total_bytes": total_bytes,
        "total_bytes_label": _human_size(total_bytes),
        "type_counts": type_counts,
    }


def _is_orphan_artifact(path: Path) -> bool:
    if path.name in {INCOMPLETE_MARKER, ".aria2"} or path.suffix.casefold() in _ORPHAN_SUFFIXES:
        return True
    try:
        # Corrupt 0-byte non-directory files
        if path.is_file() and path.stat().st_size == 0 and not path.name.startswith("."):
            return True
    except OSError:
        pass
    return False


def _is_stale_marker(marker: Path) -> bool:
    """Only classify an old marker-only job directory as abandoned."""
    try:
        if time.time() - marker.stat().st_mtime < _STALE_MARKER_SECONDS:
            return False
        entries = [entry for entry in marker.parent.iterdir() if entry.name != INCOMPLETE_MARKER]
        return all(_is_orphan_artifact(entry) for entry in entries)
    except OSError:
        return False


def _is_abandoned_marker(marker: Path, root: Path, active_job_ids: set[str] | None) -> bool:
    """Decide whether a job marker belongs to a job no longer running."""
    if active_job_ids is None:
        return _is_stale_marker(marker)
    try:
        job_id = marker.parent.relative_to(root).parts[0]
        if job_id in active_job_ids:
            return False
        return True
    except (OSError, ValueError, IndexError):
        return False


def scan_download_cleanup(
    root: str | Path = "/saas-data/downloads",
    active_job_ids: set[str] | None = None,
) -> dict:
    """Preview safe cleanup candidates in the download pool.

    Candidates are known downloader control/temporary files and genuinely
    empty directories. Active StreamVault jobs (those with a current marker)
    and symlinked paths are never returned.
    """
    base = Path(root).resolve()
    if not base.is_dir():
        return {"items": [], "total_files": 0, "total_directories": 0,
                "total_bytes": 0, "total_bytes_label": _human_size(0)}

    active_roots: list[Path] = []
    stale_markers: set[Path] = set()
    directories: list[Path] = []
    candidates: list[dict] = []
    try:
        for directory, folders, names in os.walk(base, topdown=True, followlinks=False):
            current = Path(directory)
            folders[:] = [name for name in folders if not (current / name).is_symlink()]
            directories.append(current)
            marker = current / INCOMPLETE_MARKER
            if INCOMPLETE_MARKER in names:
                if _is_abandoned_marker(marker, base, active_job_ids):
                    stale_markers.add(marker)
                else:
                    active_roots.append(current)
            for name in names:
                path = current / name
                if path.is_symlink() or not _is_orphan_artifact(path):
                    continue
                if any(active == path or active in path.parents for active in active_roots):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if path.name == INCOMPLETE_MARKER and path not in stale_markers:
                    continue
                candidates.append({
                    "name": path.name,
                    "relative_path": path.relative_to(base).as_posix(),
                    "kind": "orphan",
                    "is_directory": False,
                    "size_bytes": stat.st_size,
                    "size_label": _human_size(stat.st_size),
                    "reason": "stale download control/temporary file",
                })
    except OSError:
        return {"items": [], "total_files": 0, "total_directories": 0,
                "total_bytes": 0, "total_bytes_label": _human_size(0)}

    # Work deepest-first so the UI can remove nested empty directories safely.
    # A directory containing only artifacts already selected above will become
    # empty in the same cleanup operation and is therefore a valid candidate.
    candidate_paths = {item["relative_path"] for item in candidates}
    candidate_dirs: set[str] = set()
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        if directory == base or any(active == directory or active in directory.parents for active in active_roots):
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        if entries:
            relative_entries = [entry.relative_to(base).as_posix() for entry in entries]
            if not all(relative in candidate_paths or relative in candidate_dirs for relative in relative_entries):
                continue
        candidates.append({
            "name": directory.name,
            "relative_path": directory.relative_to(base).as_posix(),
            "kind": "empty_directory",
            "is_directory": True,
            "size_bytes": 0,
            "size_label": "0 B",
            "reason": "empty directory",
        })
        relative_directory = directory.relative_to(base).as_posix()
        candidate_dirs.add(relative_directory)
        candidate_paths.add(relative_directory)

    candidates.sort(key=lambda item: (not item["is_directory"], item["relative_path"].casefold()))
    total_bytes = sum(item["size_bytes"] for item in candidates)
    return {
        "items": candidates,
        "total_files": sum(not item["is_directory"] for item in candidates),
        "total_directories": sum(item["is_directory"] for item in candidates),
        "total_bytes": total_bytes,
        "total_bytes_label": _human_size(total_bytes),
    }


def cleanup_download_library(
    root: str | Path = "/saas-data/downloads",
    paths: list[str] | None = None,
    active_job_ids: set[str] | None = None,
) -> dict:
    """Delete only paths currently reported by :func:`scan_download_cleanup`."""
    preview = scan_download_cleanup(root, active_job_ids)
    allowed = {item["relative_path"]: item for item in preview["items"]}
    selected = set(allowed) if paths is None else {str(path) for path in paths}
    deleted: list[str] = []
    errors: list[dict] = []
    base = Path(root).resolve()
    for relative in sorted(selected, key=lambda item: len(Path(item).parts), reverse=True):
        item = allowed.get(relative)
        if item is None:
            errors.append({"relative_path": relative, "error": "Not a cleanup candidate"})
            continue
        try:
            target = resolve_library_path(base, relative)
            if target.is_symlink() or not target.exists():
                raise OSError("Path changed or is unavailable")
            if item["is_directory"]:
                target.rmdir()
            else:
                target.unlink()
            deleted.append(relative)
        except (OSError, ValueError) as exc:
            errors.append({"relative_path": relative, "error": str(exc)})
    if deleted:
        # Keep library and admin inventory caches coherent after cleanup.
        from services.media_library import _invalidate_scan_cache
        _invalidate_scan_cache(base)
    remaining = scan_download_cleanup(base, active_job_ids)
    return {"deleted": deleted, "errors": errors, "remaining": remaining}


def _directory_entries(directory: Path, recursive: bool):
    if not recursive:
        yield from directory.iterdir()
        return
    for parent, folders, files in os.walk(directory):
        folders[:] = [name for name in folders if not (Path(parent) / name).is_symlink()]
        for name in folders + files:
            yield Path(parent) / name


def _browse_directory(root, directory, query, kind, page, page_size, sort, descending):
    base = Path(root).resolve()
    if Path(directory).is_absolute():
        raise ValueError("A relative folder path is required")
    current = resolve_library_path(base, directory)
    if not current.is_dir() and (directory or base.exists()):
        raise FileNotFoundError("Folder not found")
    relative = current.relative_to(base)
    directory = relative.as_posix() if relative.parts else ""
    query = query.strip()[:160]
    kind = kind if kind in VALID_KINDS else "all"
    sort = sort if sort in {"name", "size", "modified"} else "name"
    page_size = min(100, max(1, int(page_size)))
    records = []
    if current.exists():
        for path in _directory_entries(current, bool(query)):
            try:
                if path.is_symlink():
                    continue
                is_directory = path.is_dir()
                if not is_directory and not path.is_file():
                    continue
                stat = path.stat()
                rel = path.relative_to(base)
            except OSError:
                continue
            item_kind = "folder" if is_directory else _kind_for(path)
            if not is_directory and kind != "all" and item_kind != kind:
                continue
            if query and query.casefold() not in path.relative_to(current).as_posix().casefold():
                continue
            records.append({
                "name": path.name, "relative_path": rel.as_posix(),
                "folder": rel.parent.as_posix() if rel.parent.parts else "",
                "kind": item_kind, "is_directory": is_directory,
                "size_bytes": None if is_directory else stat.st_size,
                "size_label": "—" if is_directory else _human_size(stat.st_size),
                "modified_at": stat.st_mtime,
                "modified_label": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "previewable": path.suffix.lower() in SUBTITLE_EXTENSIONS | METADATA_EXTENSIONS,
            })
    def sort_key(item):
        if sort == "name":
            return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", item["name"].casefold())]
        return (item["size_bytes"] or 0) if sort == "size" else item["modified_at"]
    records.sort(key=lambda item: item["name"].casefold())
    records.sort(key=sort_key, reverse=descending)
    records.sort(key=lambda item: not item["is_directory"])
    pages = max(1, ceil(len(records) / page_size))
    page = min(pages, max(1, int(page)))
    breadcrumbs = [{"name": "Downloads", "path": ""}]
    for index, name in enumerate(relative.parts):
        breadcrumbs.append({"name": name, "path": Path(*relative.parts[:index + 1]).as_posix()})
    return {
        "items": records[(page - 1) * page_size:page * page_size],
        "directory": directory,
        "parent": (relative.parent.as_posix() if relative.parent.parts else "") if directory else None,
        "breadcrumbs": breadcrumbs, "query": query, "kind": kind,
        "sort": sort, "descending": descending,
        "page": page, "pages": pages, "page_size": page_size,
        "total_filtered": len(records),
        "total_files": sum(not item["is_directory"] for item in records),
        "total_folders": sum(item["is_directory"] for item in records),
        "total_bytes_label": _human_size(sum(item["size_bytes"] or 0 for item in records)),
    }
