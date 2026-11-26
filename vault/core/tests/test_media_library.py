import contextlib
import re
from pathlib import Path

try:
    import pytest
except ImportError:
    class _MockPytest:
        @contextlib.contextmanager
        def raises(self, exc_type, match=None):
            try:
                yield
            except exc_type as exc:
                if match and not re.search(match, str(exc)):
                    raise AssertionError(f"Pattern {match!r} not in {str(exc)!r}") from exc
            else:
                raise AssertionError(f"Expected {exc_type} but nothing was raised")
    pytest = _MockPytest()

from services.media_library import (
    classify_video,
    find_matching_media,
    is_incomplete_media,
    recover_completed_downloads,
    resolve_library_path,
    scan_media_library,
    srt_to_vtt,
)


def test_classify_video_detects_series_episode_tokens():
    result = classify_video("Severance.S02E03.1080p.mkv")

    assert result["media_type"] == "series"
    assert result["season_number"] == 2
    assert result["episode_number"] == 3
    assert result["title"] == "Severance"


def test_scan_media_library_recurses_into_torrent_folders(tmp_path: Path):
    movie = tmp_path / "The Substance.mp4"
    episode_dir = tmp_path / "Some.Show.S01"
    episode_dir.mkdir()
    episode = episode_dir / "Some.Show.S01E02.mkv"
    movie.write_bytes(b"movie")
    episode.write_bytes(b"episode")

    result = scan_media_library(tmp_path)

    assert [item["relative_path"] for item in result["movies"]] == ["The Substance.mp4"]
    assert [item["relative_path"] for item in result["series"]] == [
        "Some.Show.S01/Some.Show.S01E02.mkv"
    ]


def test_resolve_library_path_blocks_escape(tmp_path: Path):
    with pytest.raises(ValueError, match="outside"):
        resolve_library_path(tmp_path, "../secret.mkv")


def test_scan_media_library_hides_incomplete_torrent_files(tmp_path: Path):
    empty = tmp_path / "empty.mp4"
    partial = tmp_path / "partial.mkv"
    empty.write_bytes(b"")
    partial.write_bytes(b"partial")
    (tmp_path / "partial.mkv.aria2").write_text("control")

    assert scan_media_library(tmp_path)["files"] == []


def test_find_matching_movie_in_download_library(tmp_path: Path):
    movie = tmp_path / "The.Substance.2024.1080p.mkv"
    movie.write_bytes(b"movie")
    (tmp_path / "The.Substance.2024.1080p.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")

    match = find_matching_media(tmp_path, "The Substance", "movie")

    assert match["relative_path"] == movie.name
    assert match["subtitles"][0]["language"] == "en"


def test_find_matching_series_requires_episode(tmp_path: Path):
    first = tmp_path / "Severance.S02E01.mkv"
    second = tmp_path / "Severance.S02E02.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    match = find_matching_media(tmp_path, "Severance", "series", season=2, episode=2)

    assert match["relative_path"] == second.name


def test_srt_subtitles_are_converted_to_browser_vtt():
    converted = srt_to_vtt("1\n00:00:01,250 --> 00:00:02,500\nHello\n")

    assert converted.startswith("WEBVTT\n\n")
    assert "00:00:01.250 --> 00:00:02.500" in converted


def test_scan_hides_incomplete_season_with_directory_control_file(tmp_path):
    season = tmp_path / "Show.S01"
    season.mkdir()
    (season / "Show.S01E01.mkv").write_bytes(b"preallocated, incomplete")
    (tmp_path / "Show.S01.aria2").write_bytes(b"control")
    assert scan_media_library(tmp_path)["files"] == []


def test_scan_hides_cancelled_or_running_job_directory(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / ".streamvault-incomplete").touch()
    (job / "Show.S01E01.mkv").write_bytes(b"preallocated, incomplete")
    assert scan_media_library(tmp_path)["files"] == []


def test_completed_managed_episode_remains_visible_with_season_control_file(tmp_path):
    job = tmp_path / "3c79b5ee-80fe-4bc3-ad87-5ef97e1e301c"
    season = job / "Mr.Robot.SEASON.01.S01.COMPLETE.1080p"
    season.mkdir(parents=True)
    video = season / "Mr.Robot.S01E01.eps1.0_hellofriend.mov.1080p.mkv"
    video.write_bytes(b"complete selected episode")
    control = job / f"{season.name}.aria2"
    control.write_bytes(b"other torrent episodes were not selected")

    library = scan_media_library(tmp_path)

    assert [item["name"] for item in library["series"]] == [video.name]
    assert find_matching_media(tmp_path, "Mr. Robot", "tv", season=1, episode=1) is not None
    assert not is_incomplete_media(video, tmp_path)
    assert control.exists()  # Keep aria2's resume information intact.


def test_managed_episode_stays_hidden_until_job_finishes(tmp_path):
    job = tmp_path / "3c79b5ee-80fe-4bc3-ad87-5ef97e1e301c"
    season = job / "Show.S01"
    season.mkdir(parents=True)
    video = season / "Show.S01E01.mkv"
    video.write_bytes(b"incomplete episode")
    (job / ".streamvault-incomplete").touch()
    (job / "Show.S01.aria2").touch()

    assert scan_media_library(tmp_path)["files"] == []
    assert is_incomplete_media(video, tmp_path)


def test_unmanaged_season_control_file_still_blocks_playback(tmp_path):
    season = tmp_path / "Show.S01"
    season.mkdir()
    video = season / "Show.S01E01.mkv"
    video.write_bytes(b"partial episode")
    (tmp_path / "Show.S01.aria2").touch()

    assert scan_media_library(tmp_path)["files"] == []
    assert is_incomplete_media(video, tmp_path)


def test_recover_completed_download_removes_stale_marker(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    marker = job / ".streamvault-incomplete"
    marker.touch()
    video = job / "Show.S01E01.mkv"
    video.write_bytes(b"complete video")

    recovered = recover_completed_downloads(tmp_path)

    assert recovered == [str(video)]
    assert not marker.exists()
    assert scan_media_library(tmp_path)["files"][0]["relative_path"] == "job/Show.S01E01.mkv"


def test_recovery_keeps_marker_while_aria2_control_file_exists(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    marker = job / ".streamvault-incomplete"
    marker.touch()
    (job / "Show.S01E01.mkv").write_bytes(b"partial video")
    (job / "Show.S01E01.mkv.aria2").write_bytes(b"control")

    assert recover_completed_downloads(tmp_path) == []
    assert marker.exists()


def test_scan_cache_is_not_mutated_by_callers(tmp_path):
    (tmp_path / "Movie.mp4").write_bytes(b"video")
    result = scan_media_library(tmp_path)
    result["movies"].clear()
    result["files"][0]["title"] = "changed"
    fresh = scan_media_library(tmp_path)
    assert len(fresh["movies"]) == 1
    assert fresh["files"][0]["title"] == "Movie"


def test_classify_video_accepts_underscore_episode_names():
    result = classify_video("Some_Show_S01E02_1080p.mkv")
    assert result["title"] == "Some Show"
    assert result["episode_number"] == 2


def test_classify_video_extended_patterns():
    r1 = classify_video("Breaking.Bad.1x03.720p.mkv")
    assert r1["title"] == "Breaking Bad"
    assert r1["season_number"] == 1 and r1["episode_number"] == 3

    r2 = classify_video("Attack on Titan Season 4 Episode 15.mp4")
    assert r2["title"] == "Attack on Titan"
    assert r2["season_number"] == 4 and r2["episode_number"] == 15

    r3 = classify_video("La Casa de Papel Temporada 2 Capitulo 5.mkv")
    assert r3["title"] == "La Casa de Papel"
    assert r3["season_number"] == 2 and r3["episode_number"] == 5

    r4 = classify_video("[SubsPlease] Frieren - 05 [1080p].mkv")
    assert r4["title"] == "Frieren"
    assert r4["season_number"] == 1 and r4["episode_number"] == 5

    r5 = classify_video("[Erai-raws] Jujutsu Kaisen 2nd Season - 14 [1080p][HEVC].mkv")
    assert r5["title"] == "Jujutsu Kaisen"
    assert r5["season_number"] == 2 and r5["episode_number"] == 14

    r6 = classify_video("04.mkv", relative_path="Game of Thrones/Season 03/04.mkv")
    assert r6["title"] == "Game of Thrones"
    assert r6["season_number"] == 3 and r6["episode_number"] == 4

    r7 = classify_video("The.Substance.2024.1080p.mkv")
    assert r7["title"] == "The Substance"
    assert r7["media_type"] == "movie"


def test_classify_video_uses_show_folder_below_torrent_job_directory():
    result = classify_video(
        "01.mkv",
        relative_path="7987d205-0d7e-46e8-bf68-2e2fc3c32570/Severance/Season 02/01.mkv",
    )

    assert result == {
        "title": "Severance",
        "media_type": "series",
        "season_number": 2,
        "episode_number": 1,
        "year": None,
    }


def test_find_matching_movie_distinguishes_by_year(tmp_path: Path):
    movie_2002 = tmp_path / "Resident.Evil.2002.1080p.mkv"
    movie_2021 = tmp_path / "Resident.Evil.2021.1080p.mkv"
    movie_2002.write_bytes(b"re2002")
    movie_2021.write_bytes(b"re2021")

    match_2002 = find_matching_media(tmp_path, "Resident Evil", "movie", year=2002)
    match_2021 = find_matching_media(tmp_path, "Resident Evil", "movie", year=2021)
    match_1990 = find_matching_media(tmp_path, "Resident Evil", "movie", year=1990)

    assert match_2002 is not None and match_2002["relative_path"] == movie_2002.name
    assert match_2021 is not None and match_2021["relative_path"] == movie_2021.name
    assert match_2002["year"] == 2002
    assert match_2021["year"] == 2021
    # Movie from 1990 does not exist on disk -> must not falsely match 2002 or 2021
    assert match_1990 is None


def test_scan_groups_numeric_whole_season_under_the_show_not_the_job_id(tmp_path):
    episodes = tmp_path / "7987d205-0d7e-46e8-bf68-2e2fc3c32570" / "The Bear" / "Season 03"
    episodes.mkdir(parents=True)
    for number in (1, 2, 3):
        (episodes / f"{number:02}.mkv").write_bytes(b"episode")

    series = scan_media_library(tmp_path)["series"]

    assert [(item["title"], item["season_number"], item["episode_number"])
            for item in series] == [("The Bear", 3, 3), ("The Bear", 3, 2), ("The Bear", 3, 1)]


def test_identification_preserves_numeric_titles_and_handles_specials_and_ranges(tmp_path):
    from services.media_library import identify_video

    assert classify_video("1917.2019.1080p.mkv")["title"] == "1917"
    assert classify_video("2001.Una.odisea.del.espacio.1968.mkv")["title"] == "2001 Una odisea del espacio"
    info = identify_video("01.mkv", "Águila/Temporada 00/01.mkv")
    assert (info["title"], info["season_number"], info["episode_numbers"]) == ("Águila", 0, [1])
    assert classify_video("S00E02.mkv", "Águila/Temporada 00/S00E02.mkv")["title"] == "Águila"
    info = identify_video("Águila.T01E02-E04.2160p.Dolby.Vision.Directors.Cut.mkv")
    assert info["episode_numbers"] == [2, 3, 4]
    assert info["resolution"] == "2160p"
    assert info["dynamic_range"] == "dolby_vision"
    assert info["edition"] == "director_cut"
    (tmp_path / "Águila.T01E02-E04.mkv").write_bytes(b"episodes")
    assert find_matching_media(tmp_path, "Aguila", "tv", season=1, episode=3)["episode_number"] == 3


def test_local_match_leaves_remakes_ambiguous_and_hides_extras(tmp_path):
    for name in ("Memories.2022.mkv", "Memories.2023.mkv", "Memories.2023.sample.mkv"):
        (tmp_path / name).write_bytes(b"media")

    assert find_matching_media(tmp_path, "Memories", "movie") is None
    assert find_matching_media(tmp_path, "Memories", "movie", year=2023)["name"] == "Memories.2023.mkv"
    assert len(scan_media_library(tmp_path)["movies"]) == 2


def test_opaque_names_and_absolute_episodes_do_not_guess_the_media_identity():
    from services.media_library import identify_video

    assert identify_video("0001.mp4")["identification_status"] == "review"
    number = identify_video("[Group] 作品 - 123 [1080p].mkv")
    assert number["absolute_episode"] == 123
    assert number["identification_status"] == "review"


def test_persistent_index_reopens_without_walking_and_updates_only_changed_files(tmp_path, monkeypatch):
    from services import media_library
    from services.media_index import LibraryIndex

    root = tmp_path / "downloads"
    root.mkdir()
    (root / "A.mkv").write_bytes(b"first")
    (root / "B.mp4").write_bytes(b"second")
    index = LibraryIndex(root, tmp_path / "index.sqlite3")
    index.reconcile()

    classify = media_library.identify_video
    calls = []
    def record(name, relative_path):
        calls.append(name)
        return classify(name, relative_path)
    monkeypatch.setattr(media_library, "identify_video", record)
    reopened = LibraryIndex(root, index.path)
    assert reopened.page("A", "movie", "title", 1, 10)[3] == 1
    assert calls == []
    (root / "A.mkv").write_bytes(b"changed size")
    (root / "B.mp4").unlink()
    reopened.reconcile()
    assert calls == ["A.mkv"]
    assert reopened.page("", "movie", "title", 1, 10)[3] == 1


def test_manual_identification_survives_reconcile_and_matches_original_title(tmp_path):
    from services.media_index import LibraryIndex

    root = tmp_path / "downloads"
    root.mkdir()
    (root / "0001.mkv").write_bytes(b"series")
    index = LibraryIndex(root, tmp_path / "index.sqlite3")
    index.set_override("0001.mkv", {"title": "Recuérdame", "original_title": "Remember Me",
                     "tmdb_id": 10, "media_type": "series", "season_number": 0, "episode_numbers": [1, 2]})
    index.reconcile()
    reopened = LibraryIndex(root, index.path)
    entry = reopened.candidates(["Remember Me"], "series", None)[0]
    assert entry["episode_numbers"] == [1, 2]
    assert entry["identification_status"] == "manual"
    assert reopened.candidates([], "series", 10) == [entry]
    reopened.set_override("0001.mkv", None)
    assert reopened.candidates(["Remember Me"], "series", None) == []


def test_library_index_paginates_grouped_shows_without_splitting_episodes(tmp_path):
    from services.media_index import LibraryIndex

    root = tmp_path / "downloads"
    root.mkdir()
    for name in ("A.S01E01.mkv", "A.S01E02.mkv", "B.S02E01.mkv", "C.2025.mkv"):
        (root / name).write_bytes(b"media")
    index = LibraryIndex(root, tmp_path / "index.sqlite3")
    index.reconcile()
    items, page, pages, total = index.page("", "all", "title", 1, 1)
    assert (page, pages, total) == (1, 3, 4)
    assert {item["episode_number"] for item in items} == {1, 2}


def test_startup_recovery_preserves_a_job_with_a_live_disk_reservation(tmp_path, monkeypatch):
    from config import settings
    from services.storage_reservations import reserve_space

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path / "data"))
    root = tmp_path / "downloads"
    job_id = "7987d205-0d7e-46e8-bf68-2e2fc3c32570"
    with reserve_space(root, "downloads", 1024, job_id=job_id):
        job = root / job_id
        job.mkdir(parents=True)
        marker = job / ".streamvault-incomplete"
        marker.touch()
        (job / "Video.mkv").write_bytes(b"in progress")
        assert recover_completed_downloads(root) == []
        assert marker.exists()
