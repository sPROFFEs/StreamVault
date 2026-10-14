"""
StreamVault Local Media Router

Handles local TMDB database endpoints:
- Browse
- Search
- Media details
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
import logging
import httpx
import asyncio
from urllib.parse import urljoin, urlsplit, urlencode

import auth
from security import is_safe_external_url
from shared_templates import templates
from models.media import Movie, Series, Episode
from models.sources import StreamSource
from models.base import SessionLocal
from services.streaming_response import ClosingStreamingResponse
from services.hls_proxy import MAX_MANIFEST_BYTES, rewrite_hls_manifest

logger = logging.getLogger(__name__)

router = APIRouter()


def get_current_user_safe(db: Session = None, request: Request = None):
    """Get current user or return None - simplified version"""
    try:
        if request:
            return auth.get_current_user(request, db or SessionLocal())
    except:
        pass
    return None


# --- PAGE ROUTES ---

@router.get("/player/{media_type}/{media_id}")
async def player_page(request: Request, media_type: str, media_id: int):
    """Resolve legacy local IDs to the retained TMDB detail/player route."""
    if media_type not in {"movie", "series", "tv"}:
        raise HTTPException(404, "Media not found")
    db = SessionLocal()
    try:
        model = Movie if media_type == "movie" else Series
        media = db.query(model).filter(model.id == media_id).first()
        if not media or not media.tmdb_id:
            raise HTTPException(404, "Media metadata not found")
        kind = "movie" if media_type == "movie" else "tv"
        return RedirectResponse(f"/media/{media.tmdb_id}?type={kind}", status_code=303)
    finally:
        db.close()


@router.get("/admin/workflows")
async def admin_workflows_page(request: Request):
    """Admin workflows management page"""
    # Placeholder for workflows logic if needed
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/catalog-local")
async def browse_page(request: Request):
    """Serve the indexed local library using the existing catalog template."""
    user = request.state.user
    return templates.TemplateResponse(request, "catalog.html", {
        "request": request,
        "username": user.username,
        "is_admin": user.is_admin,
    })


@router.get("/search")
async def search_page(request: Request, q: str = ""):
    """Keep existing search links on the shared browse/search interface."""
    query = "?" + urlencode({"q": q}) if q else ""
    return RedirectResponse("/browse" + query, status_code=303)


# --- LOCAL TMDB DATABASE API ENDPOINTS ---

@router.get("/api/local/search")
async def api_local_search(q: str = "", page: int = 1, media_type: str = "all",
                           request: Request = None):
    """
    Search in local TMDB database (movies and series)
    """
    db = SessionLocal()
    try:
        if not q or len(q) < 2:
            return {"items": [], "total": 0}
        
        search_pattern = f"%{q}%"
        limit = 20
        offset = (page - 1) * limit
        
        items = []
        total = 0
        
        # Search movies
        if media_type in ["all", "movie"]:
            movie_query = db.query(Movie).filter(
                or_(
                    Movie.title.ilike(search_pattern),
                    Movie.original_title.ilike(search_pattern)
                )
            )
            total += movie_query.count()
            
            movies = movie_query.limit(limit).offset(offset).all()
            for movie in movies:
                items.append({
                    "id": movie.id,
                    "type": "movie",
                    "title": movie.title,
                    "original_title": movie.original_title,
                    "year": movie.year,
                    "tmdb_id": movie.tmdb_id,
                    "poster_path": movie.poster_path,
                    "backdrop_path": movie.backdrop_path,
                    "overview": movie.overview,
                    "rating": movie.rating,
                    "popularity": movie.popularity,
                    "duration": movie.duration_minutes
                })
        
        # Search series
        if media_type in ["all", "series"]:
            series_query = db.query(Series).filter(
                or_(
                    Series.title.ilike(search_pattern),
                    Series.original_title.ilike(search_pattern)
                )
            )
            total += series_query.count()
            
            series_list = series_query.limit(limit).offset(offset).all()
            for series in series_list:
                items.append({
                    "id": series.id,
                    "type": "series",
                    "title": series.title,
                    "original_title": series.original_title,
                    "first_air_date": series.first_air_date.isoformat() if series.first_air_date else None,
                    "tmdb_id": series.tmdb_id,
                    "poster_path": series.poster_path,
                    "backdrop_path": series.backdrop_path,
                    "overview": series.overview,
                    "rating": series.rating,
                    "popularity": series.popularity,
                    "status": series.status
                })
        
        return {"items": items, "total": total, "page": page}
    
    finally:
        db.close()


@router.get("/api/local/movie/{movie_id}")
async def api_local_movie_details(movie_id: int, request: Request = None):
    """Get local movie details"""
    db = SessionLocal()
    
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        return {
            "id": movie.id,
            "type": "movie",
            "title": movie.title,
            "original_title": movie.original_title,
            "year": movie.year,
            "tmdb_id": movie.tmdb_id,
            "imdb_id": movie.imdb_id,
            "poster_path": movie.poster_path,
            "backdrop_path": movie.backdrop_path,
            "overview": movie.overview,
            "rating": movie.rating,
            "popularity": movie.popularity,
            "duration": movie.duration_minutes,
            "is_adult": movie.is_adult,
            "sources": [{"id": s.id, "provider": s.source_provider, "server": s.server_name} 
                       for s in movie.sources]
        }
    finally:
        db.close()


@router.get("/api/local/series/{series_id}")
async def api_local_series_details(series_id: int, request: Request = None):
    """Get local series details"""
    db = SessionLocal()
    
    try:
        series = db.query(Series).filter(Series.id == series_id).first()
        
        if not series:
            raise HTTPException(status_code=404, detail="Series not found")
        
        # Group episodes by season
        seasons = {}
        for episode in series.episodes:
            season_num = episode.season_number
            if season_num not in seasons:
                seasons[season_num] = []
            
            seasons[season_num].append({
                "id": episode.id,
                "episode_number": episode.episode_number,
                "title": episode.title,
                "overview": episode.overview,
                "air_date": episode.air_date.isoformat() if episode.air_date else None,
                "still_path": episode.still_path,
                "rating": episode.rating
            })
        
        return {
            "id": series.id,
            "type": "series",
            "title": series.title,
            "original_title": series.original_title,
            "first_air_date": series.first_air_date.isoformat() if series.first_air_date else None,
            "tmdb_id": series.tmdb_id,
            "imdb_id": series.imdb_id,
            "poster_path": series.poster_path,
            "backdrop_path": series.backdrop_path,
            "overview": series.overview,
            "rating": series.rating,
            "popularity": series.popularity,
            "status": series.status,
            "seasons": seasons
        }
    finally:
        db.close()


@router.get("/api/local/browse")
async def api_local_browse(sort_by: str = "popularity", order: str = "desc", 
                           page: int = 1, limit: int = 20,
                           request: Request = None):
    """Browse all local content with sorting"""
    db = SessionLocal()
    
    try:
        offset = (page - 1) * limit
        items = []
        
        # Get movies
        movie_query = db.query(Movie)
        
        if sort_by == "rating":
            movie_query = movie_query.order_by(Movie.rating.desc() if order == "desc" else Movie.rating.asc())
        elif sort_by == "popularity":
            movie_query = movie_query.order_by(Movie.popularity.desc() if order == "desc" else Movie.popularity.asc())
        elif sort_by == "year":
            movie_query = movie_query.order_by(Movie.year.desc() if order == "desc" else Movie.year.asc())
        else:
            movie_query = movie_query.order_by(Movie.added_at.desc())
        
        movies = movie_query.limit(limit).offset(offset).all()
        
        for movie in movies:
            items.append({
                "id": movie.id,
                "type": "movie",
                "title": movie.title,
                "original_title": movie.original_title,
                "year": movie.year,
                "tmdb_id": movie.tmdb_id,
                "poster_path": movie.poster_path,
                "backdrop_path": movie.backdrop_path,
                "overview": movie.overview,
                "rating": movie.rating,
                "popularity": movie.popularity,
                "duration": movie.duration_minutes
            })
        
        # Get series
        series_query = db.query(Series)
        
        if sort_by == "rating":
            series_query = series_query.order_by(Series.rating.desc() if order == "desc" else Series.rating.asc())
        elif sort_by == "popularity":
            series_query = series_query.order_by(Series.popularity.desc() if order == "desc" else Series.popularity.asc())
        else:
            series_query = series_query.order_by(Series.tmdb_id.desc())
        
        series_list = series_query.limit(limit).offset(offset).all()
        
        for series in series_list:
            items.append({
                "id": series.id,
                "type": "series",
                "title": series.title,
                "original_title": series.original_title,
                "first_air_date": series.first_air_date.isoformat() if series.first_air_date else None,
                "tmdb_id": series.tmdb_id,
                "poster_path": series.poster_path,
                "backdrop_path": series.backdrop_path,
                "overview": series.overview,
                "rating": series.rating,
                "popularity": series.popularity,
                "status": series.status
            })
        
        # Get totals
        total_movies = db.query(Movie).count()
        total_series = db.query(Series).count()
        
        return {
            "items": items,
            "total": total_movies + total_series,
            "page": page,
            "stats": {
                "total_movies": total_movies,
                "total_series": total_series
            }
        }
    finally:
        db.close()


# --- STREAMING ENDPOINTS ---

@router.get("/api/local/stream/movie/{movie_id}")
async def stream_movie(movie_id: int):
    """Get streaming sources for a movie"""
    db = SessionLocal()
    try:
        from fastapi.responses import JSONResponse
        
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            return {"error": "Película no encontrada"}
        
        sources = db.query(StreamSource).filter(StreamSource.movie_id == movie_id).all()
        
        if not sources:
            # Retornar un placeholder si no hay fuentes
            return {
                "title": movie.title,
                "sources": [],
                "quality": "none",
                "message": "No hay fuentes disponibles para esta película"
            }
        
        # Retornar primera fuente disponible (en futuro: seleccionar por calidad)
        source = sources[0]
        
        if source.stream_url:
            return JSONResponse({
                "url": source.stream_url,
                "title": movie.title,
                "quality": source.quality or "720p",
                "source": source.server_name
            })
        
        return {"error": "No hay URL válida"}
    finally:
        db.close()


@router.get("/api/local/stream/series/{series_id}")
async def stream_series(series_id: int, season: int = 1, episode: int = 1):
    """Get streaming sources for a series episode"""
    db = SessionLocal()
    try:
        from fastapi.responses import JSONResponse
        
        series = db.query(Series).filter(Series.id == series_id).first()
        if not series:
            return {"error": "Serie no encontrada"}
        
        ep = db.query(Episode).filter(
            Episode.series_id == series_id,
            Episode.season_number == season,
            Episode.episode_number == episode
        ).first()
        
        if not ep:
            return {"error": f"Episodio S{season}E{episode} no encontrado"}
        
        sources = db.query(StreamSource).filter(StreamSource.episode_id == ep.id).all()
        
        if not sources:
            return {"error": "No hay fuentes para este episodio"}
        
        source = sources[0]
        
        if source.stream_url:
            return JSONResponse({
                "url": source.stream_url,
                "title": f"{series.title} - S{season}E{episode}",
                "quality": source.quality or "720p",
                "source": source.server_name
            })
        
        return {"error": "No hay URL válida"}
    finally:
        db.close()


@router.get("/api/local/sources/{media_type}/{media_id}")
async def get_media_sources(media_type: str, media_id: int):
    """Get all available sources for a media item"""
    db = SessionLocal()
    try:
        from sqlalchemy import or_
        
        sources = []
        
        if media_type == "movie":
            sources = db.query(StreamSource).filter(StreamSource.movie_id == media_id).all()
        elif media_type == "series":
            episodes = db.query(Episode).filter(Episode.series_id == media_id).all()
            episode_ids = [e.id for e in episodes]
            if episode_ids:
                sources = db.query(StreamSource).filter(StreamSource.episode_id.in_(episode_ids)).all()
        
        return {
            "media_id": media_id,
            "media_type": media_type,
            "sources": [
                {
                    "id": s.id,
                    "name": s.server_name,  # CORREGIDO: s.name -> s.server_name
                    "type": s.source_provider, # CORREGIDO: s.type -> s.source_provider
                    "quality": s.quality,
                    "url": s.stream_url[:50] + "..." if len(s.stream_url or "") > 50 else s.stream_url
                }
                for s in sources
            ],
            "total": len(sources)
        }
    finally:
        db.close()


# --- PROXY ENDPOINT FOR CORS ISSUES ---

@router.get("/api/local/proxy-stream")
async def proxy_stream(url: str, request: Request, playlist: bool = False):
    """
    Proxy endpoint to bypass CORS issues with external video URLs.
    Usage: GET /api/local/proxy-stream?url=https://example.com/video.mp4
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter required")
    
    client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    response = None
    handed_off = False
    forwarded = {"accept-encoding": "identity", "user-agent": "Mozilla/5.0 StreamVault/1.0"}
    for name in ("range", "if-range"):
        if name in request.headers:
            forwarded[name] = request.headers[name]
    async def cleanup():
        try:
            if response is not None:
                await response.aclose()
        finally:
            await client.aclose()

    try:
        current_url = url
        for _ in range(6):
            # DNS checks run off the event loop and apply to EVERY redirect,
            # not just the first URL. Never forward session cookies upstream.
            if not await asyncio.to_thread(is_safe_external_url, current_url):
                raise HTTPException(400, "Invalid or prohibited URL target")
            response = await client.send(client.build_request("GET", current_url, headers=forwarded), stream=True, follow_redirects=False)
            if response.status_code not in (301, 302, 303, 307, 308) or "location" not in response.headers:
                break
            current_url = urljoin(str(response.url), response.headers["location"])
            await response.aclose()
        else:
            raise HTTPException(502, "Too many upstream redirects")

        content_type = response.headers.get("content-type", "").lower()
        is_playlist = playlist or urlsplit(url).path.lower().endswith((".m3u8", ".m3u")) or "mpegurl" in content_type
        headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-store"}
        if is_playlist and response.status_code == 200:
            # Only small manifests are buffered. Video segments and long-lived
            # streams keep their Range support and bounded streaming below.
            chunks, size = [], 0
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                size += len(chunk)
                if size > MAX_MANIFEST_BYTES:
                    raise HTTPException(502, "Upstream HLS playlist is too large")
                chunks.append(chunk)
            content = b"".join(chunks).decode("utf-8-sig", errors="replace")
            if not content.lstrip().startswith("#EXTM3U"):
                raise HTTPException(502, "Upstream did not return an HLS playlist")
            try:
                rewritten = rewrite_hls_manifest(content, str(response.url))
            except ValueError as exc:
                raise HTTPException(502, "Invalid upstream HLS resource") from exc
            return Response(rewritten, media_type="application/vnd.apple.mpegurl", headers=headers)

        async def iterate_file():
            async for chunk in response.aiter_raw(chunk_size=64 * 1024):
                yield chunk

        headers.update({
            name: response.headers[name] for name in (
                "content-type", "content-length", "content-range", "accept-ranges",
                "content-encoding", "etag", "last-modified",
            ) if name in response.headers
        })
        result = ClosingStreamingResponse(iterate_file(), cleanup=cleanup, status_code=response.status_code,
                                         media_type=response.headers.get("content-type", "video/mp4"), headers=headers)
        handed_off = True
        return result
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Upstream stream unavailable") from exc
    finally:
        if not handed_off:
            await cleanup()
