import logging
import asyncio
from typing import List, Optional
from .base import SourceProvider, Extractor, StreamResult
from .embed_registry import build_embed_streams
from .sources.vidsrc import VidSrcProvider
from .sources.vidsrc_icu import VidSrcIcuProvider
from .sources.autoembed import NovafixProvider
from .sources.vidsrc_xyz import VideoasyProvider
from .sources.twoembed import MoviesApiProvider
from .sources.torrentio import TorrentioProvider
from .sources.yts import YtsProvider
from .sources.eztv import EztvProvider
from .extractors.filemoon import FilemoonExtractor

logger = logging.getLogger(__name__)

class ResolverOrchestrator:
    def __init__(self):
        self.torrent_providers: List = [
            YtsProvider(),
            EztvProvider(),
            TorrentioProvider(),
        ]
        self.embed_providers: List[SourceProvider] = [
            VidSrcIcuProvider(),
            VidSrcProvider(),
            VideoasyProvider(),
            NovafixProvider(),
            MoviesApiProvider(),
        ]
        self.extractors: List[Extractor] = [FilemoonExtractor()]

    async def resolve_stream(
        self,
        tmdb_id: str,
        imdb_id: Optional[str] = None,
        media_type: str = "movie",
        season: int = None,
        episode: int = None,
        preferred_source: Optional[str] = None,
        source_order: Optional[List[str]] = None,
        include_torrents: bool = False,
    ) -> List[StreamResult]:
        registry_streams = [
            StreamResult(**item)
            for item in build_embed_streams(
                tmdb_id=tmdb_id,
                media_type=media_type,
                season=season,
                episode=episode,
                preferred_source=preferred_source,
                source_order=source_order,
            )
        ]

        # Public embed routes are returned as the primary playback registry.
        # Existing provider adapters remain available to the debug endpoint.
        if include_torrents:
            torrent_streams = await self._gather_torrents(
                tmdb_id, season, episode, imdb_id,
            )
        else:
            torrent_streams = []

        all_streams = registry_streams + torrent_streams

        # --- Deduplicate ---
        unique: List[StreamResult] = []
        seen = set()
        for s in all_streams:
            key = (s.url, s.source, s.type)
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return unique

    async def _gather_torrents(self, tmdb_id, season, episode, imdb_id) -> List[StreamResult]:
        async def _safe(provider):
            try:
                return await asyncio.wait_for(
                    provider.get_streams(tmdb_id, season, episode, imdb_id=imdb_id),
                    timeout=14.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Torrent {provider.name} timed out")
                return []
            except Exception as e:
                logger.warning(f"Torrent {provider.name} error: {e}")
                return []

        results_list = await asyncio.gather(*[_safe(p) for p in self.torrent_providers])
        streams = []
        for res in results_list:
            if not isinstance(res, list):
                continue
            for r in res:
                if r.get("type") == "torrent":
                    streams.append(StreamResult(
                        url=r["url"],
                        quality=r.get("quality", "HD"),
                        source=r.get("server", "Torrent"),
                        type="torrent",
                        info_hash=r.get("info_hash"),
                        file_idx=r.get("file_idx"),
                        size_bytes=r.get("size_bytes"),
                        seeders=r.get("seeders"),
                    ))
        logger.info(f"Torrent streams: {len(streams)}")
        return streams

    async def _gather_embeds(self, tmdb_id, season, episode) -> List[StreamResult]:
        async def _safe(provider):
            try:
                return await asyncio.wait_for(
                    provider.get_streams(tmdb_id, season, episode),
                    timeout=7.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Embed {provider.name} timed out")
                return []
            except Exception as e:
                logger.warning(f"Embed {provider.name} error: {e}")
                return []

        results_list = await asyncio.gather(*[_safe(p) for p in self.embed_providers])
        streams = []
        for res in results_list:
            if not isinstance(res, list):
                continue
            for embed in res:
                url = embed.get("url", "")
                server = embed.get("server", "Unknown")
                extractor = self._find_extractor(url)
                stream = None
                if extractor:
                    try:
                        stream = await extractor.extract(url)
                    except Exception as e:
                        logger.error(f"Extraction failed for {url}: {e}")
                if stream:
                    stream.source = server
                    streams.append(stream)
                else:
                    streams.append(StreamResult(
                        url=url,
                        quality=embed.get("quality", "Multi"),
                        source=server,
                        type="embed",
                    ))
        logger.info(f"Embed streams: {len(streams)}")
        return streams

    def _find_extractor(self, url: str) -> Optional[Extractor]:
        for ext in self.extractors:
            for domain in ext.domains:
                if domain in url:
                    return ext
        return None
