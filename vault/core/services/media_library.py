"""Filesystem-backed local video library helpers."""
from __future__ import annotations

import re
import time
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm", ".m4v"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
INCOMPLETE_MARKER = ".streamvault-incomplete"

_YEAR_REGEX = re.compile(r"\b(19\d{2}|20\d{2})\b")

_RELEASE_TOKEN = re.compile(
    r"(?i)\b(?:19|20)\d{2}\b|"
    r"\b(?:480|576|720|1080|2160)p\b|"
    r"\b(?:2k|4k|8k|uhd|fhd|hd|sd)\b|"
    r"\b(?:x264|x265|h264|h265|hevc|avc|av1|xvid|divx|10bit|8bit|hi10p)\b|"
    r"\b(?:hdr10\+?|hdr|dovi|dv|dolby[ ._-]*vision)\b|"
    r"\b(?:aac|ac3|eac3|ddp(?:5\.1|7\.1)?|dd5\.1|dts(?:-hd|-ma)?|truehd|atmos|flac|mp3|opus|vorbis|5\.1|7\.1|2\.0)\b|"
    r"\b(?:web-?dl|web-?rip|bluray|blu-ray|bdrip|brrip|hdtv|pdtv|dvdrip|dvd|remux|telesync|hdcam)\b|"
    r"\b(?:proper|repack|rerip|multi|dual|subbed|dubbed|ita|spa|eng|es|latino|castellano|french|german|unrated|extended|directors[ ._-]*cut)\b"
)

_PREFIX_TAGS = re.compile(r"^\[[^\]]+\]\s*|^\([^\)]+\)\s*")
_SUFFIX_TAGS = re.compile(r"(?i)\s*\[[^\]]+\]|\s*\([^\)]+\)|\s*-[A-Za-z0-9_]+$")

# 1. Standard S01E02 / S1E2 / S01.E02 / S01_E02 / S01E02E03 / S01E01-E02 / S01-E02
_EP_SXEX = re.compile(r"(?i)(?:^|[\b._\s-])S(\d{1,2})[ ._-]*E(\d{1,3})(?:[ ._x-]*(?:E|EP)?\d{1,3})*(?:$|[\b._\s-])")
# 2. 1x02 / 01x02 / 1x01-02
_EP_NXN = re.compile(r"(?i)(?:^|[\b._\s-])(\d{1,2})x(\d{1,3})(?:-\d{1,3})*(?:$|[\b._\s-])")
# 3. Season 1 Episode 2 / Temporada 1 Capitulo 2 / Saison 1 Episode 2 / Temp 1 Ep 2
_EP_WORD = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp)[ ._-]*(\d{1,2})[ ._/-]*(?:episode|episodio|capitulo|folge|ep\.?|ep|e)[ ._-]*(\d{1,3})\b")
# 4. T01E02 / T1E2
_EP_TXEX = re.compile(r"(?i)(?:^|[\b._\s-])T(\d{1,2})[ ._-]*E(\d{1,3})(?:$|[\b._\s-])")
# 5. Season 1 - 02 / Temporada 1 - 02
_EP_SEASON_DASH_EP = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp)[ ._-]*(\d{1,2})[ ._-]*[-_][ ._-]*(\d{1,3})\b")
# 6. Anime style: Title - 02 / Title - 002 (with optional group prefix)
_EP_ANIME_DASH = re.compile(r"^(?:\[[^\]]+\]\s*)?(.+?)\s+-\s+(\d{1,3})(?:v\d+)?(?:\s*\[|\s*\(|\s*\.|\s*$)")
# 7. Standalone Ep 02 / Episode 02 / Ep.02
_EP_STANDALONE = re.compile(r"(?i)\b(?:episode|episodio|capitulo|folge|ep\.?|ep)\s*(\d{1,3})\b")

_FOLDER_SEASON = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp|s)[ ._-]*(\d{1,2})\b")
_TORRENT_JOB_DIRECTORY = re.compile(
    r"(?i)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$|^[0-9a-f]{32}$"
)

# TTL cache for scan_media_library: prevents redundant filesystem scans when
# multiple API endpoints are called in rapid succession (downloads page loads,
# admin refresh, browse page). Cache is invalidated by library_updated SSE.
_SCAN_CACHE: dict[str, tuple[float, dict]] = {}
_SCAN_CACHE_TTL = 5.0  # seconds


def _clean_title(raw: str) -> str:
    cleaned = _PREFIX_TAGS.sub("", raw)
    rel_match = _RELEASE_TOKEN.search(cleaned)
    if rel_match:
        cleaned = cleaned[:rel_match.start()]
    cleaned = _SUFFIX_TAGS.sub("", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.sub(r"[\(\[\{\)\]\}]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return cleaned


def _show_folder(parts: tuple[str, ...], season_parent: str, season_match) -> str:
    """Find a human show folder, ignoring TorrentService's UUID job directory."""
    for folder in reversed(parts[:-2]):
        if _TORRENT_JOB_DIRECTORY.fullmatch(folder) or _FOLDER_SEASON.search(folder):
            continue
        title = _clean_title(folder)
        if title:
            return title
    # Layouts such as ``Show.S02/01.mkv`` keep both title and season in one folder.
    if season_match and season_match.start() > 0:
        return _clean_title(season_parent[:season_match.start()])
    return ""


def classify_video(filename: str, relative_path: str = "") -> dict:
    stem = Path(filename).stem
    year_match = _YEAR_REGEX.search(stem) or (_YEAR_REGEX.search(relative_path) if relative_path else None)
    year = int(year_match.group(1)) if year_match else None

    # 1. Direct SxEx (S01E02)
    m = _EP_SXEX.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": int(m.group(1)), "episode_number": int(m.group(2)), "year": year}

    # 2. Direct NxN (1x02)
    m = _EP_NXN.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": int(m.group(1)), "episode_number": int(m.group(2)), "year": year}

    # 3. Season X Episode Y
    m = _EP_WORD.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": int(m.group(1)), "episode_number": int(m.group(2)), "year": year}

    # 4. TxEx
    m = _EP_TXEX.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": int(m.group(1)), "episode_number": int(m.group(2)), "year": year}

    # 5. Season X - Y
    m = _EP_SEASON_DASH_EP.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": int(m.group(1)), "episode_number": int(m.group(2)), "year": year}

    # 6. Anime / Dash notation: Title - 05
    m = _EP_ANIME_DASH.search(stem)
    if m:
        raw_show = m.group(1)
        s_match = re.search(r"(?i)\b(?:season|temporada|s|t)\s*(\d{1,2})\b", raw_show)
        s_ord_match = re.search(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", raw_show)
        season_num = 1
        clean_show = raw_show
        if s_ord_match:
            season_num = int(s_ord_match.group(1))
            clean_show = raw_show[:s_ord_match.start()]
        elif s_match:
            season_num = int(s_match.group(1))
            clean_show = raw_show[:s_match.start()]
        title = _clean_title(clean_show) or clean_show.strip()
        return {"title": title, "media_type": "series", "season_number": season_num, "episode_number": int(m.group(2)), "year": year}

    # 7. Standalone Ep 02
    m = _EP_STANDALONE.search(stem)
    if m:
        title = _clean_title(stem[:m.start()]) or stem
        return {"title": title, "media_type": "series", "season_number": 1, "episode_number": int(m.group(1)), "year": year}

    # 8. Check folder context if provided
    if relative_path:
        parts = Path(relative_path).parts
        if len(parts) >= 2:
            parent = parts[-2]
            sm = _FOLDER_SEASON.search(parent)
            season_num = int(sm.group(1)) if sm else None
            num_match = re.search(r"^\s*(\d{1,3})\b", stem) or re.search(r"\b(?:ep\.?|e)?(\d{1,3})\b", stem)
            if season_num and num_match:
                show_title = _show_folder(parts, parent, sm)
                return {"title": show_title or stem, "media_type": "series", "season_number": season_num, "episode_number": int(num_match.group(1)), "year": year}

    # Fallback to movie
    title = _clean_title(stem) or stem
    return {
        "title": title,
        "media_type": "movie",
        "season_number": None,
        "episode_number": None,
        "year": year,
    }


def resolve_library_path(root: str | Path, relative_path: str) -> Path:
    base = Path(root).resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("Path is outside the media library") from exc
    return candidate


def _is_completed_torrent_job(path: Path, root: Path) -> bool:
    """Use StreamVault's job marker for its own UUID download directories.

    aria2 retains a season's .aria2 file when only selected episodes finished.
    TorrentService removes .streamvault-incomplete only after a successful
    transfer and removal of unselected files. This also recognizes downloads
    completed by older versions, without deleting their resume information.
    """
    try:
        job_name = path.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return False
    return bool(_TORRENT_JOB_DIRECTORY.fullmatch(job_name)) and not (
        root / job_name / INCOMPLETE_MARKER
    ).exists()


def is_incomplete_media(path: Path, root: str | Path) -> bool:
    """Distinguish completed episode selections from incomplete season packs."""
    base = Path(root).resolve()
    candidate = path.resolve()
    completed_job = _is_completed_torrent_job(candidate, base)
    for entry in (candidate, *candidate.parents):
        if entry == base:
            return (entry / INCOMPLETE_MARKER).exists()
        if (entry.is_dir() and (entry / INCOMPLETE_MARKER).exists()) or (
            not completed_job and Path(f"{entry}.aria2").exists()
        ):
            return True
    return True  # Outside the library.


def _invalidate_scan_cache(root: str | Path = None) -> None:
    """Clear the scan cache so the next call re-reads the disk.

    Called when files are deleted, downloaded, or the library_updated SSE fires.
    """
    if root is None:
        _SCAN_CACHE.clear()
    else:
        key = str(Path(root).resolve())
        _SCAN_CACHE.pop(key, None)


def recover_completed_downloads(root: str | Path) -> list[str]:
    """Expose transfers completed before StreamVault could remove its marker.

    This is intended for application startup, before any new download jobs are
    launched. An aria2 control file means the transfer is resumable/incomplete
    and must remain hidden.
    """
    base = Path(root)
    if not base.exists():
        return []

    recovered: list[str] = []
    try:
        # TorrentService always creates one direct child directory per job.
        # Avoid walking the whole media library during recovery: completed
        # libraries can contain hundreds of thousands of nested files.
        markers = list(base.glob(f"*/{INCOMPLETE_MARKER}"))
        root_marker = base / INCOMPLETE_MARKER
        if root_marker.is_file():
            markers.append(root_marker)
    except OSError:
        return []

    for marker in markers:
        directory = marker.parent
        try:
            if any(directory.rglob("*.aria2")):
                continue
            videos = sorted(
                path for path in directory.rglob("*")
                if path.is_file() and not path.is_symlink()
                and path.suffix.lower() in VIDEO_EXTENSIONS
                and path.stat().st_size > 0
            )
            if not videos:
                continue
            marker.unlink()
            recovered.extend(str(path) for path in videos)
        except OSError:
            continue

    if recovered:
        _invalidate_scan_cache(base)
    return recovered


def scan_media_library(root: str | Path) -> dict:
    base = Path(root)
    if not base.exists():
        return {"files": [], "movies": [], "series": []}

    # TTL cache: avoid redundant filesystem scans within a short window.
    cache_key = str(base.resolve())
    now = time.monotonic()
    cached = _SCAN_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _SCAN_CACHE_TTL:
        return deepcopy(cached[1])

    files = []
    for directory, folders, names in os.walk(base):
        parent = Path(directory)
        completed_job = _is_completed_torrent_job(parent, base)
        if (parent / INCOMPLETE_MARKER).exists() or (
            not completed_job and Path(f"{parent}.aria2").exists()
        ):
            folders[:] = []
            continue
        name_set = set(names)
        for name in names:
            path = parent / name
            if path.suffix.lower() not in VIDEO_EXTENSIONS or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size <= 0 or (not completed_job and f"{name}.aria2" in name_set):
                continue
            rel_path = path.relative_to(base).as_posix()
            files.append({
                "name": name,
                "relative_path": rel_path,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                **classify_video(name, relative_path=rel_path),
            })

    files.sort(key=lambda item: item["modified_at"], reverse=True)
    result = {
        "files": files,
        "movies": [item for item in files if item["media_type"] == "movie"],
        "series": [item for item in files if item["media_type"] == "series"],
    }
    _SCAN_CACHE[cache_key] = (now, result)
    return deepcopy(result)


def _normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _sidecar_subtitles(path: Path, root: Path) -> list[dict]:
    stem = path.stem.casefold()
    subtitles = []
    for candidate in path.parent.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        candidate_stem = candidate.stem.casefold()
        if not (candidate_stem.startswith(stem) or stem.startswith(candidate_stem)):
            continue
        suffix = candidate_stem[len(stem):].strip(" ._-")
        language = suffix.split(".")[-1] if suffix else "und"
        relative = candidate.relative_to(root).as_posix()
        subtitles.append({
            "language": language,
            "label": language.upper() if language != "und" else "Subtitles",
            "url": f"/api/downloads/files/subtitle?path={quote(relative)}",
        })
    return subtitles


def find_matching_media(
    root: str | Path,
    title: str,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
    year: int | None = None,
) -> dict | None:
    base = Path(root)
    target = _normalized_title(title)
    library = scan_media_library(base)
    group = "series" if media_type in {"tv", "series"} else "movies"
    candidates = []
    for item in library[group]:
        if _normalized_title(item["title"]) != target:
            continue
        if group == "series" and (
            item["season_number"] != season or item["episode_number"] != episode
        ):
            continue
        candidates.append(item)

    if not candidates:
        return None

    if group == "movies" and year is not None:
        # 1. Exact or near match on year (allow ±1 year for international release differences)
        year_match = next((item for item in candidates if item.get("year") is not None and abs(item["year"] - year) <= 1), None)
        if year_match:
            path = base / year_match["relative_path"]
            return {**year_match, "subtitles": _sidecar_subtitles(path, base)}

        # 2. Match untagged file (no year in filename) as fallback
        untagged_match = next((item for item in candidates if item.get("year") is None), None)
        if untagged_match:
            path = base / untagged_match["relative_path"]
            return {**untagged_match, "subtitles": _sidecar_subtitles(path, base)}

        # 3. All files on disk belong to different years (e.g. 2026 vs 2002) -> Do not match!
        return None

    selected = candidates[0]
    path = base / selected["relative_path"]
    return {**selected, "subtitles": _sidecar_subtitles(path, base)}


def srt_to_vtt(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return "WEBVTT\n\n" + re.sub(
        r"(?m)(\d{2}:\d{2}:\d{2}),(\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2 --> \3.\4",
        normalized,
    )
