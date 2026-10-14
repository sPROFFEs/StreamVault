import logging
import httpx
from typing import List, Dict
from ..base import SourceProvider

logger = logging.getLogger(__name__)


class VidSrcIcuProvider(SourceProvider):
    """
    VidSrc.icu — one of the most actively-maintained free embed mirrors.
    Tends to allow iframing and has multiple internal server fallbacks.
    """

    @property
    def name(self) -> str:
        return "VidSrc.icu"

    @property
    def base_url(self) -> str:
        return "https://vidsrc.icu"

    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        embeds = []
        bases = [
            ("vidsrc.icu", "https://vidsrc.icu"),
            ("vidsrc.cc", "https://vidsrc.cc/v2"),
        ]
        for label, base in bases:
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
