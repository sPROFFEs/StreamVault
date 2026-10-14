"""
IPTV Service - Gestor de canales de TV en vivo.

Trabaja con listas M3U publicas y abiertas.
"""
import asyncio
import logging
import re
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)


async def load_sources_concurrently(source_keys, loader, timeout_s: float = 15.0) -> Dict[str, Any]:
    """Load independent playlists together and preserve per-source failures."""
    async def load_one(source_key: str) -> dict:
        try:
            result = await asyncio.wait_for(loader(source_key), timeout=timeout_s)
            return {"ok": True, "key": source_key, "result": result}
        except asyncio.TimeoutError:
            return {"ok": False, "key": source_key, "error": "timeout"}
        except Exception as exc:
            return {"ok": False, "key": source_key, "error": str(getattr(exc, "detail", exc))}

    outcomes = await asyncio.gather(*(load_one(key) for key in source_keys))
    successful = [outcome for outcome in outcomes if outcome["ok"]]
    errors = [
        {"source": outcome["key"], "error": outcome["error"]}
        for outcome in outcomes if not outcome["ok"]
    ]
    imported = sum(outcome["result"].get("imported", 0) for outcome in successful)
    return {
        "success": imported > 0,
        "imported": imported,
        "sources_loaded": [outcome["key"] for outcome in successful],
        "errors": errors,
    }


class IPTVService:
    """Gestor de canales IPTV estilo TV Garden."""

    def __init__(self, db_session=None):
        self.db = db_session
        self.client = httpx.AsyncClient(timeout=60.0)

    async def import_m3u(self, source: str, source_name: str = "imported") -> Dict[str, Any]:
        """Importa canales desde un archivo M3U local o URL."""
        if source.startswith(("http://", "https://")):
            try:
                resp = await self.client.get(source, follow_redirects=True)
                resp.raise_for_status()
                content = resp.text
            except Exception as exc:
                logger.error("Error descargando M3U: %s", exc)
                return {"success": False, "error": str(exc)}
        else:
            path = Path(source)
            if not path.exists():
                return {"success": False, "error": f"Archivo no encontrado: {source}"}
            content = path.read_text(encoding="utf-8", errors="ignore")

        channels = self._deduplicate_channels(self._parse_m3u(content, source_name))

        return {
            "success": True,
            "source": source_name,
            "total_channels": len(channels),
            "channels": channels,
        }

    def _parse_m3u(self, content: str, source_name: str) -> List[Dict[str, Any]]:
        channels: List[Dict[str, Any]] = []
        lines = content.strip().splitlines()
        current_channel: Dict[str, Any] = {}
        channel_index = 0

        for line in lines:
            line = line.strip()

            if line.startswith("#EXTINF:"):
                current_channel = self._parse_extinf(line)
                current_channel["source"] = source_name
            elif line and not line.startswith("#") and current_channel:
                current_channel["stream_url"] = line
                current_channel["id"] = self._build_channel_id(
                    source_name,
                    current_channel.get("name", ""),
                    line,
                    channel_index,
                )
                current_channel["group_title"] = current_channel.get("group", "Sin categoria")
                channels.append(current_channel)
                current_channel = {}
                channel_index += 1

        return channels

    def _parse_extinf(self, line: str) -> Dict[str, Any]:
        channel: Dict[str, Any] = {
            "name": "",
            "logo_url": None,
            "group": "Sin categoria",
            "language": None,
            "country": None,
        }

        logo_match = re.search(r'tvg-logo="([^"]*)"', line)
        if logo_match:
            channel["logo_url"] = logo_match.group(1)

        group_match = re.search(r'group-title="([^"]*)"', line)
        if group_match:
            channel["group"] = group_match.group(1)

        lang_match = re.search(r'tvg-language="([^"]*)"', line)
        if lang_match:
            languages = [item.strip() for item in re.split(r"[,;]", lang_match.group(1)) if item.strip()]
            channel["languages"] = languages
            channel["language"] = languages[0] if languages else None

        country_match = re.search(r'tvg-country="([^"]*)"', line)
        if country_match:
            countries = [item.strip().upper() for item in re.split(r"[,;]", country_match.group(1)) if item.strip()]
            channel["countries"] = countries
            channel["country"] = countries[0] if countries else None

        # Attribute values can contain commas (languages, countries, groups).
        # Only the first comma OUTSIDE a quoted attribute starts the name.
        quoted = False
        for index, character in enumerate(line):
            if character == '"':
                quoted = not quoted
            elif character == "," and not quoted:
                channel["name"] = line[index + 1:].strip()
                break

        return channel

    def _build_channel_id(self, source_name: str, name: str, stream_url: str, channel_index: int) -> str:
        raw = f"{source_name}|{channel_index}|{name}|{stream_url}"
        return sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _deduplicate_channels(self, channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique_channels: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for channel in channels:
            stream_url = (channel.get("stream_url") or "").strip()
            name = (channel.get("name") or "").strip().lower()
            if not stream_url:
                continue

            dedupe_key = f"{name}|{stream_url}"
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            unique_channels.append(channel)

        return unique_channels

    async def check_channel_health(self, stream_url: str) -> Dict[str, Any]:
        """Verifica si un canal esta funcionando."""
        import time

        start = time.time()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            resp = await self.client.head(stream_url, follow_redirects=True, headers=headers)
            if resp.status_code in (405, 501):
                # Fallback to stream GET for servers that disallow HEAD on m3u8 playlists
                async with self.client.stream("GET", stream_url, follow_redirects=True, headers=headers) as stream_resp:
                    response_time = time.time() - start
                    return {
                        "working": stream_resp.status_code < 400,
                        "status_code": stream_resp.status_code,
                        "response_time": round(response_time, 2),
                        "error": None,
                    }
            response_time = time.time() - start
            return {
                "working": resp.status_code < 400,
                "status_code": resp.status_code,
                "response_time": round(response_time, 2),
                "error": None,
            }
        except Exception as exc:
            return {
                "working": False,
                "status_code": None,
                "response_time": None,
                "error": str(exc),
            }

    def group_channels(self, channels: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Agrupa canales por categoria."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for channel in channels:
            group = channel.get("group", "Sin categoria")
            groups.setdefault(group, []).append(channel)
        return groups

    def filter_channels(
        self,
        channels: List[Dict[str, Any]],
        group: str = None,
        language: str = None,
        country: str = None,
        search: str = None,
    ) -> List[Dict[str, Any]]:
        """Filtra canales por criterios."""
        result = channels

        if group:
            result = [channel for channel in result if channel.get("group", "").lower() == group.lower()]

        if language:
            result = [
                channel
                for channel in result
                if language.lower() in (channel.get("language") or "").lower()
            ]

        if country:
            result = [
                channel
                for channel in result
                if country.lower() in [item.lower() for item in (
                    channel.get("countries") or [channel.get("country") or ""]
                )]
            ]

        if search:
            search_lower = search.lower()
            result = [
                channel
                for channel in result
                if search_lower in channel.get("name", "").lower()
            ]

        return result

    async def close(self):
        """Cierra el cliente HTTP."""
        await self.client.aclose()


PUBLIC_M3U_SOURCES: Dict[str, Dict[str, str]] = {
    "tdtchannels": {
        "name": "TDTChannels",
        "description": "Televisión en abierto de España y canales internacionales.",
        "url": "https://www.tdtchannels.com/lists/tv.m3u8",
        "region": "es",
    },
    "free-tv": {
        "name": "Free-TV",
        "description": "Lista comunitaria complementaria de canales gratuitos internacionales.",
        "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
        "region": "global",
    },
    "spain": {
        "name": "Espana",
        "description": "Canales abiertos de Espana.",
        "url": "https://iptv-org.github.io/iptv/countries/es.m3u",
        "region": "es",
    },
    "latam": {
        "name": "LatAm",
        "description": "Seleccion regional de Latinoamerica.",
        "url": "https://iptv-org.github.io/iptv/regions/latam.m3u",
        "region": "latam",
    },
    "news": {
        "name": "Noticias",
        "description": "Canales informativos abiertos.",
        "url": "https://iptv-org.github.io/iptv/categories/news.m3u",
        "region": "global",
    },
    "sports": {
        "name": "Deportes",
        "description": "Canales etiquetados como deportes.",
        "url": "https://iptv-org.github.io/iptv/categories/sports.m3u",
        "region": "global",
    },
    "movies": {
        "name": "Cine",
        "description": "Canales etiquetados como peliculas.",
        "url": "https://iptv-org.github.io/iptv/categories/movies.m3u",
        "region": "global",
    },
    "iptv-org": {
        "name": "IPTV-org global",
        "description": "Indice general de canales abiertos mantenido por la comunidad.",
        "url": "https://iptv-org.github.io/iptv/index.m3u",
        "region": "global",
    },
}
