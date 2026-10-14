"""Check rendered shared controls, feedback, and accessibility at every viewport."""
import pytest
from playwright.sync_api import expect

from shared_templates import templates


pytestmark = [pytest.mark.baseline, pytest.mark.browser]


def contrast(foreground, background):
    def luminance(rgb):
        values = [value / 255 for value in rgb]
        values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
        return sum(value * weight for value, weight in zip(values, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + .05) / (dark + .05)


def rgb(value):
    import re
    return [float(channel) for channel in re.findall(r"[\d.]+", value)[:3]]


def test_login_focus_and_readable_primary_control(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/login")
    username = page.get_by_label("Username")
    username.focus()
    expect(username).to_have_css("outline-style", "solid")
    expect(username).to_have_css("outline-width", "3px")
    button = page.locator('button[type="submit"]')
    colors = button.evaluate("element => { const s = getComputedStyle(element); return [s.color, s.backgroundColor, s.backgroundImage]; }")
    assert colors[2] == "none"
    assert contrast(rgb(colors[0]), rgb(colors[1])) >= 4.5
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_feedback_wraps_and_reduced_motion_is_respected(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/login")
    feedback = templates.get_template("_feedback.html").module.feedback
    content = "".join(str(feedback(kind, title, "LongDescription" * 30)) for kind, title in
                      [("loading", "Loading your library"), ("empty", "Your library is empty"), ("error", "Unable to load your library")])
    page.set_content(f'''<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width">
        <link rel="stylesheet" href="/assets/css/app.css"><link rel="stylesheet" href="/assets/css/ui-extras.css"></head>
        <body><main style="padding:16px;max-width:960px;margin:auto">{content}
        <div class="field"><label for="probe">Library name</label><input id="probe" placeholder="Name"></div>
        <button class="button button--primary">Try again</button></main></body></html>''')
    expect(page.get_by_role("alert")).to_contain_text("Unable to load your library")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for selector in [".feedback__description", ".field label", ".feedback__title"]:
        colors = page.locator(selector).first.evaluate("element => { const s = getComputedStyle(element); return [s.color, getComputedStyle(document.body).backgroundColor]; }")
        assert contrast(rgb(colors[0]), rgb(colors[1])) >= 4.5
    # Check the palette on the brightest surface used for text and form controls.
    palette = page.evaluate("""() => {
        const style = getComputedStyle(document.documentElement);
        return Object.fromEntries(['--text', '--text2', '--text3', '--accent-text', '--success',
            '--warning', '--danger', '--surface3', '--focus', '--border-control'].map(key => [key, style.getPropertyValue(key).trim()]));
    }""")
    def hex_rgb(value):
        return [int(value[index:index + 2], 16) for index in (1, 3, 5)]
    for token in ["--text", "--text2", "--text3", "--accent-text", "--success", "--warning", "--danger"]:
        assert contrast(hex_rgb(palette[token]), hex_rgb(palette["--surface3"])) >= 4.5, token
    for token in ["--focus", "--border-control"]:
        assert contrast(hex_rgb(palette[token]), hex_rgb(palette["--surface3"])) >= 3, token
    page.locator("#probe").focus()
    expect(page.locator("#probe")).to_have_css("outline-width", "3px")
    page.emulate_media(reduced_motion="reduce")
    # Engines serialize this identical duration as either 1e-05s or 0.00001s.
    assert page.locator(".spinner").evaluate("e => parseFloat(getComputedStyle(e).animationDuration)") == pytest.approx(.00001)
    # Include a narrow phone in addition to the shared mobile/desktop/TV sizes.
    page.set_viewport_size({"width": 320, "height": 720})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("accent", ["red", "blue", "purple", "green", "orange", "pink"])
def test_accent_preferences_keep_button_contrast(baseline_page, accent):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/login")
    page.add_script_tag(url=f"{baseline_page.base_url}/assets/js/appearance.js")
    page.evaluate("accent => SVAppearance.applyAccent(accent)", accent)
    button = page.locator('button[type="submit"]')
    for hover in [False, True]:
        if hover:
            button.hover()
        colors = button.evaluate("element => { const s = getComputedStyle(element); return [s.color, s.backgroundColor]; }")
        assert contrast(rgb(colors[0]), rgb(colors[1])) >= 4.5
