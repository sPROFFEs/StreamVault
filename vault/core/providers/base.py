from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class StreamResult(BaseModel):
    """Resultado final de una resolución de stream"""
    url: str                        # URL del .m3u8/.mp4 o magnet link
    quality: str = "auto"           # 1080p, 720p, 4K, etc.
    headers: Dict = Field(default_factory=dict)  # Headers necesarios (Referer, User-Agent)
    source: str                     # VidSrc, Torrentio, etc.
    type: str = "hls"               # hls, mp4, dash, embed, torrent
    # Campos exclusivos de streams torrent
    info_hash: Optional[str] = None  # Hash del torrent (para WebTorrent)
    file_idx: Optional[int] = None   # Índice del archivo dentro del torrent
    size_bytes: Optional[int] = None # Tamaño total en bytes
    seeders: Optional[int] = None    # Número de seeders activos
    # Metadata used by the browser player for preference, failover and downloads.
    provider_id: Optional[str] = None
    origin: Optional[str] = None
    failure_event: Optional[str] = None
    failure_origin: Optional[str] = None
    download_mode: Optional[str] = None
    title: Optional[str] = None
    health: Optional[str] = None
    # Same-origin endpoints; peer bearer credentials never reach the browser.
    playback_url: Optional[str] = None
    metadata_url: Optional[str] = None
    peer_id: Optional[int] = None
    media_id: Optional[str] = None

class SourceProvider(ABC):
    """Clase base para proveedores de sources (VidSrc, 2Embed, etc)"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @property
    @abstractmethod
    def base_url(self) -> str:
        pass
        
    @abstractmethod
    async def get_streams(self, tmdb_id: str, season: int = None, episode: int = None) -> List[Dict]:
        """
        Debe devolver una lista de embeds/iframes encontrados.
        Return format: [{"server": "Filemoon", "url": "https://...", "quality": "1080p"}]
        """
        pass

class Extractor(ABC):
    """Clase base para extractores de servidores finales (Filemoon, VidCloud, etc)"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def domains(self) -> List[str]:
        """Dominios que maneja este extractor"""
        pass
        
    @abstractmethod
    async def extract(self, url: str) -> Optional[StreamResult]:
        """Desencripta/extrae el link directo del embed"""
        pass
