import logging
from typing import List, Dict
from ..base import SourceProvider

logger = logging.getLogger(__name__)


class VidSrcProvider(SourceProvider):
    """
    VidSrc mirrors — original domains redirect to .ru mirrors.
    We include both canonical and redirect targets so validation
    can pick the one that actually responds.
    """

    @property
    def name(self) -> str:
        return "VidSrc"

    @property
    def base_url(self) -> str:
        return "https://vidsrc.net"

    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        embeds = []
        # Include redirect targets so we skip the 301 hop
        mirrors = [
            ("vidsrc.net", "https://vidsrc.net"),
            ("vsembed.ru", "https://vsembed.ru"),
            ("vidsrcme.ru", "https://vidsrcme.ru"),
        ]
        for label, base in mirrors:
            if season and episode:
                url = f"{base}/embed/tv/{tmdb_id}/{season}/{episode}"
            else:
                url = f"{base}/embed/movie/{tmdb_id}"
            embeds.append({
                "server": label,
                "url": url,
                "quality": "Multi",
            })
        return embeds
