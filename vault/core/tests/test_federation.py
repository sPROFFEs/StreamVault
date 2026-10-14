"""Private federation contract, complete episodes and bounded remote playback."""
import asyncio
from pathlib import Path

import pytest

import database
from config import settings
from services import federation_service as federation


@pytest.fixture
def federated_library(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    (root / "Example.2024.mp4").write_bytes(b"movie-data")
    (root / "Show.S01E02.mkv").write_bytes(b"episode-data")
    incomplete = root / "Incomplete"
    incomplete.mkdir()
    (incomplete / ".streamvault-incomplete").touch()
    (incomplete / "Hidden.S01E01.mkv").write_bytes(b"partial")
    monkeypatch.setattr(settings, "DOWNLOADS_ROOT", str(root))
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(federation, "indexed_metadata", lambda: {})
    with database.SessionLocal() as db:
        db.query(database.FederatedMedia).delete()
        db.query(database.FederationPeer).delete()
        db.query(database.FederationGrant).delete()
        db.commit()
    yield root


def login(client, admin=True):
    assert client.post("/login", data={"username": "baseline-admin" if admin else "baseline-viewer",
                                       "password": "Baseline123!"}).status_code == 303


def grant(client):
    login(client)
    assert client.put("/admin/api/federation/config", json={"enabled": True, "node_name": "Fixture node"}).status_code == 200
    result = client.post("/admin/api/federation/grants", json={"name": "Trusted peer", "share_media": True}).json()
    return result, {"Authorization": "Bearer " + result["token"]}


def test_publisher_requires_scoped_token_not_cookie(regression_client, federated_library):
    client = regression_client
    issued, headers = grant(client)
    assert client.get("/api/federation/v1/catalog").status_code == 401
    response = client.get("/api/federation/v1/catalog", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    episode = next(item for item in items if item["media_type"] == "tv")
    assert (episode["season_number"], episode["episode_number"]) == (1, 2)
    assert "relative_path" not in str(items)
    assert str(federated_library) not in response.text
    assert "Hidden" not in response.text
    assert issued["token"] not in client.get("/admin/api/federation/grants").text
    assert client.delete(f"/admin/api/federation/grants/{issued['id']}").status_code == 200
    assert client.get("/api/federation/v1/catalog", headers=headers).status_code == 401


def test_range_stream_and_incomplete_file_disappearance(regression_client, federated_library):
    client = regression_client
    _, headers = grant(client)
    item = next(item for item in client.get("/api/federation/v1/catalog", headers=headers).json()["items"]
                if item["media_type"] == "movie")
    response = client.get(f"/api/federation/v1/media/{item['id']}/stream", headers={**headers, "Range": "bytes=1-4"})
    assert response.status_code == 206
    assert response.content == b"ovie"
    (federated_library / "Example.2024.mp4").unlink()
    assert client.get(f"/api/federation/v1/media/{item['id']}/stream", headers=headers).status_code == 404


def test_admin_only_peer_configuration_and_encrypted_tokens(regression_client, federated_library, monkeypatch):
    monkeypatch.setattr(federation, "validate_peer_url", lambda url, allow_private=False: url.rstrip("/"))
    client = regression_client
    login(client, admin=False)
    assert client.post("/admin/api/federation/peers", json={"name": "Node", "base_url": "https://peer.test",
                        "token": "a" * 40}).status_code == 403
    login(client)
    response = client.post("/admin/api/federation/peers", json={"name": "Node", "base_url": "https://peer.test",
                            "token": "a" * 40})
    assert response.status_code == 201
    assert "a" * 40 not in response.text
    with database.SessionLocal() as db:
        peer = db.get(database.FederationPeer, response.json()["id"])
        assert peer.token_ciphertext != "a" * 40
        assert federation.peer_token(peer) == "a" * 40


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254", "http://localhost",
                                 "file:///etc/passwd", "https://user:pass@peer.test", "https://peer.test/?token=secret"])
def test_federation_rejects_unsafe_targets_even_with_lan_enabled(url):
    with pytest.raises(ValueError):
        federation.validate_peer_url(url, allow_private=True)


def test_sync_is_atomic_removes_deleted_media_and_hides_offline_sources(federated_library, monkeypatch):
    with database.SessionLocal() as db:
        peer = database.FederationPeer(name="Other node", base_url="https://peer.test", enabled=True)
        db.add(peer)
        db.commit()
        peer_id = peer.id
    items = [{"id": "a" * 64, "title": "Show", "media_type": "tv", "tmdb_id": 123,
              "season_number": 1, "episode_number": 2, "size_bytes": 10}]
    async def remote(peer, route, **kwargs):
        if route == "/health":
            return {"protocol": 1, "node_name": "Other node"}
        return {"items": items, "next_cursor": None}
    monkeypatch.setattr(federation, "remote_json", remote)
    asyncio.run(federation.sync_peer(peer_id))
    assert len(federation.match_sources("123", "tv", 1, 2)) == 1
    assert federation.match_sources("123", "tv", 1, 1) == []
    items.clear()
    asyncio.run(federation.sync_peer(peer_id))
    assert federation.match_sources("123", "tv", 1, 2) == []
    items.append({"id": "b" * 64, "title": "Movie", "media_type": "movie", "tmdb_id": 456})
    asyncio.run(federation.sync_peer(peer_id))
    async def unavailable(*args, **kwargs):
        raise RuntimeError("Do not show untrusted errors or tokens")
    monkeypatch.setattr(federation, "remote_json", unavailable)
    asyncio.run(federation.sync_peer(peer_id))
    assert federation.match_sources("456", "movie") == []
    with database.SessionLocal() as db:
        assert db.query(database.FederatedMedia).count() == 1
        assert db.get(database.FederationPeer, peer_id).status == "offline"


def test_profiles_private_by_default_and_metrics_require_opt_in(regression_client, federated_library, monkeypatch, tmp_path):
    from services.user_library_service import UserLibraryService
    from routers import profiles
    service = UserLibraryService(str(tmp_path / "users"))
    monkeypatch.setattr(profiles, "user_library_service", service)
    client = regression_client
    login(client, admin=False)
    own = client.get("/api/profiles/me").json()
    assert own["visibility"] == "private"
    viewer_id = own["id"]
    service.upsert_progress("baseline-viewer", {"tmdb_id": 1, "media_type": "movie", "title": "Movie", "completed": True})
    login(client)
    assert client.get(f"/api/profiles/{viewer_id}").status_code == 404
    login(client, admin=False)
    assert client.put("/api/profiles/me", json={"visibility": "local", "display_name": "Viewer"}).status_code == 200
    login(client)
    assert "metrics" not in client.get(f"/api/profiles/{viewer_id}").json()
    login(client, admin=False)
    client.put("/api/profiles/me", json={"show_metrics": True})
    login(client)
    assert client.get(f"/api/profiles/{viewer_id}").json()["metrics"]["movies_completed"] == 1
    _, headers = grant(client)
    assert client.get("/api/federation/v1/profiles", headers=headers).status_code == 403


def test_protocol_roundtrip_resolver_playback_and_proxy_range(regression_client, federated_library, monkeypatch):
    """Call the publisher ASGI app without a cookie, just like another node."""
    import httpx
    import main
    from routers import downloads, resolver
    client = regression_client
    issued, _ = grant(client)
    with database.SessionLocal() as db:
        peer = database.FederationPeer(name="Publisher", base_url="https://publisher.test", enabled=True,
            token_ciphertext=federation.encrypt_token(issued["token"]))
        db.add(peer)
        db.commit()
        peer_id = peer.id
    async def connection(peer, route, *, params=None, headers=None, method="GET"):
        connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app))
        request = connection.build_request(method, "https://publisher.test/api/federation/v1" + route,
            params=params, headers={"Authorization": "Bearer " + federation.peer_token(peer), **(headers or {})})
        return connection, await connection.send(request, stream=True)
    monkeypatch.setattr(federation, "remote_response", connection)
    monkeypatch.setattr(downloads, "_probe_media_tracks", lambda path: {"audio": [{"stream_index": 1, "language": "eng", "title": "English", "codec": "aac"}],
        "subtitles": [], "duration_seconds": 100, "video_codec": "h264"})
    async def no_external(**kwargs):
        return []
    monkeypatch.setattr(resolver.orchestrator, "resolve_stream", no_external)
    assert asyncio.run(federation.sync_peer(peer_id))["state"] == "success"
    sources = client.get("/api/stream/resolve/7?type=movie&title=Example&year=2024").json()
    assert len(sources) == 1 and sources[0]["type"] == "federation"
    source = sources[0]
    assert issued["token"] not in str(source)
    metadata = client.get(source["metadata_url"]).json()
    assert metadata["audio_tracks"][0]["language"] == "eng"
    playback = client.get(source["playback_url"], params={"start": 20, "audio": 1}).json()
    assert playback["mode"] == "file"
    assert playback["offset"] == 0
    assert playback["url"].startswith("/api/federation/peers/")
    response = client.get(playback["url"], headers={"Range": "bytes=1-4"})
    assert response.status_code == 206
    assert response.content == b"ovie"
    assert response.headers["content-range"] == "bytes 1-4/10"
    assert response.headers["cache-control"] == "private, no-store"
    assert "Authorization" not in response.headers
    assert client.patch(f"/admin/api/federation/peers/{peer_id}", json={"enabled": False}).status_code == 200
    assert client.get(playback["url"]).status_code == 503


def test_remote_connection_pins_dns_and_rejects_redirects(monkeypatch):
    import httpx
    from types import SimpleNamespace
    requests = []
    original = httpx.AsyncClient
    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})
    monkeypatch.setattr(federation, "_addresses", lambda *args: ["93.184.216.34"])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    peer = SimpleNamespace(base_url="https://publisher.test", allow_private=False,
                           token_ciphertext=federation.encrypt_token("test-private-token"))
    with pytest.raises(ValueError, match="redirect"):
        asyncio.run(federation.remote_response(peer, "/health"))
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "publisher.test"
    assert requests[0].extensions["sni_hostname"] == "publisher.test"


def test_lan_access_requires_explicit_permission_and_http_is_not_allowed_publicly(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.5", 8000))])
    with pytest.raises(ValueError):
        federation.validate_peer_url("http://node.test:8000")
    assert federation.validate_peer_url("http://node.test:8000", allow_private=True) == "http://node.test:8000"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 8000))])
    with pytest.raises(ValueError):
        federation.validate_peer_url("http://node.test:8000")


def test_download_sharing_does_not_require_the_optional_index(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import base
    engine = create_engine("sqlite:///" + str(tmp_path / "empty-index.db"))
    monkeypatch.setattr(base, "SessionLocal", sessionmaker(bind=engine))
    try:
        assert federation.indexed_metadata() == {}
    finally:
        engine.dispose()


def test_title_fallback_is_exact_and_supports_original_titles(federated_library):
    import time
    with database.SessionLocal() as db:
        peer = database.FederationPeer(name="Peer", base_url="https://peer.test", enabled=True,
                                       status="online", last_seen_at=time.time())
        db.add(peer)
        db.flush()
        for index, title in enumerate(["The Example", "Different Movie"]):
            db.add(database.FederatedMedia(peer_id=peer.id, remote_id=str(index) * 64, title=title,
                title_key=federation._normalized_title(title), media_type="movie", year=2024))
        db.commit()
    assert len(federation.match_sources("unknown", "movie", title="The Example")) == 1
    assert len(federation.match_sources("123", "movie", title="El Ejemplo", original_title="The Example", year=2024)) == 1
    assert federation.match_sources("123", "movie", title="Not present", year=2024) == []


def test_head_request_does_not_start_live_conversion(regression_client, federated_library, monkeypatch):
    from routers import downloads
    client = regression_client
    _, headers = grant(client)
    item = next(item for item in client.get("/api/federation/v1/catalog", headers=headers).json()["items"] if item["media_type"] == "tv")
    async def forbidden_conversion(*args, **kwargs):
        pytest.fail("HEAD must never start a live video encoder")
    monkeypatch.setattr(downloads, "remux_downloaded_media", forbidden_conversion)
    response = client.head(f"/api/federation/v1/media/{item['id']}/stream?transport=remux&force_stream=true", headers=headers)
    assert response.status_code == 200
    assert response.content == b""


def test_cancelled_upstream_open_releases_proxy_slot(monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from routers import federation as routes
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(federation, "remote_response", cancelled)
    before = routes._proxy_slots._value
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(routes._proxy(SimpleNamespace(id=1), "/health", Request({"type": "http", "method": "GET", "headers": []})))
    assert routes._proxy_slots._value == before
