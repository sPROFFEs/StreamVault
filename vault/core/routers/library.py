from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

import auth
import database
from services.user_library_service import user_library_service

router = APIRouter(prefix="/api/library", tags=["Library"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    return auth.get_current_user(request, db)


class FavoritePayload(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    poster_path: str | None = None
    backdrop_path: str | None = None
    release_date: str | None = None
    rating: float | None = None


class HistoryPayload(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    poster_path: str | None = None
    backdrop_path: str | None = None
    release_date: str | None = None
    rating: float | None = None
    season_number: int | None = None
    episode_number: int | None = None
    progress_seconds: float = 0
    duration_seconds: float = 0
    completed: bool = False


@router.get("/favorites")
async def get_favorites(user: database.User = Depends(get_current_user)):
    items = user_library_service.list_favorites(user.username)
    return {"items": items, "total": len(items)}


@router.get("/favorites/{media_type}/{tmdb_id}")
async def favorite_status(
    media_type: str,
    tmdb_id: int,
    user: database.User = Depends(get_current_user),
):
    return {
        "is_favorite": user_library_service.is_favorite(user.username, tmdb_id, media_type)
    }


@router.post("/favorites")
async def add_favorite(
    payload: FavoritePayload,
    user: database.User = Depends(get_current_user),
):
    if payload.media_type not in {"movie", "tv"}:
        raise HTTPException(status_code=400, detail="media_type invalido")

    user_library_service.upsert_favorite(user.username, payload.model_dump())
    return {"success": True}


@router.delete("/favorites/{media_type}/{tmdb_id}")
async def delete_favorite(
    media_type: str,
    tmdb_id: int,
    user: database.User = Depends(get_current_user),
):
    removed = user_library_service.remove_favorite(user.username, tmdb_id, media_type)
    return {"success": removed}


@router.get("/history")
async def get_continue_watching(
    limit: int = 12,
    user: database.User = Depends(get_current_user),
):
    items = user_library_service.list_continue_watching(user.username, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/history/progress/{media_type}/{tmdb_id}")
async def get_history_progress(
    media_type: str,
    tmdb_id: int,
    season_number: int | None = None,
    episode_number: int | None = None,
    user: database.User = Depends(get_current_user),
):
    item = user_library_service.get_progress(
        user.username,
        tmdb_id,
        media_type,
        season_number=season_number,
        episode_number=episode_number,
    )
    return {"item": item}


@router.post("/history")
async def upsert_history(
    payload: HistoryPayload,
    user: database.User = Depends(get_current_user),
):
    if payload.media_type not in {"movie", "tv"}:
        raise HTTPException(status_code=400, detail="media_type invalido")

    user_library_service.upsert_progress(user.username, payload.model_dump())
    return {"success": True}
