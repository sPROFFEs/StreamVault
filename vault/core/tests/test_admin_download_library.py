from pathlib import Path
import os

from services.admin_download_library import scan_download_cleanup, scan_download_library
from jinja2 import Environment
import pytest


def test_download_inventory_is_searchable_typed_and_paginated(tmp_path: Path):
    movie = tmp_path / "Movie Folder" / "Movie.2026.mkv"
    movie.parent.mkdir()
    movie.write_bytes(b"x" * 2048)
    (movie.parent / "Movie.2026.en.srt").write_text("subtitle")
    (movie.parent / "Torrent Downloaded From Example.txt").write_text("credit")

    first = scan_download_library(tmp_path, page=1, page_size=2)

    assert first["total_files"] == 3
    assert first["total_bytes"] > 2048
    assert first["pages"] == 2
    assert len(first["items"]) == 2
    assert {item["kind"] for item in first["items"]} <= {"video", "subtitle", "metadata"}
    assert all(item["size_label"] for item in first["items"])
    assert all(item["folder"] == "Movie Folder" for item in first["items"])

    videos = scan_download_library(tmp_path, query="movie", kind="video")
    assert videos["total_filtered"] == 1
    assert videos["items"][0]["name"] == "Movie.2026.mkv"


def test_download_inventory_clamps_invalid_pages(tmp_path: Path):
    (tmp_path / "one.mp4").write_bytes(b"video")

    result = scan_download_library(tmp_path, page=99, page_size=20)

    assert result["page"] == 1
    assert result["pages"] == 1


def test_jinja_dictionary_items_key_does_not_resolve_to_dict_method():
    template = Environment().from_string('{% for item in downloads["items"] %}{{ item.name }}{% endfor %}')

    rendered = template.render(downloads={"items": [{"name": "movie.mkv"}]})

    assert rendered == "movie.mkv"


def test_file_browser_lists_only_current_folder_and_sorts_naturally(tmp_path):
    season = tmp_path / "Show" / "Season 1"
    season.mkdir(parents=True)
    for name in ("Episode 10.mkv", "Episode 2.mkv"):
        (season / name).write_bytes(b"video")
    (tmp_path / "Movie.mp4").write_bytes(b"movie")

    root = scan_download_library(tmp_path, directory="", sort="name", descending=False)
    assert [item["name"] for item in root["items"]] == ["Show", "Movie.mp4"]
    assert root["items"][0]["is_directory"] is True
    assert root["parent"] is None
    nested = scan_download_library(tmp_path, directory="Show/Season 1", sort="name", descending=False)
    assert [item["name"] for item in nested["items"]] == ["Episode 2.mkv", "Episode 10.mkv"]
    assert nested["parent"] == "Show"
    assert [crumb["path"] for crumb in nested["breadcrumbs"]] == ["", "Show", "Show/Season 1"]


def test_file_browser_search_is_scoped_and_pagination_preserves_folder(tmp_path):
    season = tmp_path / "Show" / "Season 1"
    season.mkdir(parents=True)
    (season / "Episode 1.mkv").write_bytes(b"video")
    (season / "Episode 2.mkv").write_bytes(b"video")
    (season / "Episode 1.srt").write_text("subtitle")
    (tmp_path / "Episode 3.mkv").write_bytes(b"unrelated")
    result = scan_download_library(tmp_path, directory="Show", query="Episode", kind="video", page_size=1, page=2)
    assert result["total_filtered"] == 2
    assert result["page"] == result["pages"] == 2
    assert result["directory"] == "Show"
    assert result["items"][0]["relative_path"].startswith("Show/Season 1/")


def test_file_browser_rejects_escape_and_does_not_follow_symlinks(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    for directory in ("../private", "link", str(outside)):
        with pytest.raises(ValueError):
            scan_download_library(root, directory=directory)
    assert scan_download_library(root, directory="", query="secret")["items"] == []


def test_file_browser_does_not_walk_nested_folders_for_normal_navigation(tmp_path, monkeypatch):
    (tmp_path / "Show").mkdir()
    def no_walk(*args, **kwargs):
        raise AssertionError("Navigation must not recursively scan the library")
    monkeypatch.setattr("os.walk", no_walk)
    assert scan_download_library(tmp_path, directory="")["items"][0]["name"] == "Show"


def test_download_cleanup_finds_orphan_artifacts_and_empty_folders_but_protects_active_jobs(tmp_path):
    stale = tmp_path / "old-job"
    stale.mkdir()
    (stale / "episode.mkv.aria2").write_text("control")
    marker = stale / ".streamvault-incomplete"
    marker.write_text("")
    os.utime(marker, (0, 0))
    # An active job may legitimately have transient files and must not be touched.
    active = tmp_path / "active-job"
    active.mkdir()
    (active / ".streamvault-incomplete").write_text("")
    (active / "episode.mkv.part").write_bytes(b"partial")
    empty = tmp_path / "abandoned" / "nested"
    empty.mkdir(parents=True)
    (tmp_path / "notes.txt").write_text("keep user metadata")

    result = scan_download_cleanup(tmp_path)
    paths = {item["relative_path"] for item in result["items"]}

    assert "old-job/episode.mkv.aria2" in paths
    assert "abandoned" in paths and "abandoned/nested" in paths
    assert "active-job/episode.mkv.part" not in paths
    assert "active-job/.streamvault-incomplete" not in paths
    assert "notes.txt" not in paths


def test_download_cleanup_never_follows_symlinked_directories(tmp_path):
    root = tmp_path / "downloads"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "orphan.part").write_text("partial")
    (root / "external").symlink_to(outside, target_is_directory=True)

    result = scan_download_cleanup(root)

    assert result["items"] == []


def test_download_cleanup_can_release_marker_from_job_that_is_no_longer_active(tmp_path):
    stale = tmp_path / "job-gone"
    stale.mkdir()
    marker = stale / ".streamvault-incomplete"
    marker.write_text("")
    (stale / "Movie.mkv").write_bytes(b"complete media")
    active = tmp_path / "job-live"
    active.mkdir()
    (active / ".streamvault-incomplete").write_text("")
    (active / "partial.part").write_bytes(b"partial")

    result = scan_download_cleanup(tmp_path, active_job_ids={"job-live"})
    paths = {item["relative_path"] for item in result["items"]}

    assert "job-gone/.streamvault-incomplete" in paths
    assert "job-gone/Movie.mkv" not in paths
    assert "job-live/partial.part" not in paths
