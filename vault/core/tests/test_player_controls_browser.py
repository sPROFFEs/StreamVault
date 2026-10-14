"""Deterministic control/lifecycle fixtures; actual codecs are tested separately."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect


ASSETS = Path(__file__).resolve().parents[2] / "assets"
pytestmark = [pytest.mark.browser, pytest.mark.baseline]


@pytest.fixture
def player_page(regression_browser, browser_viewport):
    context = regression_browser.new_context(viewport=browser_viewport, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_content('''<html lang="en"><meta name="viewport" content="width=device-width">
      <button id="play-button">Open playback</button><input id="outside" aria-label="Search">
      <div class="media-layout"><div id="player-container" class="player-box"><video playsinline></video></div></div>
      <div id="torrent-status" hidden></div><div id="audio-wrapper">
      <select id="audio-select"><option value="1">English</option><option value="2">Spanish</option></select></div>
      <select id="source-select"><option value="0">Fixture source</option></select>
      <div id="subtitle-list"><button class="source-item"><strong>English captions</strong></button></div>
    </html>''')
    page.add_style_tag(path=ASSETS / "css/app.css")
    page.add_style_tag(path=ASSETS / "css/ui-extras.css")
    page.add_script_tag(path=ASSETS / "js/shell.js")
    page.add_script_tag(path=ASSETS / "js/local-player.js")
    page.add_script_tag(path=ASSETS / "js/custom-player.js")
    page.add_script_tag(content=(ASSETS / "js/media_detail.js").read_text().replace(
        'document.addEventListener("DOMContentLoaded", boot);', ''))
    page.evaluate('''() => {
      window.video = document.querySelector('video'); window.seeks = [];
      window.duration = 100; window.paused = true; window.playCalls = 0;
      Object.defineProperties(video, {
        duration: {get: () => window.duration}, paused: {get: () => window.paused},
        ended: {get: () => false}
      });
      video.play = async () => { window.playCalls++; window.paused = false; video.dispatchEvent(new Event('play')); };
      video.pause = () => { window.paused = true; video.dispatchEvent(new Event('pause')); };
      document.querySelector('#play-button').focus();
      // boot() is intentionally disabled in this fixture. Bind the native
      // fullscreen synchronization that boot() registers in production;
      // Firefox consumes Escape itself instead of delivering it to handleKey.
      document.addEventListener('fullscreenchange', _onFsChange);
      document.addEventListener('webkitfullscreenchange', _onFsChange);
      window.init = () => SVInitCustomPlayer(video, document.querySelector('#player-container'), {
        title: '<img src=x onerror="window.titleInjected=true">', duration: 100,
        onSeek: value => seeks.push(value)
      });
      init();
    }''')
    try:
        yield page
    finally:
        context.close()
        assert not errors, errors


def test_offset_seek_and_accessible_controls(player_page):
    page = player_page
    page.evaluate("duration = 70; video.dataset.playbackOffset = '30'; video.currentTime = 5; video.dispatchEvent(new Event('timeupdate'))")
    expect(page.locator("#sv-seek")).to_have_value("35")
    expect(page.locator("#sv-time-remaining")).to_have_text("-1:05")
    expect(page.locator("#sv-seek")).to_have_attribute("aria-label", "Seek")
    expect(page.locator("#sv-volume")).to_have_attribute("aria-label", "Volume")
    assert not page.evaluate("!!window.titleInjected")
    assert page.locator(".sv-player__title img").count() == 0
    page.locator("#sv-forward").click()
    assert page.evaluate("seeks") == [45]


def test_focus_remote_native_ranges_and_shortcut_ownership(player_page):
    page = player_page
    play = page.locator("#sv-play")
    play.focus()
    play.press("ArrowRight")
    expect(page.locator("#sv-rewind")).to_be_focused()
    assert page.evaluate("seeks") == []
    page.locator("#sv-rewind").press("Enter")
    assert page.evaluate("seeks") == [0]
    play.focus()
    play.press("Space")
    assert page.evaluate("playCalls") == 1
    play.press("Control+f")
    assert not page.locator("#player-container").evaluate("el => el.classList.contains('player-box--pseudo-fs')")
    page.locator("#outside").focus()
    page.keyboard.press("k")
    assert page.evaluate("playCalls") == 1
    seek = page.locator("#sv-seek")
    seek.focus()
    seek.press("ArrowRight")
    assert len(page.evaluate("seeks")) == 2
    expect(seek).to_be_focused()
    volume = page.locator("#sv-volume")
    volume.focus()
    volume.press("ArrowLeft")
    assert page.evaluate("video.volume") < 1
    volume.press("Tab")
    assert not volume.evaluate("el => el === document.activeElement"), "Tab must leave the slider"


def test_buffer_error_and_focus_remain_visible(player_page):
    page = player_page
    page.clock.install()
    page.locator("#sv-play").focus()
    page.evaluate("video.play(); video.dispatchEvent(new Event('waiting'))")
    expect(page.get_by_role("status")).to_have_text("Buffering…")
    page.clock.fast_forward(4000)
    expect(page.locator(".sv-player")).to_have_css("opacity", "1")
    page.evaluate("video.dispatchEvent(new Event('playing'))")
    expect(page.get_by_role("status")).to_be_hidden()
    page.evaluate("video.dispatchEvent(new Event('error'))")
    expect(page.get_by_role("status")).to_contain_text("unavailable")
    expect(page.locator(".sv-player")).to_have_css("opacity", "1")


def test_remote_menus_restore_focus_and_skip_unrelated_mutations(player_page):
    page = player_page
    button = page.locator("#sv-audio-btn")
    button.focus()
    button.press("Enter")
    expect(button).to_have_attribute("aria-expanded", "true")
    first = page.locator("#sv-audio-menu button").first
    expect(first).to_be_focused()
    first.press("ArrowDown")
    second = page.locator("#sv-audio-menu button").nth(1)
    expect(second).to_be_focused()
    second.press("Enter")
    assert page.locator("#audio-select").input_value() == "2"
    expect(button).to_be_focused()
    button.press("Enter")
    page.evaluate("document.querySelector('#subtitle-list').style.color = 'red'")
    expect(page.locator("#sv-audio-menu button").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(button).to_be_focused()
    assert page.locator("video").count() == 1


def test_reinitialize_and_leave_release_resources(player_page):
    page = player_page
    page.clock.install()
    page.evaluate("for (let i = 0; i < 4; i++) init();")
    page.locator("#sv-play").focus()
    page.keyboard.press("k")
    assert page.evaluate("playCalls") == 1, "Only the active player's listener should respond"
    page.evaluate("window.destroyed = []; state.hls = {destroy: () => destroyed.push('hls')}; state.wtClient = {destroy: () => destroyed.push('torrent')};")
    page.locator("#sv-back").click()
    assert page.evaluate("destroyed") == ["hls", "torrent"]
    assert page.evaluate("paused")
    assert page.locator("video").count() == 0
    expect(page.locator("#play-button")).to_be_focused()
    page.clock.fast_forward(60000)
    assert not page.locator("#player-container").evaluate("el => el.classList.contains('sv-active')")


def test_touch_orientation_fullscreen_and_remote_back(player_page):
    page = player_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#sv-play").tap()
    assert page.evaluate("playCalls") == 1
    # Emulate mobile browsers rejecting native fullscreen; Chromium cannot
    # resize its native fullscreen window through the viewport API.
    page.evaluate("() => { document.querySelector('#player-container').requestFullscreen = () => Promise.reject(new Error('Unsupported')); }")
    page.locator("#sv-fullscreen").tap()
    expect(page.locator("#player-container")).to_have_class(re.compile("player-box--pseudo-fs"))
    page.set_viewport_size({"width": 844, "height": 390})
    page.evaluate("window.dispatchEvent(new Event('orientationchange'))")
    expect(page.locator("#sv-play")).to_be_visible()
    assert page.evaluate("video.currentTime") == 0
    page.locator("#sv-play").focus()
    page.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'GoBack', bubbles: true}))")
    assert not page.locator("#player-container").evaluate("el => el.classList.contains('player-box--pseudo-fs')")
    assert page.locator("video").count() == 1
    page.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'GoBack', bubbles: true}))")
    assert page.locator("video").count() == 0
    expect(page.locator("#play-button")).to_be_focused()


def test_pagehide_stops_direct_playback(player_page):
    page = player_page
    page.evaluate("video.play(); window.dispatchEvent(new Event('pagehide'))")
    assert page.evaluate("paused")


def test_native_fullscreen_keeps_controls_and_exits_before_player(player_page):
    page = player_page
    page.locator("#sv-fullscreen").click()
    page.wait_for_function("document.fullscreenElement === document.querySelector('#player-container')")
    expect(page.locator("#sv-fullscreen")).to_have_attribute("aria-label", "Exit fullscreen")
    page.locator("#sv-play").focus()
    page.keyboard.press("Escape")
    page.wait_for_function("!document.fullscreenElement")
    expect(page.locator("#sv-fullscreen")).to_have_attribute("aria-label", "Fullscreen")
    assert page.locator("video").count() == 1


def test_fullscreen_exit_restores_focus_and_closed_player_can_reopen(player_page):
    page = player_page
    page.evaluate("() => { document.querySelector('#player-container').requestFullscreen = () => Promise.reject(new Error('Unsupported')); }")
    page.locator("#sv-fullscreen").click()
    page.locator("#pseudo-fs-exit").click()
    expect(page.locator("#sv-fullscreen")).to_be_focused()
    page.evaluate("playCurrentItem = () => { window.reopened = true; }")
    page.locator("#sv-back").click()
    page.locator("#player-container").get_by_role("button", name="Play", exact=True).click()
    assert page.evaluate("!!window.reopened")


def test_shared_search_shortcut_can_open_a_visible_dialog_from_fullscreen(player_page):
    page = player_page
    page.evaluate('''() => {
      document.querySelector('#player-container').requestFullscreen = () => Promise.reject(new Error('Unsupported'));
      const dialog = document.createElement('div');
      dialog.id = 'search-dialog'; dialog.hidden = true;
      dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-label', 'Search');
      dialog.innerHTML = '<input aria-label="Search query">'; document.body.appendChild(dialog);
      document.addEventListener('keydown', event => {
        if (event.ctrlKey && event.key.toLowerCase() === 'f') {
          event.preventDefault(); SVShell.open(dialog);
        }
      });
    }''')
    page.locator("#sv-fullscreen").click()
    page.locator("#sv-play").focus()
    page.keyboard.press("Control+f")
    assert not page.locator("#player-container").evaluate("el => el.classList.contains('player-box--pseudo-fs')")
    expect(page.get_by_role("dialog", name="Search")).to_be_visible()
    expect(page.get_by_role("textbox", name="Search query")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#sv-play")).to_be_focused()
    assert page.locator("video").count() == 1


def test_fatal_hls_errors_offer_bounded_recovery_and_feedback(player_page):
    page = player_page
    page.evaluate('''() => {
      window.Hls = class {
        static isSupported() { return true; }
        static Events = {MANIFEST_PARSED: 'manifest', AUDIO_TRACKS_UPDATED: 'audio', ERROR: 'error'};
        static ErrorTypes = {NETWORK_ERROR: 'network', MEDIA_ERROR: 'media'};
        constructor() { window.hls = this; this.handlers = {}; this.network = 0; this.media = 0; }
        on(event, handler) { this.handlers[event] = handler; }
        loadSource() {} attachMedia() {} destroy() {}
        startLoad() { this.network++; } recoverMediaError() { this.media++; }
      };
      loadSource({type: 'hls', url: '/fixture.m3u8'});
      hls.handlers.error(null, {fatal: true, type: 'network'});
      hls.handlers.error(null, {fatal: true, type: 'media'});
      hls.handlers.error(null, {fatal: true, type: 'media'});
    }''')
    assert page.evaluate("[hls.network, hls.media]") == [1, 1]
    expect(page.get_by_role("status")).to_contain_text("unavailable")


def test_async_local_seek_errors_and_stale_requests(player_page):
    page = player_page
    page.evaluate('''async () => {
      window.fetch = async () => ({ok: false, json: async () => ({detail: 'Fixture playback unavailable'})});
      video._svCleanup();
      SVInitCustomPlayer(video, document.querySelector('#player-container'), {
        duration: 100, onSeek: target => SVLocalPlayback.open(video, {relative_path: 'fixture.mkv'}, {start: target})
      });
    }''')
    page.locator("#sv-forward").click()
    expect(page.get_by_role("status")).to_have_text("Fixture playback unavailable")


def test_subtitles_select_off_and_shortcuts_stay_inside_player(player_page):
    page = player_page
    page.evaluate('''() => {
      window.showToast = () => {};
      const item = {play_url: 'data:text/vtt,WEBVTT', language: 'English'};
      state.remoteSubtitles = [item];
      const button = document.querySelector('#subtitle-list button');
      button.dataset.subtitleIndex = '0';
      button.onclick = () => activateVideoSubtitle(item, video);
      window.fetch = async () => ({ok: true, text: async () => 'WEBVTT\\n\\n00:00:00.000 --> 00:00:20.000\\nFixture caption\\n'});
    }''')
    page.locator("#sv-sub-btn").click()
    page.locator("#sv-sub-menu button").nth(1).click()
    expect(page.locator("#video-subtitle-overlay")).to_have_text("Fixture caption")
    page.locator("#outside").focus()
    page.keyboard.press("Shift+ArrowRight")
    assert page.evaluate("state.videoSubtitleOffset") == 0
    page.locator("#sv-play").focus()
    page.keyboard.press("Shift+ArrowRight")
    assert page.evaluate("state.videoSubtitleOffset") == .25
    assert page.evaluate("seeks") == []
    page.locator("#sv-sub-btn").click()
    page.locator("#sv-sub-menu button").first.click()
    expect(page.locator("#video-subtitle-overlay")).to_be_hidden()
    assert page.evaluate("state._videoSubKeyHandler") is None


def test_pending_subtitle_does_not_restore_resources_after_close(player_page):
    page = player_page
    page.evaluate('''() => {
      window.showToast = () => {};
      window.fetch = () => new Promise(resolve => { window.resolveSubtitle = resolve; });
      window.pendingSubtitle = activateVideoSubtitle({play_url: '/fixture.vtt', language: 'English'}, video);
      SVDestroyPlayer();
    }''')
    page.evaluate('''async () => {
      resolveSubtitle({ok: true, text: async () => 'WEBVTT\\n\\n00:00:00.000 --> 00:00:20.000\\nFixture caption\\n'});
      await pendingSubtitle;
    }''')
    assert page.locator("#video-subtitle-overlay").count() == 0
    assert page.evaluate("state.videoSubtitles") is None


def test_embed_caption_timing_and_pending_cleanup(player_page):
    page = player_page
    page.evaluate('''async () => {
      video._svCleanup();
      const container = document.querySelector('#player-container');
      container.innerHTML = '<iframe srcdoc="Fixture embed"></iframe><div id="subtitle-overlay"></div>';
      container.tabIndex = 0;
      window.showToast = () => {};
      window.fetch = async () => ({ok: true, text: async () => 'WEBVTT\\n\\n00:00:00.000 --> 00:00:20.000\\nFixture caption\\n'});
      initEmbedSubtitleOverlay();
      await activateEmbedSubtitle({play_url: '/embed.vtt'});
    }''')
    page.locator("#outside").focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("state.embedSubtitleOffset") == 0
    page.locator("#player-container").focus()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("state.embedSubtitleOffset") == 500
    page.evaluate('''() => {
      window.fetch = () => new Promise(resolve => {window.resolveSubtitle = resolve;});
      window.pendingSubtitle = activateEmbedSubtitle({play_url: '/late.vtt'});
      SVDestroyPlayer();
    }''')
    page.evaluate('''async () => {
      resolveSubtitle({ok: true, text: async () => 'WEBVTT\\n\\n00:00:00.000 --> 00:00:20.000\\nFixture caption\\n'});
      await pendingSubtitle;
    }''')
    assert page.evaluate("state.embedSubtitles") is None
    assert page.evaluate("state.embedOverlayRaf") is None
    assert page.evaluate("state._embedSubKeyHandler") is None
