"""
StreamVault Core - Main FastAPI Application

Centro multimedia unificado: Películas, Series, YouTube, IPTV
"""
import logging
import socket
import os
import shutil
import re
import json
import time
from urllib.parse import quote, urlparse

# ---  GLOBAL NETWORK FIX (IPv4 only) ---
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(*args, **kwargs):
    responses = old_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET]
socket.getaddrinfo = new_getaddrinfo
# ---------------------------------------

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy.orm import Session
from pydantic import BaseModel
import asyncio

# Imports locales
from config import settings
from version import VERSION
from services.media_library import recover_completed_downloads
from services import federation_service
import auth
import auth_forms
import database
import i18n
import security
from contextvars import ContextVar

from fastapi.middleware.trustedhost import TrustedHostMiddleware

from shared_templates import templates

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- APP INIT ---
app = FastAPI(
    title="StreamVault",
    description="Centro Multimedia Unificado",
    version=VERSION
)

# TrustedHost solo en producción
if settings.DOMAIN != "localhost":
    app.add_middleware(
        TrustedHostMiddleware, 
        allowed_hosts=settings.all_allowed_hosts
    )

# --- STATIC FILES ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
ASSET_CANDIDATES = [
    os.path.join(BASE_DIR, "assets"),
    os.path.join(PROJECT_ROOT, "assets"),
    "/app/assets",
]
ASSETS_DIR = next((path for path in ASSET_CANDIDATES if os.path.exists(path)), None)

if not ASSETS_DIR:
    raise RuntimeError(f"No se pudo localizar el directorio de assets. Revisados: {ASSET_CANDIDATES}")

class CacheStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:
        response_headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return super().is_not_modified(response_headers, request_headers)

app.mount("/assets", CacheStaticFiles(directory=ASSETS_DIR), name="assets")

PUBLIC_PATHS = {"/", "/login", "/setup", "/api/health", "/sw.js"}
PUBLIC_PREFIXES = ("/assets/", "/set-language/", "/api/federation/v1/")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def request_is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if forwarded == "https":
        return True
    try:
        cloudflare = json.loads(request.headers.get("cf-visitor", "{}"))
    except json.JSONDecodeError:
        cloudflare = {}
    return cloudflare.get("scheme") == "https" or request.url.scheme == "https"


def origin_is_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request.headers.get("host", "").lower()


def safe_redirect_target(target: str, default: str = "/portal") -> str:
    if not target or target in ("None", "null", ""):
        return default
    target = target.strip()
    if target.startswith(("//", "\\\\", "/\\", "\\/")) or "://" in target:
        return default
    if not target.startswith("/"):
        target = f"/{target}"
    return target


def add_security_headers(response, request: Request):
    if request.url.path.startswith(("/api/federation/", "/api/profiles", "/admin/api/")):
        response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
        "font-src 'self'; media-src 'self' blob: http: https:; "
        "connect-src 'self' blob: http: https: ws: wss:; frame-src http: https:; worker-src 'self' blob:"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request_is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# --- I18N SETUP ---
current_lang = ContextVar("current_lang", default="en")

def get_text_context(key: str):
    return i18n.get_text(key, current_lang.get())

templates.env.globals["_"] = get_text_context


@app.middleware("http")
async def set_i18n_context(request: Request, call_next):
    lang = request.cookies.get("lang", "en")
    if lang not in i18n.SUPPORTED_LANGS:
        lang = i18n.DEFAULT_LANG
    lang_token = current_lang.set(lang)
    response = await call_next(request)
    current_lang.reset(lang_token)
    return response


@app.middleware("http")
async def enforce_security_boundary(request: Request, call_next):
    path = request.url.path
    if request.method not in SAFE_METHODS and not origin_is_allowed(request):
        return add_security_headers(JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403), request)

    is_public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
    renewal = None
    if not is_public:
        db = database.SessionLocal()
        try:
            try:
                user = auth.get_current_user(request, db)
            except Exception:
                user = None
            if not user or not user.is_active:
                if path.startswith("/api/"):
                    return add_security_headers(JSONResponse({"detail": "Authentication required"}, status_code=401), request)
                next_path = quote(path + (f"?{request.url.query}" if request.url.query else ""), safe="")
                return add_security_headers(RedirectResponse(f"/login?next={next_path}", status_code=303), request)
            request.state.user = user
            if path != "/logout":
                renewal = auth.session_renewal(request, user)
        finally:
            db.close()

    response = await call_next(request)
    if renewal and response.status_code < 400:
        auth.set_session_cookie(response, *renewal, secure=request_is_https(request))
    return add_security_headers(response, request)

# --- STARTUP / SHUTDOWN ---
_startup_tasks: set[asyncio.Task] = set()


async def _update_checks_background():
    # Discovery only, never automatic installation. Keep Git/network work off
    # the serving loop and let checks be disabled from the admin panel.
    await asyncio.sleep(30)
    while True:
        try:
            config = await asyncio.to_thread(admin.updater.configuration)
            status = await asyncio.to_thread(admin.updater.status)
            checked = status.get("checked_at", status.get("updated_at", 0))
            if (config["automatic_checks"] and status["state"] not in {"checking", "updating"}
                    and time.time() - checked >= config["check_interval_hours"] * 3600):
                await asyncio.to_thread(admin.updater.check)
        except Exception:
            logger.warning("[UPDATER] Scheduled check failed; see admin update status.")
        await asyncio.sleep(60)


async def _recover_downloads_background():
    try:
        recovered = await asyncio.to_thread(
            recover_completed_downloads, settings.DOWNLOADS_ROOT
        )
        if recovered:
            logger.info("[DOWNLOADS] Recovered %d completed media file(s)", len(recovered))
    except Exception:
        logger.exception("[DOWNLOADS] Completed-download recovery failed")


@app.on_event("startup")
async def startup_event():
    if settings.SECRET_KEY in {"unsafe-default-secret-key-change-me-in-production", "streamvault-dev-secret-change-me"} or len(settings.SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY must be a unique random value of at least 32 characters")
    i18n.load_translations()
    logger.info(f"[I18N] Idiomas cargados: {list(i18n.translations.keys())}")
    database.run_migrations()
    # Recovery can touch a large mounted library. It must never delay HTTP
    # readiness or keep the Cloudflare tunnel waiting for the app to start.
    recovery_task = asyncio.create_task(_recover_downloads_background())
    _startup_tasks.add(recovery_task)
    recovery_task.add_done_callback(_startup_tasks.discard)
    for coroutine in (_update_checks_background(), federation_service.background_loop()):
        task = asyncio.create_task(coroutine)
        _startup_tasks.add(task)
        task.add_done_callback(_startup_tasks.discard)
    if (
        not admin.cloudflared_manager.external
        and not admin.cloudflared_manager.tunnel_url
        and admin.cloudflared_manager.load_config().get("enabled")
    ):
        try:
            await admin.cloudflared_manager.start()
        except Exception as exc:
            logger.warning("[TUNNEL] Could not restore Cloudflare Tunnel: %s", exc)
    logger.info("[STARTUP] StreamVault iniciado correctamente")

@app.on_event("shutdown")
async def shutdown_event():
    for task in list(_startup_tasks):
        task.cancel()
    await asyncio.gather(*_startup_tasks, return_exceptions=True)
    await federation.close_tasks()
    await downloads.torrent_service.close()
    await downloads.close_playback_tasks()
    await iptv.iptv_service.close()
    if not admin.cloudflared_manager.external and not admin.cloudflared_manager.tunnel_url:
        await admin.cloudflared_manager.stop(disable=False)
    logger.info("[SHUTDOWN] StreamVault detenido")

# --- DATABASE DEPENDENCY ---
def get_db():
    db = database.SessionLocal()
    try: 
        yield db
    finally: 
        db.close()

# --- CONECTAR ROUTERS ---
from routers import (
    admin,
    youtube,
    iptv,
    tmdb,
    media_local,
    resolver,
    downloads,
    subtitles,
    library,
    anime,
    preferences,
    age_rating,
    trailers,
    allmanga,
    block_stats,
    sniffer,
    federation,
    profiles,
    client,
)
app.include_router(admin.router)
app.include_router(youtube.router)
app.include_router(iptv.router)
app.include_router(tmdb.router)
app.include_router(resolver.router)
app.include_router(media_local.router)
app.include_router(downloads.router)
app.include_router(subtitles.router)
app.include_router(library.router)
app.include_router(anime.router)
app.include_router(preferences.router)
app.include_router(age_rating.router)
app.include_router(trailers.router)
app.include_router(allmanga.router)
app.include_router(block_stats.router)
app.include_router(sniffer.router)
app.include_router(federation.router)
app.include_router(profiles.router)
app.include_router(client.router)

# --- PYDANTIC MODELS ---
class UserCreate(BaseModel):
    username: str
    password: str

# --- HELPER: Password Strength ---
def is_password_strong(password: str) -> bool:
    if len(password) < 8: return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password): return False
    return True

# --- SETUP ROUTE (First User) ---

@app.get("/setup")
async def setup_page(request: Request, db: Session = Depends(get_db)):
    # Solo permitir si no hay usuarios
    user_count = db.query(database.User).count()
    if user_count > 0:
        return RedirectResponse("/login?error=configured", status_code=303)
    
    return templates.TemplateResponse(request, "login.html", {
        "request": request,
        "title": "Initial Setup - Create Admin",
        "action": "/setup",
        **auth_forms.form_context(request),
        "next": ""
    })

@app.post("/setup")
async def setup_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db)
):
    # Verificar si ya existen usuarios
    if db.query(database.User).count() > 0:
        return auth_forms.failure(request, "action_not_allowed", status_code=403)
    if not auth.is_valid_username(username):
        return auth_forms.failure(request, "invalid_username", path="/setup")
    
    if not is_password_strong(password):
        return auth_forms.failure(request, "weak_password", path="/setup")
    
    # Crear admin
    hashed = auth.pwd_context.hash(password)
    new_user = database.User(
        username=username,
        hashed_password=hashed,
        is_admin=True,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    
    return auth_forms.success(request, "/login?notice=created")

# --- AUTH ROUTES ---

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "request": request, 
        **auth_forms.form_context(request),
        "remember_days": settings.REMEMBER_SESSION_DAYS,
        "next": safe_redirect_target(request.query_params.get("next", ""), default="")
    })

@app.post("/login")
async def login(
    request: Request, 
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""), 
    remember: bool = Form(False),
    db: Session = Depends(get_db)
):
    # 1. Brute force protection
    try:
        security.check_brute_force(request) 
    except HTTPException:
        return auth_forms.failure(request, "blocked", next_target=safe_redirect_target(next, default=""), status_code=429)

    user = auth.get_user_by_username(db, username)
    
    # 2. Verificación
    if not username or not password or not user or not user.is_active or not auth.verify_password(password, user.hashed_password):
        is_blocked = security.register_failed_attempt(request)
        code = "blocked" if is_blocked else "invalid_credentials"
        return auth_forms.failure(request, code, next_target=safe_redirect_target(next, default=""),
                                  status_code=429 if is_blocked else 401)
    
    # 3. Éxito
    security.clear_attempts(request)
    lifetime = auth.session_expiry(remember)
    access_token = auth.create_access_token(auth.session_data(user, remember), lifetime)
    
    redirect_url = safe_redirect_target(next, default="/portal")

    response = auth_forms.success(request, redirect_url)
    auth.set_session_cookie(response, access_token, lifetime, secure=request_is_https(request))
    return response

@app.get("/portal")
async def portal(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: 
        return RedirectResponse("/login")
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not user: 
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "portal.html", {
        "request": request, 
        "username": username,
        "is_admin": user.is_admin,
    })

@app.get("/iptv")
async def iptv_page(request: Request, db: Session = Depends(get_db)):
    """Página de IPTV"""
    token = request.cookies.get("access_token")
    if not token: 
        return RedirectResponse("/login")
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "iptv.html", {"request": request, "username": username, "is_admin": user.is_admin})

@app.get("/youtube")
async def youtube_page(request: Request, db: Session = Depends(get_db)):
    """Página de YouTube"""
    token = request.cookies.get("access_token")
    if not token: 
        return RedirectResponse("/login")
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "youtube.html", {"request": request, "username": username, "is_admin": user.is_admin})

@app.get("/browse")
async def browse_page(request: Request, db: Session = Depends(get_db)):
    """Página de catálogo de películas/series"""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login")

    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "browse.html", {"request": request, "username": username, "is_admin": user.is_admin})

@app.get("/downloads")
async def downloads_page(request: Request, db: Session = Depends(get_db)):
    """Página de gestión de descargas"""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "downloads.html", {"request": request, "username": username, "is_admin": user.is_admin})

@app.get("/media/{tmdb_id}")
async def media_detail_page(tmdb_id: str, request: Request, db: Session = Depends(get_db)):
    """Full-page media detail view"""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")
    media_type = request.query_params.get("type", "movie")
    return templates.TemplateResponse(request, "media_detail.html", {
        "request": request,
        "username": username,
        "is_admin": user.is_admin,
        "tmdb_id": tmdb_id,
        "media_type": media_type,
    })

@app.get("/anime")
async def anime_page(request: Request, db: Session = Depends(get_db)):
    """Página de anime"""
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "anime.html", {"request": request, "username": username, "is_admin": user.is_admin})

@app.post("/logout")
def logout():
    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie("access_token")
    return res

@app.get("/")
def home(request: Request):
    if request.cookies.get("access_token"):
        return RedirectResponse("/portal")
    return RedirectResponse("/login")

# --- SETTINGS ROUTES ---

@app.get("/settings")
async def settings_page(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return RedirectResponse("/login")

    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user:
        return RedirectResponse("/login")

    return templates.TemplateResponse(request, "settings.html", {
        "request": request, 
        "username": username,
        "msg": request.query_params.get("msg"), 
        "type": request.query_params.get("type", "info")
    })

@app.post("/settings")
async def change_password(
    request: Request, 
    old_password: str = Form(...), 
    new_password: str = Form(...), 
    confirm_password: str = Form(...), 
    db: Session = Depends(get_db)
):
    token = request.cookies.get("access_token")
    if not token: 
        return RedirectResponse("/login")
    
    if new_password != confirm_password:
        return RedirectResponse("/settings?msg=Passwords do not match&type=error", status_code=303)
    
    if not is_password_strong(new_password):
        return RedirectResponse("/settings?msg=Weak password: use 8 characters, uppercase, numbers and symbols&type=error", status_code=303)

    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not auth.verify_password(old_password, user.hashed_password):
        return RedirectResponse("/settings?msg=Current password is incorrect&type=error", status_code=303)
    
    user.hashed_password = auth.get_password_hash(new_password)
    db.commit()
    
    return RedirectResponse("/settings?msg=Password updated&type=success", status_code=303)

@app.get("/set-language/{lang}")
async def set_language(lang: str, request: Request):
    if lang not in i18n.SUPPORTED_LANGS: 
        lang = i18n.DEFAULT_LANG
    
    referer = request.headers.get("referer", "/portal")
    response = RedirectResponse(referer, status_code=303)
    response.set_cookie(key="lang", value=lang, httponly=True, samesite='lax', max_age=31536000)
    return response

# --- API ROUTES ---

@app.post("/api/users")
async def create_user(
    user: UserCreate,
    db: Session = Depends(get_db),
    admin_user: database.User = Depends(admin.get_current_admin),
):
    """Crear nuevo usuario (solo admin)"""
    del admin_user
    if not auth.is_valid_username(user.username):
        return JSONResponse({"detail": "Invalid username"}, status_code=422)
    if not is_password_strong(user.password):
        return JSONResponse({"detail": "Password does not meet complexity requirements"}, status_code=422)
    auth.create_user_in_db(db, user.username, user.password)
    
    # Crear directorio de datos del usuario
    user_dir = f"{settings.DATA_ROOT}/users/{user.username}"
    os.makedirs(user_dir, exist_ok=True)
    
    return {"status": "ok", "user": user.username}

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "StreamVault",
        "version": VERSION
    }


@app.get("/sw.js")
async def service_worker():
    """Serve the PWA service worker with correct MIME type."""
    from fastapi.responses import FileResponse
    sw_path = os.path.join(ASSETS_DIR, "sw.js")
    if not os.path.isfile(sw_path):
        raise HTTPException(404, "Service worker not found")
    return FileResponse(sw_path, media_type="application/javascript")
