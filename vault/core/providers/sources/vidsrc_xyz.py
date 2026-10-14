import logging
from typing import List, Dict
from ..base import SourceProvider

logger = logging.getLogger(__name__)


class VideoasyProvider(SourceProvider):
    """player.videasy.net — TMDB-based embed, iframe-friendly."""

    @property
    def name(self) -> str:
        return "Videasy"

    @property
    def base_url(self) -> str:
        return "https://player.videasy.net"

    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        if season and episode:
            url = f"{self.base_url}/tv/{tmdb_id}/{season}/{episode}"
        else:
            url = f"{self.base_url}/movie/{tmdb_id}"
        return [{"server": "videasy.net", "url": url, "quality": "Multi"}]
