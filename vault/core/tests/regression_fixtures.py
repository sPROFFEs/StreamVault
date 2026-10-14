"""Offline browser fixtures shared by isolated feature tests."""
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright
from starlette.requests import Request

from shared_templates import templates


@pytest.fixture
def baseline_payloads():
    movie = {"id": 7, "title": "Fixture Movie", "media_type": "movie", "release_date": "2024-01-01", "vote_average": 8.5, "poster_path": None}
    series = {"id": 8, "name": "Fixture Series", "media_type": "tv", "first_air_date": "2023-01-01", "vote_average": 7.5, "poster_path": None}
    return {
        "/api/preferences": (200, {}),
        "/api/library/favorites": (200, {"items": []}),
        "/api/tmdb/genres/movie": (200, [{"id": 18, "name": "Drama"}]),
        "/api/tmdb/genres/tv": (200, [{"id": 18, "name": "Drama"}]),
        "/api/tmdb/discover/movie": (200, [movie]),
        "/api/tmdb/discover/tv": (200, [series]),
        "/api/tmdb/search/movie": (200, [movie]),
        "/api/tmdb/search/tv": (200, [series]),
        "/admin/api/tunnel/status": (200, {"running": False, "connected": False, "installed": False}),
        "/admin/api/tmdb-key": (200, {"configured": False, "source": None}),
        "/admin/system/update/status": (200, {"state": "available", "message": "Fixture update available", "repository": "https://github.com/fixture/project.git", "remote": "configured", "branch": "release", "local_commit": "a" * 40, "remote_commit": "b" * 40}),
        "/admin/system/update/config": (200, {"mode": "branch", "provider": "auto", "api_url": "", "repository": "", "automatic_checks": True, "check_interval_hours": 24, "has_token": False}),
        "/admin/system/update/check": (200, {"state": "current", "message": "Fixture branch is current"}),
        "/admin/system/update/git-pull": (202, {"state": "updating", "message": "Fixture worker queued"}),
        "/admin/system/update/docker-restart": (202, {"state": "updating", "message": "Fixture restart queued"}),
        "/admin/system/update/docker-recreate": (202, {"state": "updating", "message": "Fixture rebuild queued"}),
        "/api/health": (200, {"status": "ok"}),
        "/api/downloads/files/playback": (200, {"mode": "file", "offset": 0, "url": "/__test__/video.mp4", "duration_seconds": 100}),
    }


@pytest.fixture
def regression_browser():
    # The sync API owns an asyncio loop until stopped. Session scope would
    # leave that loop active during unrelated tests calling asyncio.run().
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def regression_server(regression_client):
    """Loopback ASGI server for real form submissions and browser redirects."""
    import main
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started, "Regression ASGI server did not start"
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "Regression ASGI server did not stop"


@pytest.fixture
def baseline_page(regression_browser, browser_viewport, regression_server, baseline_payloads):
    """Real templates/assets/auth; APIs are mutable fixtures, with no network."""
    assets = Path(__file__).resolve().parents[2] / "assets"
    context = regression_browser.new_context(viewport=browser_viewport, service_workers="block")
    page = context.new_page()
    errors, unexpected, requests = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def respond(route):
        request = route.request
        url = urlsplit(request.url)
        requests.append((request.method, url.path, url.query))
        if url.netloc != urlsplit(regression_server).netloc:
            unexpected.append(request.url)
            return route.fulfill(status=404)
        if url.path.startswith("/assets/"):
            asset = (assets / url.path.removeprefix("/assets/")).resolve()
            if asset.is_relative_to(assets) and asset.is_file():
                return route.fulfill(path=asset)
            unexpected.append(request.url)
            return route.fulfill(status=404)
        if url.path in baseline_payloads:
            status, payload = baseline_payloads[url.path]
            return route.fulfill(status=status, json=payload)
        if url.path.rstrip("/") == "/admin":
            user = SimpleNamespace(username="baseline-admin", is_admin=True, can_download=True)
            scope = {"type": "http", "headers": [(b"cookie", b"lang=en")], "state": {"user": user}, "query_string": url.query.encode()}
            html = templates.get_template("admin.html").render(request=Request(scope), username=user.username)
            return route.fulfill(content_type="text/html", body=html)
        if url.path == "/__test__/player":
            html = '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><video id="video" controls muted></video><script src="/assets/js/local-player.js"></script></html>'
            return route.fulfill(content_type="text/html", body=html)
        if url.path == "/__test__/video.mp4":
            return route.fulfill(status=204)
        if url.path in {"/login", "/logout", "/browse", "/settings"}:
            # Chromium follows fulfilled redirects outside Playwright routing.
            # A real loopback server preserves normal redirect/cookie behavior.
            return route.continue_()
        unexpected.append(request.url)
        return route.fulfill(status=404, json={"detail": "Missing deterministic fixture"})

    context.route("**/*", respond)

    def login(username="baseline-viewer"):
        import auth
        context.add_cookies([{"name": "access_token", "value": auth.create_access_token({"sub": username}), "url": regression_server, "httpOnly": True}])

    try:
        yield SimpleNamespace(page=page, base_url=regression_server, requests=requests, payloads=baseline_payloads, login=login)
    finally:
        context.close()
        assert not errors, errors
        assert not unexpected, unexpected
