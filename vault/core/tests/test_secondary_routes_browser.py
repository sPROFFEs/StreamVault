"""Audit live TV and YouTube with offline providers and the real shared shell."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from starlette.requests import Request

from shared_templates import templates


pytestmark = pytest.mark.browser
ASSETS = Path(__file__).resolve().parents[2] / "assets"


@pytest.fixture
def secondary_page(regression_browser, browser_viewport):
    context = regression_browser.new_context(viewport=browser_viewport, service_workers="block")
    page = context.new_page()
    errors, unexpected, pending, requests = [], [], [], []
    video = {"video_id": "fixture", "title": "Fixture video", "author": "Fixture channel"}
    channel = {"id": "fixture", "name": "Fixture channel", "source": "fixture",
               "stream_url": "https://provider.test/live.m3u8"}
    payloads = {
        "/api/preferences": {},
        "/api/youtube/trending": [video], "/api/youtube/search": [video],
        "/api/youtube/favorites": {"items": []},
        "/api/youtube/subscriptions/local": {"items": []},
        "/api/youtube/history": {"items": []},
        "/api/youtube/video/fixture/qualities": [720],
        "/api/iptv/bootstrap": {"success": True},
        "/api/iptv/channels": {"channels": [channel], "total": 1},
        "/api/iptv/sources": [{"key": "fixture", "name": "Fixture"}],
        "/api/iptv/channels/groups": {}, "/api/iptv/countries": [],
        "/api/iptv/languages": [], "/api/iptv/favorites": {"items": []},
        "/api/iptv/recents": {"items": []}, "/api/iptv/recents/fixture": {},
    }

    def respond(route):
        url = urlsplit(route.request.url)
        requests.append(url.path)
        if url.netloc == "cdn.jsdelivr.net":
            return route.fulfill(content_type="application/javascript", body="window.Hls = {isSupported: () => false};")
        if url.netloc == "www.youtube-nocookie.com":
            return route.fulfill(content_type="text/html", body="Offline embedded player")
        if url.path in {"/youtube", "/iptv"}:
            request = Request({"type": "http", "headers": [(b"cookie", b"lang=en")], "state": {}})
            html = templates.get_template(url.path[1:] + ".html").render(request=request, is_admin=True)
            return route.fulfill(content_type="text/html", body=html)
        if url.path.startswith("/assets/"):
            asset = (ASSETS / url.path.removeprefix("/assets/")).resolve()
            if asset.is_relative_to(ASSETS) and asset.is_file():
                return route.fulfill(path=asset)
        if url.path.endswith("/stream"):
            pending.append(route)
            return
        if url.path == "/api/youtube/search" and parse_qs(url.query).get("q") == ["held"]:
            pending.append(route)
            return
        if url.path == "/api/youtube/channel/channel/videos":
            pending.append(route)
            return
        if url.path == "/api/local/proxy-stream":
            return route.fulfill(status=204)
        if url.path in payloads:
            value = payloads[url.path]
            status, value = value if isinstance(value, tuple) else (200, value)
            return route.fulfill(status=status, json=value)
        unexpected.append(route.request.url)
        return route.fulfill(status=404)

    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };")
    context.route("**/*", respond)
    try:
        yield SimpleNamespace(page=page, pending=pending, payloads=payloads, requests=requests)
    finally:
        context.close()
        assert not errors, errors
        assert not unexpected, unexpected


@pytest.mark.parametrize("route", ["youtube", "iptv"])
def test_secondary_routes_keyboard_tabs_and_responsive_layout(secondary_page, route):
    page = secondary_page.page
    page.goto(f"https://streamvault.test/{route}")
    prefix = "yt" if route == "youtube" else "iptv"
    expect(page.locator(f"#{prefix}-grid").locator("a" if route == "youtube" else ".iptv-card")).to_have_count(1)
    for control in page.locator(f"#{prefix}-tabs button").all():
        assert control.bounding_box()['height'] <= 56
    tab = page.locator(f"#{prefix}-tabs [data-tab=favorites]")
    assert tab.evaluate("element => element.tabIndex") == 0
    tab.focus()
    tab.press("Enter")
    expect(tab).to_have_class("active")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_iptv_channel_activation_and_import_native_back(secondary_page):
    page = secondary_page.page
    page.goto("https://streamvault.test/iptv")
    card = page.locator(".iptv-card")
    expect(card).to_have_count(1)
    assert card.evaluate("element => element.tabIndex") == 0
    card.focus()
    card.press("Enter")
    expect(page.locator("#iptv-now-name")).to_have_text("Fixture channel")
    trigger = page.locator("#iptv-open-import")
    trigger.focus()
    trigger.press("Enter")
    dialog = page.get_by_role("dialog", name="Import M3U")
    expect(dialog).to_be_visible()
    expect(page.locator("#iptv-import-source")).to_be_focused()
    page.locator("#iptv-import-go").focus()
    page.keyboard.press("Tab")
    expect(page.locator("#iptv-import-close")).to_be_focused()
    assert page.evaluate("SVShell.nativeBack()")
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()


def test_youtube_modal_back_cancels_pending_native_playback(secondary_page):
    page = secondary_page.page
    page.goto("https://streamvault.test/youtube")
    card = page.locator(".yt-card")
    expect(card).to_have_count(1)
    card.focus()
    card.press("Enter")
    dialog = page.get_by_role("dialog", name="Fixture video")
    expect(dialog).to_be_visible()
    expect(page.locator("#yt-modal-close")).to_be_focused()
    expect(page.locator("#yt-quality")).to_be_visible()
    with page.expect_request("**/api/youtube/video/fixture/stream?*"):
        page.locator("#yt-mode-toggle").click()
    assert page.evaluate("SVShell.nativeBack()")
    expect(dialog).to_be_hidden()
    expect(card).to_be_focused()
    secondary_page.pending[0].fulfill(json={"url": "/__test__/late-video.mp4"})
    page.wait_for_timeout(100)
    assert "/__test__/late-video.mp4" not in secondary_page.requests
    assert page.locator("#yt-player-native").evaluate("video => !video.getAttribute('src')")


def test_youtube_failure_retry_and_expired_search(secondary_page):
    ui = secondary_page
    ui.payloads['/api/youtube/subscriptions/local'] = {'items': [
        {'channel_id': 'channel', 'channel_name': 'Fixture channel'}]}
    ui.payloads['/api/youtube/trending'] = (502, {"detail": "Provider unavailable"})
    page = ui.page
    page.goto("https://streamvault.test/youtube")
    expect(page.locator("#yt-error")).to_be_visible()
    expect(page.locator("#yt-empty")).to_be_hidden()
    ui.payloads['/api/youtube/trending'] = [{"video_id": "fixture", "title": "Recovered"}]
    page.locator("#yt-retry").click()
    expect(page.locator(".yt-card")).to_contain_text("Recovered")
    with page.expect_request("**/api/youtube/search?q=held*"):
        page.locator("#yt-search").fill("held")
    ui.payloads['/api/youtube/search'] = [{"video_id": "fixture", "title": "Latest"}]
    page.locator("#yt-search").fill("latest")
    expect(page.locator(".yt-card")).to_contain_text("Latest")
    ui.pending[0].fulfill(json=[{"video_id": "expired", "title": "Expired"}])
    page.wait_for_timeout(100)
    expect(page.locator(".yt-card")).to_contain_text("Latest")
    if page.locator('.yt-sub-row').is_visible():
        with page.expect_request('**/api/youtube/channel/channel/videos'):
            page.locator('.yt-sub-row').click()
        with page.expect_request('**/api/youtube/search?q=newer*'):
            page.locator('#yt-search').fill('newer')
        expect(page.locator('.yt-card')).to_contain_text('Latest')
        ui.pending[1].fulfill(json=[{'video_id': 'expired', 'title': 'Expired channel'}])
        page.wait_for_timeout(100)
        expect(page.locator('.yt-card')).to_contain_text('Latest')
