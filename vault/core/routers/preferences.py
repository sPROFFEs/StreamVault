"""
User preferences router — UI/UX settings (accent, font size, home layout,
age rating policy, default source, subtitle keys) persisted in the
`user_preferences.ui_prefs` JSON column.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import auth
import database
from config import settings
from providers.embed_registry import normalize_source_preferences

router = APIRouter(prefix="/api/preferences", tags=["Preferences"])


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    return auth.get_current_user(request, db)


DEFAULT_UI_PREFS: Dict[str, Any] = {
    "accent": "red",
    "font_size": "normal",
    "compact_mode": False,
    "no_anim": False,
    "home_view": "carousel",
    "home_order": [
        "continue",
        "similar",
        "trending_movies",
        "trending_tv",
        "top_rated",
    ],
    "home_visibility": {
        "continue": True,
        "similar": True,
        "trending_movies": True,
        "trending_tv": True,
        "top_rated": True,
    },
    "age_limit": "none",
    "rating_country": "US",
    "default_source": "videasy",
    "source_order": ["videasy", "vidlink", "vidfast", "vidrock"],
    "torrent_sources": False,
    "auto_source_failover": True,
    "library_sort": "manual",
    "history_enabled": True,
    "trailer_invidious": "inv.nadeko.net",
    "notif_download_done": True,
    "notif_new_episodes": True,
    "aniskip_mode": "off",
    "subtitle_lang": "en",
}


def _load_prefs(prefs_row) -> Dict[str, Any]:
    base = dict(DEFAULT_UI_PREFS)
    if prefs_row and prefs_row.ui_prefs:
        try:
            base.update(json.loads(prefs_row.ui_prefs) or {})
        except (json.JSONDecodeError, TypeError):
            pass
    preferred, source_order = normalize_source_preferences(
        base.get("default_source"),
        base.get("source_order"),
    )
    base["default_source"] = preferred
    base["source_order"] = source_order
    return base


def _save_prefs(db: Session, user: database.User, data: Dict[str, Any]) -> None:
    prefs = user.preferences
    if prefs is None:
        prefs = database.UserPreferences(user_id=user.id)
        db.add(prefs)
        db.flush()
    prefs.ui_prefs = json.dumps(data)
    db.commit()


class PreferencesUpdate(BaseModel):
    accent: str | None = None
    font_size: str | None = Field(None, pattern="^(sm|normal|lg)$")
    compact_mode: bool | None = None
    no_anim: bool | None = None
    home_view: str | None = Field(None, pattern="^(carousel|list)$")
    home_order: list[str] | None = None
    home_visibility: dict[str, bool] | None = None
    age_limit: str | None = None
    rating_country: str | None = None
    default_source: str | None = None
    source_order: list[str] | None = None
    torrent_sources: bool | None = None
    auto_source_failover: bool | None = None
    library_sort: str | None = None
    history_enabled: bool | None = None
    trailer_invidious: str | None = None
    notif_download_done: bool | None = None
    notif_new_episodes: bool | None = None
    aniskip_mode: str | None = Field(None, pattern="^(off|auto|manual)$")
    subtitle_lang: str | None = Field(None, pattern="^(en|es|pt|fr|de)$")


@router.get("")
async def read_prefs(user: database.User = Depends(get_current_user)) -> Dict[str, Any]:
    return _load_prefs(user.preferences)


@router.put("")
async def update_prefs(
    payload: PreferencesUpdate,
    user: database.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    current = _load_prefs(user.preferences)
    diff = payload.model_dump(exclude_unset=True)
    current.update(diff)
    # refresh attached user to current session
    user = db.merge(user)
    _save_prefs(db, user, current)
    return current


class SubtitleKeysPayload(BaseModel):
    subdl_api_key: str | None = None
    wyzie_api_key: str | None = None
    opensubtitles_api_key: str | None = None


@router.get("/subtitles")
async def read_subtitle_keys(user: database.User = Depends(get_current_user)) -> Dict[str, Any]:
    prefs = user.preferences
    return {
        "subdl_api_key_set": bool(prefs and prefs.subdl_api_key),
        "wyzie_api_key_set": bool(prefs and prefs.wyzie_api_key),
        "opensubtitles_api_key_set": bool(
            (prefs and prefs.opensubtitles_api_key) or settings.OPENSUBTITLES_API_KEY
        ),
        "opensubtitles_api_key_source": (
            "user" if prefs and prefs.opensubtitles_api_key
            else "environment" if settings.OPENSUBTITLES_API_KEY else None
        ),
    }


@router.put("/subtitles")
async def update_subtitle_keys(
    payload: SubtitleKeysPayload,
    user: database.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    user = db.merge(user)
    prefs = user.preferences
    if prefs is None:
        prefs = database.UserPreferences(user_id=user.id)
        db.add(prefs)
        db.flush()
    if payload.subdl_api_key is not None:
        prefs.subdl_api_key = payload.subdl_api_key or None
    if payload.wyzie_api_key is not None:
        prefs.wyzie_api_key = payload.wyzie_api_key or None
    if payload.opensubtitles_api_key is not None:
        prefs.opensubtitles_api_key = payload.opensubtitles_api_key or None
    db.commit()
    return {
        "subdl_api_key_set": bool(prefs.subdl_api_key),
        "wyzie_api_key_set": bool(prefs.wyzie_api_key),
        "opensubtitles_api_key_set": bool(
            prefs.opensubtitles_api_key or settings.OPENSUBTITLES_API_KEY
        ),
        "opensubtitles_api_key_source": (
            "user" if prefs.opensubtitles_api_key
            else "environment" if settings.OPENSUBTITLES_API_KEY else None
        ),
    }
