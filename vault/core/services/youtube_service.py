"""
YouTube Service - Powered by yt-dlp
Direct extraction for maximum reliability without Invidious proxy issues.
"""
import logging
import asyncio
import re
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor
import yt_dlp

logger = logging.getLogger(__name__)

class YouTubeService:
    """
    Servicio de YouTube que usa yt-dlp para extraer información directamente.
    Es mucho más estable que depender de instancias públicas de Invidious.
    """
    
    def __init__(self):
        # Opciones base para yt-dlp
        # Opciones base para yt-dlp - Optimizadas para evitar "Sign in to confirm"
        self.ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'best',
            'skip_download': True,
            'extract_flat': True,
            'nocheckcertificate': True,
            'source_address': '0.0.0.0',
            # Usar cliente de Android suele saltar bloqueos de bot mejor que User-Agent de PC
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'socket_timeout': 8,
            'retries': 1,
            'extractor_retries': 0,
        }
        # Executor para correr llamadas bloqueantes en hilos
        self.executor = ThreadPoolExecutor(max_workers=10)

    async def _run_extract(self, query: str, opts_override: dict = None) -> Optional[Dict]:
        """Ejecuta yt-dlp en un hilo separado para no bloquear FastAPI"""
        options = self.ydl_opts.copy()
        if opts_override:
            options.update(opts_override)
            
        def extract():
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(query, download=False)

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(self.executor, extract)
        except Exception as e:
            logger.error(f"Error extrayendo de YouTube ({query}): {e}")
            return None

    async def _run_extract_timed(
        self,
        query: str,
        opts_override: Optional[dict] = None,
        timeout_seconds: float = 8,
    ) -> Optional[Dict]:
        try:
            return await asyncio.wait_for(
                self._run_extract(query, opts_override=opts_override),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout extracting YouTube query: {query}")
            return None

    async def search(self, query: str, page: int = 1, sort_by: str = "relevance") -> List[Dict]:
        """Busca videos usando ytsearch"""
        # Limitamos a 20 resultados por página aprox (ytsearch no es paginado igual que API)
        limit = 20
        search_query = f"ytsearch{limit}:{query}"
        
        result = await self._run_extract(search_query)
        if not result or 'entries' not in result:
            return []
            
        videos = []
        for entry in result.get('entries', []):
            if not entry: continue
            # Algunas veces entry['thumbnail'] es suficiente, otras necesitamos buscar en 'thumbnails'
            thumb = entry.get("thumbnail")
            if not thumb and entry.get("thumbnails"):
                thumb = entry.get("thumbnails")[0].get("url")

            videos.append({
                "videoId": entry.get("id"),
                "title": entry.get("title"),
                "author": entry.get("uploader"),
                "authorId": entry.get("uploader_id"),
                "lengthSeconds": entry.get("duration"),
                "viewCount": entry.get("view_count"),
                "videoThumbnails": [{"url": thumb}],
                "publishedText": entry.get("upload_date"),
            })
        return videos

    async def get_video(self, video_id: str) -> Optional[Dict]:
        """Obtiene metadatos completos y formatos"""
        url = f"https://www.youtube.com/watch?v={video_id}"
        # Aquí NO usamos extract_flat para obtener todos los formatos
        return await self._run_extract(url, {'extract_flat': False})

    async def get_stream_url(self, video_id: str, quality: str = "720p") -> Optional[str]:
        """Obtiene la URL directa del stream"""
        info = await self.get_video(video_id)
        if not info or 'formats' not in info:
            return None

        selected = self._select_progressive_format(info.get("formats", []), quality)
        return selected.get("url") if selected else None

    async def get_stream_data(self, video_id: str, quality: str = "720p") -> Optional[Dict]:
        info = await self.get_video(video_id)
        if not info or "formats" not in info:
            return None

        selected = self._select_progressive_format(info.get("formats", []), quality)
        if not selected:
            return None

        return {
            "url": selected.get("url"),
            "quality": self._format_quality_label(selected),
            "available_qualities": self._extract_available_qualities(info.get("formats", [])),
        }

    async def get_audio_url(self, video_id: str) -> Optional[str]:
        """Obtiene URL de solo audio"""
        info = await self.get_video(video_id)
        if not info or 'formats' not in info:
            return None
            
        # Buscar el mejor audio disponible
        best_audio = None
        for f in info.get('formats', []):
            if f.get('vcodec') == 'none' and f.get('acodec') != 'none':
                best_audio = f.get('url')
        
        return best_audio

    async def get_trending(self, region: str = "ES") -> List[Dict]:
        """
        Obtiene videos populares.
        yt-dlp no soporta /feed/trending directamente, así que usamos 
        una búsqueda de videos populares recientes como alternativa.
        """
        # Alternativa: buscar videos populares/virales recientes
        search_query = "ytsearch20:tendencias viral popular 2026"
        result = await self._run_extract(search_query)
        if not result or 'entries' not in result:
            return []
            
        videos = []
        for entry in result.get('entries', [])[:20]:
            if not entry: continue
            thumb = entry.get("thumbnail")
            if not thumb and entry.get("thumbnails"):
                thumb = entry.get("thumbnails")[0].get("url")

            videos.append({
                "videoId": entry.get("id"),
                "title": entry.get("title"),
                "author": entry.get("uploader"),
                "videoThumbnails": [{"url": thumb}],
                "lengthSeconds": entry.get("duration"),
            })
        return videos

    async def get_channel(self, channel_id: str) -> Optional[Dict]:
        if channel_id.startswith("UC"):
            query = f"https://www.youtube.com/channel/{channel_id}"
        elif channel_id.startswith("@"):
            query = f"https://www.youtube.com/{channel_id}"
        else:
            query = f"https://www.youtube.com/@{channel_id}"
        info = await self._run_extract(query, {'extract_flat': True})
        if not info:
            return None

        return {
            "id": channel_id,
            "title": info.get("channel") or info.get("title") or channel_id,
            "channel_id": info.get("channel_id") or channel_id,
            "uploader_id": info.get("uploader_id") or channel_id,
            "webpage_url": info.get("webpage_url") or query,
        }

    async def get_channel_videos(
        self,
        channel_id: str,
        page: int = 1,
        channel_url: Optional[str] = None,
        channel_name: Optional[str] = None,
    ) -> List[Dict]:
        queries: list[str] = []

        if channel_url:
            normalized = channel_url.rstrip("/")
            if normalized.endswith("/videos"):
                queries.append(normalized)
            else:
                queries.append(f"{normalized}/videos")

        if channel_id.startswith("UC"):
            queries.append(f"https://www.youtube.com/channel/{channel_id}/videos")
        elif channel_id.startswith("@"):
            queries.append(f"https://www.youtube.com/{channel_id}/videos")
        else:
            queries.append(f"https://www.youtube.com/@{channel_id}/videos")
            queries.append(f"https://www.youtube.com/channel/{channel_id}/videos")

        unique_queries = list(dict.fromkeys(queries))
        entries: list[dict] = []
        search_term = channel_name or channel_id
        search_info = await self._run_extract_timed(f"ytsearch30:{search_term}", timeout_seconds=6)
        search_entries = [entry for entry in (search_info or {}).get("entries", []) if entry]
        entries = self._filter_channel_search_entries(search_entries, channel_id, channel_name)

        if entries:
            start = max(page - 1, 0) * 20
            end = start + 20
            return self._format_channel_entries(entries[start:end], channel_id)

        for query in unique_queries:
            info = await self._run_extract_timed(
                query,
                {'extract_flat': True},
                timeout_seconds=6,
            )
            if info and info.get("entries"):
                entries = [entry for entry in info.get("entries", []) if entry]
                if entries:
                    break

        start = max(page - 1, 0) * 20
        end = start + 20
        return self._format_channel_entries(entries[start:end], channel_id)

    def _filter_channel_search_entries(
        self,
        entries: List[Dict],
        channel_id: str,
        channel_name: Optional[str],
    ) -> List[Dict]:
        target = (channel_id or "").lower().lstrip("@")
        channel_name_l = (channel_name or "").strip().lower()

        filtered: list[dict] = []
        for entry in entries:
            uploader_id = (entry.get("uploader_id") or "").lower().lstrip("@")
            channel_ref = (entry.get("channel_id") or "").lower().lstrip("@")
            uploader = (entry.get("uploader") or "").lower()
            channel_label = (entry.get("channel") or "").lower()

            id_match = target and (uploader_id == target or channel_ref == target)
            name_match = channel_name_l and (channel_name_l in uploader or channel_name_l in channel_label)
            fuzzy_target_match = target and (target in uploader or target in channel_label)

            if id_match or name_match or fuzzy_target_match:
                filtered.append(entry)

        if filtered:
            return filtered
        return entries

    def _format_channel_entry(self, entry: Dict, fallback_channel_id: str) -> Dict:
        thumb = entry.get("thumbnail")
        if not thumb and entry.get("thumbnails"):
            thumb = entry.get("thumbnails")[0].get("url")

        return {
            "videoId": entry.get("id"),
            "title": entry.get("title"),
            "author": entry.get("channel") or entry.get("uploader"),
            "authorId": entry.get("channel_id") or entry.get("uploader_id") or fallback_channel_id,
            "lengthSeconds": entry.get("duration"),
            "viewCount": entry.get("view_count"),
            "videoThumbnails": [{"url": thumb}],
            "publishedText": entry.get("upload_date"),
        }

    def _format_channel_entries(self, entries: List[Dict], fallback_channel_id: str) -> List[Dict]:
        formatted: list[dict] = []
        for entry in entries:
            if not self._looks_like_video_id(entry.get("id")):
                continue
            formatted.append(self._format_channel_entry(entry, fallback_channel_id))
        return formatted

    def _looks_like_video_id(self, value: Optional[str]) -> bool:
        if not value:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", str(value)))

    def format_video_result(self, video: Dict) -> Dict:
        """Frontend-friendly shape; includes both `id` and `video_id` so
        old/new templates keep working."""
        vid = video.get("videoId")
        return {
            "id": vid,
            "video_id": vid,
            "title": video.get("title"),
            "author": video.get("author"),
            "author_id": video.get("authorId"),
            "channel_url": video.get("authorUrl") or video.get("channel_url"),
            "duration": video.get("lengthSeconds", 0),
            "duration_text": self._format_duration(video.get("lengthSeconds", 0) or 0),
            "views": video.get("viewCount", 0) or 0,
            "views_text": self._format_views(video.get("viewCount", 0) or 0),
            "published": video.get("publishedText") or video.get("upload_date"),
            "thumbnail": video.get("videoThumbnails", [{}])[0].get("url"),
            "description": "",
        }

    def _select_progressive_format(self, formats: List[Dict], quality: str) -> Optional[Dict]:
        try:
            target = int(str(quality).replace("p", ""))
        except Exception:
            target = 720

        progressive = [
            f for f in formats
            if f.get("vcodec") != "none"
            and f.get("acodec") != "none"
            and f.get("url")
            and f.get("height")
        ]

        if not progressive:
            return None

        progressive.sort(key=lambda f: int(f.get("height") or 0))
        candidates = [f for f in progressive if int(f.get("height") or 0) <= target]
        selected = candidates[-1] if candidates else progressive[0]
        return selected

    def _extract_available_qualities(self, formats: List[Dict]) -> List[str]:
        qualities = sorted(
            {
                f"{int(f.get('height'))}p"
                for f in formats
                if f.get("vcodec") != "none"
                and f.get("acodec") != "none"
                and f.get("height")
            },
            key=lambda item: int(item.replace("p", "")),
        )
        return qualities or ["360p", "480p", "720p"]

    def _format_quality_label(self, fmt: Dict) -> str:
        height = fmt.get("height")
        if height:
            return f"{int(height)}p"
        return str(fmt.get("format_note") or "auto")

    @staticmethod
    def _format_duration(seconds) -> str:
        try:
            s = int(seconds or 0)
            if s < 3600:
                return f"{s // 60}:{s % 60:02d}"
            return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
        except:
            return "0:00"

    @staticmethod
    def _format_views(count: int) -> str:
        try:
            cnt = int(count)
            if cnt >= 1_000_000: return f"{cnt / 1_000_000:.1f}M"
            if cnt >= 1_000: return f"{cnt / 1_000:.1f}K"
            return str(cnt)
        except:
            return "0"

    async def close(self):
        self.executor.shutdown()
