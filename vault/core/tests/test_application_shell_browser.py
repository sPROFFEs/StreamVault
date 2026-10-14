"""Exercise real shell controls with offline pages and all supported viewports."""
import pytest
from playwright.sync_api import expect


pytestmark = [pytest.mark.baseline, pytest.mark.browser]


def browse(baseline_page):
    baseline_page.login()
    baseline_page.page.goto(f"{baseline_page.base_url}/browse?type=movie")
    return baseline_page.page


def test_search_dialog_contains_focus_and_restores_its_trigger(baseline_page):
    page = browse(baseline_page)
    trigger = page.locator("#search-trigger")
    trigger.focus()
    trigger.press("Enter")
    dialog = page.get_by_role("dialog", name="Search")
    expect(dialog).to_be_visible()
    expect(dialog.locator("input")).to_be_focused()
    assert page.locator("main").evaluate("element => element.inert")
    page.keyboard.press("Shift+Tab")
    expect(dialog.get_by_role("button", name="Close search")).to_be_focused()
    page.keyboard.press("Tab")
    expect(dialog.locator("input")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert not page.locator("main").evaluate("element => element.inert")
    # A nested shared overlay must close independently and restore its parent.
    trigger.press("Enter")
    page.keyboard.press("?")  # Typed text remains in the search field.
    expect(dialog.locator("input")).to_have_value("?")
    page.evaluate("SVShortcutsModal.open()")
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog.locator("input")).to_be_focused()
    page.keyboard.press("Escape")
    expect(trigger).to_be_focused()


def test_more_menu_remote_back_browser_back_and_resize(baseline_page):
    page = browse(baseline_page)
    page.set_viewport_size({"width": 390, "height": 844})
    trigger = page.locator("#mobile-more-trigger")
    trigger.focus()
    trigger.press("Enter")
    dialog = page.get_by_role("dialog", name="More options")
    expect(trigger).to_have_attribute("aria-expanded", "true")
    first = dialog.get_by_role("button", name="Close menu")
    expect(first).to_be_focused()
    first.press("ArrowDown")
    expect(dialog.locator('a[href*="favorites"]')).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(first).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(dialog.locator('button[type="submit"]')).to_be_focused()
    # A remote Back key closes the overlay before navigating away.
    page.keyboard.press("Backspace")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")
    trigger.press("Enter")
    page.go_back()
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    trigger.press("Enter")
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(dialog).not_to_be_visible()
    expect(page.locator(".top-nav__brand")).to_be_focused()
    assert not page.locator("main").evaluate("element => element.inert")


def test_skip_link_arrows_and_selected_route(baseline_page):
    page = browse(baseline_page)
    page.keyboard.press("Tab")
    skip = page.get_by_role("link", name="Skip to content")
    expect(skip).to_be_focused()
    skip.press("Enter")
    expect(page.locator("#main-content")).to_be_focused()
    nav = page.locator(".mobile-tabbar" if page.viewport_size["width"] <= 640 else ".top-nav__links")
    movie = nav.locator('a[href="/browse?type=movie"]')
    expect(movie).to_have_attribute("aria-current", "page")
    movie.focus()
    movie.press("ArrowRight")
    expect(nav.locator('a[href="/browse?type=tv"]')).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(page.locator("#type-filter")).to_be_focused()
    # Normal editing/navigation keys belong to native form controls.
    search = page.locator("#browse-search")
    if search.count():
        search.focus()
        search.fill("fixture")
        search.press("ArrowLeft")
        expect(search).to_be_focused()


def test_menu_navigation_does_not_leave_an_extra_back_step(baseline_page):
    page = browse(baseline_page)
    baseline_page.payloads["/api/preferences/subtitles"] = (200, {})
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#mobile-more-trigger").click()
    dialog = page.get_by_role("dialog", name="More options")
    dialog.locator('a[href="/settings"]').press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/settings")
    page.go_back()
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")
    expect(page.get_by_role("dialog", name="More options")).not_to_be_visible()
    assert not page.locator("main").evaluate("element => element.inert")
    page.evaluate("history.pushState({}, '', '?type=tv')")
    page.locator(".top-nav__brand").focus()
    page.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'GoBack', bubbles: true}))")
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")


def test_remote_search_results_receive_real_focus(baseline_page):
    page = browse(baseline_page)
    page.locator("#search-trigger").click()
    dialog = page.get_by_role("dialog", name="Search")
    field = dialog.locator("input")
    field.fill("fixture")
    first = dialog.locator('a[href="/media/7?type=movie"]')
    expect(first).to_be_visible()
    field.press("ArrowDown")
    expect(first).to_be_focused()
    first.press("ArrowUp")
    expect(field).to_be_focused()
    field.press("Escape")
    expect(page.locator("#search-trigger")).to_be_focused()


def test_navigation_arrows_do_not_trigger_page_player_shortcuts(baseline_page):
    page = browse(baseline_page)
    page.evaluate("""() => {
        window.pageArrowCount = 0;
        document.addEventListener('keydown', event => {
            if (event.key === 'ArrowRight') window.pageArrowCount++;
        });
        const player = document.createElement('div');
        player.className = 'sv-player';
        player.innerHTML = '<button id="fixture-play">Play</button><button id="fixture-volume">Volume</button>';
        document.querySelector('main').appendChild(player);
    }""")
    nav = page.locator(".mobile-tabbar" if page.viewport_size["width"] <= 640 else ".top-nav__links")
    nav.locator('a[href="/browse?type=movie"]').press("ArrowRight")
    expect(nav.locator('a[href="/browse?type=tv"]')).to_be_focused()
    assert page.evaluate("window.pageArrowCount") == 0
    play = page.locator("#fixture-play")
    play.focus()
    play.press("ArrowRight")
    expect(play).to_be_focused()
    assert page.evaluate("window.pageArrowCount") == 1


def test_resizing_shell_never_overflows_or_covers_content(baseline_page):
    page = browse(baseline_page)
    for width in (320, 390, 640, 641, 720, 768, 1024, 1121, 1280, 1440, 1599, 1600, 1920):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert page.locator("main").bounding_box()["y"] >= page.locator("#top-nav").bounding_box()["height"], width
        if width <= 640:
            bar = page.locator(".mobile-tabbar").bounding_box()
            assert bar["y"] > page.locator("#top-nav").bounding_box()["height"]
            padding = page.locator("body").evaluate("element => parseFloat(getComputedStyle(element).paddingBottom)")
            assert padding >= bar["height"]
        if width >= 1920:
            size = page.locator(".top-nav__links a").first.evaluate("element => parseFloat(getComputedStyle(element).fontSize)")
            assert size >= 20


def test_android_back_consumes_overlays_and_css_fullscreen_before_navigation(baseline_page):
    page = browse(baseline_page)
    trigger = page.locator("#search-trigger")
    trigger.press("Enter")
    page.evaluate("SVShortcutsModal.open()")
    assert page.evaluate("SVShell.nativeBack()") is True
    expect(page.get_by_role("dialog", name="Keyboard shortcuts")).not_to_be_visible()
    expect(page.get_by_role("dialog", name="Search")).to_be_visible()
    assert page.evaluate("SVShell.nativeBack()") is True
    expect(page.get_by_role("dialog", name="Search")).not_to_be_visible()
    expect(trigger).to_be_focused()
    page.evaluate("""() => {
        const player = document.createElement('div');
        player.className = 'player-box--pseudo-fs';
        player.innerHTML = '<button id="pseudo-fs-exit">Exit</button>';
        player.querySelector('button').onclick = () => player.remove();
        document.body.append(player);
    }""")
    assert page.evaluate("SVShell.nativeBack()") is True
    assert page.locator(".player-box--pseudo-fs").count() == 0
    assert page.evaluate("SVShell.nativeBack()") is False
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=movie")


def test_updater_explains_separate_android_installation(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    expect(page.locator("#system-update")).to_contain_text("does not install a new Android APK")
