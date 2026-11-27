from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from services.tmdb_service import TMDBService


@pytest.fixture
def service_factory(tmp_path):
    instances = []

    def build(handler, **options):
        service = TMDBService(api_key="test-key", cache_path=tmp_path / "tmdb.sqlite3",
                              transport=httpx.MockTransport(handler), **options)
        instances.append(service)
        return service

    yield build
    for service in instances:
        service.close()


def test_discover_media_forwards_page_and_language(service_factory):
    def handler(request):
        assert request.url.path == "/3/discover/movie"
        assert request.url.params["page"] == "3"
        assert request.url.params["language"] == "es-ES"
        return httpx.Response(200, json={"results": [{"id": 1}]})

    service = service_factory(handler)
    assert service.discover_media("movie", page=3, language="es") == [{"id": 1}]


def test_startup_does_not_contact_tmdb_and_cached_data_survives_restart(service_factory):
    calls = []
    service = service_factory(lambda request: calls.append(request) or httpx.Response(
        200, json={"title": "Título", "original_title": "Title"}))
    assert calls == []
    assert service.get_movie_details(3, "es")["title"] == "Título"
    service.close()

    def forbidden(request):
        pytest.fail("A fresh cached entry should survive restarting the provider")
    reopened = service_factory(forbidden)
    details = reopened.get_movie_details(3, "es-ES")
    assert details["original_title"] == "Title"
    assert len(calls) == 1


def test_tmdb_cache_evicts_lru_under_entry_and_byte_limits(service_factory):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"title": "x" * 60})

    service = service_factory(handler, max_entries=2, max_bytes=160)
    service.get_movie_details(1)
    service.get_movie_details(2)
    service.get_movie_details(1)
    service.get_movie_details(3)
    service.get_movie_details(1)
    assert calls == ["/3/movie/1", "/3/movie/2", "/3/movie/3"]
    service.get_movie_details(2)
    assert calls[-1] == "/3/movie/2"

    smaller = service_factory(handler, max_entries=10, max_bytes=80)
    smaller.get_movie_details(4)
    smaller.get_movie_details(2)
    assert calls[-2:] == ["/3/movie/4", "/3/movie/2"]


def test_parallel_cold_requests_share_one_call(service_factory):
    started, release = Event(), Event()
    calls = []

    def handler(request):
        calls.append(request)
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json={"title": "Example"})

    service = service_factory(handler)
    with ThreadPoolExecutor(max_workers=8) as callers:
        results = [callers.submit(service.get_movie_details, 3) for _ in range(8)]
        try:
            assert started.wait(2)
        finally:
            release.set()
        assert [result.result(timeout=5)["title"] for result in results] == ["Example"] * 8
    assert len(calls) == 1


@pytest.mark.parametrize("date_header", (False, True))
def test_stale_data_is_returned_while_refresh_is_running_and_429_honors_retry_after(service_factory, date_header):
    now = 1_000_000
    started, release = Event(), Event()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, json={"title": "Cached"})
        if len(calls) == 2:
            started.set()
            assert release.wait(5)
            header = format_datetime(datetime.fromtimestamp(now + 120, timezone.utc), usegmt=True) if date_header else "120"
            return httpx.Response(429, headers={"Retry-After": header})
        return httpx.Response(200, json={"title": "Updated"})

    service = service_factory(handler, clock=lambda: now)
    assert service.get_movie_details(1)["title"] == "Cached"
    now += 24 * 3600 + 1
    assert service.get_movie_details(1)["title"] == "Cached"
    assert started.wait(2)
    refresh = next(iter(service._inflight.values()))
    release.set()
    refresh.result(timeout=5)
    assert service.get_movie_details(2) is None
    assert len(calls) == 2
    now += 121
    assert service.get_movie_details(2)["title"] == "Updated"


@pytest.mark.parametrize("failure", (httpx.ReadTimeout("sensitive value"), 503))
def test_network_failures_keep_stale_metadata_and_do_not_leak_credentials(service_factory, failure, caplog):
    now = 1_000_000
    calls = []
    started = Event()

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"title": "Cached"})
        started.set()
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure)

    service = service_factory(handler, clock=lambda: now)
    with caplog.at_level("INFO"):
        assert service.get_movie_details(1)
        now += 24 * 3600 + 1
        assert service.get_movie_details(1)["title"] == "Cached"
        assert started.wait(2)
        service.close()
    assert len(calls) == 2
    assert "test-key" not in caplog.text
    assert "sensitive value" not in caplog.text


def test_tmdb_enrichment_prefers_exact_year_and_refuses_ambiguous_remakes(monkeypatch):
    service = TMDBService(api_key="test-key")
    candidates = [
        {"id": 10, "title": "Recuerdos", "release_date": "2022-01-01"},
        {"id": 20, "title": "Memories", "original_title": "Recuerdos", "release_date": "2023-01-01"},
    ]
    monkeypatch.setattr(service, "search_movie", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(service, "get_movie_details", lambda *args, **kwargs: None)
    assert service.enrich_media("Recuerdos", 2023)["tmdb_id"] == 20
    assert service.enrich_media("Recuerdos") is None


def test_local_series_details_and_specials_are_available_when_tmdb_is_disabled(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from config import settings
    from routers import downloads, tmdb
    from services.media_index import get_index

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "DOWNLOADS_ROOT", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "TMDB_API_KEY", "")
    monkeypatch.setattr(settings, "TMDB_TOKEN", "")
    root = tmp_path / "downloads"
    root.mkdir()
    (root / "episode.mkv").write_bytes(b"video")
    get_index(root).set_override("episode.mkv", {
        "title": "Example", "localized_title": "Ejemplo", "original_title": "Example",
        "media_type": "series", "season_number": 0, "episode_numbers": [1, 2], "tmdb_id": 6,
    })
    app = FastAPI()
    app.include_router(tmdb.router)
    app.include_router(downloads.router)
    app.dependency_overrides[downloads.get_current_user] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        details = client.get("/api/tmdb/tv/6?lang=es").json()
        assert details["name"] == "Ejemplo"
        assert details["seasons"] == [{"season_number": 0, "episode_count": 2}]
        season = client.get("/api/tmdb/tv/6/season/0?lang=es").json()
        assert [part["episode_number"] for part in season["episodes"]] == [1, 2]
        match = client.get("/api/downloads/files/match", params={
            "title": details["name"], "tmdb_id": 6, "media_type": "tv", "season": 0, "episode": 2,
        }).json()["match"]
        assert match["episode_number"] == 2
        clip = client.get(match["url"], headers={"Range": "bytes=0-1"})
        assert clip.status_code == 206 and clip.content == b"vi"
        assert client.get("/api/tmdb/tv/7?lang=es").status_code == 503


def test_admin_key_switch_is_visible_to_the_running_provider(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from config import settings
    from routers import admin, tmdb

    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "TMDB_API_KEY", "")
    monkeypatch.setattr(settings, "TMDB_TOKEN", "test-bearer")
    seen = []

    def handler(request):
        seen.append((request.url.params.get("api_key"), request.headers.get("Authorization")))
        return httpx.Response(200, json={"title": "Example"})

    provider = TMDBService(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(admin, "tmdb_service", provider)
    monkeypatch.setattr(tmdb, "tmdb_service", provider)
    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(tmdb.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: None
    try:
        with TestClient(app) as client:
            url = "/admin/api/tmdb-key"
            assert client.get("/api/tmdb/movie/1").status_code == 200
            assert client.put(url, json={"api_key": "  "}).status_code == 422
            assert client.put(url, json={"api_key": " file-key "}).json()["source"] == "file"
            assert client.get("/api/tmdb/movie/2").status_code == 200
            assert client.delete(url).json() == {"configured": True, "source": "environment"}
            assert client.get("/api/tmdb/movie/3").status_code == 200
        assert seen == [(None, "Bearer test-bearer"), ("file-key", None), (None, "Bearer test-bearer")]
    finally:
        provider.close()
