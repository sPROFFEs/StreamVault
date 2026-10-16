"""Remote sources use the local playback engine, audio and timeline offsets."""
import pytest
from playwright.sync_api import expect

from test_discovery_routes_browser import discovery_page

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("defer_resolver", [False, True], ids=["immediate", "deferred"])
def test_cached_node_sources_do_not_wait_for_external_providers(discovery_page, defer_resolver):
    ui, page = discovery_page, discovery_page.page
    if defer_resolver:
        # Model a queued request without relying on network or CPU timing.
        page.add_init_script("""
            const nativeFetch = window.fetch.bind(window);
            window.fetch = (url, options) => {
                if (String(url).startsWith('/api/stream/resolve/7?')) {
                    return new Promise(resolve => {
                        window.releaseExternalResolver = () => resolve(nativeFetch(url, options));
                    });
                }
                return nativeFetch(url, options);
            };
        """)
    base = "/api/federation/peers/1/media/" + "a" * 64
    ui.payloads["/api/federation/sources/7"] = [{"source": "Node · Ready now", "type": "federation", "quality": "1080p",
        "url": base + "/stream", "playback_url": base + "/playback", "metadata_url": base + "/metadata"}]
    external_source = {"source": "External provider", "type": "direct", "url": "/external.mp4"}

    def resolve(route):
        # The cached source must already be visible before the provider replies.
        expect(page.locator("#source-select")).to_contain_text("Ready now")
        route.fulfill(json=[*ui.payloads["/api/federation/sources/7"], external_source])

    page.route("**/api/stream/resolve/7?*", resolve)
    with page.expect_response("**/api/stream/resolve/7?*") as response:
        page.goto("https://streamvault.test/media/7?type=movie")
        if defer_resolver:
            expect(page.locator("#source-select")).to_contain_text("Ready now")
            page.wait_for_function("typeof window.releaseExternalResolver === 'function'")
            page.evaluate("window.releaseExternalResolver(); undefined")
    assert response.value.ok
    # Wait for the merge, not an option count that was true before the response.
    expect(page.locator("#source-select option")).to_have_count(2)
    expect(page.locator("#source-select option").filter(has_text="Ready now")).to_have_count(1)
    expect(page.locator("#source-select")).to_contain_text("External provider")


@pytest.mark.parametrize("prepared", [False, True], ids=["legacy", "prepared"])
def test_federated_source_metadata_audio_and_seek_use_same_origin(discovery_page, prepared):
    ui, page = discovery_page, discovery_page.page
    base = "/api/federation/peers/1/media/" + "a" * 64
    ui.payloads["/api/stream/resolve/7"] = [{"source": "Node · Other server", "type": "federation", "quality": "1080p",
        "url": base + "/stream", "playback_url": base + "/playback", "metadata_url": base + "/metadata"}]
    ui.payloads[base + "/metadata"] = {"duration_seconds": 100, "subtitles": [], "audio_tracks": [
        {"stream_index": 1, "language": "eng", "codec": "aac"}, {"stream_index": 2, "language": "spa", "codec": "aac"}]}
    ui.payloads[base + "/playback"] = {"mode": "stream", "offset": 30, "duration_seconds": 100, "url": base + "/stream?start=30"}
    if prepared:
        ui.payloads[base + "/metadata"]["prepared_playback"] = {
            **ui.payloads[base + "/playback"], "audio": 1, "start": 0}
    # Do not decode a fabricated video: this tests descriptor/control wiring.
    page.add_init_script("HTMLMediaElement.prototype.play = async () => {}; HTMLMediaElement.prototype.load = () => {};")
    page.route("**" + base + "/stream*", lambda route: route.fulfill(status=204))
    page.goto("https://streamvault.test/media/7?type=movie")
    expect(page.locator("#source-select")).to_contain_text("Other server")
    page.evaluate('() => { SVOfflineUI.download = (file, options) => { window.deviceFile = file; window.deviceAudio = options.audio; }; }')
    page.locator('#device-download-button').click()
    assert page.evaluate('deviceFile.playback_url') == base + '/playback'
    page.evaluate("playCurrentItem()")
    expect(page.locator("video")).to_have_attribute("src", base + "/stream?start=30")
    expect(page.locator("#audio-select")).to_contain_text("SPA")
    initial_requests = [query for path, query in ui.calls if path == base + "/playback"]
    assert initial_requests == [] if prepared else any("audio=1" in query and "path=" not in query for query in initial_requests)
    page.locator("#audio-select").select_option("2")
    page.wait_for_function("document.querySelector('video').dataset.playbackOffset === '30'")
    assert any(path == base + "/playback" and "audio=2" in query for path, query in ui.calls)
    page.locator('#device-download-button').click()
    assert page.evaluate('deviceAudio') == 2
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
