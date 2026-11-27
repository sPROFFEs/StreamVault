"""Exercise the actual downloads template and scripts, including older browsers."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from starlette.requests import Request

from shared_templates import templates
from regression_fixtures import CHROMIUM_ARGS


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
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
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


def test_interrupted_download_can_resume_without_creating_a_new_job():
    request = Request({"type": "http", "headers": [], "state": {}})
    html = templates.get_template("downloads.html").render(request=request, is_admin=True)
    assets = Path(__file__).resolve().parents[2] / "assets"
    job = {"id": "existing-job", "title": "Partial download", "status": "interrupted",
           "progress": 42, "can_resume": True}
    resume_path = "/api/downloads/jobs/existing-job/resume"
    resumed = []

    def respond(route):
        from urllib.parse import urlsplit
        path = urlsplit(route.request.url).path
        if path == "/downloads":
            return route.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            asset = assets / path.removeprefix("/assets/")
            return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        if path == "/api/downloads/jobs":
            return route.fulfill(json={"torrent": [], "yt_dlp": [job]})
        if path == resume_path:
            resumed.append(route.request.method)
            job.update(status="pending", can_resume=False)
            return route.fulfill(json={"success": True, "job_id": job["id"]})
        if path == "/api/downloads/notifications":
            return route.fulfill(content_type="text/event-stream", body=": connected\n\n")
        return route.fulfill(json={})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page()
            page.route("**/*", respond)
            page.goto("https://streamvault.test/downloads")
            row = page.locator('[data-job-id="existing-job"]')
            expect(row).to_contain_text("Interrupted")
            expect(row).to_contain_text("42%")
            row.get_by_role("button", name="Resume").click()
            expect(row).to_contain_text("Pending")
            assert resumed == ["POST"]
        finally:
            browser.close()
