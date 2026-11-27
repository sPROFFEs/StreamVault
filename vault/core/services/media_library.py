"""Filesystem-backed local video library helpers."""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from stat import S_ISREG
from urllib.parse import quote

from services.media_identity import normalized_title as _normalized_title, release_year

logger = logging.getLogger(__name__)


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm", ".m4v", ".m2ts", ".mpg", ".mpeg"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
INCOMPLETE_MARKER = ".streamvault-incomplete"

_TRAILING_YEAR = re.compile(r"[ ._\-\[(]+([12]\d{3})[)\]]?\s*$")

_RELEASE_TOKEN = re.compile(
    r"(?i)\b(?:480|576|720|1080|2160)p\b|"
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
_EP_SXEX = re.compile(r"(?i)(?:^|[._\s-])[ST](\d{1,2})[ ._-]*E(\d{1,3})((?:[ ._]*E\d{1,3}|-(?:E)?\d{1,3})*)(?=$|[._\s-])")
# 2. 1x02 / 01x02 / 1x01-02
_EP_NXN = re.compile(r"(?i)(?:^|[._\s-])(\d{1,2})x(\d{1,3})((?:-\d{1,3})*)(?=$|[._\s-])")
# 3. Season 1 Episode 2 / Temporada 1 Capitulo 2 / Saison 1 Episode 2 / Temp 1 Ep 2
_EP_WORD = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp)[ ._-]*(\d{1,2})[ ._/-]*(?:episode|episodio|cap[ií]tulo|folge|ep\.?|ep|e)[ ._-]*(\d{1,3})\b")
# 5. Season 1 - 02 / Temporada 1 - 02
_EP_SEASON_DASH_EP = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp)[ ._-]*(\d{1,2})[ ._-]*[-_][ ._-]*(\d{1,3})\b")
# 6. Anime style: Title - 02 / Title - 002 (with optional group prefix)
_EP_ANIME_DASH = re.compile(r"^(?:\[[^\]]+\]\s*)?(.+?)\s+-\s+(\d{1,3})(?:v\d+)?(?:\s*\[|\s*\(|\s*\.|\s*$)")
# 7. Standalone Ep 02 / Episode 02 / Ep.02
_EP_STANDALONE = re.compile(r"(?i)\b(?:episode|episodio|cap[ií]tulo|folge|ep\.?|ep)\s*(\d{1,3})\b")

_FOLDER_SEASON = re.compile(r"(?i)\b(?:season|temporada|saison|staffel|temp\.?|temp|s)[ ._-]*(\d{1,2})\b")
_TORRENT_JOB_DIRECTORY = re.compile(
    r"(?i)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$|^[0-9a-f]{32}$"
)


def _title_year(raw: str) -> tuple[str, int | None]:
    raw = _PREFIX_TAGS.sub("", raw)
    tags = _RELEASE_TOKEN.search(raw.replace("_", " "))
    if tags:
        raw = raw[:tags.start()].rstrip(" ._-")
    raw = raw.rstrip()
    tagged_year = _TRAILING_YEAR.search(raw)
    year = release_year(tagged_year[1]) if tagged_year else None
    if tagged_year and year and raw[:tagged_year.start()].strip(" ._-"):
        raw = raw[:tagged_year.start()]
    else:
        year = None
    cleaned = _SUFFIX_TAGS.sub("", raw)
    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.sub(r"[\(\[\{\)\]\}]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -_"), year


def _clean_title(raw: str) -> str:
    return _title_year(raw)[0]


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
    year = None

    for pattern in (_EP_SXEX, _EP_NXN, _EP_WORD, _EP_SEASON_DASH_EP):
        match = pattern.search(stem)
        if not match:
            continue
        title, year = _title_year(stem[:match.start()])
        if not title:
            parts = Path(relative_path).parts
            if len(parts) >= 2:
                parent = parts[-2]
                season = _FOLDER_SEASON.search(parent)
                if season:
                    title = _show_folder(parts, parent, season)
                elif not _TORRENT_JOB_DIRECTORY.fullmatch(parent):
                    title = _clean_title(parent)
        return {"title": title or stem, "media_type": "series", "season_number": int(match[1]),
                "episode_number": int(match[2]), "year": year}

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
        title, year = _title_year(stem[:m.start()])
        parts = Path(relative_path).parts
        parent = parts[-2] if len(parts) >= 2 else ""
        season = _FOLDER_SEASON.search(parent)
        if season:
            title = title or _show_folder(parts, parent, season)
        return {"title": title or stem, "media_type": "series", "season_number": int(season[1]) if season else 1,
                "episode_number": int(m[1]), "year": year}

    # 8. Check folder context if provided
    if relative_path:
        parts = Path(relative_path).parts
        if len(parts) >= 2:
            parent = parts[-2]
            sm = _FOLDER_SEASON.search(parent)
            season_num = int(sm.group(1)) if sm else None
            num_match = re.match(r"(?i)^\s*(?:ep?\.?\s*)?(\d{1,3})(?=$|[ ._-])", stem)
            if season_num is not None and num_match:
                show_title = _show_folder(parts, parent, sm)
                return {"title": show_title or stem, "media_type": "series", "season_number": season_num, "episode_number": int(num_match.group(1)), "year": year}

    # Fallback to movie
    title, year = _title_year(stem)
    return {
        "title": title or stem,
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


def identify_video(filename: str, relative_path: str = "") -> dict:
    info = classify_video(filename, relative_path)
    stem = Path(filename).stem
    tags = re.sub(r"[._]+", " ", stem.casefold())
    explicit = _EP_SXEX.search(stem) or _EP_NXN.search(stem)
    episodes = [info["episode_number"]] if info["episode_number"] is not None else []
    if explicit:
        for separator, number in re.findall(r"(-?)\s*E?(\d{1,3})", explicit[3], re.I):
            end = int(number)
            if separator and end > episodes[-1]:
                episodes.extend(range(episodes[-1] + 1, end + 1))
            else:
                episodes.append(end)

    absolute = None
    anime = _EP_ANIME_DASH.search(stem)
    if anime and not explicit and int(anime[2]) >= 100 and not _FOLDER_SEASON.search(anime[1]):
        absolute = int(anime[2])
    tags_for_media = {
        "resolution": r"\b(?:480|576|720|1080|1440|2160|4320)p\b",
        "video_codec_hint": r"\b(?:x26[45]|h26[45]|hevc|avc|av1|vp9|xvid)\b",
        "audio_codec_hint": r"\b(?:aac|eac3|ac3|ddp|dts|truehd|opus|flac)\b",
    }
    for key, pattern in tags_for_media.items():
        match = re.search(pattern, tags)
        info[key] = match[0] if match else None
    edition = None
    for label, pattern in (
        ("director_cut", r"\bdirector'?s[ -]+cut\b"),
        ("extended", r"\bextended\b"), ("remastered", r"\bremaster(?:ed)?\b"),
        ("unrated", r"\bunrated\b"),
    ):
        if re.search(pattern, tags):
            edition = label
            break
    dolby = re.search(r"\b(?:dolby[ -]+vision|dovi|dv)\b", tags)
    hdr = re.search(r"\b(?:hdr10\+?|hdr|hlg)\b", tags)
    imdb = re.search(r"\btt\d{7,12}\b", tags)
    tmdb = re.search(r"\btmdb[ -]+(\d+)\b", tags)
    extra = any(part.casefold() in {"sample", "samples", "extras", "featurettes", "trailers", "bonus"}
                for part in Path(relative_path).parts[:-1]) or bool(
        re.search(r"(?:^|[._-])(?:sample|trailer|featurette|behind[._ -]+the[._ -]+scenes)(?:$|[._-])", stem, re.I)
    )
    opaque = not any(char.isalpha() for char in info["title"]) and (
        not re.fullmatch(r"[0-9]{4}", info["title"]) or info["title"].startswith("0")
    )
    opaque |= bool(re.fullmatch(r"[a-f0-9]{20,}", info["title"], re.I))
    opaque |= bool(explicit and explicit.start() == 0 and info["title"] == stem)
    status = "extra" if extra else "review" if opaque or absolute is not None else "parsed"
    info.update(filename_title=stem, original_title=None, localized_title=None,
                episode_numbers=sorted(set(episodes)), absolute_episode=absolute, edition=edition,
                dynamic_range="dolby_vision" if dolby else hdr[0] if hdr else None,
                language_hints=sorted(set(re.findall(
                    r"\b(?:eng|spa|ita|jpn|castellano|latino|french|german|dual|multi)\b", tags))),
                imdb_id=imdb[0] if imdb else None, tmdb_id=int(tmdb[1]) if tmdb else None,
                identification_status=status, identification_confidence=.9 if status == "parsed" else 0)
    return info


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


def sync_library_paths(root, paths):
    from services.storage_policy import invalidate_path_size
    from services.media_index import get_index

    invalidate_path_size(root)
    try:
        get_index(root).sync_paths(paths)
    except (OSError, sqlite3.Error):
        logger.warning("A scoped library update failed; periodic reconciliation will retry")


def sync_completed_job(root, job_id):
    """Support completion events from a downloader deployed before the index."""
    if not isinstance(job_id, str) or not _TORRENT_JOB_DIRECTORY.fullmatch(job_id):
        return
    directory = Path(root) / job_id
    if directory.is_symlink() or not directory.is_dir():
        return
    sync_library_paths(root, [path for path in directory.rglob("*") if path.suffix.lower() in VIDEO_EXTENSIONS])


def recover_completed_downloads(root: str | Path) -> list[str]:
    """Expose transfers completed before StreamVault could remove its marker.

    This is intended for application startup, before any new download jobs are
    launched. An aria2 control file means the transfer is resumable/incomplete
    and must remain hidden.
    """
    base = Path(root)
    if not base.exists():
        return []

    from services.storage_reservations import active_download_job_ids

    active_jobs = active_download_job_ids(base)
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
            if directory != base and directory.name in active_jobs:
                continue
            if directory.is_symlink() or marker.is_symlink():
                continue
            # Managed jobs publish only from the durable verified file list.
            # Empty markers from older releases retain the legacy recovery.
            if marker.stat().st_size:
                continue
            if any(next(directory.rglob(f"*{suffix}"), None) for suffix in (".aria2", ".part", ".ytdl")):
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
        sync_library_paths(base, recovered)
    return recovered


def iter_library_entries(root):
    """Enumerate without following links. A failed traversal retains the last snapshot."""
    def fail(error):
        raise error

    if not root.exists():
        return
    for directory, folders, names in os.walk(root, followlinks=False, onerror=fail):
        parent = Path(directory)
        completed_job = _is_completed_torrent_job(parent, root)
        folder_has_control = not completed_job and any(
            Path(f"{part}.aria2").exists() for part in (parent, *parent.parents) if part == root or part.is_relative_to(root)
        )
        incomplete = folder_has_control or any(
            (part / INCOMPLETE_MARKER).exists() for part in (parent, *parent.parents) if part == root or part.is_relative_to(root)
        )
        name_set = set(names)
        for name in names:
            path = parent / name
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                stat = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.st_size > 0 and S_ISREG(stat.st_mode):
                status = "incomplete" if incomplete or (not completed_job and f"{name}.aria2" in name_set) else None
                yield path.relative_to(root).as_posix(), stat, status


def scan_media_library(root: str | Path) -> dict:
    from services.media_index import get_index

    files = get_index(root).all()
    return {
        "files": files,
        "movies": [item for item in files if item["media_type"] == "movie"],
        "series": [item for item in files if item["media_type"] == "series"],
    }


def _sidecar_subtitles(path: Path, root: Path) -> list[dict]:
    stem = path.stem.casefold()
    subtitles = []
    for candidate in path.parent.iterdir():
        if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        candidate_stem = candidate.stem.casefold()
        if candidate_stem != stem and not any(candidate_stem.startswith(stem + separator) for separator in ". _-"):
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
    original_title: str | None = None,
    tmdb_id: int | None = None,
) -> dict | None:
    base = Path(root)
    from services.media_index import get_index

    group = "series" if media_type in {"tv", "series"} else "movie"
    entries = get_index(base).candidates((title, original_title), group, tmdb_id)
    candidates = []
    for item in entries:
        item_id = item.get("tmdb_id")
        if tmdb_id and item_id and tmdb_id != item_id:
            continue
        if group == "series" and (
            item["season_number"] != season or episode not in item["episode_numbers"]
        ):
            continue
        candidates.append(item)

    if not candidates:
        return None

    id_matches = [item for item in candidates if tmdb_id and item.get("tmdb_id") == tmdb_id]
    if id_matches:
        candidates = id_matches
    elif year is not None:
        for distance in (0, 1, None):
            matches = [item for item in candidates
                       if (abs(item["year"] - year) if item["year"] is not None else None) == distance]
            if matches:
                candidates = matches
                break
        else:
            return None
    if len({item["year"] for item in candidates if item["year"] is not None}) > 1 and not id_matches:
        return None

    for selected in candidates:
        path = base / selected["relative_path"]
        if path.is_file() and not path.is_symlink() and not is_incomplete_media(path, base):
            return {**selected, "episode_number": episode if group == "series" else None,
                    "subtitles": _sidecar_subtitles(path, base)}
    return None


def srt_to_vtt(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return "WEBVTT\n\n" + re.sub(
        r"(?m)(\d{2}:\d{2}:\d{2}),(\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2 --> \3.\4",
        normalized,
    )
