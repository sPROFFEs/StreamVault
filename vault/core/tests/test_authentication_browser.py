"""Real authentication UI at phone, desktop and television sizes."""
import pytest
from playwright.sync_api import expect
from test_authentication import empty_accounts
from test_visual_foundations_browser import contrast, rgb

pytestmark = pytest.mark.browser


def open_login(fixture):
    fixture.page.goto(f"{fixture.base_url}/login?next=%2Fbrowse%3Ftype%3Dtv")
    return fixture.page


def fill_login(page, password="Baseline123!"):
    page.get_by_label("Username", exact=True).fill("baseline-viewer")
    page.get_by_label("Password", exact=True).fill(password)


def test_password_visibility_autofill_and_remote_navigation(baseline_page):
    page = open_login(baseline_page)
    username = page.get_by_label("Username", exact=True)
    password = page.get_by_label("Password", exact=True)
    expect(username).to_have_attribute("autocapitalize", "none")
    expect(username).to_have_attribute("enterkeyhint", "next")
    expect(password).to_have_attribute("autocomplete", "current-password")
    expect(password).to_have_attribute("enterkeyhint", "go")
    username.focus()
    username.press("ArrowDown")
    expect(password).to_be_focused()
    password.fill("Baseline123!")
    password.press("ArrowDown")
    toggle = page.locator("#password-toggle")
    expect(toggle).to_have_text("Show password")
    expect(toggle).to_be_focused()
    toggle.press("Enter")
    expect(password).to_have_attribute("type", "text")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    toggle.press("Enter")
    expect(password).to_have_attribute("type", "password")
    toggle.press("ArrowDown")
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_focused()
    for selector in ["#username", "#password", "#password-toggle", 'button[type="submit"]', ".auth-language a"]:
        for control in page.locator(selector).all():
            rect = control.bounding_box()
            assert rect["height"] >= 44 and rect["width"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if page.viewport_size["width"] >= 1800:
        assert float(username.evaluate("e => parseFloat(getComputedStyle(e).fontSize)")) >= 20
    page.set_viewport_size({"width": 320, "height": 480})
    password.focus()
    page.get_by_role("button", name="Sign in", exact=True).scroll_into_view_if_needed()
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_username_next_key_and_remote_header_access(baseline_page):
    page = open_login(baseline_page)
    username = page.get_by_label("Username", exact=True)
    username.fill("baseline-viewer")
    username.press("Enter")
    expect(page.get_by_label("Password", exact=True)).to_be_focused()
    assert not any(method == "POST" and path == "/login" for method, path, _ in baseline_page.requests)
    username.focus()
    username.press("ArrowUp")
    expect(page.get_by_role("link", name="English", exact=True)).to_be_focused()


def test_validation_invalid_credentials_retry_reload_and_logout(baseline_page):
    page = open_login(baseline_page)
    submit = page.get_by_role("button", name="Sign in", exact=True)
    submit.click()
    expect(page.get_by_label("Username", exact=True)).to_be_focused()
    expect(page.locator("#username-error")).to_contain_text("Enter your username")
    assert not any(method == "POST" and path == "/login" for method, path, _ in baseline_page.requests)
    fill_login(page, "WrongSensitiveSecret!")
    submit.click()
    expect(page.get_by_role("alert")).to_contain_text("Invalid username or password")
    expect(page.get_by_label("Username", exact=True)).to_have_value("baseline-viewer")
    expect(page.get_by_label("Password", exact=True)).to_have_value("")
    assert "WrongSensitiveSecret" not in page.locator("body").inner_text()
    assert page.locator('input[name="next"]').input_value() == "/browse?type=tv"
    page.get_by_label("Password", exact=True).fill("Baseline123!")
    page.get_by_label("Password", exact=True).press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=tv")
    page.reload()
    expect(page.locator("#browse-grid")).to_contain_text("Fixture Series")
    cookies = page.context.cookies()
    assert any(cookie["name"] == "access_token" and cookie["httpOnly"] and cookie["expires"] > 0 for cookie in cookies)
    if page.viewport_size["width"] < 641:
        page.get_by_role("button", name="More", exact=True).click()
        page.locator('.mobile-more-sheet__logout button').press("Enter")
    else:
        page.get_by_role("button", name="Sign out", exact=True).press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/login")
    page.goto(f"{baseline_page.base_url}/browse?type=tv")
    expect(page).to_have_url(f"{baseline_page.base_url}/login?next=%2Fbrowse%3Ftype%3Dtv")


def test_network_failure_duplicate_submission_and_retry(baseline_page):
    page = open_login(baseline_page)
    messages = []
    page.on("console", lambda message: messages.append(message.text))
    fill_login(page)
    attempts = []

    def fail(route):
        attempts.append(route.request.method)
        route.abort("failed")

    page.route("**/login", fail)
    page.evaluate("""() => { const form = document.querySelector('form');
        form.requestSubmit(); form.requestSubmit(); }""")
    expect(page.get_by_role("alert")).to_contain_text("Check your connection")
    assert attempts == ["POST"]
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_enabled()
    expect(page.get_by_label("Password", exact=True)).to_have_value("Baseline123!")
    assert "Baseline123!" not in " ".join(messages) + page.locator("body").inner_text()
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    page.unroute("**/login", fail)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=tv")


def test_submission_feedback_and_untrusted_server_error(baseline_page):
    page = open_login(baseline_page)
    fill_login(page)
    page.evaluate("""() => { window.fetch = () => new Promise(resolve => { window.finishLogin = resolve; });
        document.querySelector('form').requestSubmit(); }""")
    expect(page.get_by_role("button", name="Signing in…", exact=True)).to_be_disabled()
    expect(page.locator("#auth-status")).to_contain_text("Signing in")
    expect(page.locator("form")).to_have_attribute("aria-busy", "true")
    page.evaluate("finishLogin(new Response(JSON.stringify({detail:'password=SensitiveSecret!', code:'unknown'}), {status:500}))")
    expect(page.get_by_role("alert")).to_contain_text("Unable to sign in")
    assert "SensitiveSecret" not in page.locator("body").inner_text()
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_enabled()


def test_native_form_fallback_keeps_destination(baseline_page):
    page = baseline_page.page
    page.route("**/assets/js/auth.js*", lambda route: route.fulfill(content_type="text/javascript", body=""))
    open_login(baseline_page)
    fill_login(page, "wrong")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Invalid username or password")
    assert page.locator('input[name="next"]').input_value() == "/browse?type=tv"
    fill_login(page)
    page.get_by_label("Password", exact=True).press("Enter")
    expect(page).to_have_url(f"{baseline_page.base_url}/browse?type=tv")


def test_timeout_releases_form_for_retry(baseline_page):
    page = open_login(baseline_page)
    page.clock.install()
    fill_login(page)
    page.evaluate("""() => { window.fetch = (url, options) => new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }); document.querySelector('form').requestSubmit(); }""")
    expect(page.get_by_role("button", name="Signing in…", exact=True)).to_be_disabled()
    page.clock.fast_forward(15001)
    expect(page.get_by_role("alert")).to_contain_text("Check your connection")
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_enabled()
    expect(page.locator("form")).to_have_attribute("aria-busy", "false")


def test_initial_setup_uses_creation_rules_and_success_notice(empty_accounts, baseline_page):
    page = baseline_page.page
    page.route("**/setup", lambda route: route.continue_())
    page.goto(f"{baseline_page.base_url}/setup")
    expect(page.get_by_label("Password", exact=True)).to_have_attribute("autocomplete", "new-password")
    page.get_by_label("Username", exact=True).fill("../bad")
    page.get_by_label("Password", exact=True).fill("weak")
    page.get_by_role("button", name="Create account", exact=True).click()
    expect(page.locator("#username-error")).to_contain_text("3–32")
    expect(page.locator("#password-error")).to_contain_text("at least 8")
    page.get_by_label("Username", exact=True).fill("auth-created-admin")
    page.get_by_label("Password", exact=True).fill("Baseline123!")
    page.get_by_role("button", name="Create account", exact=True).click()
    expect(page).to_have_url(f"{baseline_page.base_url}/login?notice=created")
    expect(page.get_by_role("status").filter(has_text="Administrator account created")).to_be_visible()
    assert "Baseline123!" not in page.locator("body").inner_text()


@pytest.mark.parametrize("language", ["en", "es"])
def test_language_selector_has_readable_selected_hover_and_focus_states(baseline_page, language):
    page = baseline_page.page
    page.context.add_cookies([{"name": "lang", "value": language, "url": baseline_page.base_url}])
    open_login(baseline_page)
    selected = page.locator('.auth-language [aria-current="true"]')
    expect(selected).to_have_attribute("lang", language)
    for state in ("normal", "hover", "focus"):
        if state == "hover": selected.hover()
        if state == "focus": selected.focus()
        colors = selected.evaluate("e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor]; }")
        assert contrast(rgb(colors[0]), rgb(colors[1])) >= 4.5, (language, state, colors)
    if language == "es":
        expect(page.get_by_role("button", name="Mostrar contraseña", exact=True)).to_be_visible()


def test_password_eye_is_inline_and_preserves_editing_focus_and_selection(baseline_page):
    page = open_login(baseline_page)
    password = page.get_by_label("Password", exact=True)
    toggle = page.locator("#password-toggle")
    expect(toggle).to_have_accessible_name("Show password")
    input_box, button_box = password.bounding_box(), toggle.bounding_box()
    assert input_box["y"] <= button_box["y"]
    assert button_box["y"] + button_box["height"] <= input_box["y"] + input_box["height"]
    expect(toggle.locator("svg:visible")).to_have_count(1)
    password.fill("Baseline123!")
    password.focus()
    password.evaluate("e => e.setSelectionRange(2, 5)")
    toggle.click()
    expect(password).to_be_focused()
    expect(password).to_have_attribute("type", "text")
    expect(password).to_have_js_property("selectionStart", 2)
    expect(password).to_have_js_property("selectionEnd", 5)
    expect(toggle).to_have_accessible_name("Hide password")
    expect(toggle).to_have_attribute("title", "Hide password")
    toggle.click()
    expect(password).to_be_focused()
    expect(password).to_have_attribute("type", "password")


def test_language_switch_preserves_login_destination_and_localizes_password_button(baseline_page):
    page = open_login(baseline_page)
    page.route("**/set-language/*", lambda route: route.continue_())
    page.get_by_role("link", name="Español", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "es")
    expect(page.locator('input[name="next"]')).to_have_value("/browse?type=tv")
    toggle = page.locator("#password-toggle")
    expect(toggle).to_have_accessible_name("Mostrar contraseña")
    toggle.click()
    expect(toggle).to_have_accessible_name("Ocultar contraseña")
    expect(page.get_by_label("Contraseña", exact=True)).to_have_attribute("type", "text")
    page.get_by_role("link", name="English", exact=True).click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator('input[name="next"]')).to_have_value("/browse?type=tv")


def test_submission_and_restored_page_reset_password_visibility(baseline_page):
    page = open_login(baseline_page)
    fill_login(page)
    toggle = page.locator("#password-toggle")
    toggle.click()
    page.evaluate("""() => { window.fetch = () => new Promise(resolve => { window.finishLogin = resolve; });
        document.querySelector('form').requestSubmit(); }""")
    expect(toggle).to_be_disabled()
    expect(page.locator("#password")).to_have_attribute("type", "password")
    page.evaluate("finishLogin(new Response(JSON.stringify({code:'invalid_credentials'}), {status:401}))")
    expect(toggle).to_be_enabled()
    expect(toggle).to_have_accessible_name("Show password")
    expect(toggle.locator("svg:visible")).to_have_count(1)
    toggle.click()
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
    expect(page.locator("#password")).to_have_value("")
    expect(toggle).to_have_accessible_name("Show password")
    expect(toggle).to_have_attribute("aria-pressed", "false")
