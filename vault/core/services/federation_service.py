"""Private, versioned video federation. Import metadata, never duplicate media.

All network targets are admin-configured, DNS-pinned and non-redirecting. Media
requests are streamed with bounded buffers, preserving Range and disconnects.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError

import database
from config import settings
from services.media_library import scan_media_library, resolve_library_path, is_incomplete_media, _normalized_title

logger = logging.getLogger(__name__)
PROTOCOL = 1
PAGE_SIZE = 100
MAX_ITEMS = 10000
MAX_JSON_BYTES = 2 * 1024**2
HEALTH_INTERVAL = 60
SYNC_INTERVAL = 15 * 60
PLAYABLE_WINDOW = 180
_syncing: set[int] = set()
_catalog_cache = {}
_catalog_lock = threading.Lock()


class CatalogItem(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    tmdb_id: int | None = Field(None, gt=0)
    media_type: str = Field(pattern="^(movie|tv)$")
    title: str = Field(min_length=1, max_length=255)
    year: int | None = Field(None, ge=1800, le=2200)
    season_number: int | None = Field(None, ge=0, le=200)
    episode_number: int | None = Field(None, ge=1, le=10000)
    size_bytes: int = Field(0, ge=0)
    quality: str = Field("auto", max_length=50)
    poster_path: str | None = Field(None, max_length=1000)


def node_config():
    path = Path(settings.DATA_ROOT) / "config/federation.json"
    try:
        return {"enabled": False, "node_name": "StreamVault", **json.loads(path.read_text())}
    except FileNotFoundError:
        return {"enabled": False, "node_name": "StreamVault"}


def save_node_config(patch):
    path = Path(settings.DATA_ROOT) / "config/federation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {**node_config(), **patch}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config))
    temporary.replace(path)
    return config


def _cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()))


def encrypt_token(token):
    return _cipher().encrypt(token.encode()).decode()


def peer_token(peer):
    try:
        return _cipher().decrypt((peer.token_ciphertext or "").encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Peer credential cannot be decrypted; re-enter it after changing SECRET_KEY.") from exc


def _addresses(host, port, allow_private=False):
    if host.lower() in {"localhost", "core", "nginx", "downloader", "cloudflared", "host.docker.internal"}:
        raise ValueError("Internal service targets are not allowed.")
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(entry[4][0] for entry in infos))
    if not addresses:
        raise ValueError("Peer hostname could not be resolved.")
    private_networks = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                        ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        lan = allow_private and any(ip in network for network in private_networks)
        if not ip.is_global and not lan:
            raise ValueError("Peer must use a public address; LAN requires explicit permission. Loopback and metadata addresses are forbidden.")
    return addresses


def validate_peer_url(url, allow_private=False):
    parsed = urlsplit(url.strip())
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "\\" in url or any(ord(c) < 32 for c in url)):
        raise ValueError("Use an HTTP(S) node URL without credentials, query or fragment.")
    if any(part in {".", ".."} for part in parsed.path.split("/")) or "%" in parsed.path:
        raise ValueError("Invalid node base path.")
    try:
        addresses = _addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), allow_private)
        _require_secure_transport(parsed, addresses)
    except (OSError, ValueError) as exc:
        raise ValueError("Unsafe or unresolvable node address.") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _require_secure_transport(parsed, addresses):
    if parsed.scheme != "https" and any(ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Public federation nodes require HTTPS to protect bearer credentials.")


async def remote_response(peer, route, *, params=None, headers=None, method="GET"):
    if not route.startswith("/") or ".." in route or "?" in route:
        raise ValueError("Invalid federation route.")
    parsed = urlsplit(peer.base_url)
    addresses = await asyncio.to_thread(_addresses, parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80), peer.allow_private)
    _require_secure_transport(parsed, addresses)
    original = httpx.URL(peer.base_url + "/api/federation/v1" + route)
    # Pin this request to a validated IP; preserve HTTP Host and TLS SNI.
    url = original.copy_with(host=addresses[0])
    request_headers = {"Authorization": "Bearer " + peer_token(peer), "Host": original.netloc.decode(),
                       "Accept-Encoding": "identity", **(headers or {})}
    client = httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5, read=90 if route.endswith("/stream") else 20), follow_redirects=False,
                              trust_env=False, limits=httpx.Limits(max_connections=2))
    try:
        request = client.build_request(method, url, params=params, headers=request_headers,
                                       extensions={"sni_hostname": parsed.hostname})
        response = await client.send(request, stream=True)
        if response.is_redirect:
            raise ValueError("Federation redirects are not allowed.")
        return client, response
    except BaseException:
        await client.aclose()
        raise


async def remote_json(peer, route, *, params=None):
    client, response = await remote_response(peer, route, params=params)
    try:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise ValueError("Compressed federation metadata is not accepted.")
        body = bytearray()
        async for chunk in response.aiter_bytes(64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_JSON_BYTES:
                raise ValueError("Federation response exceeds the metadata limit.")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Invalid federation response.")
        return data
    finally:
        await response.aclose()
        await client.aclose()


def indexed_metadata():
    try:
        return _indexed_metadata()
    except SQLAlchemyError:
        # The workflow catalog is optional. Filesystem downloads remain usable
        # on installations that never initialized/imported that separate DB.
        return {}


def _indexed_metadata():
    from models.base import SessionLocal
    from models.media import Episode
    from models.sources import StreamSource
    result = {}
    with SessionLocal() as db:
        sources = db.query(StreamSource).filter(StreamSource.download_status == "complete",
            StreamSource.local_path.isnot(None)).options(joinedload(StreamSource.movie),
            joinedload(StreamSource.episode).joinedload(Episode.series)).all()
        for source in sources:
            path = Path(source.local_path).resolve()
            media = source.movie or (source.episode.series if source.episode else None)
            if media:
                result[str(path)] = {"tmdb_id": media.tmdb_id, "title": media.title,
                    "poster_path": media.poster_path, "quality": source.quality or "auto"}
    return result


def published_catalog():
    key = (settings.DOWNLOADS_ROOT, settings.SECRET_KEY)
    with _catalog_lock:
        cached = _catalog_cache.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
        records = _build_catalog()
        _catalog_cache.clear()
        _catalog_cache[key] = (time.monotonic(), records)
        return records


def _build_catalog():
    metadata = indexed_metadata()
    records = []
    for entry in scan_media_library(settings.DOWNLOADS_ROOT)["files"]:
        try:
            path = resolve_library_path(settings.DOWNLOADS_ROOT, entry["relative_path"])
        except (ValueError, OSError):
            continue
        identity = hmac.new(settings.SECRET_KEY.encode(), entry["relative_path"].encode(), hashlib.sha256).hexdigest()
        record = {"id": identity, "title": entry["title"], "media_type": "tv" if entry["media_type"] == "series" else "movie",
                  "year": entry.get("year"), "season_number": entry.get("season_number"),
                  "episode_number": entry.get("episode_number"), "size_bytes": entry["size_bytes"],
                  **metadata.get(str(path), {})}
        records.append((record, entry["relative_path"]))
    return sorted(records, key=lambda item: item[0]["id"])


def media_path(remote_id):
    if not re.fullmatch(r"[a-f0-9]{64}", remote_id):
        raise FileNotFoundError("Media not found")
    for record, relative in published_catalog():
        if record["id"] == remote_id:
            path = resolve_library_path(settings.DOWNLOADS_ROOT, relative)
            if path.is_file() and not is_incomplete_media(path, settings.DOWNLOADS_ROOT):
                return relative
    raise FileNotFoundError("Media not found")


def get_peer(peer_id, *, playable=False):
    with database.SessionLocal() as db:
        peer = db.get(database.FederationPeer, peer_id)
        if not peer or not peer.enabled:
            raise LookupError("Node is disabled or missing.")
        if playable and (peer.status != "online" or time.time() - (peer.last_seen_at or 0) > PLAYABLE_WINDOW):
            raise LookupError("Node is offline. Synchronize it before playing.")
        db.expunge(peer)
        return peer


def peer_view(peer, *, admin=False):
    result = {"id": peer.id, "name": peer.name, "enabled": peer.enabled, "status": peer.status,
              "last_seen_at": peer.last_seen_at, "last_synced_at": peer.last_synced_at, "syncing": peer.id in _syncing}
    if admin:
        result.update(base_url=peer.base_url, allow_private=peer.allow_private,
                      has_token=bool(peer.token_ciphertext), last_error=peer.last_error)
    return result


def mark_offline(peer_id):
    with database.SessionLocal() as db:
        peer = db.get(database.FederationPeer, peer_id)
        if peer:
            peer.status = "offline"
            peer.last_error = "Node unavailable, access revoked or invalid response. Check its URL and credential."
            db.commit()


def _store_catalog(peer_id, items):
    with database.SessionLocal() as db:
        peer = db.get(database.FederationPeer, peer_id)
        if not peer or not peer.enabled:
            return
        db.query(database.FederatedMedia).filter_by(peer_id=peer_id).delete()
        for item in items:
            data = item.model_dump()
            data["remote_id"] = data.pop("id")
            db.add(database.FederatedMedia(peer_id=peer_id, title_key=_normalized_title(item.title), **data))
        peer.status, peer.last_error = "online", None
        peer.last_seen_at = peer.last_synced_at = time.time()
        db.commit()


async def sync_peer(peer_id):
    if peer_id in _syncing:
        return {"state": "syncing"}
    _syncing.add(peer_id)
    try:
        started = time.monotonic()
        peer = await asyncio.to_thread(get_peer, peer_id)
        health = await remote_json(peer, "/health")
        if health.get("protocol") != PROTOCOL:
            raise ValueError("Unsupported federation protocol.")
        items, ids, cursors = [], set(), set()
        cursor = ""
        while True:
            page = await remote_json(peer, "/catalog", params={"after": cursor, "limit": PAGE_SIZE})
            if time.monotonic() - started > 120:
                raise TimeoutError("Catalog synchronization deadline exceeded.")
            entries = page.get("items")
            if not isinstance(entries, list) or len(entries) > PAGE_SIZE:
                raise ValueError("Invalid catalog page.")
            for entry in entries:
                item = CatalogItem.model_validate(entry)
                if item.id in ids or (item.media_type == "tv" and (item.season_number is None or item.episode_number is None)):
                    raise ValueError("Invalid or duplicate catalog identity.")
                ids.add(item.id)
                items.append(item)
            if len(items) > MAX_ITEMS:
                raise ValueError("Node catalog exceeds the configured safety limit.")
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or not re.fullmatch(r"[a-f0-9]{64}", cursor) or cursor in cursors:
                raise ValueError("Invalid catalog cursor.")
            cursors.add(cursor)
            await asyncio.sleep(0.05)
        await asyncio.to_thread(_store_catalog, peer_id, items)
        return {"state": "success", "items": len(items)}
    except asyncio.CancelledError:
        raise
    except Exception:
        await asyncio.to_thread(mark_offline, peer_id)
        return {"state": "failure", "message": "Node synchronization failed. Previous metadata was preserved."}
    finally:
        _syncing.discard(peer_id)


def match_sources(tmdb_id, media_type, season=None, episode=None, *, title=None, original_title=None, year=None):
    from providers.base import StreamResult
    with database.SessionLocal() as db:
        query = db.query(database.FederatedMedia).join(database.FederationPeer).filter(
            database.FederationPeer.enabled.is_(True), database.FederationPeer.status == "online",
            database.FederationPeer.last_seen_at >= time.time() - PLAYABLE_WINDOW,
            database.FederatedMedia.media_type == media_type)
        from sqlalchemy import or_, and_, false
        identity = int(tmdb_id) if str(tmdb_id).isdigit() and len(str(tmdb_id)) <= 10 else None
        identity_match = database.FederatedMedia.tmdb_id == identity if identity is not None else false()
        keys = {_normalized_title(value) for value in (title, original_title) if isinstance(value, str) and _normalized_title(value)}
        if keys:
            query = query.filter(or_(identity_match, and_(database.FederatedMedia.tmdb_id.is_(None), database.FederatedMedia.title_key.in_(keys))))
        elif identity is not None:
            query = query.filter(database.FederatedMedia.tmdb_id == identity)
        else:
            return []
        if media_type == "tv":
            if season is None or episode is None:
                return []
            query = query.filter(database.FederatedMedia.season_number == season, database.FederatedMedia.episode_number == episode)
        if year:
            query = query.filter(or_(identity_match, database.FederatedMedia.year == year, database.FederatedMedia.year.is_(None)))
        sources = []
        for item in query.options(joinedload(database.FederatedMedia.peer)).order_by(database.FederatedMedia.peer_id).limit(20):
            base = f"/api/federation/peers/{item.peer_id}/media/{item.remote_id}"
            sources.append(StreamResult(url=base + "/stream", playback_url=base + "/playback", metadata_url=base + "/metadata",
                type="federation", source="Node · " + item.peer.name,
                quality=item.quality, size_bytes=item.size_bytes, peer_id=item.peer_id, media_id=item.remote_id))
        return sources


async def background_loop():
    # Delay heavy work until HTTP is ready; one peer at a time, bounded timeouts.
    await asyncio.sleep(10)
    while True:
        try:
            peers = await asyncio.to_thread(_peer_snapshot)
        except SQLAlchemyError:
            await asyncio.sleep(HEALTH_INTERVAL)
            continue
        for peer_id, synced_at in peers:
            try:
                peer = await asyncio.to_thread(get_peer, peer_id)
            except LookupError:
                continue  # An administrator may disable/delete a queued peer.
            if not synced_at or peer.status != "online" or time.time() - synced_at >= SYNC_INTERVAL:
                await sync_peer(peer_id)
            else:
                try:
                    health = await remote_json(peer, "/health")
                    if health.get("protocol") != PROTOCOL:
                        raise ValueError("Invalid health response")
                    await asyncio.to_thread(_touch_peer, peer_id)
                except Exception:
                    await asyncio.to_thread(mark_offline, peer_id)
            await asyncio.sleep(1)
        await asyncio.sleep(HEALTH_INTERVAL)


def _peer_snapshot():
    with database.SessionLocal() as db:
        return [(peer.id, peer.last_synced_at) for peer in db.query(database.FederationPeer).filter_by(enabled=True).limit(20)]


def _touch_peer(peer_id):
    with database.SessionLocal() as db:
        peer = db.get(database.FederationPeer, peer_id)
        if peer and peer.enabled:
            peer.last_seen_at, peer.status = time.time(), "online"
            db.commit()
