"""Remote sources use the local playback engine, audio and timeline offsets."""
import pytest
from playwright.sync_api import expect

from test_discovery_routes_browser import discovery_page

pytestmark = pytest.mark.browser


def test_cached_node_sources_do_not_wait_for_external_providers(discovery_page):
    ui, page = discovery_page, discovery_page.page
    base = "/api/federation/peers/1/media/" + "a" * 64
    ui.payloads["/api/federation/sources/7"] = [{"source": "Node · Ready now", "type": "federation", "quality": "1080p",
        "url": base + "/stream", "playback_url": base + "/playback", "metadata_url": base + "/metadata"}]
    pending = []
    page.route("**/api/stream/resolve/7?*", lambda route: pending.append(route))
    page.goto("https://streamvault.test/media/7?type=movie")
    expect(page.locator("#source-select")).to_contain_text("Ready now")
    assert len(pending) == 1
    pending[0].fulfill(json=ui.payloads["/api/federation/sources/7"])
    expect(page.locator("#source-select option")).to_have_count(1)


def test_federated_source_metadata_audio_and_seek_use_same_origin(discovery_page):
    ui, page = discovery_page, discovery_page.page
    base = "/api/federation/peers/1/media/" + "a" * 64
    ui.payloads["/api/stream/resolve/7"] = [{"source": "Node · Other server", "type": "federation", "quality": "1080p",
        "url": base + "/stream", "playback_url": base + "/playback", "metadata_url": base + "/metadata"}]
    ui.payloads[base + "/metadata"] = {"duration_seconds": 100, "subtitles": [], "audio_tracks": [
        {"stream_index": 1, "language": "eng", "codec": "aac"}, {"stream_index": 2, "language": "spa", "codec": "aac"}]}
    ui.payloads[base + "/playback"] = {"mode": "stream", "offset": 30, "duration_seconds": 100, "url": base + "/stream?start=30"}
    # Do not decode a fabricated video: this tests descriptor/control wiring.
    page.add_init_script("HTMLMediaElement.prototype.play = async () => {}; HTMLMediaElement.prototype.load = () => {};")
    page.route("**" + base + "/stream*", lambda route: route.fulfill(status=204))
    page.goto("https://streamvault.test/media/7?type=movie")
    expect(page.locator("#source-select")).to_contain_text("Other server")
    page.evaluate("playCurrentItem()")
    expect(page.locator("video")).to_have_attribute("src", base + "/stream?start=30")
    expect(page.locator("#audio-select")).to_contain_text("SPA")
    assert any(path == base + "/playback" and "audio=1" in query and "path=" not in query for path, query in ui.calls)
    page.locator("#audio-select").select_option("2")
    page.wait_for_function("document.querySelector('video').dataset.playbackOffset === '30'")
    assert any(path == base + "/playback" and "audio=2" in query for path, query in ui.calls)
    assert not any("hls.min.js" in path or "webtorrent" in path for path, _ in ui.calls)


def test_closed_player_ignores_late_remote_metadata(discovery_page):
    page = discovery_page.page
    page.goto("https://streamvault.test/media/7?type=movie")
    expect(page.locator("#detail-title")).to_have_text("Example")
    pending = []
    page.route("**/remote-metadata", lambda route: pending.append(route))
    page.evaluate("loadSource({type:'federation', source:'Node', metadata_url:'/remote-metadata', playback_url:'/remote-playback'})")
    page.wait_for_timeout(50)
    page.evaluate("destroyPlayer()")
    assert len(pending) == 1
    pending[0].fulfill(json={"audio_tracks": [], "duration_seconds": 100, "subtitles": []})
    page.wait_for_timeout(50)
    assert page.locator("video").count() == 0
