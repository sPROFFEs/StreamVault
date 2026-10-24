"""Exercise the real IPTV page with broken logos and browser playback paths."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright
from starlette.requests import Request

from shared_templates import templates
from regression_fixtures import CHROMIUM_ARGS


@pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1", reason="Opt-in browser check")
@pytest.mark.parametrize("playback", ["hls", "native"])
def test_iptv_broken_logos_filters_and_same_origin_playback(playback):
    request = Request({"type": "http", "headers": [(b"cookie", b"lang=es")], "state": {}})
    html = templates.get_template("iptv.html").render(request=request, is_admin=True)
    assets = Path(__file__).resolve().parents[2] / "assets"
    channels = [
        {"id": "animax", "name": 'Animax "HD"', "source": "iptv-org", "group": "Anime", "logo_url": "https://images.example/missing.png", "stream_url": "https://amagi.example/playlist.m3u8"},
        {"id": "news", "name": "News '24' & more", "source": "free-tv", "sources": ["free-tv", "tdtchannels"], "group": "News", "logo_url": "https://images.example/missing.png", "stream_url": "https://example.com/live.m3u8"},
    ]
    errors, requests = [], []

    def route_request(route):
        url = urlsplit(route.request.url)
        requests.append(url.path)
        if url.path == "/iptv":
            return route.fulfill(content_type="text/html", body=html)
        if url.path.startswith("/assets/"):
            asset = assets / url.path.removeprefix("/assets/")
            return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        if "hls.js" in route.request.url:
            script = """
                window.loadedStreams = []; window.recoveries = [];
                window.Hls = class {
                    static isSupported() { return SUPPORTED; }
                    static Events = {MANIFEST_PARSED: 'manifest', ERROR: 'error'};
                    static ErrorTypes = {NETWORK_ERROR: 'network', MEDIA_ERROR: 'media'};
                    constructor(config) { this.handlers = {}; this.config = config; window.lastHls = this; }
                    loadSource(url) { loadedStreams.push(url); }
                    attachMedia(video) {}
                    on(event, handler) { this.handlers[event] = handler; }
                    destroy() { this.destroyed = true; }
                    startLoad() { recoveries.push('network'); }
                    recoverMediaError() { recoveries.push('media'); }
                };
            """.replace("SUPPORTED", "true" if playback == "hls" else "false")
            return route.fulfill(content_type="application/javascript", body=script)
        if url.path == "/api/iptv/channels":
            search = parse_qs(url.query).get("search", [""])[0].lower()
            filtered = [channel for channel in channels if search in channel["name"].lower()]
            return route.fulfill(json={"total": len(filtered), "channels": filtered})
        if url.path == "/api/iptv/sources":
            return route.fulfill(json=[{"key": "iptv-org", "name": "Global"}, {"key": "tdtchannels", "name": "TDTChannels"}])
        if url.path in ("/api/iptv/favorites", "/api/iptv/recents"):
            return route.fulfill(json={"items": []})
        if url.path in ("/api/iptv/countries", "/api/iptv/languages"):
            return route.fulfill(json=[])
        if url.path == "/api/iptv/channels/groups":
            return route.fulfill(json={"Anime": 1, "News": 1})
        if url.path == "/api/iptv/channel/animax/health":
            return route.fulfill(json={"working": True})
        if url.netloc == "images.example":
            return route.fulfill(status=404)
        return route.fulfill(json={})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page()
            page.route("**/*", route_request)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };")
            page.goto("https://streamvault.test/iptv")
            expect(page.locator(".iptv-card .placeholder")).to_have_count(2)
            expect(page.locator(".iptv-card img")).to_have_count(0)
            assert not errors
            page.locator('.iptv-card[data-id="animax"]').click()
            expect(page.locator("#iptv-now-name")).to_have_text('Animax "HD"')
            if playback == "hls":
                assert page.evaluate("loadedStreams") == ["/api/local/proxy-stream?url=https%3A%2F%2Famagi.example%2Fplaylist.m3u8"]
                assert page.evaluate("lastHls.config.backBufferLength") <= 30
                page.evaluate("window.previousHls = lastHls")
                page.evaluate("lastHls.handlers.error(null, {fatal: true, type: Hls.ErrorTypes.NETWORK_ERROR, details: 'manifestLoadError'})")
                assert page.evaluate("recoveries") == ["network"]
                expect(page.locator("#iptv-player-error")).to_be_hidden()
                page.evaluate("lastHls.handlers.error(null, {fatal: true, type: Hls.ErrorTypes.NETWORK_ERROR, details: 'manifestLoadError'})")
                expect(page.locator("#iptv-player-error")).to_be_visible()
                assert page.evaluate("previousHls.destroyed")
                page.locator("#iptv-retry").click()
                expect(page.locator("#iptv-player-error")).to_be_hidden()
                page.evaluate("previousHls.handlers.error(null, {fatal: true, type: Hls.ErrorTypes.MEDIA_ERROR, details: 'staleError'})")
                expect(page.locator("#iptv-player-error")).to_be_hidden()
                page.evaluate("lastHls.handlers.error(null, {fatal: true, type: Hls.ErrorTypes.MEDIA_ERROR, details: 'mediaError'})")
                assert page.evaluate("recoveries") == ["network", "media"]
                expect(page.locator("#iptv-player-error")).to_be_hidden()
            else:
                expect(page.locator("#iptv-player")).to_have_attribute("src", "/api/local/proxy-stream?url=https%3A%2F%2Famagi.example%2Fplaylist.m3u8")
            page.locator("#iptv-health-check").click()
            expect(page.locator('.iptv-card[data-id="animax"] .iptv-card__health')).to_have_class("iptv-card__health alive")
            page.locator("#iptv-source-sel").select_option("tdtchannels")
            expect(page.locator(".iptv-card")).to_have_count(1)
            expect(page.locator(".iptv-card__name")).to_contain_text("News")
            page.locator("#iptv-reload").click()
            expect(page.locator("#iptv-source-sel")).to_have_value("tdtchannels")
            expect(page.locator(".iptv-card")).to_have_count(1)
            page.locator("#iptv-source-sel").select_option("")
            page.locator("#iptv-search").fill("News")
            expect(page.locator(".iptv-card")).to_have_count(1)
            page.locator("#iptv-reload").click()
            expect(page.locator("#iptv-pane-loading")).to_be_hidden()
            page.locator("#iptv-search").fill("")
            expect(page.locator(".iptv-card")).to_have_count(2)
            page.locator("#iptv-open-import").click()
            expect(page.locator("#iptv-tabs [data-tab].active")).to_have_attribute("data-tab", "featured")
            expect(page.locator("#iptv-import-source option")).to_have_count(3)
            page.locator("#iptv-import-source").select_option("tdtchannels")
            expect(page.locator("#iptv-import-url")).to_be_disabled()
            page.locator("#iptv-import-go").click()
            expect(page.locator("#iptv-import-modal")).to_be_hidden()
            assert "/api/iptv/import/tdtchannels" in requests
            assert not errors
        finally:
            browser.close()
