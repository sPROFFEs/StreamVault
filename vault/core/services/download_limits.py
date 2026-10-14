"""Shared transfer slots keep downloads from starving playback and the tunnel."""
import asyncio

from config import settings


download_slots = asyncio.Semaphore(settings.CONCURRENT_DOWNLOADS)


def download_rate_per_job() -> int:
    return max(1, settings.DOWNLOAD_RATE_LIMIT // settings.CONCURRENT_DOWNLOADS)


async def stop_process(process) -> None:
    """Reap subprocesses, including ones stuck after a client disconnect."""
    if process is None:
        return
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        # wait() can block on a full stdout pipe even after the child exits.
        # Drain both pipes so cancelled playback releases its slot promptly.
        await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.communicate(), timeout=5)
