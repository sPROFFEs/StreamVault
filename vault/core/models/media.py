from sqlalchemy import Column, Integer, String, Float, Text, Date, ForeignKey, Table, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base

# Tabla de asociación para géneros (Many-to-Many)
media_genres = Table(
    'media_genres', Base.metadata,
    Column('media_id', Integer, ForeignKey('movies.id', ondelete="CASCADE")), # Compartido para movie/series si usamos tabla única, pero aquí separaremos por claridad o usaremos herencia simple
    Column('genre_id', Integer, ForeignKey('genres.id', ondelete="CASCADE"))
)

class Genre(Base):
    __tablename__ = "genres"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=True)

class Movie(Base):
    __tablename__ = "movies"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    original_title = Column(String, nullable=True)
    year = Column(Integer, index=True)
    
    # Metadatos TMDB
    tmdb_id = Column(Integer, unique=True, index=True, nullable=True)
    imdb_id = Column(String, unique=True, nullable=True)
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    rating = Column(Float, default=0.0)
    popularity = Column(Float, default=0.0)
    duration_minutes = Column(Integer, nullable=True)
    
    # Flags
    is_adult = Column(Boolean, default=False)
    added_at = Column(Date, server_default=func.now())
    updated_at = Column(Date, onupdate=func.now())
    
    # Relaciones
    genres = relationship("Genre", secondary=media_genres, backref="movies")
    sources = relationship("StreamSource", back_populates="movie", cascade="all, delete-orphan")

class Series(Base):
    __tablename__ = "series"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    original_title = Column(String, nullable=True)
    first_air_date = Column(Date, nullable=True)
    
    # Metadatos TMDB
    tmdb_id = Column(Integer, unique=True, index=True, nullable=True)
    imdb_id = Column(String, unique=True, nullable=True)
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    rating = Column(Float, default=0.0)
    popularity = Column(Float, default=0.0)
    status = Column(String, nullable=True) # "Ended", "Returning Series"
    
    # Relaciones
    episodes = relationship("Episode", back_populates="series", cascade="all, delete-orphan")
    
    # Como los generos usan 'movies.id' en la tabla de asociacion, necesitamos una tabla de asociacion separada para series
    # O usar una tabla 'media_items' base. Por simplicidad, creamos association table para series aquí:

series_genres = Table(
    'series_genres', Base.metadata,
    Column('series_id', Integer, ForeignKey('series.id', ondelete="CASCADE")),
    Column('genre_id', Integer, ForeignKey('genres.id', ondelete="CASCADE"))
)

# Bind manually properly
Series.genres = relationship("Genre", secondary=series_genres, backref="series")


class Episode(Base):
    __tablename__ = "episodes"
    
    id = Column(Integer, primary_key=True, index=True)
    series_id = Column(Integer, ForeignKey("series.id", ondelete="CASCADE"))
    
    season_number = Column(Integer, nullable=False)
    episode_number = Column(Integer, nullable=False)
    
    title = Column(String, nullable=True)
    overview = Column(Text, nullable=True)
    air_date = Column(Date, nullable=True)
    still_path = Column(String, nullable=True)
    rating = Column(Float, default=0.0)
    
    # Relaciones
    series = relationship("Series", back_populates="episodes")
    sources = relationship("StreamSource", back_populates="episode", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('series_id', 'season_number', 'episode_number', name='unique_episode'),
    )
