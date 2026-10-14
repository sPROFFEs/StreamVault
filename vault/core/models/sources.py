from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base

class StreamSource(Base):
    """Cada servidor/opción de streaming para un contenido"""
    __tablename__ = "stream_sources"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Puede pertenecer a una película O a un episodio
    movie_id = Column(Integer, ForeignKey("movies.id", ondelete="CASCADE"), nullable=True)
    episode_id = Column(Integer, ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True)
    
    # Información del stream
    source_provider = Column(String)  # "Cuevana3", "Fembed", "YouTube"
    server_name = Column(String)      # "Streamtape", "Gamma", "Piped"
    stream_url = Column(String)       # URL directa o embed code
    
    quality = Column(String, nullable=True)          # "1080p", "720p", "480p"
    language = Column(String, nullable=True)         # "Spanish", "English", "Latino"
    subtitle_lang = Column(String, nullable=True)    # "Spanish", null si doblado (None = audio en idioma)
    
    # Estado
    is_working = Column(Boolean, default=True)
    last_checked = Column(DateTime, default=func.now())
    fail_count = Column(Integer, default=0)
    
    # Ruta local si está descargado (Cache)
    local_path = Column(String, nullable=True)
    download_status = Column(String, default="pending")  # pending, downloading, complete, failed
    
    # Relaciones
    movie = relationship("Movie", back_populates="sources")
    episode = relationship("Episode", back_populates="sources")
