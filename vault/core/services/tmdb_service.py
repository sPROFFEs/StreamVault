"""
TMDB Service - Integración con The Movie Database API

Obtén tu API key gratuita en: https://www.themoviedb.org/settings/api
Añádela a .env: TMDB_API_KEY=[redacted]
"""
import os
import time
import logging
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# Intentar importar tmdbsimple, fallback si no está
try:
    import tmdbsimple as tmdb
    TMDB_AVAILABLE = True
except ImportError:
    TMDB_AVAILABLE = False
    logger.warning("tmdbsimple no instalado. TMDB Service deshabilitado.")


class TMDBService:
    """Servicio para obtener metadatos de películas/series desde TMDB"""

    # URLs base para imágenes
    IMAGE_BASE = "https://image.tmdb.org/t/p/"
    POSTER_SIZE = "w500"
    BACKDROP_SIZE = "original"

    # TTL cache: keyed by (method, args tuple) → (timestamp, result).
    # TMDB metadata rarely changes, so a 10-minute TTL dramatically reduces
    # outbound API calls and event-loop blocking from the sync requests lib.
    _cache: Dict[Tuple, Tuple[float, Any]] = {}
    _CACHE_TTL = 600.0  # 10 minutes
    _CACHE_MAX = 500    # prune expired entries past this size

    @classmethod
    def _cache_get(cls, key: Tuple):
        entry = cls._cache.get(key)
        if entry and (time.monotonic() - entry[0]) < cls._CACHE_TTL:
            return entry[1]
        return None

    @classmethod
    def _cache_set(cls, key: Tuple, value: Any) -> Any:
        cls._cache[key] = (time.monotonic(), value)
        if len(cls._cache) > cls._CACHE_MAX:
            now = time.monotonic()
            expired = [k for k, (ts, _) in cls._cache.items()
                       if (now - ts) >= cls._CACHE_TTL]
            for k in expired:
                del cls._cache[k]
        return value

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("TMDB_API_KEY")
        self.enabled = False
        self.config = None

        if not TMDB_AVAILABLE:
            logger.warning("TMDB no disponible (falta tmdbsimple)")
            return

        if not self.api_key:
            logger.warning("TMDB_API_KEY no configurada. Servicio deshabilitado.")
            return

        tmdb.API_KEY = self.api_key
        self.enabled = True
        self._load_config()

    @staticmethod
    def _normalize_language(language: Optional[str]) -> str:
        value = (language or "en").strip().lower()
        if value.startswith("es"):
            return "es-ES"
        return "en-US"

    def _load_config(self):
        """Carga configuración de TMDB (tamaños de imagen, etc.)"""
        try:
            config = tmdb.Configuration()
            self.config = config.info()
            logger.info("TMDB Service inicializado correctamente")
        except Exception as e:
            logger.error(f"Error cargando config TMDB: {e}")
            self.enabled = False

    def search_movie(self, query: str, year: int = None, language: str = "en-US") -> List[Dict]:
        """Busca películas por título y opcionalmente año"""
        if not self.enabled:
            return []
        cache_key = ("search_movie", query, year, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            search = tmdb.Search()
            kwargs = {"query": query, "language": self._normalize_language(language)}
            if year:
                kwargs["primary_release_year"] = int(year)
            search.movie(**kwargs)
            results = list(search.results or [])

            # Fallback if specific year search had no results
            if not results and year:
                search.movie(query=query, language=self._normalize_language(language))
                results = list(search.results or [])

            # Sort closest to target year if specified
            if year and results:
                def year_distance(item):
                    date = item.get("release_date") or ""
                    if len(date) >= 4 and date[:4].isdigit():
                        return abs(int(date[:4]) - int(year))
                    return 999
                results.sort(key=year_distance)

            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error buscando película '{query}': {e}")
            return []

    def search_tv(self, query: str, year: int = None, language: str = "en-US") -> List[Dict]:
        """Busca series por título y opcionalmente año"""
        if not self.enabled:
            return []
        cache_key = ("search_tv", query, year, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            search = tmdb.Search()
            kwargs = {"query": query, "language": self._normalize_language(language)}
            if year:
                kwargs["first_air_date_year"] = int(year)
            search.tv(**kwargs)
            results = list(search.results or [])

            if not results and year:
                search.tv(query=query, language=self._normalize_language(language))
                results = list(search.results or [])

            if year and results:
                def year_distance(item):
                    date = item.get("first_air_date") or ""
                    if len(date) >= 4 and date[:4].isdigit():
                        return abs(int(date[:4]) - int(year))
                    return 999
                results.sort(key=year_distance)

            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error buscando serie '{query}': {e}")
            return []

    def get_movie_details(self, tmdb_id: int, language: str = "en-US") -> Optional[Dict]:
        """Obtiene detalles completos de una película"""
        if not self.enabled:
            return None
        cache_key = ("movie_details", tmdb_id, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            movie = tmdb.Movies(tmdb_id)
            details = movie.info(language=self._normalize_language(language), append_to_response='credits,videos')
            return self._cache_set(cache_key, details)
        except Exception as e:
            logger.error(f"Error obteniendo película {tmdb_id}: {e}")
            return None

    def get_season_details(self, tmdb_id: int, season_number: int, language: str = "en-US") -> Optional[Dict]:
        """Obtiene detalles de una temporada (episodios)"""
        if not self.enabled:
            return None
        cache_key = ("season_details", tmdb_id, season_number, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            season = tmdb.TV_Seasons(tmdb_id, season_number)
            details = season.info(language=self._normalize_language(language))
            return self._cache_set(cache_key, details)
        except Exception as e:
            logger.error(f"Error obteniendo temporada {season_number} de {tmdb_id}: {e}")
            return None

    def get_tv_details(self, tmdb_id: int, language: str = "en-US") -> Optional[Dict]:
        """Obtiene detalles completos de una serie"""
        if not self.enabled:
            return None
        cache_key = ("tv_details", tmdb_id, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            tv = tmdb.TV(tmdb_id)
            details = tv.info(language=self._normalize_language(language), append_to_response='credits')
            return self._cache_set(cache_key, details)
        except Exception as e:
            logger.error(f"Error obteniendo serie {tmdb_id}: {e}")
            return None

    def get_poster_url(self, poster_path: str, size: str = None) -> Optional[str]:
        """Construye URL completa del poster"""
        if not poster_path:
            return None
        size = size or self.POSTER_SIZE
        return f"{self.IMAGE_BASE}{size}{poster_path}"

    def get_backdrop_url(self, backdrop_path: str) -> Optional[str]:
        """Construye URL completa del backdrop"""
        if not backdrop_path:
            return None
        return f"{self.IMAGE_BASE}{self.BACKDROP_SIZE}{backdrop_path}"

    def enrich_media(
        self,
        title: str,
        year: int = None,
        media_type: str = 'movie',
        language: str = "en-US",
    ) -> Optional[Dict[str, Any]]:
        """
        Busca y devuelve metadatos enriquecidos para una película/serie.

        Returns:
            Dict con tmdb_id, title, overview, poster_url, backdrop_url, rating, genres, etc.
        """
        if not self.enabled:
            return None

        # Buscar
        if media_type == 'movie':
            results = self.search_movie(title, year, language=language)
        else:
            results = self.search_tv(title, language=language)

        if not results:
            logger.info(f"No se encontraron resultados para '{title}'")
            return None

        best_match = results[0]
        tmdb_id = best_match['id']

        # Obtener detalles completos
        if media_type == 'movie':
            details = self.get_movie_details(tmdb_id, language=language)
        else:
            details = self.get_tv_details(tmdb_id, language=language)

        if not details:
            # Usar datos básicos del search
            details = best_match

        return {
            'tmdb_id': tmdb_id,
            'title': details.get('title') or details.get('name'),
            'original_title': details.get('original_title') or details.get('original_name'),
            'overview': details.get('overview'),
            'poster_url': self.get_poster_url(details.get('poster_path')),
            'backdrop_url': self.get_backdrop_url(details.get('backdrop_path')),
            'rating': details.get('vote_average'),
            'popularity': details.get('popularity'),
            'release_date': details.get('release_date') or details.get('first_air_date'),
            'genres': [g['name'] for g in details.get('genres', [])],
            'runtime': details.get('runtime'),  # Solo películas
            'status': details.get('status'),  # Solo series
        }

    def get_trending(self, media_type: str = 'movie', time_window: str = 'week', language: str = "en-US") -> List[Dict]:
        """Obtiene contenido trending"""
        if not self.enabled:
            return []
        cache_key = ("trending", media_type, time_window, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            trending = tmdb.Trending(media_type=media_type, time_window=time_window)
            results = trending.info(language=self._normalize_language(language)).get('results', [])
            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error obteniendo trending: {e}")
            return []

    def get_genres(self, media_type: str = 'movie', language: str = "en-US") -> List[Dict]:
        """Obtiene lista de géneros"""
        if not self.enabled:
            return []
        cache_key = ("genres", media_type, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            genres = tmdb.Genres()
            if media_type == 'movie':
                results = genres.movie_list(language=self._normalize_language(language)).get('genres', [])
            else:
                results = genres.tv_list(language=self._normalize_language(language)).get('genres', [])
            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error obteniendo géneros: {e}")
            return []

    def discover_media(
        self,
        media_type: str = 'movie',
        with_genres: str = None,
        language: str = "en-US",
        sort_by: str = None,
        vote_count_gte: int = None,
        page: int = 1,
    ) -> List[Dict]:
        """Descubre contenido por filtros (géneros, ranking, etc.)"""
        if not self.enabled:
            return []
        cache_key = ("discover", media_type, with_genres, self._normalize_language(language), sort_by, vote_count_gte, page)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            discover = tmdb.Discover()
            kwargs = {"language": self._normalize_language(language), "page": page}
            if with_genres:
                kwargs["with_genres"] = with_genres
            if sort_by:
                kwargs["sort_by"] = sort_by
            if vote_count_gte is not None:
                kwargs["vote_count.gte"] = vote_count_gte
            if media_type == 'movie':
                results = discover.movie(**kwargs).get('results', [])
            else:
                results = discover.tv(**kwargs).get('results', [])
            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error descubriendo media: {e}")
            return []

    def get_similar(self, tmdb_id: int, media_type: str = 'movie', language: str = "en-US") -> List[Dict]:
        """Similar movies / TV series."""
        if not self.enabled:
            return []
        cache_key = ("similar", tmdb_id, media_type, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            lang = self._normalize_language(language)
            if media_type == 'tv':
                results = tmdb.TV(tmdb_id).similar(language=lang).get('results', [])
            else:
                results = tmdb.Movies(tmdb_id).similar(language=lang).get('results', [])
            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error similar: {e}")
            return []

    def get_recommendations(self, tmdb_id: int, media_type: str = 'movie', language: str = "en-US") -> List[Dict]:
        """Recommendations (alternate to similar)."""
        if not self.enabled:
            return []
        cache_key = ("recommendations", tmdb_id, media_type, self._normalize_language(language))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            lang = self._normalize_language(language)
            if media_type == 'tv':
                results = tmdb.TV(tmdb_id).recommendations(language=lang).get('results', [])
            else:
                results = tmdb.Movies(tmdb_id).recommendations(language=lang).get('results', [])
            return self._cache_set(cache_key, results)
        except Exception as e:
            logger.error(f"Error recommendations: {e}")
            return []


def _load_file_tmdb_key() -> str:
    """Load TMDB API key from the admin-managed file if it exists."""
    try:
        key_path = os.path.join(os.getenv("DATA_ROOT", "/saas-data"), "tmdb_key.txt")
        if os.path.isfile(key_path):
            return open(key_path, encoding="utf-8").read().strip()
    except OSError:
        pass
    return ""


# Instantiate singleton: file key takes precedence over env.
_file_key = _load_file_tmdb_key()
if _file_key:
    os.environ["TMDB_API_KEY"] = _file_key

tmdb_service = TMDBService()
