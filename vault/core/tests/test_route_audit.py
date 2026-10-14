"""Registered page aliases must resolve to the retained templates and player."""
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from test_regression_baseline import sign_in


@pytest.mark.parametrize("path", ["/portal", "/browse", "/downloads", "/media/7",
    "/settings", "/youtube", "/iptv", "/anime", "/catalog-local", "/search",
    "/player/movie/1", "/admin", "/admin/workflows"])
def test_registered_pages_require_a_session(regression_client, path):
    response = regression_client.get(path)
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/login"


def test_search_alias_preserves_query_in_live_browse_route(regression_client):
    sign_in(regression_client)
    response = regression_client.get("/search", params={"q": "Niño & film"})
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/browse"
    assert parse_qs(location.query) == {"q": ["Niño & film"]}


def test_workflows_alias_redirects_without_missing_import(regression_client):
    sign_in(regression_client, "baseline-admin")
    response = regression_client.get("/admin/workflows")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_local_catalog_route_serves_the_local_library(regression_client):
    sign_in(regression_client)
    response = regression_client.get("/catalog-local")
    assert response.status_code == 200
    assert 'id="catalog-search"' in response.text
    assert 'id="media-grid"' in response.text
    assert "baseline-viewer" in response.text


def test_registered_anime_route_keeps_external_redirect(regression_client):
    sign_in(regression_client)
    response = regression_client.get("/anime")
    assert response.status_code == 302
    assert response.headers["location"] == "https://www.enma.lol"


@pytest.mark.parametrize("media_type,expected", [("movie", "movie"), ("series", "tv"), ("tv", "tv")])
def test_legacy_player_uses_indexed_tmdb_id(regression_client, monkeypatch, media_type, expected):
    from routers import media_local

    session = Mock()
    session.query.return_value.filter.return_value.first.return_value = SimpleNamespace(tmdb_id=77)
    monkeypatch.setattr(media_local, "SessionLocal", lambda: session)
    sign_in(regression_client)
    response = regression_client.get(f"/player/{media_type}/2")
    assert response.status_code == 303
    assert response.headers["location"] == f"/media/77?type={expected}"
    session.close.assert_called_once()


@pytest.mark.parametrize("item", [None, SimpleNamespace(tmdb_id=None)])
def test_legacy_player_missing_metadata_returns_not_found(regression_client, monkeypatch, item):
    from routers import media_local

    session = Mock()
    session.query.return_value.filter.return_value.first.return_value = item
    monkeypatch.setattr(media_local, "SessionLocal", lambda: session)
    sign_in(regression_client)
    assert regression_client.get("/player/movie/2").status_code == 404
    session.close.assert_called_once()
