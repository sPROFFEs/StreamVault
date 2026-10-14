import logging
from typing import List, Dict
from ..base import SourceProvider

logger = logging.getLogger(__name__)


class NovafixProvider(SourceProvider):
    """
    Novafix / 2embed.skin — TMDB-based embed player.
    Replaces dead embed.su (DNS failure from Docker).
    """

    @property
    def name(self) -> str:
        return "2embed"

    @property
    def base_url(self) -> str:
        return "https://2embed.skin"

    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        embeds = []
        for label, base in [("2embed.skin", "https://2embed.skin"), ("nontongo.win", "https://www.nontongo.win")]:
            if season and episode:
                url = f"{base}/embed/tv/{tmdb_id}/{season}/{episode}"
            else:
                url = f"{base}/embed/movie/{tmdb_id}"
            embeds.append({"server": label, "url": url, "quality": "Multi"})
        return embeds
