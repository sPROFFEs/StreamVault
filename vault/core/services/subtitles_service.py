"""
SubtitlesService — Subdl.com + Wyzie merged search (parity with Streambert).

Sources, polled in parallel and merged:
  • Subdl    https://api.subdl.com/api/v1/subtitles   (free, key boosts quota)
  • Wyzie    https://sub.wyzie.io/search              (key tier) or
             https://subs.wyzie.ru/search             (free fallback)

Per-user API keys are passed via the `subdl_api_key` / `wyzie_api_key`
arguments and override the env-var defaults.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SUBDL_BASE = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DOWNLOAD_BASE = "https://dl.subdl.com"
SUBDL_API_KEY_ENV = os.getenv("SUBDL_API_KEY", "")

WYZIE_FREE_BASE = "https://sub.wyzie.ru/search"
WYZIE_PRO_BASE = "https://sub.wyzie.io/search"
WYZIE_API_KEY_ENV = os.getenv("WYZIE_API_KEY", "")
OPENSUBTITLES_BASE = "https://api.opensubtitles.com/api/v1"
OPENSUBTITLES_API_KEY_ENV = os.getenv("OPENSUBTITLES_API_KEY", "")
OPENSUBTITLES_USER_AGENT = os.getenv("OPENSUBTITLES_USER_AGENT", "StreamVault/1.0")

LANG_FALLBACK = ["es", "en", "pt", "fr", "de"]


class SubtitlesService:
    async def search(
        self,
        title: str,
        imdb_id: Optional[str] = None,
        media_type: str = "movie",
        season: Optional[int] = None,
        episode: Optional[int] = None,
        language: str = "en",
        year: Optional[int] = None,
        subdl_api_key: Optional[str] = None,
        wyzie_api_key: Optional[str] = None,
        opensubtitles_api_key: Optional[str] = None,
    ) -> Dict:
        lang_codes = self._resolve_lang_codes(language)
        subdl_key = subdl_api_key or SUBDL_API_KEY_ENV
        wyzie_key = wyzie_api_key or WYZIE_API_KEY_ENV
        opensubtitles_key = opensubtitles_api_key or OPENSUBTITLES_API_KEY_ENV

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "StreamVault/1.0 (+https://github.com/streamvault)"},
        ) as client:
            subdl_task = self._search_subdl(
                client, title, imdb_id, media_type, season, episode,
                lang_codes, subdl_key,
            )
            wyzie_task = self._search_wyzie(
                client, imdb_id, season, episode, lang_codes, wyzie_key,
            )
            opensubtitles_task = self._search_opensubtitles(
                client, title, imdb_id, season, episode, lang_codes, opensubtitles_key,
                year=year,
            )
            subdl_items, wyzie_items, opensubtitles_items = await asyncio.gather(
                subdl_task, wyzie_task, opensubtitles_task, return_exceptions=True,
            )

        items: List[dict] = []
        if not isinstance(subdl_items, Exception) and subdl_items:
            items.extend(subdl_items)
        if not isinstance(wyzie_items, Exception) and wyzie_items:
            items.extend(wyzie_items)
        if not isinstance(opensubtitles_items, Exception) and opensubtitles_items:
            items.extend(opensubtitles_items)

        if not items:
            return self._empty(f"No subtitles found for {', '.join(lang_codes)}",
                               configured=True)
        return {
            "items": items[:24],
            "message": f"{len(items)} subtitle(s) found",
            "configured": True,
        }

    # ── Subdl ───────────────────────────────────────────────────────────────

    async def _search_subdl(
        self,
        client: httpx.AsyncClient,
        title: str,
        imdb_id: Optional[str],
        media_type: str,
        season: Optional[int],
        episode: Optional[int],
        lang_codes: List[str],
        api_key: str,
    ) -> List[dict]:
        # Subdl now requires an API key — requests without one get 403.
        if not api_key:
            logger.debug("subdl skipped: no api key configured")
            return []
        params: dict = {
            "api_key": api_key,
            "languages": ",".join(lang_codes),
            "subs_per_page": 8,
            "type": "tv" if media_type == "tv" else "movie",
        }
        if imdb_id:
            params["imdb_id"] = imdb_id.lstrip("t") if imdb_id.startswith("tt") else imdb_id
        else:
            params["film_name"] = title
        if media_type == "tv" and season is not None:
            params["season_number"] = season
        if media_type == "tv" and episode is not None:
            params["episode_number"] = episode

        try:
            r = await client.get(
                SUBDL_BASE, params=params,
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug("subdl failed: %s", e)
            return []

        out: List[dict] = []
        for sub in (payload.get("subtitles") or [])[:8]:
            url = sub.get("url", "")
            if url and not url.startswith("http"):
                url = f"{SUBDL_DOWNLOAD_BASE}{url}"
            out.append({
                "provider": "Subdl",
                "server": sub.get("release_name") or sub.get("name") or "Subdl",
                "language": sub.get("language") or lang_codes[0],
                "stream_url": url,
                "type": "srt",
                "hearing_impaired": sub.get("hearing_impaired") or False,
            })
        return out

    # ── Wyzie ───────────────────────────────────────────────────────────────

    async def _search_wyzie(
        self,
        client: httpx.AsyncClient,
        imdb_id: Optional[str],
        season: Optional[int],
        episode: Optional[int],
        lang_codes: List[str],
        api_key: str,
    ) -> List[dict]:
        # Wyzie expects the full imdb id (tt-prefixed). If not provided,
        # the service is skipped (Wyzie requires an id).
        if not imdb_id:
            return []
        clean = imdb_id if imdb_id.startswith("tt") else f"tt{imdb_id}"
        params: dict = {"id": clean, "format": "srt"}
        if season is not None:
            params["season"] = season
        if episode is not None:
            params["episode"] = episode
        if lang_codes:
            params["language"] = lang_codes[0]
        headers = {}
        if api_key:
            params["key"] = api_key

        # Try pro endpoint first (with key), then fall back to free endpoint.
        bases = [WYZIE_PRO_BASE, WYZIE_FREE_BASE] if api_key else [WYZIE_FREE_BASE]
        payload = None
        for base in bases:
            try:
                r = await client.get(base, params=params, headers=headers)
                r.raise_for_status()
                payload = r.json()
                break
            except Exception as e:
                logger.debug("wyzie %s failed: %s", base, e)
                # Remove key for free endpoint (it doesn't accept keys)
                params.pop("key", None)
                headers.pop("Authorization", None)
                continue
        if not payload:
            return []
        items: List[dict] = []
        for sub in (payload or [])[:8]:
            url = sub.get("url") or sub.get("downloadUrl") or ""
            if not url:
                continue
            items.append({
                "provider": "Wyzie",
                "server": sub.get("release") or sub.get("title") or "Wyzie",
                "language": sub.get("language") or lang_codes[0],
                "stream_url": url,
                "type": (sub.get("format") or "srt").lower(),
                "hearing_impaired": sub.get("hearing_impaired") or False,
                "ai_translated": sub.get("ai_translated") or False,
            })
        return items

    async def _search_opensubtitles(
        self, client, title, imdb_id, season, episode, lang_codes, api_key,
        year: Optional[int] = None,
    ) -> List[dict]:
        if not api_key:
            return []
        params: dict = {"languages": ",".join(lang_codes)}
        if imdb_id:
            params["imdb_id"] = imdb_id.removeprefix("tt")
        else:
            params["query"] = title
            if year:
                params["year"] = year
        if season is not None:
            params["season_number"] = season
        if episode is not None:
            params["episode_number"] = episode
        try:
            response = await client.get(
                f"{OPENSUBTITLES_BASE}/subtitles",
                params=params,
                headers={"Api-Key": api_key, "User-Agent": OPENSUBTITLES_USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.debug("opensubtitles failed: %s", exc)
            return []
        items = []
        for entry in payload.get("data", [])[:15]:
            attributes = entry.get("attributes") or {}
            files = attributes.get("files") or []
            if not files or not files[0].get("file_id"):
                continue
            items.append({
                "provider": "OpenSubtitles",
                "server": attributes.get("release") or attributes.get("feature_details", {}).get("title") or "OpenSubtitles",
                "language": attributes.get("language") or lang_codes[0],
                "file_id": files[0]["file_id"],
                "type": attributes.get("format") or "srt",
                "hearing_impaired": attributes.get("hearing_impaired") or False,
                "rating": attributes.get("ratings") or 0,
                "download_count": attributes.get("download_count") or 0,
            })
        # Sort by popularity so the best match surfaces first.
        items.sort(key=lambda i: (i.get("download_count", 0), i.get("rating", 0)), reverse=True)
        return items

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _resolve_lang_codes(self, language: str) -> List[str]:
        primary = (language or "en")[:2].lower()
        codes = [primary]
        if "en" not in codes:
            codes.append("en")
        return codes

    @staticmethod
    def _empty(message: str, configured: bool = False) -> dict:
        return {"items": [], "message": message, "configured": configured}
