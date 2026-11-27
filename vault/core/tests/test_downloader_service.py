from services.download_client import DownloaderClient
from services.download_service import DownloadService
from services.torrent_service import TorrentService, TorrentJob
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import settings
from services import media_library


def test_ytdlp_download_owns_a_marked_directory_until_completion(tmp_path, monkeypatch):
    import services.download_service as module
    monkeypatch.setattr(module, 'DOWNLOADS_DIR', str(tmp_path))
    service = DownloadService()
    async def transfer(job, reservation):
        from pathlib import Path
        directory = Path(job.download_dir)
        assert directory.parent == tmp_path
        assert (directory / media_library.INCOMPLETE_MARKER).exists()
        video = directory / 'Movie.mp4'
        video.write_bytes(b'completed')
    async def verify(job):
        from services.download_queue import downloaded_videos, verified_paths
        return verified_paths(job.download_dir, downloaded_videos(job))
    monkeypatch.setattr(service, '_transfer', transfer)
    monkeypatch.setattr(service, '_verify', verify)
    async def scenario():
        job_id = service.start_download('https://video.example/watch', 'Movie')
        await service._bg_tasks[job_id]
        assert service.get_job(job_id)['status'] == 'completed'
        assert not (tmp_path / job_id / media_library.INCOMPLETE_MARKER).exists()
        assert (tmp_path / job_id / 'Movie.mp4').exists()
    asyncio.run(scenario())


def test_download_worker_rechecks_disk_before_writing_queued_job(tmp_path, monkeypatch):
    import services.torrent_service as module
    from services import storage_policy
    monkeypatch.setattr(module, 'DOWNLOADS_DIR', str(tmp_path))
    monkeypatch.setattr(module, '_aria2c_available', lambda: True)
    service = TorrentService()
    launch = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    monkeypatch.setattr(storage_policy, 'disk_free_bytes', lambda _: 0)
    async def scenario():
        job_id = service.start_torrent_download('Movie', 'magnet:?xt=urn:btih:abc')
        await service._bg_tasks[job_id]
        assert service.get_job(job_id)['status'] == 'error'
        assert 'disk' in service.get_job(job_id)['error']
        launch.assert_not_called()
    asyncio.run(scenario())


def test_downloader_client_local_fallback():
    client = DownloaderClient(downloader_url="")
    assert not client.is_remote
    jobs = asyncio.run(client.get_jobs())
    assert "yt_dlp" in jobs and "torrent" in jobs


def test_downloader_client_remote_delegation():
    client = DownloaderClient(downloader_url="http://downloader:8001")
    assert client.is_remote


def test_remote_completion_refreshes_core_library_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DOWNLOADS_ROOT", str(tmp_path))
    job_id = "3c79b5ee-80fe-4bc3-ad87-5ef97e1e301c"
    job = tmp_path / job_id
    season = job / "Show.S01"
    season.mkdir(parents=True)
    marker = job / media_library.INCOMPLETE_MARKER
    marker.touch()
    video = season / "Show.S01E01.mkv"
    video.write_bytes(b"episode")
    (job / "Show.S01.aria2").touch()
    assert media_library.scan_media_library(tmp_path)["series"] == []
    payload = {"torrent": [{"id": job_id, "status": "downloading"}], "yt_dlp": []}
    client = DownloaderClient(downloader_url="http://downloader:8001")

    async def scenario():
        with patch("services.download_client.httpx.AsyncClient") as http_client:
            http_client.return_value.__aenter__.return_value.get.return_value = SimpleNamespace(
                is_success=True, json=lambda: payload,
            )
            await client.get_jobs()
            # A legacy downloader does not publish any SQLite rows.
            marker.unlink()
            payload["torrent"][0]["status"] = "completed"
            await client.get_jobs()
            assert [item["name"] for item in media_library.scan_media_library(tmp_path)["series"]] == [video.name]

    asyncio.run(scenario())


def test_ytdlp_zero_exit_without_output_keeps_managed_files_hidden(tmp_path, monkeypatch):
    import services.download_service as module
    monkeypatch.setattr(module, "DOWNLOADS_DIR", str(tmp_path))

    async def scenario():
        service = DownloadService()
        async def transfer(job, reservation):
            assert (tmp_path / job.id / media_library.INCOMPLETE_MARKER).exists()
        monkeypatch.setattr(service, "_transfer", transfer)
        job_id = service.start_download("https://video.example/watch", "Incomplete")
        await service._bg_tasks[job_id]
        assert service.get_job(job_id)["state"] == "failed"
        assert (tmp_path / job_id / media_library.INCOMPLETE_MARKER).exists()
        assert media_library.recover_completed_downloads(tmp_path) == []

    asyncio.run(scenario())


def test_interrupted_download_restores_same_id_and_directory(tmp_path, monkeypatch):
    import services.download_service as module
    monkeypatch.setattr(module, "DOWNLOADS_DIR", str(tmp_path))

    async def scenario():
        first = DownloadService()
        started = asyncio.Event()

        async def transfer(job, reservation):
            (tmp_path / job.id / "Movie.mp4.part").write_bytes(b"partial")
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(first, "_transfer", transfer)
        job_id = first.start_download("https://video.example/watch", "Movie")
        await asyncio.wait_for(started.wait(), 2)
        await first.close()
        assert first.get_job(job_id)["state"] == "interrupted"

        second = DownloadService()
        async def resumed(job, reservation):
            assert job.id == job_id
            assert (tmp_path / job.id / "Movie.mp4.part").read_bytes() == b"partial"
        monkeypatch.setattr(second, "_transfer", resumed)
        await second.restore()
        await second._bg_tasks[job_id]
        assert second.get_job(job_id)["id"] == job_id
        assert second.get_job(job_id)["attempts"] == 2

    asyncio.run(scenario())


def test_legacy_recovery_does_not_guess_managed_job_completion(tmp_path):
    job = tmp_path / "managed-job"
    job.mkdir()
    marker = job / media_library.INCOMPLETE_MARKER
    marker.write_text("streamvault-download-v2")
    (job / "Unmerged.f137.mp4").write_bytes(b"preallocated or unfinished")
    assert media_library.recover_completed_downloads(tmp_path) == []
    assert marker.is_file()


def test_verified_publication_is_replayed_without_restarting_transfer(tmp_path, monkeypatch):
    import services.download_service as module
    from services.download_queue import PARTIAL_GUARD, prepare_directory, source_fingerprint, verified_paths

    monkeypatch.setattr(module, "DOWNLOADS_DIR", str(tmp_path))
    url = "https://video.example/clip"
    job = module.DownloadJob(url, "Verified")
    first = DownloadService()
    first.store.add("yt_dlp", job, {"url": url}, source_fingerprint(url, "best", {}), "original")
    prepare_directory(job)
    directory = Path(job.download_dir)
    assert (directory / PARTIAL_GUARD).exists()
    media = directory / "Verified.mp4"
    media.write_bytes(b"known verified output")
    job.status = "processing"
    first.store.save(job, verified=verified_paths(directory, [media]))

    async def scenario():
        second = DownloadService()
        start_network = AsyncMock(side_effect=AssertionError("A verified download must not transfer again"))
        monkeypatch.setattr(second, "_transfer", start_network)
        await second.restore()
        await second._bg_tasks[job.id]
        assert second.get_job(job.id)["state"] == "completed"
        assert second.store.get(job.id)["payload"] is None
        assert not (directory / media_library.INCOMPLETE_MARKER).exists()
        assert not (directory / PARTIAL_GUARD).exists()
        start_network.assert_not_awaited()
        assert second.start_download(url, "Verified", request_key="original", quota_checked=True) == job.id
        assert second.get_job(job.id)["local_paths"] == [str(media)]

    asyncio.run(scenario())


def test_duplicate_request_key_keeps_its_original_job_after_a_second_client_enqueues(tmp_path, monkeypatch):
    import services.download_service as module
    monkeypatch.setattr(module, "DOWNLOADS_DIR", str(tmp_path))

    async def scenario():
        first, other = DownloadService(), DownloadService()
        url = "https://video.example/clip"
        job = first.start_download(url, "Clip", request_key="initial")
        duplicate = other.start_download(url, "Clip", request_key="another")
        assert duplicate == job
        transfer = first._bg_tasks[job]
        assert first.cancel_job(job)
        await asyncio.gather(transfer, return_exceptions=True)
        assert other.start_download(url, "Clip", request_key="another") == job
        assert other.get_job(job)["state"] == "cancelled"

    asyncio.run(scenario())


def test_header_bearing_sources_are_never_written_to_the_queue(tmp_path, monkeypatch):
    import services.download_service as module
    monkeypatch.setattr(module, "DOWNLOADS_DIR", str(tmp_path))

    async def scenario():
        service = DownloadService()
        job = service.start_download("https://video.example/private?key=private-fixture", "Clip",
                                     extra_headers={"Cookie": "private-fixture"})
        transfer = service._bg_tasks[job]
        assert service.store.get(job)["payload"] is None
        await service.close()
        assert transfer.done()
        assert b"private-fixture" not in service.store.path.read_bytes()
        second = DownloadService()
        await second.restore()
        await second._bg_tasks[job]
        assert second.get_job(job)["state"] == "interrupted"
        assert not second.get_job(job)["can_resume"]

    asyncio.run(scenario())
