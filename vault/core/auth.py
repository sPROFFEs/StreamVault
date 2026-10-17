from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from fastapi import Request, HTTPException # <--- IMPORTANTE
import database
import re
# CONFIGURACIÓN
from config import settings

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user_by_username(db: Session, username: str):
    return db.query(database.User).filter(database.User.username == username).first()

def create_user_in_db(db: Session, username: str, password: str):
    if not is_valid_username(username):
        raise ValueError("Username must be 3-32 characters and use only letters, numbers, dots, dashes, or underscores")
    hashed_password = get_password_hash(password)
    db_user = database.User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    db.flush()
    db.add(database.UserPreferences(user_id=db_user.id))
    db.commit()
    db.refresh(db_user)
    return db_user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_username_from_token(token: str):
    payload = get_token_payload(token)
    return payload.get("sub") if payload else None

def get_token_payload(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def session_expiry(remember: bool) -> timedelta:
    return (timedelta(days=settings.REMEMBER_SESSION_DAYS) if remember
            else timedelta(minutes=max(1, ACCESS_TOKEN_EXPIRE_MINUTES)))

def session_data(user, remember: bool) -> dict:
    # Bind new sessions to the current password without exposing its hash.
    stamp = hmac.new(SECRET_KEY.encode(), user.hashed_password.encode(), hashlib.sha256).hexdigest()
    return {"sub": user.username, "remember": remember, "auth": stamp}

def set_session_cookie(response, token: str, lifetime: timedelta, *, secure: bool):
    response.set_cookie("access_token", token, httponly=True, secure=secure,
                        samesite="lax", max_age=int(lifetime.total_seconds()))
    response.headers["Cache-Control"] = "private, no-store"

def session_renewal(request: Request, user):
    payload = getattr(request.state, "session_payload", None)
    if not payload or not isinstance(payload.get("exp"), (int, float)):
        return None
    lifetime = session_expiry(payload.get("remember") is True)
    remaining = payload["exp"] - datetime.now(timezone.utc).timestamp()
    # Do not rotate on every progress/range request, and never revive an expired token.
    if 0 < remaining <= min(3600, lifetime.total_seconds() / 4):
        return create_access_token(session_data(user, payload.get("remember") is True), lifetime), lifetime
    return None

# --- ESTA ES LA FUNCIÓN QUE FALTABA Y QUE PIDE EL ROUTER DE RADIOS ---
def get_current_user(request: Request, db: Session):
    """
    Busca la cookie de sesión, extrae el usuario y lo valida contra la DB.
    Si algo falla, lanza un 401 para redirigir al login.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="No session")
    
    payload = get_token_payload(token)
    username = payload.get("sub") if payload else None
    if not username:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    if "auth" in payload and (not isinstance(payload["auth"], str)
            or not hmac.compare_digest(payload["auth"], session_data(user, False)["auth"])):
        raise HTTPException(status_code=401, detail="Session revoked after password change")
    request.state.session_payload = payload
        
    return user

def verify_token(token: str, expected_username: str) -> bool:
    return get_username_from_token(token) == expected_username

def is_valid_username(username: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}", username or "")) and ".." not in username

def is_password_strong(password: str) -> bool:
    if len(password) < 8: return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password): return False
    return True
