"""Opt-in browser playback check: STREAMVAULT_BROWSER_TESTS=1, Chromium and FFmpeg."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from playwright.sync_api import sync_playwright
import uvicorn

from routers import downloads
from regression_fixtures import CHROMIUM_ARGS


@pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1", reason="Opt-in browser check")
def test_browser_plays_seeks_and_resumes_uncached_and_cached_video(tmp_path, monkeypatch):
    assert shutil.which("ffmpeg") and shutil.which("ffprobe")
    source = tmp_path / "Show.S01E01.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "40",
        "-c:v", "libx264", "-g", "10", "-threads", "1", "-c:a", "ac3", str(source),
    ], check=True, capture_output=True, timeout=20)
    monkeypatch.setattr(downloads.settings, "DOWNLOADS_ROOT", str(tmp_path))
    monkeypatch.setattr(downloads.settings, "MEDIA_STORAGE_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(downloads, "_ensure_background_remux", lambda *_: None)
    monkeypatch.setattr(downloads, "_live_remux_slots", asyncio.Semaphore(2))
    cached = downloads._remux_cache_path(source, "0:1")
    app = FastAPI()
    app.include_router(downloads.router)
    app.dependency_overrides[downloads.get_current_user] = lambda: SimpleNamespace(is_admin=True)
    assets = Path(__file__).resolve().parents[2] / "assets"

    @app.get("/")
    async def index():
        css = (assets / "css/app.css").read_text() + (assets / "css/ui-extras.css").read_text()
        js = (assets / "js/local-player.js").read_text()
        return HTMLResponse(f'<style>{css}</style><div class="player-box" style="width:640px"><video id="video" controls muted></video></div><script>{js}</script>')

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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
            try:
                page = browser.new_page()
                remux_attempts = []
                def temporarily_busy(route):
                    remux_attempts.append(route.request.url)
                    if len(remux_attempts) == 1:
                        route.fulfill(status=503, json={"detail": "Playback is busy. Try again shortly."})
                    else:
                        route.continue_()
                page.route("**/api/downloads/files/remux?*", temporarily_busy)
                page.goto(f"http://127.0.0.1:{port}/")
                page.evaluate("""async () => {
                    window.source = {relative_path: 'Show.S01E01.mkv', duration_seconds: 40};
                    await SVLocalPlayback.open(document.querySelector('video'), source, {start: 5, audio: 1});
                }""")
                page.wait_for_function("document.querySelector('video').currentTime > 1", timeout=15000)
                assert len(remux_attempts) >= 2, "A temporary media 503 should recover without a page reload"
                assert page.evaluate("SVLocalPlayback.currentTime(document.querySelector('video'))") >= 6
                assert page.locator(".local-playback-timeline").count() == 0
                assert page.locator("video").evaluate("video => video.controls")
                page.evaluate("SVLocalPlayback.open(document.querySelector('video'), source, {start: 20, audio: 1})")
                page.wait_for_function("document.querySelector('video').dataset.playbackOffset === '20' && document.querySelector('video').currentTime > .5", timeout=15000)
                # Leave Chromium before invoking asyncio; Playwright's sync API
                # owns an event loop while this context is active.
                page.evaluate("SVLocalPlayback.close(document.querySelector('video'))")
            finally:
                browser.close()

        asyncio.run(downloads._build_remux_cache(source, "0:1", cached))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
            try:
                assert cached.is_file()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/")
                page.evaluate("window.source = {relative_path: 'Show.S01E01.mkv', duration_seconds: 40}")
                page.evaluate("SVLocalPlayback.open(document.querySelector('video'), source, {start: 25, audio: 1})")
                page.wait_for_function("document.querySelector('video').currentTime > 25.5", timeout=15000)
                assert page.evaluate("document.querySelector('video').dataset.playbackOffset") == "0"
                assert page.locator(".local-playback-timeline").count() == 0
                page.evaluate("SVLocalPlayback.close(document.querySelector('video'))")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
