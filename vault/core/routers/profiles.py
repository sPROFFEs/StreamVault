"""Opt-in community profiles. Authentication never implies public history."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import database
from routers.library import get_db, get_current_user
from services.user_library_service import user_library_service
from shared_templates import templates

router = APIRouter()


class ProfilePayload(BaseModel):
    display_name: str | None = Field(None, max_length=80)
    bio: str | None = Field(None, max_length=500)
    visibility: str | None = Field(None, pattern="^(private|local|federated)$")
    show_metrics: bool | None = None


def serialize_profile(user, profile, *, own=False):
    result = {"id": user.id, "username": user.username, "display_name": profile.display_name or user.username,
              "bio": profile.bio or "", "visibility": profile.visibility, "show_metrics": profile.show_metrics}
    if own or profile.show_metrics:
        result["metrics"] = user_library_service.profile_metrics(user.username)
    return result


def visible_profiles(db, *, federated=False, limit=100, offset=0):
    query = db.query(database.User, database.UserProfile).join(database.UserProfile).filter(database.User.is_active.is_(True))
    query = query.filter(database.UserProfile.visibility == "federated" if federated
                         else database.UserProfile.visibility.in_(["local", "federated"]))
    return [serialize_profile(user, profile) for user, profile in query.order_by(database.User.id).offset(offset).limit(limit)]


@router.get("/community")
def community(request: Request, user: database.User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "community.html", {"request": request, "username": user.username})


@router.get("/api/profiles")
def list_profiles(limit: int = 50, page: int = 1, user: database.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not 1 <= limit <= 100 or not 1 <= page <= 1000:
        raise HTTPException(422, "Invalid profile pagination")
    return {"items": visible_profiles(db, limit=limit, offset=(page - 1) * limit)}


@router.get("/api/profiles/me")
def own_profile(user: database.User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.get(database.UserProfile, user.id)
    if not profile:
        profile = database.UserProfile(user_id=user.id, display_name="", visibility="private", show_metrics=False)
        db.add(profile)
        db.commit()
    return serialize_profile(user, profile, own=True)


@router.put("/api/profiles/me")
def save_profile(payload: ProfilePayload, user: database.User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.get(database.UserProfile, user.id)
    if not profile:
        profile = database.UserProfile(user_id=user.id)
        db.add(profile)
    for name, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "Profile settings cannot be null")
        setattr(profile, name, value.strip() if isinstance(value, str) else value)
    db.commit()
    return serialize_profile(user, profile, own=True)


@router.get("/api/profiles/{user_id}")
def read_profile(user_id: int, user: database.User = Depends(get_current_user), db: Session = Depends(get_db)):
    owner, profile = db.get(database.User, user_id), db.get(database.UserProfile, user_id)
    if not owner or not owner.is_active or not profile or (owner.id != user.id and profile.visibility == "private"):
        raise HTTPException(404, "Profile not found")
    return serialize_profile(owner, profile, own=owner.id == user.id)
