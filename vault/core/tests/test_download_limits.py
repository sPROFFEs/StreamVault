import asyncio
from unittest.mock import AsyncMock

from services.torrent_service import TorrentJob, TorrentService
from pathlib import Path
import sys

from services.download_limits import stop_process


def test_stopping_encoder_with_full_stdout_does_not_deadlock():
    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import os,time; os.write(1, b'x' * 1048576); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024,
        )
        try:
            await asyncio.sleep(.15)  # Let the child fill its unread output pipe.
            await asyncio.wait_for(stop_process(process), timeout=2)
            assert process.returncode is not None
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
    asyncio.run(scenario())


def test_default_transfer_budget_leaves_headroom_and_matches_compose():
    from config import Settings
    settings = Settings(_env_file=None)
    assert settings.DOWNLOAD_RATE_LIMIT == 4 * 1024**2
    assert settings.TORRENT_UPLOAD_RATE_LIMIT == 64 * 1024
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yaml").read_text()
    assert compose.count(f"DOWNLOAD_RATE_LIMIT:-{settings.DOWNLOAD_RATE_LIMIT}") == 2
    assert compose.count(f"TORRENT_UPLOAD_RATE_LIMIT:-{settings.TORRENT_UPLOAD_RATE_LIMIT}") == 2
    downloader = compose.split("\n  downloader:", 1)[1].split("\n  cloudflared:", 1)[0]
    assert "cpu_shares: 256" in downloader
    assert "DOWNLOADER_MEMORY_LIMIT:-768m" in downloader


def test_only_configured_number_of_downloads_run_at_once(monkeypatch):
    import services.torrent_service as torrents

    async def scenario():
        service = TorrentService()
        gate = asyncio.Event()
        entered = []

        async def transfer(job):
            entered.append(job.id)
            await gate.wait()

        monkeypatch.setattr(torrents, "download_slots", asyncio.Semaphore(1), raising=False)
        monkeypatch.setattr(service, "_download", transfer, raising=False)
        jobs = [TorrentJob(str(i), f"magnet:?xt=urn:btih:{i}") for i in range(3)]
        tasks = [asyncio.create_task(service._run(job)) for job in jobs]
        await asyncio.sleep(0)
        try:
            assert len(entered) == 1
            assert jobs[1].status == "pending"
        finally:
            gate.set()
            await asyncio.gather(*tasks)
        assert len(entered) == 3
    asyncio.run(scenario())
