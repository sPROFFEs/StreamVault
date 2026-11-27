"""Elect one maintenance worker for processes sharing a StreamVault volume."""
import asyncio
import fcntl
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def run_periodic(data_root, *loops, retry_seconds=5):
    directory = Path(data_root) / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "maintenance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                await asyncio.sleep(retry_seconds)
                continue
            try:
                async with asyncio.TaskGroup() as group:
                    for loop in loops:
                        group.create_task(loop())
            except Exception as exc:
                logger.warning("Periodic maintenance interrupted (%s)", type(exc).__name__)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
            await asyncio.sleep(retry_seconds)
    finally:
        os.close(fd)
