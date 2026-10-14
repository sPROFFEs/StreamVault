import re
import logging
import httpx
from typing import Optional, List
from ..base import Extractor, StreamResult

logger = logging.getLogger(__name__)

class FilemoonExtractor(Extractor):
    """
    Extractor para Filemoon / MoonPlayer.
    Maneja la extracción de links directos .m3u8 incluso si están ofuscados.
    """
    
    @property
    def name(self) -> str:
        return "Filemoon"
        
    @property
    def domains(self) -> List[str]:
        return ["filemoon.sx", "filemoon.wf", "moonplayer.net", "vidsrc.to", "vidsrc.me"]
    
    async def extract(self, url: str) -> Optional[StreamResult]:
        client = httpx.AsyncClient(timeout=10, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": url
        })
        
        try:
            resp = await client.get(url)
            html = resp.text
            
            # 1. Buscar m3u8 directo
            m3u8 = re.search(r'file\s*:\s*"([^"]+\.m3u8[^"]*)"', html)
            if m3u8:
                return StreamResult(
                    url=m3u8.group(1),
                    quality="Multi",
                    source="Filemoon",
                    headers={"Referer": "https://filemoon.sx/"}
                )
            
            # 2. Buscar packed JS
            packed_js = re.search(r'(eval\(function\(p,a,c,k,e,d\).*?\.split\("\|"\)\)\))', html)
            if packed_js:
                unpacked = self._unpack(packed_js.group(1))
                m3u8_packed = re.search(r'file\s*:\s*"([^"]+\.m3u8[^"]*)"', unpacked)
                if m3u8_packed:
                    return StreamResult(
                        url=m3u8_packed.group(1),
                        quality="Multi",
                        source="Filemoon",
                        headers={"Referer": "https://filemoon.sx/"}
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting Filemoon: {e}")
            return None
        finally:
            await client.aclose()

    def _unpack(self, packed_js: str) -> str:
        """
        Heuristic unpacker for Dean Edwards Packer.
        """
        args = re.search(r"}\('(.*)',(\d+),(\d+),'(.*?)'\.split\('\|'\)", packed_js)
        if not args:
            return packed_js
            
        _, _, _, dict_str = args.groups()
        dictionary = dict_str.split('|')
        
        # Heuristic: the URL is often a single token in the dictionary
        for item in dictionary:
            if ".m3u8" in item and "http" in item:
                return f'file:"{item}"'
        return packed_js
