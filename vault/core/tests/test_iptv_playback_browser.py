"""Opt-in real HLS playback against an upstream that deliberately has no CORS."""
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx
from playwright.sync_api import expect, sync_playwright
import pytest
import uvicorn

from routers import media_local
from shared_templates import templates
from regression_fixtures import CHROMIUM_ARGS


@contextmanager
def running_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


@pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1" or not os.getenv("STREAMVAULT_HLS_JS"), reason="Opt-in browser check with local Hls.js bundle")
@pytest.mark.parametrize('screen', ['iptv', 'detail'])
def test_real_iptv_hls_plays_without_upstream_cors(tmp_path, monkeypatch, browser_viewport, screen):
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "8",
        "-c:v", "libx264", "-profile:v", "baseline", "-g", "10", "-threads", "1",
        "-c:a", "aac", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
        str(tmp_path / "video.m3u8"),
    ], check=True, capture_output=True, timeout=20)
    upstream = FastAPI()

    @upstream.get("/live/master.m3u8")
    async def master():
        return Response('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=250000\nvariant\n', media_type="application/vnd.apple.mpegurl")

    @upstream.get("/live/variant")
    async def variant():
        # No extension or HLS MIME: the rewritten master must identify this
        # resource as another playlist, not leave its segments cross-origin.
        return FileResponse(tmp_path / "video.m3u8", media_type="application/octet-stream")

    upstream.mount("/live", StaticFiles(directory=tmp_path))
    assets = Path(__file__).resolve().parents[2] / "assets"
    app = FastAPI()
    app.include_router(media_local.router)
    app.mount("/assets", StaticFiles(directory=assets))

    @app.get("/iptv")
    async def index(request: Request):
        return HTMLResponse(templates.get_template("iptv.html").render(request=request, is_admin=False))

    @app.get('/media/7')
    async def detail(request: Request):
        return HTMLResponse(templates.get_template('media_detail.html').render(request=request, tmdb_id=7, media_type='movie'))

    # Only this isolated test may proxy its local fixture. Production rejects
    # loopback/private upstreams, including redirects (see test_proxy_stream).
    monkeypatch.setattr(media_local, "is_safe_external_url", lambda _: True)
    with running_server(upstream) as origin, running_server(app) as site:
        manifest_url = f"{origin}/live/master.m3u8"
        assert "access-control-allow-origin" not in httpx.get(manifest_url).headers
        channel = {"id": "test-hls", "name": "Test live channel", "group": "Test", "source": "test", "stream_url": manifest_url}

        def iptv_api(route):
            path = urlsplit(route.request.url).path
            if path == "/api/iptv/channels":
                return route.fulfill(json={"total": 1, "channels": [channel]})
            if path in ("/api/iptv/favorites", "/api/iptv/recents"):
                return route.fulfill(json={"items": []})
            if path in ("/api/iptv/sources", "/api/iptv/countries", "/api/iptv/languages"):
                return route.fulfill(json=[])
            return route.fulfill(json={})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
            try:
                page = browser.new_page(viewport=browser_viewport)
                errors, requested = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requested.append(request.url))
                page.route("**/api/iptv/**", iptv_api)
                page.route("**/hls.min.js", lambda route: route.fulfill(path=os.environ["STREAMVAULT_HLS_JS"], content_type="application/javascript"))
                if screen == 'iptv':
                    page.goto(f"{site}/iptv")
                    page.locator(".iptv-card").click()
                    page.wait_for_function("document.querySelector('#iptv-player').currentTime > 1.5", timeout=15000)
                    expect(page.locator("#iptv-player-error")).to_be_hidden()
                else:
                    page.goto(f'{site}/media/7')
                    assert not any('hls.min.js' in url for url in requested)
                    proxy_url = '/api/local/proxy-stream?url=' + quote(manifest_url, safe='')
                    # No .m3u8 extension in the proxy URL; the query retains it.
                    page.evaluate('url => loadSource({type: "direct", url})', proxy_url)
                    page.wait_for_function("document.querySelector('#media-video').currentTime > 1.5", timeout=15000)
                    page.evaluate('window.closedVideo = document.querySelector("#media-video"); destroyPlayer()')
                    assert page.evaluate('closedVideo.paused && state.hls === null')
                assert any("video0.ts" in url for url in requested)
                assert all(not url.startswith(origin) for url in requested), "Browser must not contact the non-CORS upstream"
                assert not errors
            finally:
                browser.close()
