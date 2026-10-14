"""Exercise the actual downloads template and scripts, including older browsers."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from starlette.requests import Request

from shared_templates import templates


@pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1", reason="Opt-in browser check")
@pytest.mark.parametrize("compatibility", ["current", "no_timeout_api", "cached_template", "retry", "retry_auto", "legacy_proxy"])
def test_downloads_page_loads_and_recovers(compatibility):
    request = Request({"type": "http", "headers": [], "state": {}})
    html = templates.get_template("downloads.html").render(request=request, is_admin=True)
    if compatibility == "cached_template":
        import re
        html = re.sub(r'<(?:button|span)[^>]*id="library-(?:previous|next|page)".*?</(?:button|span)>', '', html)
    assets = Path(__file__).resolve().parents[2] / "assets"
    movie = {"name": "Example.mp4", "title": "Example", "media_type": "movie",
             "relative_path": "Example.mp4", "size_bytes": 42, "modified_at": 1}
    files = {"files": [movie], "movies": [movie], "series": [], "page": 1,
             "pages": 1, "total": 1, "used_bytes": 42, "quota_bytes": 1024}
    requests = []
    errors = []

    def route_request(route):
        from urllib.parse import urlsplit
        path = urlsplit(route.request.url).path
        requests.append(path)
        if path == "/downloads":
            return route.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            asset = assets / path.removeprefix("/assets/")
            return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        if path == "/api/downloads/jobs":
            return route.fulfill(json={"torrent": [], "yt_dlp": []})
        if path == "/api/downloads/files" and compatibility == "legacy_proxy":
            return route.fulfill(status=301, headers={"Location": "http://streamvault.test/api/downloads/files/?page=1"})
        if path in {"/api/downloads/files", "/api/downloads/library"}:
            if compatibility.startswith("retry") and requests.count(path) == 1:
                return route.fulfill(status=503, json={"detail": "Temporarily unavailable"})
            return route.fulfill(json=files)
        if path == "/api/downloads/notifications":
            return route.fulfill(content_type="text/event-stream", body=": connected\n\n")
        return route.fulfill(json={})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", route_request)
            if compatibility == "no_timeout_api":
                page.add_init_script("AbortSignal.timeout = undefined;")
            page.goto("https://streamvault.test/downloads")
            if compatibility.startswith("retry"):
                expect(page.locator("#storage-label")).to_contain_text("Could not load", timeout=2000)
            if compatibility == "retry":
                page.locator("#refresh-btn").click()
            expect(page.locator("#library-count")).to_have_text("1", timeout=7000)
            expect(page.locator("#library-list")).to_contain_text("Example")
            assert "/api/downloads/library" in requests
            assert "/api/downloads/files" not in requests
            assert not errors
        finally:
            browser.close()
