"""Exercise custom controls with a generated H.264/AAC fixture, without providers."""
from pathlib import Path
import shutil
import subprocess

import pytest
from playwright.sync_api import expect


ASSETS = Path(__file__).resolve().parents[2] / "assets"
pytestmark = [pytest.mark.browser, pytest.mark.media,
              pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required for deterministic media fixture")]


def test_custom_controls_play_seek_pause_and_release_real_media(regression_browser, browser_viewport, tmp_path):
    fixture = tmp_path / "fixture.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12",
        "-c:v", "libx264", "-profile:v", "baseline", "-g", "10", "-threads", "1",
        "-c:a", "aac", "-movflags", "+faststart", str(fixture),
    ], check=True, capture_output=True, timeout=20)
    context = regression_browser.new_context(viewport=browser_viewport)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def respond(route):
        if route.request.url.endswith("fixture.mp4"):
            data = fixture.read_bytes()
            headers = {"Accept-Ranges": "bytes"}
            requested = route.request.headers.get("range")
            if requested:
                start, end = requested.removeprefix("bytes=").split("-")
                start, end = int(start), int(end) if end else len(data) - 1
                headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                return route.fulfill(status=206, content_type="video/mp4", headers=headers, body=data[start:end + 1])
            return route.fulfill(content_type="video/mp4", headers=headers, body=data)
        return route.fulfill(content_type="text/html", body='''
      <html lang="en"><meta name="viewport" content="width=device-width"><button id="play-button">Open</button>
      <div id="player-container" class="player-box" style="max-width:900px"></div><div id="torrent-status" hidden></div>
      </html>''')
    context.route("https://playback.test/**", respond)
    try:
        page.goto("https://playback.test/")
        page.add_style_tag(path=ASSETS / "css/app.css")
        for name in ["local-player.js", "custom-player.js"]:
            page.add_script_tag(path=ASSETS / f"js/{name}")
        page.add_script_tag(content=(ASSETS / "js/media_detail.js").read_text().replace(
            'document.addEventListener("DOMContentLoaded", boot);', ''))
        for _ in range(3):
            page.evaluate('''() => {
              const container = document.querySelector('#player-container');
              container.innerHTML = '<video playsinline preload="auto" src="/fixture.mp4"></video>';
              document.querySelector('#play-button').focus();
              SVInitCustomPlayer(container.querySelector('video'), container, {title: 'Deterministic fixture'});
            }''')
            page.wait_for_function("document.querySelector('video').readyState >= 2")
            page.locator("#sv-play").click()
            page.wait_for_function("document.querySelector('video').currentTime > .3")
            expect(page.locator("#sv-play")).to_have_attribute("aria-label", "Pause")
            page.locator("#sv-play").click()
            assert page.locator("video").evaluate("video => video.paused")
            page.locator("#sv-seek").evaluate('''seek => {
              seek.value = 50;
              seek.dispatchEvent(new Event('input'));
              seek.dispatchEvent(new Event('change'));
            }''')
            page.wait_for_function("Math.abs(document.querySelector('video').currentTime - 6) < .5", timeout=5000)
            page.locator("#sv-volume").focus()
            page.keyboard.press("ArrowLeft")
            assert page.locator("video").evaluate("video => video.volume") < 1
            page.evaluate("window.closedVideo = document.querySelector('video')")
            page.locator("#sv-back").click()
            assert page.evaluate("closedVideo.paused && !closedVideo.hasAttribute('src') && !closedVideo._svCleanup")
            expect(page.locator("#play-button")).to_be_focused()
        assert not errors, errors
    finally:
        context.close()
