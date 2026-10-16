"""Real service-worker/IndexedDB downloads, byte ranges and airplane-mode launch."""
import hashlib
import os
import shutil
import subprocess

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.browser


@pytest.fixture
def offline_page(regression_browser, regression_server, browser_viewport):
    context = regression_browser.new_context(viewport=browser_viewport)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(regression_server + '/assets/offline.html')
    expect(page.locator('#offline-title')).to_be_visible()
    page.evaluate('() => SVOffline.ensureWorker()')
    assert page.evaluate('() => !!navigator.serviceWorker.controller')
    yield page, context, regression_server
    context.close()
    assert not errors, errors


@pytest.mark.parametrize('remote', [False, True], ids=['local', 'federated'])
def test_device_download_survives_offline_reload_and_supports_ranges(offline_page, remote):
    page, context, origin = offline_page
    payload = b'abcdefgh' * (256 * 1024) + b'last-fragment'
    base = '/api/federation/peers/1/media/' + 'a' * 64
    playback = base + '/playback' if remote else '/api/downloads/files/playback'
    stream = base + '/stream' if remote else '/api/downloads/files/stream?path=Movie.mp4'
    file = {'title': 'Movie <Cinema>', 'name': 'Movie.mp4', 'relative_path': 'Movie.mp4',
            'size_bytes': len(payload), 'audio_tracks': [], 'subtitles': []}
    if remote:
        file.update(origin='federation', peer_name='Cinema', relative_path='federation:1:' + 'a' * 64,
                    playback_url=playback, metadata_url=base + '/metadata', season_number=1, episode_number=2)
    page.route('**' + playback + '?*', lambda route: route.fulfill(json={
        'url': stream, 'mode': 'file', 'offset': 0, 'duration_seconds': 100}))
    page.route('**' + stream, lambda route: route.fulfill(body=payload, content_type='video/mp4'))
    entry = page.evaluate('file => SVOffline.save(file)', file)
    assert entry['status'] == 'complete' and entry['size_bytes'] == len(payload)
    assert 'relative_path' not in entry and 'playback_url' not in entry
    assert 'access_token' not in str(entry)
    context.set_offline(True)
    page.goto(origin + '/portal')
    expect(page.locator('#offline-list')).to_contain_text('Movie <Cinema>')
    assert page.locator('#offline-list script').count() == 0
    result = page.evaluate('''async id => {
        const url = SVOffline.mediaUrl(id);
        const full = await fetch(url);
        const digest = await crypto.subtle.digest('SHA-256', await full.arrayBuffer());
        const range = await fetch(url, {headers: {Range: 'bytes=1048573-1048580'}});
        const suffix = await fetch(url, {headers: {Range: 'bytes=-4'}});
        const invalid = await fetch(url, {headers: {Range: 'bytes=999999999-'}});
        const head = await fetch(url, {method: 'HEAD'});
        return {hash: [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join(''),
            status: range.status, text: await range.text(), suffix: await suffix.text(),
            contentRange: range.headers.get('Content-Range'), invalid: invalid.status,
            length: Number(head.headers.get('Content-Length')), head: await head.text()};
    }''', entry['id'])
    assert result == {'hash': hashlib.sha256(payload).hexdigest(), 'status': 206,
                      'text': payload[1048573:1048581].decode(), 'suffix': payload[-4:].decode(),
                      'contentRange': f'bytes 1048573-1048580/{len(payload)}', 'invalid': 416,
                      'length': len(payload), 'head': ''}
    page.locator('[data-offline-remove]').evaluate('button => button.click()')
    page.get_by_role('dialog').get_by_role('button', name='Delete', exact=True).evaluate('button => button.click()')
    expect(page.locator('#offline-empty')).to_be_visible()
    assert page.evaluate('SVOffline.list()') == []
    assert page.evaluate('''async () => {
        const db = await SVOffline.database();
        return await new Promise(resolve => {
            const request = db.transaction('chunks').objectStore('chunks').count();
            request.onsuccess = () => resolve(request.result);
        });
    }''') == 0


def test_failed_quota_and_cancelled_downloads_never_become_playable(offline_page):
    page, _, _ = offline_page
    result = page.evaluate('''async () => {
        const file = {title: 'Partial', relative_path: 'Partial.mp4', audio_tracks: [], subtitles: []};
        const nativeFetch = window.fetch;
        let failRead = true;
        window.fetch = async url => String(url).includes('/playback')
            ? {ok: true, json: async () => ({url: '/api/downloads/files/stream?path=Partial.mp4', mode: 'file'})}
            : new Response(new ReadableStream({pull(controller) {
                if (this.sent) {
                    if (failRead) controller.error(new Error('Interrupted download'));
                    else controller.close();
                } else {
                    this.sent = true;
                    controller.enqueue(new Uint8Array(1048576));
                }
            }}), {headers: {'Content-Type': 'video/mp4'}});
        const failures = [];
        const snapshots = [];
        try { await SVOffline.save(file, {onProgress: () => snapshots.push(SVOffline.list())}); }
        catch (error) { failures.push(error.message); }
        const partials = (await Promise.all(snapshots)).flat();
        failRead = false;
        const originalEstimate = navigator.storage.estimate.bind(navigator.storage);
        navigator.storage.estimate = async () => ({usage: 0, quota: 100});
        try { await SVOffline.save(file); } catch (error) { failures.push(error.message); }
        navigator.storage.estimate = originalEstimate;
        const controller = new AbortController(); controller.abort();
        try { await SVOffline.save(file, {signal: controller.signal}); } catch (error) { failures.push(error.name); }
        const midTransfer = new AbortController();
        try { await SVOffline.save(file, {signal: midTransfer.signal, onProgress: () => midTransfer.abort()}); }
        catch (error) { failures.push(error.name); }
        window.fetch = nativeFetch;
        const db = await SVOffline.database();
        const chunks = await new Promise(resolve => {
            const request = db.transaction('chunks').objectStore('chunks').count();
            request.onsuccess = () => resolve(request.result);
        });
        return {failures, partials, chunks, items: await SVOffline.list()};
    }''')
    assert len(result['failures']) == 4
    assert any(item['status'] == 'downloading' and item['size_bytes'] == 1048576 for item in result['partials'])
    assert result['chunks'] == 0
    assert result['items'] == []


def test_offline_download_rejects_external_urls_without_leaking_cookies(offline_page):
    page, _, _ = offline_page
    result = page.evaluate('''async () => {
        const file = {title: 'Unsafe', relative_path: 'Unsafe.mp4', audio_tracks: [], subtitles: []};
        const nativeFetch = window.fetch;
        const calls = [];
        window.fetch = async url => {
            calls.push(String(url));
            return {ok: true, json: async () => ({url: 'https://untrusted.test/video.mp4', mode: 'file'})};
        };
        let rejected = false;
        try { await SVOffline.save(file); } catch (_) { rejected = true; }
        window.fetch = nativeFetch;
        return {rejected, calls, items: await SVOffline.list()};
    }''')
    assert result['rejected'] and result['items'] == []
    assert len(result['calls']) == 1 and '/files/playback?' in result['calls'][0]


@pytest.mark.parametrize('fragmented', [False, True], ids=['mp4', 'fragmented-mp4'])
def test_real_video_and_included_subtitles_play_without_a_network(offline_page, tmp_path, fragmented):
    page, context, origin = offline_page
    ffmpeg = os.getenv('STREAMVAULT_TEST_FFMPEG') or shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required for deterministic offline video fixture')
    video = tmp_path / 'fixture.mp4'
    flags = 'frag_keyframe+empty_moov+default_base_moof' if fragmented else '+faststart'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10',
                    '-t', '3', '-an', '-c:v', 'libx264', '-threads', '1', '-g', '5',
                    '-pix_fmt', 'yuv420p', '-movflags', flags, str(video)],
                   check=True, capture_output=True, timeout=30)
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'stream' if fragmented else 'file',
        'offset': 0, 'duration_seconds': 3}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=video.read_bytes(), content_type='video/mp4'))
    page.route('**/api/downloads/files/subtitle?*', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nOffline caption\n', content_type='text/vtt'))
    entry = page.evaluate('''() => SVOffline.save({title: 'Offline playback fixture', relative_path: 'Fixture.mp4', audio_tracks: [],
        subtitles: [{url: '/api/downloads/files/subtitle?path=Fixture.vtt', language: 'en', label: 'English'}]})''')
    assert len(entry['subtitles']) == 1
    context.set_offline(True)
    page.goto(origin + '/portal')
    expect(page.locator('#offline-list')).to_contain_text('Offline playback fixture')
    page.evaluate('() => { document.querySelector("#offline-video").muted = true; }')
    page.get_by_role('button', name='Play', exact=True).evaluate('button => button.click()')
    page.wait_for_function('() => document.querySelector("#offline-video").currentTime > .1', polling=100)
    page.evaluate('() => { const video = document.querySelector("#offline-video"); video.pause(); video.currentTime = .5; video.textTracks[0].mode = "showing"; }')
    page.wait_for_function('() => !document.querySelector("#offline-video").seeking', polling=100)
    assert page.evaluate('() => document.querySelector("#offline-video").error') is None
    assert page.evaluate('() => document.querySelector("#offline-video").currentTime') >= .5
    page.wait_for_function('() => document.querySelector("#offline-video").textTracks[0].cues?.length === 1', polling=100)


def test_offline_shell_avoids_session_cache_and_recovers_from_server_errors(offline_page):
    page, context, origin = offline_page
    keys = page.evaluate('''async () => {
        const names = await caches.keys();
        return (await Promise.all(names.map(async name => (await (await caches.open(name)).keys()).map(request => new URL(request.url).pathname)))).flat();
    }''')
    assert all(path.startswith('/assets/') for path in keys)
    assert '/portal' not in keys and '/login' not in keys
    context.route('**/portal', lambda route: route.fulfill(status=503, body='Server temporarily unavailable'))
    page.goto(origin + '/portal')
    expect(page.locator('#offline-title')).to_be_visible()


def test_device_library_opens_after_browser_restart_without_network_or_session(
        regression_browser, regression_server, browser_viewport, tmp_path):
    profile = str(tmp_path / 'offline-profile')
    browser_type = regression_browser.browser_type

    def launch():
        return browser_type.launch_persistent_context(
            profile, viewport=browser_viewport,
            args=['--disable-gpu', '--disable-dev-shm-usage'])

    context = launch()
    try:
        page = context.pages[0]
        page.goto(regression_server + '/assets/offline.html')
        page.evaluate('() => SVOffline.ensureWorker()')
        page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
            'url': '/api/downloads/files/stream?path=Stored.mp4', 'mode': 'file',
            'offset': 0, 'duration_seconds': 100}))
        page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(
            body=b'offline-copy', content_type='video/mp4'))
        entry = page.evaluate('''() => SVOffline.save({title: 'Saved before restart',
            relative_path: 'Stored.mp4', audio_tracks: [], subtitles: []})''')
    finally:
        context.close()

    for path in ['/portal', '/downloads', '/assets/offline.html']:
        context = launch()
        try:
            context.clear_cookies()
            # Also block worker-owned requests: this loopback origin must not
            # remain reachable through a restarted worker's network stack.
            context.route('**/*', lambda route: route.abort('internetdisconnected'))
            context.set_offline(True)
            page = context.pages[0]
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(regression_server + path)
            expect(page.locator('#offline-title')).to_be_visible()
            expect(page.locator('#offline-list')).to_contain_text('Saved before restart')
            assert page.evaluate('id => fetch(SVOffline.mediaUrl(id)).then(response => response.text())', entry['id']) == 'offline-copy'
            assert not errors, errors
        finally:
            context.close()
