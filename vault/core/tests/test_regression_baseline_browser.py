"""Offline baseline at mobile, desktop and TV sizes with keyboard checks."""
import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.baseline, pytest.mark.browser]


def test_keyboard_login_and_session(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/login?next=%2Fbrowse%3Ftype%3Dmovie")
    page.get_by_label("Username").focus()
    page.keyboard.type("baseline-viewer")
    page.keyboard.press("Tab")
    expect(page.get_by_label("Password")).to_be_focused()
    page.keyboard.type("Baseline123!")
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")
    expect(page.locator("#browse-grid")).to_contain_text("Fixture Movie")
    assert any(cookie["name"] == "access_token" and cookie["httpOnly"] for cookie in page.context.cookies())
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_catalog_filters_and_keyboard_navigation(baseline_page):
    page = baseline_page.page
    baseline_page.login()
    page.goto(f"{baseline_page.base_url}/browse?type=movie")
    expect(page.locator("#browse-grid .poster-card")).to_have_attribute("href", "/media/7?type=movie")
    page.locator("#type-filter").select_option("tv")
    expect(page.locator("#browse-grid")).to_contain_text("Fixture Series")
    card = page.locator("#browse-grid .poster-card")
    card.focus()
    expect(card).to_be_focused()
    expect(card.locator(".card-overlay")).to_have_css("opacity", "1")
    selector = '.mobile-tabbar a[href="/browse?type=movie"]' if page.viewport_size["width"] < 641 else '.top-nav__links a[href="/browse?type=movie"]'
    navigation = page.locator(selector)
    navigation.focus()
    navigation.press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")
    expect(page.locator("#browse-grid")).to_contain_text("Fixture Movie")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_updater_error_and_confirmation_are_keyboard_operable(baseline_page):
    page = baseline_page.page
    baseline_page.payloads["/admin/system/update/git-pull"] = (500, {"state": "failure", "detail": "Fixture update failed", "requires_rebuild": True})
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    button = page.locator("#btn-update-pull")
    expect(button).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    button.focus()
    button.press("Enter")
    expect(page.locator("#update-result")).to_contain_text("Fixture update failed")
    expect(button).to_be_enabled()
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.locator("#btn-update-restart").press("Enter")
    assert not any(path.endswith("docker-restart") for _, path, _ in baseline_page.requests)


def test_local_playback_resume_and_close_control_logic(baseline_page):
    page = baseline_page.page
    # Stub only the media decoder. Real decoding/remux uses existing media tests.
    page.add_init_script("""HTMLMediaElement.prototype.play = async function () { this.dataset.played = 'yes'; };
        HTMLMediaElement.prototype.pause = function () { this.dataset.paused = 'yes'; };
        HTMLMediaElement.prototype.load = function () {};""")
    page.goto(f"{baseline_page.base_url}/__test__/player")
    page.evaluate("""async () => {
        const video = document.querySelector('video');
        await SVLocalPlayback.open(video, {relative_path: 'Fixture.mp4'}, {start: 25, audio: 1});
        video.dispatchEvent(new Event('loadedmetadata'));
    }""")
    video = page.locator("video")
    expect(video).to_have_attribute("controls", "")
    expect(video).to_have_attribute("data-played", "yes")
    assert page.evaluate("SVLocalPlayback.currentTime(document.querySelector('video'))") == 25
    assert any("start=25" in query and "audio=1" in query for _, path, query in baseline_page.requests if path.endswith("/playback"))
    page.evaluate("SVLocalPlayback.close(document.querySelector('video'))")
    expect(video).not_to_have_attribute("src", re.compile(".+"))
    expect(video).to_have_attribute("data-playback-offset", "0")
    expect(video).to_have_attribute("data-paused", "yes")
