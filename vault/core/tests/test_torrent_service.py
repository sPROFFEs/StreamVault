from services.torrent_service import TorrentJob, TorrentService, build_aria2_command
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import services.torrent_service as torrents
from config import settings


def test_aria2_command_is_conservative_for_personal_server():
    command = build_aria2_command(
        magnet="magnet:?xt=urn:btih:test",
        downloads_dir="/tmp/downloads",
    )

    assert "--bt-max-peers=8" in command
    assert "--disk-cache=64M" in command
    assert "--file-allocation=falloc" in command
    assert "--bt-enable-lpd=false" in command
    assert f"--max-overall-download-limit={settings.DOWNLOAD_RATE_LIMIT // settings.CONCURRENT_DOWNLOADS}" in command
    assert command[-1] == "magnet:?xt=urn:btih:test"


def test_aria2_command_selects_requested_file_with_one_based_index():
    command = build_aria2_command(
        magnet="magnet:?xt=urn:btih:test",
        downloads_dir="/tmp/downloads",
        file_idx=0,
    )

    assert "--select-file=1" in command


def test_torrent_service_detects_same_active_source():
    service = TorrentService()
    job = TorrentJob("Movie", "magnet:?xt=urn:btih:abc", info_hash="abc")
    service._jobs[job.id] = job

    assert service.find_active_duplicate("magnet:?xt=urn:btih:abc", "abc") == job.id


def test_cancelled_torrents_are_not_returned_after_reload():
    service = TorrentService()
    job = TorrentJob("Movie", "magnet:?xt=urn:btih:abc")
    job.status = "cancelled"
    service._history.append(job)

    assert service.get_all_jobs() == []


def test_different_episodes_in_same_torrent_are_not_duplicates():
    service = TorrentService()
    job = TorrentJob("Episode 1", "magnet:?xt=urn:btih:abc", info_hash="abc", file_idx=0)
    service._jobs[job.id] = job
    assert service.find_active_duplicate(job.magnet, "ABC", 0) == job.id
    assert service.find_active_duplicate(job.magnet, "abc", 1) is None
    assert service.find_active_duplicate(job.magnet, "abc", None) is None


def test_cancelling_queued_torrent_never_launches_process(monkeypatch):
    async def scenario():
        service = TorrentService()
        monkeypatch.setattr(torrents, "_aria2c_available", lambda: True)
        launch = AsyncMock()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
        job_id = service.start_torrent_download("Episode", "magnet:?xt=urn:btih:abc")
        task = service._bg_tasks[job_id]
        assert service.cancel_job(job_id)
        await asyncio.gather(task, return_exceptions=True)
        launch.assert_not_called()
        assert service.get_job(job_id)["status"] == "cancelled"
    asyncio.run(scenario())


def test_torrent_completion_only_finds_its_own_nested_files(tmp_path, monkeypatch):
    monkeypatch.setattr(torrents, "DOWNLOADS_DIR", str(tmp_path))
    unrelated = tmp_path / "Unrelated.mp4"
    unrelated.write_bytes(b"unrelated")
    job = TorrentJob("Season", "magnet:?xt=urn:btih:abc")
    job.download_dir = str(tmp_path / job.id)
    season = Path(job.download_dir) / "Show.S01"
    season.mkdir(parents=True)
    for episode in (1, 2):
        (season / f"Show.S01E0{episode}.mkv").write_bytes(b"video")
    paths = TorrentService()._find_downloaded_files(job)
    assert len(paths) == 2
    assert str(unrelated) not in paths


def test_torrent_cannot_register_completion_without_video(tmp_path):
    job = TorrentJob("Episode", "magnet:?xt=urn:btih:abc")
    job.download_dir = str(tmp_path / job.id)
    directory = Path(job.download_dir)
    directory.mkdir()
    marker = directory / ".streamvault-incomplete"
    marker.touch()
    (directory / "readme.txt").write_text("not playable")

    try:
        TorrentService()._register_completed_download(job, marker)
    except RuntimeError as exc:
        assert "no playable video" in str(exc).lower()
    else:
        raise AssertionError("completion without a video must fail")
    assert marker.exists()


def test_carriage_return_progress_does_not_overflow_reader():
    async def scenario():
        reader = asyncio.StreamReader()
        line = b"[#abc123 10MiB/20MiB(50%) CN:4 SD:2 DL:1MiB ETA:10s]\r"
        reader.feed_data(line * 2000)
        reader.feed_eof()
        from types import SimpleNamespace
        job = TorrentJob("Episode", "magnet:?xt=urn:btih:abc")
        await TorrentService()._parse_output(job, SimpleNamespace(stdout=reader), [])
        assert job.progress == 50
        assert job.speed_bytes == 1024**2
    asyncio.run(scenario())
