"""Federation administration, token-authenticated publication and session proxy."""
import asyncio
import hashlib
import json
import math
import mimetypes
import re
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

import database
from config import settings
from routers.admin import get_current_admin, get_db
from routers.library import get_current_user
from routers import downloads, profiles
from services import federation_service as federation
from services.media_library import resolve_library_path
from services.streaming_response import ClosingStreamingResponse

router = APIRouter()
_tasks: set[asyncio.Task] = set()
_proxy_slots = asyncio.Semaphore(8)


class NodeConfigPayload(BaseModel):
    enabled: bool | None = None
    node_name: str | None = Field(None, min_length=1, max_length=80)


class PeerPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=1000)
    token: str = Field(min_length=32, max_length=4096)
    enabled: bool = True
    allow_private: bool = False


class PeerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    token: str | None = Field(None, min_length=32, max_length=4096)
    enabled: bool | None = None


class GrantPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    share_media: bool = True
    share_profiles: bool = False


def require_grant(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    if not federation.node_config()["enabled"]:
        raise HTTPException(403, "Federation publication is disabled")
    if not authorization or not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 4096:
        raise HTTPException(401, "Federation credential required")
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    grant = db.query(database.FederationGrant).filter_by(token_hash=digest, enabled=True).first()
    if not grant:
        raise HTTPException(401, "Invalid federation credential")
    if not grant.last_seen_at or time.time() - grant.last_seen_at > 30:
        grant.last_seen_at = time.time()
        db.commit()
    return grant


def media_grant(grant: database.FederationGrant = Depends(require_grant)):
    if not grant.share_media:
        raise HTTPException(403, "Media sharing is not permitted for this credential")
    return grant


def grant_view(grant):
    return {key: getattr(grant, key) for key in ("id", "name", "enabled", "share_media", "share_profiles", "created_at", "last_seen_at")}


@router.get("/admin/api/federation/config")
def configuration(admin: database.User = Depends(get_current_admin)):
    return federation.node_config()


@router.put("/admin/api/federation/config")
def configure(payload: NodeConfigPayload, admin: database.User = Depends(get_current_admin)):
    patch = payload.model_dump(exclude_unset=True)
    if any(value is None for value in patch.values()):
        raise HTTPException(422, "Configuration cannot be null")
    return federation.save_node_config(patch)


@router.get("/admin/api/federation/peers")
def admin_peers(admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return {"items": [federation.peer_view(peer, admin=True) for peer in db.query(database.FederationPeer).order_by(database.FederationPeer.id)]}


@router.post("/admin/api/federation/peers", status_code=201)
def add_peer(payload: PeerPayload, admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    if db.query(database.FederationPeer).count() >= 20:
        raise HTTPException(409, "At most 20 peers may be configured")
    try:
        base_url = federation.validate_peer_url(payload.base_url, payload.allow_private)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    peer = database.FederationPeer(name=payload.name, base_url=base_url, enabled=payload.enabled,
                                  allow_private=payload.allow_private, token_ciphertext=federation.encrypt_token(payload.token))
    db.add(peer)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Node already configured") from exc
    return federation.peer_view(peer, admin=True)


@router.patch("/admin/api/federation/peers/{peer_id}")
def change_peer(peer_id: int, payload: PeerPatch, admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    peer = db.get(database.FederationPeer, peer_id)
    if not peer:
        raise HTTPException(404, "Node not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "Peer settings cannot be null")
        if key == "token":
            peer.token_ciphertext = federation.encrypt_token(value)
            peer.status = "pending"
        else:
            setattr(peer, key, value)
    db.commit()
    return federation.peer_view(peer, admin=True)


@router.delete("/admin/api/federation/peers/{peer_id}")
def delete_peer(peer_id: int, admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    peer = db.get(database.FederationPeer, peer_id)
    if not peer:
        raise HTTPException(404, "Node not found")
    db.delete(peer)
    db.commit()
    return {"success": True}


@router.post("/admin/api/federation/peers/{peer_id}/sync", status_code=202)
async def synchronize(peer_id: int, admin: database.User = Depends(get_current_admin)):
    try:
        await asyncio.to_thread(federation.get_peer, peer_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    if len(_tasks) >= 2:
        raise HTTPException(409, "A synchronization is already queued; wait before starting another")
    task = asyncio.create_task(federation.sync_peer(peer_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"state": "syncing"}


@router.get("/admin/api/federation/grants")
def list_grants(admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return {"items": [grant_view(grant) for grant in db.query(database.FederationGrant).order_by(database.FederationGrant.id)]}


@router.post("/admin/api/federation/grants", status_code=201)
def issue_grant(payload: GrantPayload, admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    if db.query(database.FederationGrant).count() >= 100:
        raise HTTPException(409, "Remove old credentials before issuing more")
    token = secrets.token_urlsafe(40)
    grant = database.FederationGrant(**payload.model_dump(), token_hash=hashlib.sha256(token.encode()).hexdigest(), created_at=time.time())
    db.add(grant)
    db.commit()
    return {**grant_view(grant), "token": token}


@router.delete("/admin/api/federation/grants/{grant_id}")
def revoke_grant(grant_id: int, admin: database.User = Depends(get_current_admin), db: Session = Depends(get_db)):
    grant = db.get(database.FederationGrant, grant_id)
    if not grant:
        raise HTTPException(404, "Credential not found")
    db.delete(grant)
    db.commit()
    return {"success": True}


@router.get("/api/federation/v1/health")
def health(grant: database.FederationGrant = Depends(require_grant)):
    return {"protocol": federation.PROTOCOL, "node_name": federation.node_config()["node_name"]}


@router.get("/api/federation/v1/catalog")
def catalog(after: str = "", limit: int = Query(100, ge=1, le=100), grant: database.FederationGrant = Depends(media_grant)):
    if after and not re.fullmatch(r"[a-f0-9]{64}", after):
        raise HTTPException(422, "Invalid catalog cursor")
    records = [item for item, _ in federation.published_catalog() if item["id"] > after]
    return {"items": records[:limit], "next_cursor": records[limit - 1]["id"] if len(records) > limit else None}


async def _relative(media_id):
    try:
        return await asyncio.to_thread(federation.media_path, media_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Shared media not found or download incomplete") from exc


@router.get("/api/federation/v1/media/{media_id}/metadata")
async def media_metadata(media_id: str, grant: database.FederationGrant = Depends(media_grant)):
    match = (await downloads.match_downloaded_media(title="", path=await _relative(media_id)))["match"]
    return {"audio_tracks": match["audio_tracks"], "duration_seconds": match["duration_seconds"],
            "subtitles": [{"language": item["language"], "label": item["label"], "index": index}
                          for index, item in enumerate(match["subtitles"])]}


@router.get("/api/federation/v1/media/{media_id}/playback")
async def media_playback(media_id: str, audio: int | None = None, start: float = Query(0, ge=0, le=86400),
                         grant: database.FederationGrant = Depends(media_grant)):
    result = await downloads.prepare_local_playback(await _relative(media_id), audio=audio, start=start)
    return {key: result[key] for key in ("mode", "offset", "duration_seconds")} | {
        "transport": "remux" if "/remux?" in result["url"] else "native"}


@router.api_route("/api/federation/v1/media/{media_id}/stream", methods=["GET", "HEAD"])
async def media_stream(media_id: str, request: Request, audio: int | None = None, start: float = Query(0, ge=0, le=86400),
                       transport: str = Query("native", pattern="^(native|remux)$"), force_stream: bool = False,
                       grant: database.FederationGrant = Depends(media_grant)):
    relative = await _relative(media_id)
    if transport == "remux":
        if request.method == "HEAD":
            # Probing a media URL must not launch a full video conversion.
            path = resolve_library_path(settings.DOWNLOADS_ROOT, relative)
            tracks = await asyncio.to_thread(downloads._probe_media_tracks, path)
            audio_map = downloads._audio_map(tracks, audio)
            cached = downloads._remux_cache_path(path, audio_map)
            if not force_stream and cached.is_file() and cached.stat().st_size:
                return FileResponse(cached, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})
            response = Response(media_type="video/mp4", headers={"Cache-Control": "private, no-store"})
            del response.headers["content-length"]  # A live conversion has unknown length.
            return response
        return await downloads.remux_downloaded_media(relative, audio=audio, start=start, force_stream=force_stream)
    path = resolve_library_path(settings.DOWNLOADS_ROOT, relative)
    return FileResponse(path, media_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/api/federation/v1/media/{media_id}/subtitles/{index}")
async def media_subtitle(media_id: str, index: int, grant: database.FederationGrant = Depends(media_grant)):
    from urllib.parse import parse_qs, urlsplit
    relative = await _relative(media_id)
    match = (await downloads.match_downloaded_media(title="", path=relative))["match"]
    if not 0 <= index < len(match["subtitles"]):
        raise HTTPException(404, "Subtitle not found")
    url = match["subtitles"][index]["url"]
    params = parse_qs(urlsplit(url).query)
    if "embedded-subtitle" in url:
        return await downloads.serve_embedded_subtitle(relative, int(params["stream"][0]))
    return await downloads.serve_downloaded_subtitle(params["path"][0])


@router.get("/api/federation/v1/profiles")
def shared_profiles(limit: int = Query(100, ge=1, le=100), page: int = Query(1, ge=1, le=1000),
                    grant: database.FederationGrant = Depends(require_grant), db: Session = Depends(get_db)):
    if not grant.share_profiles:
        raise HTTPException(403, "Profile sharing is not permitted for this credential")
    return {"items": profiles.visible_profiles(db, federated=True, limit=limit, offset=(page - 1) * limit)}


@router.get("/api/federation/peers")
def peers(user: database.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"items": [federation.peer_view(peer) for peer in db.query(database.FederationPeer).filter_by(enabled=True)]}


@router.get("/api/federation/sources/{tmdb_id}")
def available_sources(tmdb_id: str, media_type: str = Query("movie", alias="type", pattern="^(movie|tv|series)$"),
                      s: int | None = Query(None, ge=0, le=200), e: int | None = Query(None, ge=1, le=10000),
                      title: str | None = Query(None, max_length=255), original_title: str | None = Query(None, max_length=255),
                      year: int | None = Query(None, ge=1800, le=2200), user: database.User = Depends(get_current_user)):
    # Cached metadata only: remote downloads must not wait for embed providers.
    return federation.match_sources(tmdb_id, "tv" if media_type == "series" else media_type, s, e,
                                    title=title, original_title=original_title, year=year)


def _consumer_media(peer_id, media_id):
    try:
        peer = federation.get_peer(peer_id, playable=True)
    except LookupError as exc:
        raise HTTPException(503, str(exc)) from exc
    with database.SessionLocal() as db:
        if not db.query(database.FederatedMedia).filter_by(peer_id=peer_id, remote_id=media_id).first():
            raise HTTPException(404, "Media not found in this node's catalog")
    return peer


async def _remote_json(peer, route, params=None):
    try:
        return await federation.remote_json(peer, route, params=params)
    except Exception as exc:
        await asyncio.to_thread(federation.mark_offline, peer.id)
        raise HTTPException(503, "Remote node is unavailable or access was revoked") from exc


@router.get("/api/federation/peers/{peer_id}/media/{media_id}/metadata")
async def proxy_metadata(peer_id: int, media_id: str, user: database.User = Depends(get_current_user)):
    peer = await asyncio.to_thread(_consumer_media, peer_id, media_id)
    result = await _remote_json(peer, f"/media/{media_id}/metadata")
    from pydantic import TypeAdapter
    class AudioTrack(BaseModel):
        stream_index: int = Field(ge=0, le=1000)
        language: str = Field("und", max_length=32)
        title: str | None = Field(None, max_length=255)
        codec: str = Field("unknown", max_length=50)
    try:
        tracks = TypeAdapter(list[AudioTrack]).validate_python(result.get("audio_tracks", [])[:50])
        duration = float(result.get("duration_seconds", 0))
        if not math.isfinite(duration) or not 0 <= duration <= 86400:
            raise ValueError("Invalid duration")
        subtitles = [{"language": str(item.get("language", "und"))[:32], "label": str(item.get("label", "Subtitles"))[:255],
                      "url": f"/api/federation/peers/{peer_id}/media/{media_id}/subtitles/{int(item['index'])}"}
                     for item in result.get("subtitles", [])[:50] if 0 <= int(item["index"]) < 50]
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(502, "Invalid remote media metadata") from exc
    return {"audio_tracks": [track.model_dump() for track in tracks], "duration_seconds": duration, "subtitles": subtitles}


@router.get("/api/federation/peers/{peer_id}/media/{media_id}/playback")
async def proxy_playback(peer_id: int, media_id: str, audio: int | None = None, start: float = Query(0, ge=0, le=86400),
                         user: database.User = Depends(get_current_user)):
    peer = await asyncio.to_thread(_consumer_media, peer_id, media_id)
    params = {"start": start}
    if audio is not None:
        params["audio"] = audio
    result = await _remote_json(peer, f"/media/{media_id}/playback", params)
    timing = {}
    try:
        if result["mode"] not in {"file", "stream"} or result["transport"] not in {"native", "remux"}:
            raise ValueError("Invalid playback mode")
        for key in ("offset", "duration_seconds"):
            value = float(result[key])
            if not math.isfinite(value) or not 0 <= value <= 86400:
                raise ValueError("Invalid playback timing")
            timing[key] = value
        params.update(transport=result["transport"], force_stream="true" if result["mode"] == "stream" else "false")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, "Invalid remote playback response") from exc
    return {"mode": result["mode"], **timing,
        "url": f"/api/federation/peers/{peer_id}/media/{media_id}/stream?" + urlencode(params)}


async def _proxy(peer, route, request, params=None, *, subtitle=False):
    if _proxy_slots.locked():
        raise HTTPException(503, "Too many concurrent federation streams")
    await _proxy_slots.acquire()
    client = response = None
    async def close():
        try:
            if response is not None:
                await response.aclose()
        finally:
            try:
                if client is not None:
                    await client.aclose()
            finally:
                _proxy_slots.release()
    try:
        headers = {}
        if request.headers.get("range"):
            if not re.fullmatch(r"bytes=\d*-\d*", request.headers["range"]):
                raise HTTPException(416, "Unsupported byte range")
            headers["Range"] = request.headers["range"]
        client, response = await federation.remote_response(peer, route, params=params, headers=headers, method=request.method)
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("Compressed media relay is not supported")
        if response.status_code not in {200, 206, 416}:
            if response.status_code == 404:
                raise HTTPException(404, "Remote media no longer exists")
            raise ValueError("Remote stream failed")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if subtitle:
            if content_type != "text/vtt":
                raise ValueError("Invalid subtitle content type")
        elif not (content_type.startswith(("video/", "audio/")) or content_type == "application/octet-stream" or response.status_code == 416):
            raise ValueError("Invalid stream content type")
        forwarded = {key: response.headers[key] for key in ("content-length", "content-range", "accept-ranges", "etag", "last-modified")
                     if key in response.headers}
        forwarded["Cache-Control"] = "private, no-store"
        return ClosingStreamingResponse(response.aiter_raw(64 * 1024), status_code=response.status_code,
                                        media_type=content_type, headers=forwarded, cleanup=close)
    except HTTPException:
        await close()
        raise
    except BaseException as exc:
        await close()
        if isinstance(exc, asyncio.CancelledError):
            raise
        await asyncio.to_thread(federation.mark_offline, peer.id)
        raise HTTPException(503, "Remote stream is unavailable") from exc


@router.api_route("/api/federation/peers/{peer_id}/media/{media_id}/stream", methods=["GET", "HEAD"])
async def proxy_stream(peer_id: int, media_id: str, request: Request, audio: int | None = None,
                       start: float = Query(0, ge=0, le=86400), transport: str = Query("native", pattern="^(native|remux)$"),
                       force_stream: bool = False, user: database.User = Depends(get_current_user)):
    peer = await asyncio.to_thread(_consumer_media, peer_id, media_id)
    params = {"start": start, "transport": transport, "force_stream": str(force_stream).lower()}
    if audio is not None:
        params["audio"] = audio
    return await _proxy(peer, f"/media/{media_id}/stream", request, params)


@router.get("/api/federation/peers/{peer_id}/media/{media_id}/subtitles/{index}")
async def proxy_subtitle(peer_id: int, media_id: str, index: int, request: Request, user: database.User = Depends(get_current_user)):
    if not 0 <= index < 50:
        raise HTTPException(404, "Subtitle not found")
    peer = await asyncio.to_thread(_consumer_media, peer_id, media_id)
    return await _proxy(peer, f"/media/{media_id}/subtitles/{index}", request, subtitle=True)


@router.get("/api/federation/peers/{peer_id}/profiles")
async def proxy_profiles(peer_id: int, user: database.User = Depends(get_current_user)):
    try:
        peer = await asyncio.to_thread(federation.get_peer, peer_id, playable=True)
        result = await federation.remote_json(peer, "/profiles", params={"limit": 100})
    except Exception as exc:
        raise HTTPException(503, "Node unavailable or profiles are not shared with this node") from exc
    items = []
    for item in result.get("items", [])[:100]:
        if not isinstance(item, dict) or item.get("visibility") != "federated":
            continue
        profile = {"id": str(item.get("id", ""))[:80], "display_name": str(item.get("display_name", ""))[:80],
                   "bio": str(item.get("bio", ""))[:500], "node_name": peer.name}
        if item.get("show_metrics") is True and isinstance(item.get("metrics"), dict):
            profile["metrics"] = {key: value for key, value in item["metrics"].items()
                                  if key in {"entries_started", "movies_completed", "episodes_completed", "favorites"}
                                  and type(value) is int and 0 <= value <= 10**9}
        items.append(profile)
    return {"items": items}


async def close_tasks():
    for task in list(_tasks):
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
