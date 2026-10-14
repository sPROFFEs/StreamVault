import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import downloads


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
    monkeypatch.setattr(downloads.settings, "MEDIA_STORAGE_PATH", str(tmp_path / "cache"))
    app = FastAPI()
    app.include_router(downloads.router)
    app.dependency_overrides[downloads.get_current_user] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as test_client:
        yield test_client


def test_local_files_support_byte_ranges(client, tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"0123456789")
    response = client.get("/api/downloads/files/stream?path=video.mp4", headers={"Range": "bytes=3-6"})
    assert response.status_code == 206
    assert response.content == b"3456"
    assert response.headers["content-range"] == "bytes 3-6/10"


def test_partial_season_cannot_be_streamed_directly(client, tmp_path):
    folder = tmp_path / "season"
    folder.mkdir()
    (folder / "episode.mkv").write_bytes(b"partial")
    (tmp_path / "season.aria2").touch()
    assert client.get("/api/downloads/files/stream?path=season/episode.mkv").status_code == 409


def test_completed_selected_episode_is_listed_matched_and_streamable(client, tmp_path, monkeypatch):
    job = tmp_path / "3c79b5ee-80fe-4bc3-ad87-5ef97e1e301c"
    season = job / "Mr.Robot.S01"
    season.mkdir(parents=True)
    video = season / "Mr.Robot.S01E01.mkv"
    video.write_bytes(b"complete episode")
    (job / "Mr.Robot.S01.aria2").touch()
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
        "audio": [], "subtitles": [], "duration_seconds": 10,
    })

    library = client.get("/api/downloads/library").json()
    assert [item["name"] for item in library["series"]] == [video.name]
    response = client.get("/api/downloads/files/match", params={
        "title": "Mr. Robot", "media_type": "tv", "season": 1, "episode": 1,
    })
    match = response.json()["match"]
    assert match["relative_path"] == video.relative_to(tmp_path).as_posix()
    playback = client.get(match["url"])
    assert playback.status_code == 200
    assert playback.content == b"complete episode"


def test_library_pagination_has_matching_files_and_groups(client, tmp_path):
    for name in ("Movie.mp4", "Show.S01E01.mkv", "Show.S01E02.mkv"):
        (tmp_path / name).write_bytes(b"video")
    for page in range(1, 4):
        result = client.get(f"/api/downloads/files?page={page}&page_size=1").json()
        assert result["files"] == result["movies"] + result["series"]


def test_library_page_keeps_an_entire_series_together():
    movie = {"media_type": "movie", "relative_path": "Movie.mp4"}
    episodes = [
        {
            "media_type": "series",
            "relative_path": f"Show.S01E{episode:02}.mkv",
            "title": "Show",
            "episode_number": episode,
        }
        for episode in range(1, 71)
    ]

    first_page, page, pages = downloads._paginate_library_items([movie, *episodes], 1, 1)

    assert page == 1
    assert pages == 2
    assert first_page == [movie]
    second_page, _, _ = downloads._paginate_library_items([movie, *episodes], 2, 1)
    assert {item["episode_number"] for item in second_page} == set(range(1, 71))


def test_library_search_finds_numeric_episodes_by_show_title():
    episode = {
        "media_type": "series",
        "name": "01.mkv",
        "relative_path": "The Bear/Season 03/01.mkv",
        "title": "The Bear",
    }

    assert downloads._filter_library_items([episode], "the bear") == [episode]


def test_library_alias_preserves_search_and_pagination_without_redirect(client, tmp_path):
    for name in ("Movie.mp4", "Show.S01E01.mkv", "Show.S01E02.mkv"):
        (tmp_path / name).write_bytes(b"video")
    query = "?q=Show&page=2&page_size=1"
    response = client.get("/api/downloads/library" + query, follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == client.get("/api/downloads/files" + query).json()


def test_library_filters_before_paginating_and_keeps_series_together(client, tmp_path):
    for name in ("Movie.mp4", "Alpha.S01E01.mkv", "Alpha.S01E02.mkv", "Beta.S02E01.mkv"):
        (tmp_path / name).write_bytes(b"video")

    result = client.get("/api/downloads/library", params={
        "media_type": "series", "sort": "title", "page_size": 1, "page": 2,
    }).json()
    assert result["total"] == 3
    assert result["pages"] == 2
    assert [item["title"] for item in result["files"]] == ["Beta"]

    result = client.get("/api/downloads/library", params={
        "media_type": "series", "q": "alpha", "sort": "title", "page_size": 1,
    }).json()
    assert result["pages"] == 1
    assert result["total"] == 2
    assert {item["episode_number"] for item in result["series"]} == {1, 2}


@pytest.mark.parametrize("sort,expected", [
    ("title", ["Alpha", "Beta", "Zeta"]),
    ("recent", ["Beta", "Zeta", "Alpha"]),
    ("size", ["Zeta", "Alpha", "Beta"]),
])
def test_library_sorts_logical_entries_before_pagination(client, monkeypatch, sort, expected):
    files = [
        {"title": "Zeta", "name": "Zeta.mkv", "media_type": "movie", "relative_path": "Zeta.mkv", "modified_at": 2, "size_bytes": 20},
        {"title": "Alpha", "name": "Alpha.S01E01.mkv", "media_type": "series", "relative_path": "a1.mkv", "modified_at": 1, "size_bytes": 6},
        {"title": "Alpha", "name": "Alpha.S01E02.mkv", "media_type": "series", "relative_path": "a2.mkv", "modified_at": 1, "size_bytes": 6},
        {"title": "Beta", "name": "Beta.mkv", "media_type": "movie", "relative_path": "Beta.mkv", "modified_at": 3, "size_bytes": 10},
    ]
    monkeypatch.setattr(downloads, "scan_media_library", lambda _: {"files": files})
    titles = []
    for page in range(1, 4):
        result = client.get("/api/downloads/library", params={"sort": sort, "page": page, "page_size": 1}).json()
        titles.append(result["files"][0]["title"])
    assert titles == expected


@pytest.mark.parametrize("params", [{"media_type": "invalid"}, {"sort": "invalid"}])
def test_library_rejects_unknown_filters(client, params):
    assert client.get("/api/downloads/library", params=params).status_code == 422


def test_library_alias_deletes_file_without_redirect(client, tmp_path):
    video = tmp_path / "Movie.mp4"
    video.write_bytes(b"video")
    client.app.dependency_overrides[downloads.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    response = client.delete("/api/downloads/library?path=Movie.mp4", follow_redirects=False)
    assert response.status_code == 200
    assert not video.exists()


def test_playback_uses_direct_compatible_video_without_ffmpeg(client, tmp_path, monkeypatch):
    (tmp_path / "video.mp4").write_bytes(b"video")
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
        "audio": [{"stream_index": 1, "codec": "aac"}], "video_codec": "h264",
        "subtitles": [], "duration_seconds": 120,
    })
    response = client.get("/api/downloads/files/playback?path=video.mp4&start=30")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "file"
    assert "/files/stream?" in data["url"]
    assert data["offset"] == 0


def test_playback_returns_explicit_offset_for_uncached_remux(client, tmp_path, monkeypatch):
    (tmp_path / "episode.mkv").write_bytes(b"video")
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
        "audio": [{"stream_index": 1, "codec": "ac3"}], "video_codec": "h264",
        "subtitles": [], "duration_seconds": 120,
    })
    monkeypatch.setattr(downloads.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    result = client.get("/api/downloads/files/playback?path=episode.mkv&start=30").json()
    assert result["mode"] == "stream"
    assert result["offset"] == 30
    assert "start=30" in result["url"]
    assert "force_stream=true" in result["url"]


def test_match_can_select_exact_release_path(client, tmp_path, monkeypatch):
    (tmp_path / "Movie.720p.mp4").write_bytes(b"first")
    (tmp_path / "Movie.1080p.mp4").write_bytes(b"second")
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
        "audio": [], "subtitles": [], "duration_seconds": 10,
    })
    response = client.get("/api/downloads/files/match?title=Movie&path=Movie.720p.mp4")
    assert response.json()["match"]["relative_path"] == "Movie.720p.mp4"


def test_match_keeps_nested_numeric_episode_classification(client, tmp_path, monkeypatch):
    episode = tmp_path / "7987d205-0d7e-46e8-bf68-2e2fc3c32570" / "Severance" / "Season 02"
    episode.mkdir(parents=True)
    (episode / "01.mkv").write_bytes(b"episode")
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
        "audio": [], "subtitles": [], "duration_seconds": 10,
    })

    response = client.get(
        "/api/downloads/files/match?title=Severance&path=7987d205-0d7e-46e8-bf68-2e2fc3c32570/Severance/Season%2002/01.mkv"
    )

    assert response.status_code == 200
    match = response.json()["match"]
    assert match["title"] == "Severance"
    assert match["media_type"] == "series"
    assert match["season_number"] == 2
    assert match["episode_number"] == 1


def test_media_probe_is_cached_by_file_identity(tmp_path, monkeypatch):
    file = tmp_path / "video.mkv"
    file.write_bytes(b"video")
    from unittest.mock import Mock
    probe = Mock(return_value=SimpleNamespace(stdout='{"streams": [], "format": {"duration": "10"}}'))
    monkeypatch.setattr(downloads.shutil, "which", lambda _: "ffprobe")
    monkeypatch.setattr(downloads.subprocess, "run", probe)
    downloads._probe_media_tracks(file)
    downloads._probe_media_tracks(file)
    assert probe.call_count == 1
    file.write_bytes(b"changed video")
    downloads._probe_media_tracks(file)
    assert probe.call_count == 2


def test_cached_remux_does_not_change_a_pinned_stream_offset(tmp_path, monkeypatch):
    async def scenario():
        media = tmp_path / "episode.mkv"
        media.write_bytes(b"video")
        monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
        monkeypatch.setattr(downloads.settings, "MEDIA_STORAGE_PATH", str(tmp_path / "cache"))
        monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {
            "audio": [{"stream_index": 1, "codec": "aac"}], "video_codec": "h264",
        })
        monkeypatch.setattr(downloads.shutil, "which", lambda _: "ffmpeg")
        cached = downloads._remux_cache_path(media, "0:1")
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"cached full file")
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(downloads, "_live_remux_slots", slots)
        process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(read=AsyncMock(side_effect=[b"fragment", b""])), wait=AsyncMock(), communicate=AsyncMock())
        launch = AsyncMock(return_value=process)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
        response = await downloads.remux_downloaded_media("episode.mkv", audio=1, start=30, force_stream=True)
        assert b"".join([part async for part in response.body_iterator]) == b"fragment"
        args = launch.call_args.args
        assert "-ss" in args
        assert "-threads" in args
        assert not slots.locked()
    asyncio.run(scenario())


def test_disconnect_before_first_body_chunk_releases_remux_slot(tmp_path, monkeypatch):
    async def scenario():
        media = tmp_path / "video.mkv"
        media.write_bytes(b"video")
        monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
        monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {"audio": []})
        monkeypatch.setattr(downloads.shutil, "which", lambda _: "ffmpeg")
        monkeypatch.setattr(downloads, "_ensure_background_remux", lambda *_: None)
        slots = asyncio.Semaphore(1)
        monkeypatch.setattr(downloads, "_live_remux_slots", slots)
        response = await downloads.remux_downloaded_media("video.mkv", start=0)

        async def send(message):
            raise OSError("Client disconnected before response headers")

        async def receive():
            return {"type": "http.disconnect"}

        try:
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        except Exception as exc:
            from starlette.requests import ClientDisconnect
            assert isinstance(exc, (OSError, ClientDisconnect))
        assert not slots.locked()
    asyncio.run(scenario())


def test_live_playback_defers_cache_encoding_until_stream_closes(tmp_path, monkeypatch):
    async def scenario():
        media = tmp_path / "video.mkv"
        media.write_bytes(b"video")
        monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
        monkeypatch.setattr(downloads.settings, "MEDIA_STORAGE_PATH", str(tmp_path / "cache"))
        monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {"audio": [], "video_codec": "hevc"})
        monkeypatch.setattr(downloads.shutil, "which", lambda _: "ffmpeg")
        monkeypatch.setattr(downloads, "_live_remux_slots", asyncio.Semaphore(1))
        schedule_cache = Mock()
        monkeypatch.setattr(downloads, "_ensure_background_remux", schedule_cache)
        process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(
            read=AsyncMock(side_effect=[b"fragment", b""])), wait=AsyncMock(), communicate=AsyncMock())
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        response = await downloads.remux_downloaded_media("video.mkv", start=0)
        schedule_cache.assert_not_called()
        assert b"".join([part async for part in response.body_iterator]) == b"fragment"
        schedule_cache.assert_called_once()
    asyncio.run(scenario())


def test_new_playback_stops_existing_background_encoder(tmp_path, monkeypatch):
    async def scenario():
        (tmp_path / "video.mkv").write_bytes(b"video")
        monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
        monkeypatch.setattr(downloads, "_probe_media_tracks", lambda _: {"audio": []})
        monkeypatch.setattr(downloads.shutil, "which", lambda _: "ffmpeg")
        monkeypatch.setattr(downloads, "_live_remux_slots", asyncio.Semaphore(1))
        monkeypatch.setattr(downloads, "_ensure_background_remux", lambda *_: None)
        stopped = asyncio.Event()

        async def encode():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        task = asyncio.create_task(encode())
        monkeypatch.setattr(downloads, "_background_remux_tasks", {"old": task})
        await asyncio.sleep(0)
        response = await downloads.remux_downloaded_media("video.mkv", start=0)
        try:
            assert stopped.is_set(), "Background encoding must not compete with playback"
        finally:
            await response.aclose()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
