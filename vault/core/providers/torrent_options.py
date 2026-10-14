"""Presentation metadata for explicit torrent download choices."""
from __future__ import annotations

from typing import Iterable


def _health(seeders: int) -> str:
    if seeders <= 0:
        return "unknown"
    if seeders < 5:
        return "weak"
    if seeders < 20:
        return "limited"
    return "healthy"


def prepare_torrent_options(streams: Iterable[dict], limit: int = 12) -> list[dict]:
    options = []
    for stream in streams:
        if stream.get("type") != "torrent":
            continue
        option = dict(stream)
        seeders = int(option.get("seeders") or 0)
        option["seeders"] = seeders
        option["health"] = _health(seeders)
        options.append(option)
    options.sort(key=lambda item: item["seeders"], reverse=True)
    return options[:limit]
