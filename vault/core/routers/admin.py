import asyncio

from fastapi import APIRouter, Request, Depends, Form, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import database, auth
import psutil
import shutil
import subprocess
import os
from pathlib import Path
from pydantic import BaseModel, Field
import httpx
from config import settings
from services.updater_service import UpdateError, UpdateManager
from services.cloudflared_service import CloudflaredManager
from services.storage_policy import get_global_download_quota_gb, get_path_size_bytes, set_global_download_quota_gb
from services.admin_download_library import (
    cleanup_download_library,
    scan_download_cleanup,
    scan_download_library,
)
from services.media_library import resolve_library_path, _invalidate_scan_cache

cloudflared_manager = CloudflaredManager(
    settings.DATA_ROOT,
    settings.SECRET_KEY,
    external=settings.CLOUDFLARED_EXTERNAL,
    tunnel_url=settings.CLOUDFLARED_URL,
)

updater = UpdateManager(settings.UPDATE_WORKSPACE, settings.DATA_ROOT, settings.UPDATE_REMOTE, settings.UPDATE_BRANCH,
                       mode=settings.UPDATE_MODE, provider=settings.UPDATE_PROVIDER, api_url=settings.UPDATE_API_URL,
                       repository=settings.UPDATE_REPOSITORY, check_interval_hours=settings.UPDATE_CHECK_INTERVAL_HOURS)

router = APIRouter(prefix="/admin")
from shared_templates import templates

def get_db():
    db = database.SessionLocal()
    try: yield db
    finally: db.close()

# DEPENDENCIA DE SEGURIDAD
def get_current_admin(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: raise HTTPException(status_code=401)
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied.")
    return user

def get_dir_size(path):
    """Calcula el tamaño total de un directorio en bytes."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += get_dir_size(entry.path)
    except Exception: pass
    return total


class TunnelConfigPayload(BaseModel):
    enabled: bool | None = None
    mode: str | None = Field(None, pattern="^(quick|managed)$")
    origin: str | None = None
    token: str | None = None


class TunnelProvisionPayload(BaseModel):
    account_id: str = Field(min_length=1, max_length=32)
    zone_id: str = Field(min_length=1, max_length=32)
    api_token: str = Field(min_length=1)
    tunnel_name: str = Field(default="streamvault", min_length=1, max_length=100)
    hostname: str = Field(min_length=3, max_length=253, pattern=r"^[A-Za-z0-9.-]+$")
    origin: str = "http://nginx:80"


class CleanupPayload(BaseModel):
    # None means all currently detected safe candidates. Supplying paths lets
    # the UI preserve an administrator's explicit deselections.
    paths: list[str] | None = Field(default=None, max_length=500)


async def _active_downloader_job_ids() -> set[str] | None:
    """Return active torrent IDs, or None when the downloader cannot be queried."""
    try:
        if settings.DOWNLOADER_URL:
            async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                response = await client.get(f"{settings.DOWNLOADER_URL.rstrip('/')}/api/downloads/jobs")
                response.raise_for_status()
                jobs = response.json()
        else:
            from services.torrent_service import torrent_service
            jobs = {"torrent": torrent_service.get_all_jobs()}
        return {
            str(job["id"])
            for job in jobs.get("torrent", [])
            if job.get("status") in {"pending", "connecting", "downloading"}
        }
    except Exception:
        # If status is unavailable, the filesystem scanner stays conservative.
        return None


@router.get("/api/tunnel/status")
async def tunnel_status(admin: database.User = Depends(get_current_admin)):
    del admin
    return await cloudflared_manager.async_status()


@router.put("/api/tunnel/config")
async def tunnel_config(
    payload: TunnelConfigPayload,
    admin: database.User = Depends(get_current_admin),
):
    del admin
    patch = payload.model_dump(exclude_unset=True)
    enabled = patch.pop("enabled", None)
    try:
        if patch:
            await cloudflared_manager.async_save_config(patch)
        if enabled is True:
            return await cloudflared_manager.start()
        if enabled is False:
            return await cloudflared_manager.stop()
        return await cloudflared_manager.async_status()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/tunnel/create")
async def tunnel_create(
    payload: TunnelProvisionPayload,
    admin: database.User = Depends(get_current_admin),
):
    del admin
    try:
        return await cloudflared_manager.provision(**payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/tunnel/start")
async def tunnel_start(admin: database.User = Depends(get_current_admin)):
    del admin
    try:
        return await cloudflared_manager.start()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/tunnel/stop")
async def tunnel_stop(admin: database.User = Depends(get_current_admin)):
    del admin
    return await cloudflared_manager.stop()

# VISTA PANEL
@router.get("/")
def admin_panel(request: Request, db: Session = Depends(get_db)):
    try:
        admin = get_current_admin(request, db)
    except:
        return RedirectResponse("/portal")

    # Calculamos el total de disco para el atributo 'max' del HTML
    disk = shutil.disk_usage("/saas-data")
    download_usage_bytes = get_path_size_bytes("/saas-data/downloads")
    download_quota_gb = get_global_download_quota_gb()
    download_quota_bytes = download_quota_gb * (1024**3)
    download_usage_percent = round(min(100.0, (download_usage_bytes / download_quota_bytes) * 100), 1) if download_quota_bytes else 0.0
    stats = {
        "disk_total": int(disk.total / (1024**3)),
        "download_quota_gb": download_quota_gb,
        "download_usage_gb": round(download_usage_bytes / (1024**3), 2),
        "download_usage_percent": download_usage_percent,
    }


    # System Stats for Dashboard
    disk_total_gb = round(disk.total / (1024**3), 2)
    disk_used_gb = round(disk.used / (1024**3), 2)
    disk_percent = round((disk.used / disk.total) * 100, 1) if disk.total > 0 else 0
    

    sys_stats = {
        "cpu_usage": psutil.cpu_percent(interval=0.1),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_total": disk_total_gb,
        "disk_used": disk_used_gb,
        "disk_percent": disk_percent
    }

    users = db.query(database.User).all()
    
    return templates.TemplateResponse(request, "admin.html", {
        "request": request, 
        "username": admin.username,
        "users": users,
        "stats": stats,
        "sys_stats": sys_stats,
    })

# API ACCIONES
@router.post("/users/create")
async def create_user(
    username: str = Form(...), 
    password: str = Form(...), 
    is_admin: bool = Form(False),
    can_download: bool = Form(False),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    # 1. EL IF AQUÍ (Validar complejidad antes de crear)
    if not auth.is_valid_username(username):
        return RedirectResponse("/admin?manage_users=1&error=Invalid username", status_code=303)
    if not auth.is_password_strong(password):
        return RedirectResponse("/admin?manage_users=1&error=Weak password: use 8 characters, uppercase, numbers and symbols", status_code=303)

    existing = auth.get_user_by_username(db, username)
    if existing: return RedirectResponse("/admin?manage_users=1&error=User already exists", status_code=303)
    
    new_user = auth.create_user_in_db(db, username, password)
    if is_admin or can_download:
        new_user.is_admin = is_admin
        new_user.can_download = is_admin or can_download
        db.commit()
    
    # Crear directorio de datos del usuario
    user_dir = f"/saas-data/users/{username}"
    os.makedirs(user_dir, exist_ok=True)

    return RedirectResponse("/admin?manage_users=1&msg=Account created", status_code=303)

@router.post("/users/delete")
async def delete_user(
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    user_del = db.query(database.User).filter(database.User.id == user_id).first()
    
    # Seguridad de Nivel 0: No borrar al admin actual
    if not user_del or user_del.id == admin.id:
        return RedirectResponse("/admin?manage_users=1&error=Action not allowed", status_code=303)

    username = user_del.username
    if not auth.is_valid_username(username):
        return RedirectResponse("/admin?manage_users=1&error=Invalid stored username", status_code=303)
    user_data_path = resolve_library_path("/saas-data/users", username)
    bytes_to_free = 0

    try:
        # 1. CALCULAR ESPACIO (Antes de borrar)
        if user_data_path.exists():
            bytes_to_free = get_dir_size(user_data_path)

        # 2. PURGA DOCKER
        # Intentamos limpiar tanto el nombre legado como el nombre nuevo.
        docker_bin = shutil.which("docker")
        if docker_bin:
            subprocess.run([docker_bin, "rm", "-f", f"navidrome-{username}"], check=False, capture_output=True)
            subprocess.run([docker_bin, "rm", "-f", f"streamvault-media-{username}"], check=False, capture_output=True)

        # 3. PURGA DISCO
        if user_data_path.exists():
            shutil.rmtree(user_data_path)

        # 4. PURGA DB (Manual para evitar huérfanos si no hay CASCADE)
        # Borramos sus playlists y tracks primero si es necesario
        # db.query(database.DownloadJob).filter(database.DownloadJob.user_id == user_id).delete()
        
        db.delete(user_del)
        db.commit()

        # Conversión a formato legible
        if bytes_to_free > 1024**3:
            freed_str = f"{round(bytes_to_free / 1024**3, 2)} GB"
        else:
            freed_str = f"{round(bytes_to_free / 1024**2, 2)} MB"

        return RedirectResponse(f"/admin?manage_users=1&msg=User {username} removed. Recovered {freed_str}", status_code=303)

    except Exception as e:
        db.rollback()
        return RedirectResponse(f"/admin?manage_users=1&error=Purge error: {str(e)}", status_code=303)
        
@router.post("/users/reset-password")
async def reset_user_password(
    user_id: int = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    # 2. EL IF AQUÍ (Validar complejidad antes de resetear)
    if not auth.is_password_strong(new_password):
        return RedirectResponse("/admin?manage_users=1&error=The new password is too weak", status_code=303)

    user_to_edit = db.query(database.User).filter(database.User.id == user_id).first()
    if not user_to_edit:
        return RedirectResponse("/admin?manage_users=1&error=User not found", status_code=303)
    
    # Hashear y actualizar
    user_to_edit.hashed_password = auth.get_password_hash(new_password)
    db.commit()
    
    return RedirectResponse(f"/admin?manage_users=1&msg=Password updated for {user_to_edit.username}", status_code=303)


@router.post("/users/update-state")
async def update_user_state(
    user_id: int = Form(...),
    is_admin: bool = Form(False),
    is_active: bool = Form(False),
    can_download: bool = Form(False),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    user_to_edit = db.query(database.User).filter(database.User.id == user_id).first()
    if not user_to_edit or user_to_edit.id == admin.id:
        return RedirectResponse("/admin?manage_users=1&error=Action not allowed", status_code=303)

    user_to_edit.is_admin = is_admin
    user_to_edit.is_active = is_active
    user_to_edit.can_download = is_admin or can_download
    db.commit()
    return RedirectResponse(f"/admin?manage_users=1&msg=State updated for {user_to_edit.username}", status_code=303)

@router.post("/system/flush-ram")
async def flush_ram(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Limpia el cache de la RAM (Requiere privilegios de root en el host)"""
    try:
        # Sincroniza y limpia caches
        subprocess.run(["sync"], check=True)
        # Nota: En Docker, esto solo funciona si el contenedor tiene privilegios o mapeo de /proc
        # Si falla, es normal por restricciones de seguridad de Docker.
        os.system("echo 3 > /proc/sys/vm/drop_caches")
        return RedirectResponse("/admin/system?msg=RAM cache flushed successfully", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Could not flush RAM: {str(e)}", status_code=303)

@router.post("/system/update-download-quota")
async def update_download_quota(storage_limit: int = Form(...), admin: database.User = Depends(get_current_admin)):
    # Blindaje de lógica: No permitas que el sistema acepte basura
    if storage_limit < 1:
        return RedirectResponse("/admin?error=Quota value not allowed", status_code=303)

    # Limitar al máximo físico del disco para evitar overselling accidental
    disk = shutil.disk_usage("/saas-data")
    max_disk_gb = int(disk.total / (1024**3))
    
    if storage_limit > max_disk_gb:
        return RedirectResponse(f"/admin?error=Quota exceeds physical disk ({max_disk_gb}GB)", status_code=303)

    set_global_download_quota_gb(storage_limit)
    return RedirectResponse(f"/admin?msg=Global download quota updated to {storage_limit}GB", status_code=303)

@router.get("/system")
async def system_monitor(request: Request, db: Session = Depends(get_db)):
    get_current_admin(request, db)
    return RedirectResponse(url="/admin/?tab=system", status_code=303)


@router.get("/api/system/monitor")
def system_metrics(admin: database.User = Depends(get_current_admin)):
    from services.system_monitor import snapshot
    return snapshot()

@router.post("/system/purge-storage")

async def purge_storage(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Elimina residuos de descargas y archivos temporales"""
    import shutil
    
    # Directorios a limpiar
    paths_to_clean = ["/tmp", "/app/temp"] # Ajusta según tu Dockerfile
    
    # Extensiones de archivos basura (descargas incompletas)
    trash_extensions = [".part", ".ytdl", ".tmp", ".cache"]
    
    bytes_freed = 0
    
    try:
        # 1. Limpiar carpetas temporales globales
        for path in paths_to_clean:
            if os.path.exists(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        file_path = os.path.join(root, f)
                        bytes_freed += os.path.getsize(file_path)
                        os.remove(file_path)

        # 2. Buscar basura en las carpetas de los usuarios
        users_root = "/saas-data/users"
        for root, dirs, files in os.walk(users_root):
            # Eliminar archivos temporales de motores
            for f in files:
                if any(f.endswith(ext) for ext in trash_extensions):
                    file_path = os.path.join(root, f)
                    bytes_freed += os.path.getsize(file_path)
                    os.remove(file_path)
            
            # Eliminar caches residuales de herramientas de descarga
            for d in dirs:
                if d == ".spotdl-cache":
                    dir_path = os.path.join(root, d)
                    bytes_freed += sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(dir_path) for f in fs)
                    shutil.rmtree(dir_path)

        freed_gb = round(bytes_freed / (1024**3), 3)
        return RedirectResponse(f"/admin/system?msg=Purge completed. Freed {freed_gb} GB", status_code=303)
    
    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Error during purge: {str(e)}", status_code=303)


@router.get("/api/downloads/list")
def list_downloads_api(
    request: Request,
    admin: database.User = Depends(get_current_admin),
):
    """JSON endpoint for AJAX refresh of the admin media inventory.
    Returns the same data as the server-rendered page, preserving
    search filters and pagination from the query string."""
    del admin
    try:
        download_page = int(request.query_params.get("download_page", "1"))
    except ValueError:
        download_page = 1
    try:
        return scan_download_library(
            root=settings.DOWNLOADS_ROOT,
            directory=request.query_params.get("download_path", ""),
            query=request.query_params.get("download_q", ""),
            kind=request.query_params.get("download_kind", "all"),
            page=download_page,
            sort=request.query_params.get("download_sort", "name"),
            descending=request.query_params.get("download_order", "asc") == "desc",
        )
    except ValueError as exc:
        raise HTTPException(403, "Invalid folder path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Folder not found") from exc
    except OSError as exc:
        raise HTTPException(403, "Folder is not accessible") from exc


@router.get("/api/downloads/cleanup")
async def preview_download_cleanup(admin: database.User = Depends(get_current_admin)):
    """Preview stale downloader artifacts and empty folders for the file manager."""
    del admin
    active_job_ids = await _active_downloader_job_ids()
    return await asyncio.to_thread(scan_download_cleanup, settings.DOWNLOADS_ROOT, active_job_ids)


@router.post("/api/downloads/cleanup")
async def run_download_cleanup(
    payload: CleanupPayload | None = None,
    admin: database.User = Depends(get_current_admin),
):
    """Remove only the candidates shown by the cleanup preview."""
    del admin
    active_job_ids = await _active_downloader_job_ids()
    return await asyncio.to_thread(
        cleanup_download_library,
        settings.DOWNLOADS_ROOT,
        None if payload is None else payload.paths,
        active_job_ids,
    )


@router.get("/api/downloads/file")
def download_admin_file(
    relative_path: str = Query(...),
    admin: database.User = Depends(get_current_admin),
):
    del admin
    try:
        target = resolve_library_path(settings.DOWNLOADS_ROOT, relative_path)
    except ValueError as exc:
        raise HTTPException(403, "Invalid file path") from exc
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@router.post("/downloads/delete")
async def delete_download(
    relative_path: str = Form(...),
    admin: database.User = Depends(get_current_admin),
):
    try:
        target = resolve_library_path(settings.DOWNLOADS_ROOT, relative_path)
    except ValueError:
        return RedirectResponse("/admin?error=Invalid file", status_code=303)
    if not target.exists() or not target.is_file():
        return RedirectResponse("/admin?error=Invalid file", status_code=303)

    try:
        target.unlink()
        _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
        return RedirectResponse(f"/admin?msg=File deleted: {relative_path}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/admin?error=Could not delete file: {exc}", status_code=303)


@router.delete("/downloads/delete")
async def delete_download_api(
    relative_path: str = Query(...),
    admin: database.User = Depends(get_current_admin),
):
    """AJAX file deletion for the admin media inventory. Returns JSON so the
    frontend can remove the row without a full page reload, preserving scroll
    position and filters."""
    del admin
    try:
        target = resolve_library_path(settings.DOWNLOADS_ROOT, relative_path)
    except ValueError:
        raise HTTPException(403, "Invalid file path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    try:
        target.unlink()
        _invalidate_scan_cache(settings.DOWNLOADS_ROOT)
        return {"success": True, "relative_path": relative_path}
    except OSError as exc:
        raise HTTPException(500, f"Could not delete file: {exc}")


# ------------------------------------------------------------------ #
# #9: TMDB API key management (admin-only, hot-reloadable)            #
# ------------------------------------------------------------------ #
from services import tmdb_service as _tmdb_service_module

TMDB_KEY_FILE = os.path.join(settings.DATA_ROOT, "tmdb_key.txt")


@router.get("/api/tmdb-key")
async def get_tmdb_key(admin: database.User = Depends(get_current_admin)):
    """Return whether a TMDB key is configured and its source."""
    del admin
    env_key = os.getenv("TMDB_API_KEY", "") or settings.TMDB_API_KEY
    file_key = ""
    if os.path.isfile(TMDB_KEY_FILE):
        try:
            file_key = Path(TMDB_KEY_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    active_key = file_key or env_key
    return {
        "configured": bool(active_key),
        "source": "file" if file_key else ("environment" if env_key else None),
    }


class TmdbKeyPayload(BaseModel):
    api_key: str = Field(min_length=1, max_length=256)


@router.put("/api/tmdb-key")
async def set_tmdb_key(
    payload: TmdbKeyPayload,
    admin: database.User = Depends(get_current_admin),
):
    """Persist the TMDB API key to disk and hot-reload it into settings."""
    del admin
    try:
        os.makedirs(os.path.dirname(TMDB_KEY_FILE), exist_ok=True)
        Path(TMDB_KEY_FILE).write_text(payload.api_key.strip(), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"Could not save TMDB key: {exc}") from exc
    # Hot-reload into the running process.
    settings.TMDB_API_KEY = payload.api_key.strip()
    _tmdb_service_module.tmdb_service = _tmdb_service_module.TmdbService()
    return {"configured": True, "source": "file"}


@router.delete("/api/tmdb-key")
async def delete_tmdb_key(admin: database.User = Depends(get_current_admin)):
    """Remove the admin-managed TMDB key file and hot-reload."""
    del admin
    if os.path.isfile(TMDB_KEY_FILE):
        try:
            os.remove(TMDB_KEY_FILE)
        except OSError as exc:
            raise HTTPException(500, f"Could not delete TMDB key: {exc}") from exc
    settings.TMDB_API_KEY = ""
    _tmdb_service_module.tmdb_service = _tmdb_service_module.TmdbService()
    return {"configured": False, "source": None}


def _update_response(action):
    try:
        result = action()
        return JSONResponse(result, status_code=202 if result["state"] == "updating" else 200)
    except UpdateError as exc:
        return JSONResponse({**updater.status(), "detail": str(exc)}, status_code=exc.status_code)


@router.get("/system/update/status")
def system_update_status(admin: database.User = Depends(get_current_admin)):
    return updater.status()


class UpdateSourcePayload(BaseModel):
    mode: str | None = Field(None, pattern="^(branch|release)$")
    provider: str | None = Field(None, pattern="^(auto|gitea|github)$")
    api_url: str | None = Field(None, max_length=1000)
    repository: str | None = Field(None, max_length=255)
    api_token: str | None = Field(None, max_length=4096)
    automatic_checks: bool | None = None
    check_interval_hours: int | None = Field(None, ge=1, le=168)


@router.get("/system/update/config")
def update_source(admin: database.User = Depends(get_current_admin)):
    return updater.configuration()


@router.put("/system/update/config")
def configure_update_source(payload: UpdateSourcePayload, admin: database.User = Depends(get_current_admin)):
    patch = payload.model_dump(exclude_unset=True)
    if any(value is None for value in patch.values()):
        raise HTTPException(422, "Update source settings cannot be null")
    try:
        return updater.configure(patch)
    except UpdateError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/system/update/history")
def update_history(admin: database.User = Depends(get_current_admin)):
    return {"items": updater.history()}


@router.get("/system/update/backups")
def update_backups(admin: database.User = Depends(get_current_admin)):
    return {"items": updater.backups()}


@router.post("/system/update/backups", status_code=201)
def create_update_backup(admin: database.User = Depends(get_current_admin)):
    try:
        with updater.lock():
            updater.recover()
            return updater.backup()
    except UpdateError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/system/update/backups/{name}")
def download_update_backup(name: str, admin: database.User = Depends(get_current_admin)):
    import re
    if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{8}\.zip", name):
        raise HTTPException(404, "Backup not found")
    path = updater.directory / "backups" / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, filename=name, media_type="application/zip", headers={"Cache-Control": "private, no-store"})


@router.post("/system/update/check")
def system_update_check(admin: database.User = Depends(get_current_admin)):
    return _update_response(updater.check)


@router.post("/system/update/git-pull")
def system_git_pull(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    # Keep the existing URL for clients; it now completes the Git/Compose update.
    return _update_response(lambda: updater.start("update"))


@router.post("/system/update/docker-restart")
def system_docker_restart(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    return _update_response(lambda: updater.start("restart"))


@router.post("/system/update/docker-recreate")
def system_docker_recreate(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    return _update_response(lambda: updater.start("rebuild"))
