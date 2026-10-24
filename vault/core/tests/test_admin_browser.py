"""Exercise admin navigation and the real file browser renderer."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect
from starlette.requests import Request

from services.admin_download_library import scan_download_library
from shared_templates import templates
from regression_fixtures import CHROMIUM_ARGS


@pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1", reason="Opt-in browser check")
@pytest.mark.parametrize("width", [1440, 390])
def test_admin_tabs_and_folder_navigation(tmp_path, width):
    folder = tmp_path / "Mr. Robot" / "Season 1"
    folder.mkdir(parents=True)
    (folder / "Episode 2.mkv").write_bytes(b"video")
    (folder / "Episode 10.mkv").write_bytes(b"video")
    (folder / 'Episode 2 & notes.txt').write_text("A subtitle <not HTML>")
    assets = Path(__file__).resolve().parents[2] / "assets"
    errors, scans = [], []
    fail_listing = False
    cleanup_items = [{"name": "old.part", "relative_path": "old.part", "kind": "orphan", "is_directory": False, "size_label": "12 B"}, {"name": "empty", "relative_path": "empty", "kind": "empty_directory", "is_directory": True, "size_label": "0 B"}]

    def route_request(route):
        url = urlsplit(route.request.url)
        query = parse_qs(url.query)
        param = lambda key, default="": query.get(key, [default])[0]
        if url.path.rstrip("/") == "/admin":
            request = Request({"type": "http", "headers": [(b"cookie", b"lang=es")],
                               "query_string": url.query.encode(), "state": {}})
            html = templates.get_template("admin.html").render(request=request, username="admin")
            return route.fulfill(content_type="text/html", body=html)
        if url.path.startswith("/assets/"):
            asset = assets / url.path.removeprefix("/assets/")
            return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        if url.path == "/admin/api/downloads/list":
            scans.append(url.query)
            if fail_listing:
                return route.fulfill(status=503, json={"detail": "Storage unavailable"})
            return route.fulfill(json=scan_download_library(
                tmp_path, directory=param("download_path"), query=param("download_q"),
                kind=param("download_kind", "all"), sort=param("download_sort", "name"),
                descending=param("download_order", "asc") == "desc",
                page=int(param("download_page", "1")),
            ))
        if url.path == "/api/downloads/files/preview":
            return route.fulfill(content_type="text/plain", body="A subtitle <not HTML>")
        if url.path == "/admin/downloads/delete":
            (tmp_path / param("relative_path")).unlink()
            return route.fulfill(json={"success": True})
        if url.path == "/admin/api/downloads/cleanup":
            if route.request.method == "GET":
                return route.fulfill(json={"items": cleanup_items, "total_bytes_label": "12 B"})
            removed = [item["relative_path"] for item in cleanup_items]
            cleanup_items.clear()
            return route.fulfill(json={"deleted": removed, "errors": [], "remaining": {"items": []}})
        if url.path == "/api/downloads/notifications":
            return route.fulfill(content_type="text/event-stream", body=": connected\n\n")
        return route.fulfill(json={})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", route_request)
            page.goto("https://streamvault.test/admin/")
            expect(page.locator("#admin-panel-overview")).to_be_visible()
            assert not scans  # Don't scan the library until the Files tab opens.
            page.get_by_role("tab", name="Usuarios", exact=True).click()
            expect(page.locator("#user-manager-section")).to_be_visible()
            expect(page.locator("#admin-panel-overview")).to_be_hidden()
            page.get_by_role("tab", name="Archivos", exact=True).click()
            page.get_by_role("button", name="Mr. Robot", exact=True).click()
            page.get_by_role("button", name="Season 1", exact=True).click()
            expect(page.locator("#fm-list")).to_contain_text("Episode 2.mkv")
            page.get_by_role("button", name="Cuadrícula", exact=True).click()
            expect(page.locator("#fm-list")).to_have_attribute("data-view", "grid")
            page.get_by_role("button", name="Lista", exact=True).click()
            page.get_by_role("button", name="Episode 2 & notes.txt", exact=True).click()
            expect(page.get_by_role("dialog", name="Vista previa")).to_be_visible()
            expect(page.locator("#preview-body")).to_contain_text("<not HTML>")
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog", name="Vista previa")).to_be_hidden()
            page.locator("#fm-search").fill("Episode 10")
            page.locator("#fm-search").press("Enter")
            expect(page.locator("#fm-list [data-entry]")).to_have_count(1)
            page.locator("#fm-select-all").check()
            expect(page.locator("#fm-selection")).to_contain_text("1")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.locator("#fm-delete-selected").click()
            assert (folder / "Episode 10.mkv").exists()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#fm-delete-selected").click()
            expect(page.locator("#fm-list [data-entry]")).to_have_count(0)
            page.locator("#fm-clear").click()
            expect(page.locator("#fm-list [data-entry]")).to_have_count(2)
            page.reload()
            expect(page.locator("#admin-panel-files")).to_be_visible()
            expect(page.locator("#fm-breadcrumbs")).to_contain_text("Season 1")
            page.locator("#fm-up").click()
            expect(page.locator("#fm-list")).to_contain_text("Season 1")
            page.go_back()
            expect(page.locator("#fm-list")).to_contain_text("Episode 2.mkv")
            fail_listing = True
            page.locator("#fm-refresh").click()
            expect(page.locator("#fm-error")).to_contain_text("Storage unavailable")
            expect(page.locator("#fm-select-all")).to_be_disabled()
            expect(page.locator("#fm-delete-selected")).to_be_hidden()
            fail_listing = False
            page.locator("#fm-refresh").click()
            expect(page.locator("#fm-list")).to_contain_text("Episode 2.mkv")
            expect(page.locator("#fm-select-all")).to_be_enabled()
            page.locator("#fm-cleaner").click()
            expect(page.locator("#fm-cleaner-overlay")).to_be_visible()
            expect(page.locator("#fm-cleaner-summary")).to_contain_text("2")
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#fm-cleaner-run").click()
            expect(page.locator("#fm-cleaner-summary")).to_contain_text("2")
            expect(page.locator("#fm-cleaner-count")).to_be_hidden()
            page.locator("#fm-cleaner-close").click()
            expect(page.locator("#fm-cleaner-overlay")).to_be_hidden()
            page.get_by_role("tab", name="Catálogo", exact=True).click()
            expect(page.locator("#tmdb-key-input")).to_be_visible()
            page.get_by_role("tab", name="Catálogo", exact=True).focus()
            page.keyboard.press("Home")
            expect(page.get_by_role("tab", name="Resumen", exact=True)).to_be_focused()
            page.get_by_role("tab", name="Archivos", exact=True).click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if os.getenv("STREAMVAULT_SCREENSHOTS_DIR"):
                page.screenshot(path=f"{os.environ['STREAMVAULT_SCREENSHOTS_DIR']}/admin-files-{width}.png", full_page=True)
            assert not errors
        finally:
            browser.close()
