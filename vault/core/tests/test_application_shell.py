"""Server-rendered navigation routes and active state, without a browser."""
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from starlette.requests import Request

from shared_templates import templates


def navigation(path="/browse", query=b"type=movie", admin=True):
    scope = {"type": "http", "path": path, "headers": [], "query_string": query,
             "state": {"user": SimpleNamespace(username="viewer", is_admin=admin, can_download=False)}}
    html = templates.get_template("_header.html").render(request=Request(scope), lang="en")
    return BeautifulSoup(html, "html.parser")


def test_every_top_level_destination_is_in_desktop_and_compact_navigation():
    soup = navigation()
    routes = {"/portal", "/browse?type=movie", "/browse?type=tv", "/iptv", "/anime", "/youtube",
              "/browse?type=movie&view=favorites", "/downloads", "/settings", "/admin"}
    for selectors in ((".top-nav__links", ".top-nav__actions"), (".mobile-tabbar", ".mobile-more-sheet")):
        links = {link["href"] for selector in selectors for link in soup.select(f"{selector} a[href]")}
        assert routes <= links
    assert soup.select_one('form[action="/logout"][method="post"]')
    assert not navigation(admin=False).select('a[href="/admin"]')


@pytest.mark.parametrize("path,query,target", [
    ("/portal", b"", "/portal"),
    ("/browse", b"type=tv", "/browse?type=tv"),
    ("/browse", b"type=movie&view=favorites", "/browse?type=movie&view=favorites"),
    ("/youtube", b"", "/youtube"),
    ("/settings", b"", "/settings"),
])
def test_selected_destination_has_semantic_current_state(path, query, target):
    soup = navigation(path, query)
    selected = soup.select('a[aria-current="page"]')
    assert selected
    assert {link["href"] for link in selected} == {target}


def test_shell_routes_resolve_with_existing_security_and_anime_redirect(regression_client, monkeypatch, tmp_path):
    import auth
    from routers import admin
    # Keep production-only disk paths and updater probes out of this route check.
    disk_usage = admin.shutil.disk_usage
    monkeypatch.setattr(admin.shutil, "disk_usage", lambda path: disk_usage(tmp_path))
    monkeypatch.setattr(admin.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="", returncode=0))
    regression_client.cookies.set("access_token", auth.create_access_token({"sub": "baseline-admin"}))
    for path in ("/portal", "/browse?type=movie", "/browse?type=tv", "/browse?type=movie&view=favorites",
                 "/iptv", "/youtube", "/downloads", "/settings", "/admin", "/media/7?type=movie"):
        response = regression_client.get(path, follow_redirects=True)
        assert response.status_code == 200, path
        assert BeautifulSoup(response.text, "html.parser").select_one("#top-nav"), path
    response = regression_client.get("/anime")
    assert response.status_code == 302
    assert response.headers["location"] == "https://www.enma.lol"
