import logging
from typing import List, Dict
from ..base import SourceProvider

logger = logging.getLogger(__name__)


class MoviesApiProvider(SourceProvider):
    """
    moviesapi.club — TMDB-based embed.
    Replaces dead multiembed.mov (403 + X-Frame-Options).
    """

    @property
    def name(self) -> str:
        return "MoviesAPI"

    @property
    def base_url(self) -> str:
        return "https://moviesapi.club"

    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        if season and episode:
            url = f"{self.base_url}/tv/{tmdb_id}-{season}-{episode}"
        else:
            url = f"{self.base_url}/movie/{tmdb_id}"
        return [{"server": "moviesapi.club", "url": url, "quality": "Multi"}]
