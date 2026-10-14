"""Real authentication UI at phone, desktop and television sizes."""
import pytest
from playwright.sync_api import expect
from test_authentication import empty_accounts

pytestmark = pytest.mark.browser


def open_login(fixture):
    fixture.page.goto(f"{fixture.base_url}/login?next=%2Fbrowse%3Ftype%3Dtv")
    return fixture.page


def fill_login(page, password="Baseline123!"):
    page.get_by_label("Username", exact=True).fill("baseline-viewer")
    page.get_by_label("Password", exact=True).fill(password)


def test_password_visibility_autofill_and_remote_navigation(baseline_page, tmp_path):
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
    page.screenshot(path=str(tmp_path / f"auth-{page.viewport_size['width']}.png"), full_page=True)
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
