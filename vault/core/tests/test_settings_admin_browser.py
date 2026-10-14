"""Settings and non-updater administration behavior at mobile, desktop and TV sizes."""
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect
from starlette.requests import Request

from shared_templates import templates
from routers.preferences import DEFAULT_UI_PREFS


pytestmark = pytest.mark.browser


def settings(driver):
    driver.payloads["/api/preferences"] = (200, dict(DEFAULT_UI_PREFS))
    driver.payloads["/api/preferences/subtitles"] = (200, {})
    driver.login()
    driver.page.goto(driver.base_url + "/settings")
    expect(driver.page.locator("#accent-picker button")).to_have_count(6)


def screenshot(page, name):
    directory = os.getenv("STREAMVAULT_SCREENSHOTS_DIR")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / f"{name}-{page.viewport_size['width']}.png"))


def test_settings_labels_keyboard_and_layout(baseline_page):
    persisted = dict(DEFAULT_UI_PREFS)
    reads = []

    def preferences(route):
        if route.request.method == "PUT":
            persisted.update(route.request.post_data_json)
        else:
            reads.append(route.request.url)
        route.fulfill(json=persisted)

    baseline_page.page.route("**/api/preferences", preferences)
    settings(baseline_page)
    page = baseline_page.page
    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Blue", exact=True)).to_be_visible()
    assert len(reads) == 1
    screenshot(page, "settings")
    for section in ["account", "interface", "playback", "subtitles", "age", "downloads", "library", "notifications"]:
        page.locator(f"#sec-{section}").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), section
        screenshot(page, "settings-" + section)
    page.get_by_role("link", name="Interface", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#sec-interface")).to_be_focused()
    page.get_by_role("button", name="Blue", exact=True).focus()
    with page.expect_response(lambda response: response.url.endswith("/api/preferences") and response.request.method == "PUT"):
        page.keyboard.press("Enter")
    expect(page.get_by_role("button", name="Blue", exact=True)).to_have_attribute("aria-pressed", "true")
    assert persisted["accent"] == "blue"
    assert page.locator(".setting-row input, .setting-row select").evaluate_all(
        "els => els.every(el => el.labels.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby'))"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#settings-find").fill("zzzz-no-setting")
    expect(page.locator("#settings-find-status")).to_contain_text("No matching settings")


def test_preferences_batch_and_retry(baseline_page):
    writes = []
    fail = True

    def preferences(route):
        if route.request.method == "PUT":
            writes.append(route.request.post_data_json)
            route.fulfill(status=503 if fail else 200, json={"detail": "Unavailable"} if fail else writes[-1])
        else:
            route.fulfill(json={"font_size": "normal", "compact_mode": False})

    baseline_page.page.route("**/api/preferences", preferences)
    settings(baseline_page)
    page = baseline_page.page
    page.evaluate("""() => {
        const size = document.querySelector('#font-size');
        size.value = 'lg'; size.dispatchEvent(new Event('change'));
        const compact = document.querySelector('#opt-compact');
        compact.checked = true; compact.dispatchEvent(new Event('change'));
    }""")
    expect(page.locator("#settings-save-status")).to_contain_text("Could not save")
    assert writes == [{"font_size": "lg", "compact_mode": True}]
    expect(page.locator("#font-size")).to_have_value("lg")
    fail = False
    page.locator("#settings-save-retry").click()
    expect(page.locator("#settings-save-status")).to_have_text("Saved")
    assert writes[-1] == writes[0]


def test_preferences_initial_load_failure_keeps_defaults(baseline_page):
    baseline_page.payloads["/api/preferences"] = (503, {"detail": "Unavailable"})
    baseline_page.payloads["/api/preferences/subtitles"] = (200, {})
    baseline_page.login()
    page = baseline_page.page
    page.goto(baseline_page.base_url + "/settings")
    expect(page.locator("#settings-save-status")).to_contain_text("Could not load settings")
    expect(page.locator("#font-size")).to_have_value("normal")
    expect(page.locator("#opt-history")).to_be_checked()
    baseline_page.payloads["/api/preferences"] = (200, dict(DEFAULT_UI_PREFS))
    page.locator("#settings-save-retry").click()
    expect(page.locator("#settings-save-status")).to_contain_text("Preferences save automatically")
    expect(page.locator("#settings-save-retry")).to_be_hidden()


@pytest.mark.parametrize("provider", ["opensubtitles", "subdl", "wyzie"])
def test_subtitle_key_failure_preserves_input_and_retries(baseline_page, provider):
    baseline_page.payloads["/api/preferences/subtitles"] = (200, {})
    baseline_page.login()
    page = baseline_page.page
    page.goto(baseline_page.base_url + "/settings")
    page.locator(f"#key-{provider}").fill("private-test-key")
    baseline_page.payloads["/api/preferences/subtitles"] = (503, {"detail": "Provider unavailable"})
    page.locator(f"#save-{provider}").click()
    expect(page.locator(f"#{provider}-key-status")).to_contain_text("Provider unavailable")
    expect(page.locator(f"#key-{provider}")).to_have_value("private-test-key")
    expect(page.locator(f"#key-{provider}")).to_have_attribute("aria-invalid", "true")
    baseline_page.payloads["/api/preferences/subtitles"] = (200, {})
    page.locator(f"#save-{provider}").click()
    expect(page.locator(f"#{provider}-key-status")).to_have_text("Saved")
    expect(page.locator(f"#key-{provider}")).to_have_value("")


def admin(driver):
    def render(route):
        query = urlsplit(route.request.url).query.encode()
        user = SimpleNamespace(username="baseline-admin", is_admin=True, can_download=True)
        request = Request({"type": "http", "headers": [(b"cookie", b"lang=en")],
                           "query_string": query, "state": {"user": user}})
        users = [SimpleNamespace(id=1, username=user.username, is_admin=True, can_download=True, is_active=True),
                 SimpleNamespace(id=2, username="Viewer with a long account name", is_admin=False, can_download=True, is_active=True)]
        html = templates.get_template("admin.html").render(
            request=request, username=user.username, users=users,
            sys_stats={"cpu_usage": 12, "ram_percent": 34, "disk_total": 100, "disk_used": 10, "disk_percent": 10},
            stats={"download_quota_gb": 50, "download_usage_gb": 10, "download_usage_percent": 20})
        route.fulfill(content_type="text/html", body=html)

    driver.page.route("**/admin/?*", render)
    driver.payloads["/admin/api/downloads/list"] = (200, {
        "items": [{"relative_path": "notes.txt", "name": "notes.txt", "is_directory": False,
                   "kind": "file", "folder": "", "size_label": "1 B", "modified_label": "Today", "previewable": True}],
        "query": "", "kind": "all", "sort": "name", "descending": False, "page": 1,
        "pages": 1, "parent": None, "breadcrumbs": [{"name": "Downloads", "path": ""}],
        "total_items": 1, "total_size_label": "1 B",
    })
    driver.payloads["/admin/api/downloads/cleanup"] = (200, {
        "items": [{"relative_path": "old.part", "name": "old.part", "reason": "Abandoned", "size_label": "1 B"}],
        "total_bytes_label": "1 B"})
    driver.page.route("**/api/downloads/notifications", lambda route: route.fulfill(content_type="text/event-stream", body=": connected\n\n"))
    driver.page.route("**/api/downloads/files/preview?*", lambda route: route.fulfill(body="A readable preview"))
    driver.page.goto(driver.base_url + "/admin/?tab=overview")


def test_admin_panels_labels_and_layout(baseline_page):
    admin(baseline_page)
    page = baseline_page.page
    for name in ["Overview", "Users", "Remote access", "Catalog", "Files"]:
        page.get_by_role("tab", name=name, exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), name
        screenshot(page, "admin-" + name.lower().replace(" ", "-"))
    page.get_by_role("tab", name="Remote access", exact=True).click()
    page.locator("#tunnel-mode").select_option("managed")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("main input:not([type=hidden]), main select").evaluate_all(
        "els => els.every(el => el.labels.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby'))"
    )
    page.get_by_role("tab", name="Remote access", exact=True).focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Catalog", exact=True)).to_be_focused()
    expect(page.locator("#admin-panel-catalog")).to_be_visible()
    page.locator("#tmdb-save").click()
    expect(page.locator("#tmdb-key-input")).to_have_attribute("aria-invalid", "true")
    baseline_page.payloads["/admin/api/tmdb-key"] = (503, {"detail": "Catalog unavailable"})
    page.locator("#tmdb-key-input").fill("keep-this-key")
    page.locator("#tmdb-save").click()
    expect(page.locator("#tmdb-result")).to_contain_text("Catalog unavailable")
    expect(page.locator("#tmdb-key-input")).to_have_value("keep-this-key")
    baseline_page.payloads["/admin/api/tmdb-key"] = (200, {"configured": True, "source": "file"})
    page.locator("#tmdb-save").click()
    expect(page.locator("#tmdb-result")).to_contain_text("Key saved")


def test_admin_dialog_tab_cycle_cancel_and_back(baseline_page):
    admin(baseline_page)
    page = baseline_page.page
    page.get_by_role("tab", name="Files", exact=True).click()
    page.locator("#fm-cleaner").click()
    expect(page.locator("[data-cleanup-select]")).to_be_visible()
    expect(page.locator("#fm-cleaner-close")).to_be_focused()
    screenshot(page, "admin-cleaner")
    page.keyboard.press("Tab")
    expect(page.locator("[data-cleanup-select]")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#fm-cleaner-close")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#fm-cleaner-run")).to_be_focused()
    page.locator("#fm-cleaner-cancel").click()
    expect(page.locator("#fm-cleaner-overlay")).to_be_hidden()
    expect(page.locator("#fm-cleaner")).to_be_focused()
    page.locator("#fm-cleaner").click()
    page.go_back()
    expect(page.locator("#fm-cleaner-overlay")).to_be_hidden()
    expect(page.locator("#admin-panel-files")).to_be_visible()
    page.get_by_role("button", name="notes.txt", exact=True).click()
    expect(page.locator("#preview-body")).to_contain_text("A readable preview")
    screenshot(page, "admin-preview")
    page.keyboard.press("Escape")
    expect(page.locator("#preview-overlay")).to_be_hidden()
    expect(page.get_by_role("button", name="notes.txt", exact=True)).to_be_focused()


def test_password_validation_and_save(baseline_page):
    settings(baseline_page)
    page = baseline_page.page
    page.locator("#sec-account button[type=submit]").click()
    expect(page.locator("#old_password")).to_be_focused()
    assert not page.locator("#old_password").evaluate("el => el.validity.valid")
    for current, new, confirm, error in [
        ("Baseline123!", "Updated123!", "Different123!", "Passwords do not match"),
        ("Baseline123!", "weak", "weak", "Weak password"),
        ("wrong", "Updated123!", "Updated123!", "Current password is incorrect"),
    ]:
        page.locator("#old_password").fill(current)
        page.locator("#new_password").fill(new)
        page.locator("#confirm_password").fill(confirm)
        page.locator("#sec-account button[type=submit]").click()
        expect(page.locator("main .message[role=alert]")).to_contain_text(error)
    page.locator("#old_password").fill("Baseline123!")
    page.locator("#new_password").fill("Updated123!")
    page.locator("#confirm_password").fill("Updated123!")
    page.locator("#sec-account button[type=submit]").click()
    expect(page.locator("main .message[role=status]")).to_have_text("Password updated")


def test_remote_access_status_and_save_recovery(baseline_page):
    baseline_page.payloads["/admin/api/tunnel/status"] = (503, {"detail": "Tunnel unavailable"})
    admin(baseline_page)
    page = baseline_page.page
    page.get_by_role("tab", name="Remote access", exact=True).click()
    expect(page.locator("#tunnel-result")).to_contain_text("Tunnel unavailable")
    baseline_page.payloads["/admin/api/tunnel/status"] = (200, {"installed": True, "enabled": False, "origin": "http://nginx:80"})
    page.locator("#tunnel-refresh").click()
    expect(page.locator("#tunnel-status")).to_have_text("Stopped")
    page.locator("#tunnel-origin").fill("http://changed:80")
    baseline_page.payloads["/admin/api/tunnel/config"] = (503, {"detail": "Save unavailable"})
    page.locator("#tunnel-save").click()
    expect(page.locator("#tunnel-result")).to_contain_text("Save unavailable")
    expect(page.locator("#tunnel-origin")).to_have_value("http://changed:80")
    baseline_page.payloads["/admin/api/tunnel/config"] = (200, {"installed": True, "origin": "http://changed:80"})
    page.locator("#tunnel-save").click()
    expect(page.locator("#tunnel-result")).to_contain_text("Configuration saved")
