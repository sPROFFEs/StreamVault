import asyncio
from pathlib import Path
import sys

import pytest

from services.download_limits import TransferSlots, stop_process, stop_process_group
from services.torrent_service import TorrentService


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


@pytest.mark.parametrize("parent_exits", [False, True])
def test_stopping_worker_group_stops_child_before_it_writes(tmp_path, parent_exits):
    destination = tmp_path / "child-wrote"
    child = "import pathlib,sys,time; print('ready', flush=True); time.sleep(1); pathlib.Path(sys.argv[1]).touch()"
    spawn = "Popen" if parent_exits else "run"
    supervisor = f"import subprocess,sys; subprocess.{spawn}([sys.executable, '-c', sys.argv[1], sys.argv[2]])"

    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", supervisor, child, str(destination),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        try:
            assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"ready\n"
            if parent_exits:
                async with asyncio.timeout(0.8):
                    while process.returncode is None:
                        await asyncio.sleep(0.01)
            await asyncio.wait_for(stop_process_group(process), timeout=6)
            await asyncio.sleep(1.1)
            assert not destination.exists()
        finally:
            await stop_process_group(process)

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


def test_only_configured_number_of_downloads_run_at_once(tmp_path, monkeypatch):
    import services.torrent_service as torrents

    async def scenario():
        service = TorrentService()
        gate = asyncio.Event()
        first_entered = asyncio.Event()
        entered = []

        async def transfer(job):
            entered.append(job.id)
            first_entered.set()
            await gate.wait()

        monkeypatch.setattr(torrents, "DOWNLOADS_DIR", str(tmp_path))
        monkeypatch.setattr(torrents, "_aria2c_available", lambda: True)
        monkeypatch.setattr(torrents, "download_slots", TransferSlots(1))
        monkeypatch.setattr(service, "_download", transfer, raising=False)
        ids = [service.start_torrent_download(str(i), f"magnet:?xt=urn:btih:{i}", priority=priority)
               for i, priority in enumerate((0, -1, 1))]
        tasks = list(service._bg_tasks.values())
        await asyncio.wait_for(first_entered.wait(), timeout=2)
        try:
            assert len(entered) == 1
            assert service.get_job(ids[1])["status"] == "pending"
        finally:
            gate.set()
            await asyncio.gather(*tasks)
        assert entered == [ids[0], ids[2], ids[1]]
    asyncio.run(scenario())
