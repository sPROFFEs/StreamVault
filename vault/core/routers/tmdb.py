"""
API Routes para TMDB Service
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/tmdb", tags=["TMDB"])

# Instancia global
tmdb_service = TMDBService()


@router.get("/status")
def get_tmdb_status():
    """Verifica estado del servicio TMDB"""
    return {
        "enabled": tmdb_service.enabled,
        "api_key_set": bool(tmdb_service.api_key),
        "message": "OK" if tmdb_service.enabled else "TMDB_API_KEY no configurada",
    }


@router.get("/search/movie")
def search_movie(
    q: str = Query(..., description="Titulo de la pelicula"),
    year: Optional[int] = Query(None, description="Anio de estreno"),
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Busca peliculas en TMDB"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible. Configura TMDB_API_KEY.")

    results = tmdb_service.search_movie(q, year, language=lang)

    for result in results:
        result["poster_url"] = tmdb_service.get_poster_url(result.get("poster_path"))
        result["backdrop_url"] = tmdb_service.get_backdrop_url(result.get("backdrop_path"))

    return results


@router.get("/search/tv")
def search_tv(
    q: str = Query(..., description="Titulo de la serie"),
    year: Optional[int] = Query(None, description="Anio de estreno"),
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Busca series en TMDB"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    results = tmdb_service.search_tv(q, year, language=lang)

    for result in results:
        result["poster_url"] = tmdb_service.get_poster_url(result.get("poster_path"))
        result["backdrop_url"] = tmdb_service.get_backdrop_url(result.get("backdrop_path"))

    return results


@router.get("/movie/{tmdb_id}")
def get_movie_details(
    tmdb_id: int,
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Obtiene detalles completos de una pelicula"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    details = tmdb_service.get_movie_details(tmdb_id, language=lang)
    if not details:
        raise HTTPException(404, "Pelicula no encontrada")

    details["poster_url"] = tmdb_service.get_poster_url(details.get("poster_path"))
    details["backdrop_url"] = tmdb_service.get_backdrop_url(details.get("backdrop_path"))

    return details


@router.get("/tv/{tmdb_id}")
def get_tv_details(
    tmdb_id: int,
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Obtiene detalles completos de una serie"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    details = tmdb_service.get_tv_details(tmdb_id, language=lang)
    if not details:
        raise HTTPException(404, "Serie no encontrada")

    details["poster_url"] = tmdb_service.get_poster_url(details.get("poster_path"))
    details["backdrop_url"] = tmdb_service.get_backdrop_url(details.get("backdrop_path"))

    return details


@router.get("/tv/{tmdb_id}/season/{season_number}")
def get_season_details(
    tmdb_id: int,
    season_number: int,
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Obtiene detalles de una temporada (episodios)"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    details = tmdb_service.get_season_details(tmdb_id, season_number, language=lang)
    if not details:
        raise HTTPException(404, "Temporada no encontrada")

    for episode in details.get("episodes", []):
        episode["still_url"] = tmdb_service.get_poster_url(episode.get("still_path"))

    return details


@router.get("/enrich")
def enrich_media(
    title: str = Query(...),
    year: Optional[int] = None,
    media_type: str = Query("movie", pattern="^(movie|tv)$"),
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Busca y enriquece metadatos para un titulo."""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    enriched = tmdb_service.enrich_media(title, year, media_type, language=lang)

    if not enriched:
        raise HTTPException(404, f"No se encontro '{title}' en TMDB")

    return enriched


@router.get("/trending/{media_type}")
def get_trending(
    media_type: str = "movie",
    time_window: str = Query("week", pattern="^(day|week)$"),
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Obtiene contenido trending"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    results = tmdb_service.get_trending(media_type, time_window, language=lang)

    for result in results:
        result["poster_url"] = tmdb_service.get_poster_url(result.get("poster_path"))

    return results


@router.get("/genres/{media_type}")
def get_genres(
    media_type: str = "movie",
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Obtiene lista de generos"""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")
    return tmdb_service.get_genres(media_type, language=lang)


@router.get("/discover/{media_type}")
def discover_media(
    media_type: str = "movie",
    with_genres: str = Query(None),
    sort_by: str = Query(None),
    vote_count_gte: int = Query(None, alias="vote_count.gte"),
    page: int = Query(1, ge=1, le=500),
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    """Descubre contenido por genero, ranking, etc."""
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")

    results = tmdb_service.discover_media(
        media_type,
        with_genres,
        language=lang,
        sort_by=sort_by,
        vote_count_gte=vote_count_gte,
        page=page,
    )

    for result in results:
        result["poster_url"] = tmdb_service.get_poster_url(result.get("poster_path"))
        result["backdrop_url"] = tmdb_service.get_backdrop_url(result.get("backdrop_path"))

    return {"results": results}


@router.get("/{media_type}/{tmdb_id}/similar")
def similar_media(
    media_type: str,
    tmdb_id: int,
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    if media_type not in ("movie", "tv"):
        raise HTTPException(400, "media_type must be movie|tv")
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")
    results = tmdb_service.get_similar(tmdb_id, media_type, language=lang)
    for r in results:
        r["poster_url"] = tmdb_service.get_poster_url(r.get("poster_path"))
        r["backdrop_url"] = tmdb_service.get_backdrop_url(r.get("backdrop_path"))
    return {"results": results}


@router.get("/{media_type}/{tmdb_id}/recommendations")
def recommendations_media(
    media_type: str,
    tmdb_id: int,
    lang: str = Query("en", pattern="^(en|es|en-US|es-ES)$"),
):
    if media_type not in ("movie", "tv"):
        raise HTTPException(400, "media_type must be movie|tv")
    if not tmdb_service.enabled:
        raise HTTPException(503, "TMDB Service no disponible")
    results = tmdb_service.get_recommendations(tmdb_id, media_type, language=lang)
    for r in results:
        r["poster_url"] = tmdb_service.get_poster_url(r.get("poster_path"))
        r["backdrop_url"] = tmdb_service.get_backdrop_url(r.get("backdrop_path"))
    return {"results": results}
