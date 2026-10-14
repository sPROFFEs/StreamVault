"""AniSkip — intro/outro skip-timing lookup by MAL id (api.aniskip.com/v2)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

ANISKIP_BASE = "https://api.aniskip.com/v2"
CACHE_TTL_S = 7 * 24 * 3600
_cache: Dict[str, tuple[float, dict]] = {}


def _cache_key(mal_id: int, ep: int) -> str:
    return f"{mal_id}:{ep}"


async def get_skip_times(mal_id: int, episode_number: int, episode_length: float = 0) -> Dict[str, Any]:
    """Return {intro: {start, end}, outro: {start, end}} or empties if not found."""
    key = _cache_key(mal_id, episode_number)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL_S:
        return cached[1]
    out: Dict[str, Any] = {"intro": None, "outro": None}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                f"{ANISKIP_BASE}/skip-times/{mal_id}/{episode_number}",
                params={"types": ["op", "ed"], "episodeLength": episode_length or 0},
            )
            if r.status_code != 200:
                _cache[key] = (time.time(), out)
                return out
            data = r.json()
            for entry in data.get("results", []) or []:
                t = entry.get("skipType")
                interval = entry.get("interval") or {}
                if t == "op":
                    out["intro"] = {
                        "start": interval.get("startTime"),
                        "end": interval.get("endTime"),
                    }
                elif t == "ed":
                    out["outro"] = {
                        "start": interval.get("startTime"),
                        "end": interval.get("endTime"),
                    }
    except Exception as e:
        logger.debug("aniskip failed: %s", e)
    _cache[key] = (time.time(), out)
    return out
