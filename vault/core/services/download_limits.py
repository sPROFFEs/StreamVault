"""Shared transfer slots keep downloads from starving playback and the tunnel."""
import asyncio
from contextlib import asynccontextmanager
import heapq
from itertools import count
import os
import signal

from config import settings


class TransferSlots:
    """Prefer high-priority queued work and preserve FIFO within a priority."""

    def __init__(self, limit):
        self.available = limit
        self.waiting = []
        self.sequence = count()

    def _wake(self):
        while self.waiting and self.available:
            _, _, waiter = heapq.heappop(self.waiting)
            if not waiter.done():
                self.available -= 1
                waiter.set_result(None)

    @asynccontextmanager
    async def hold(self, priority):
        waiter = asyncio.get_running_loop().create_future()
        entry = (-priority, next(self.sequence), waiter)
        heapq.heappush(self.waiting, entry)
        self._wake()
        try:
            await waiter
            yield
        finally:
            if waiter.done() and not waiter.cancelled():
                self.available += 1
            if entry in self.waiting:
                self.waiting.remove(entry)
                heapq.heapify(self.waiting)
            self._wake()


download_slots = TransferSlots(settings.CONCURRENT_DOWNLOADS)


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


async def stop_process_group(process) -> None:
    """Stop yt-dlp and the child FFmpeg writing its postprocessing outputs."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await asyncio.wait_for(process.communicate(), timeout=5)
