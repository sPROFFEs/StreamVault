from services.download_client import DownloaderClient
from services.download_service import DownloadService
from services.torrent_service import TorrentService, TorrentJob
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from config import settings
from services import media_library


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
    monkeypatch.setattr(media_library, "_SCAN_CACHE_TTL", 600)
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
            # Simulate completion in the other container, which cannot clear
            # this process's filesystem cache directly.
            marker.unlink()
            payload["torrent"][0]["status"] = "completed"
            await client.get_jobs()
            assert [item["name"] for item in media_library.scan_media_library(tmp_path)["series"]] == [video.name]

    asyncio.run(scenario())
