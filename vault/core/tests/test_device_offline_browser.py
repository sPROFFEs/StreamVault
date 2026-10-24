"""Real service-worker/IndexedDB downloads, byte ranges and airplane-mode launch."""
import hashlib
import os
import shutil
import subprocess
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from regression_fixtures import CHROMIUM_ARGS


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
@pytest.mark.parametrize('provider', [False, True], ids=['included', 'auto-provider'])
def test_real_video_and_included_subtitles_play_without_a_network(offline_page, tmp_path, fragmented, provider):
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
    page.route('**/api/subtitles/search?*', lambda route: route.fulfill(json={'items': [{
        'language': parse_qs(urlsplit(route.request.url).query)['language'][0], 'provider': 'Fixture',
        'play_url': '/api/subtitles/fetch?provider=opensubtitles&file_id=1'}]}))
    page.route('**/api/subtitles/fetch?*', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nOffline provider caption\n', content_type='text/vtt'))
    entry = page.evaluate('''provider => SVOffline.save({title: 'Offline playback fixture', relative_path: 'Fixture.mp4', audio_tracks: [], tmdb_id: 7,
        overview: 'A complete offline cinema experience.', year: '2026', media_type: 'movie',
        subtitles: provider ? [] : [{url: '/api/downloads/files/subtitle?path=Fixture.vtt', language: 'en', label: 'English'},
                    {url: '/api/downloads/files/subtitle?path=Fixture.es.vtt', language: 'es', label: 'Español'}]},
                    {languages: ['en', 'es']})''', provider)
    assert len(entry['subtitles']) == 2
    context.set_offline(True)
    page.goto(origin + '/portal')
    expect(page.locator('#offline-list')).to_contain_text('Offline playback fixture')
    page.evaluate('() => { document.querySelector("#offline-video").muted = true; }')
    page.get_by_role('button', name='Play', exact=True).evaluate('button => button.click()')
    expect(page.locator('#offline-player-description')).to_contain_text('complete offline cinema')
    expect(page.locator('#sv-play')).to_be_visible()
    page.wait_for_function('() => document.querySelector("#offline-video").currentTime > .1', polling=100)
    page.evaluate('''() => {
        document.querySelector('#offline-player').requestFullscreen = async () => {
            window.offlineFullscreenRequested = true;
            throw new Error('Native fullscreen unavailable; retain CSS fallback');
        };
    }''')
    page.locator('#sv-fullscreen').click()
    assert page.evaluate('!!window.offlineFullscreenRequested')
    assert page.evaluate('SVShell.nativeBack()')
    expect(page.locator('#offline-player-panel')).to_be_visible()
    assert not page.locator('#offline-player').evaluate('el => el.classList.contains("player-box--pseudo-fs")')
    page.evaluate('() => { const video = document.querySelector("#offline-video"); video.pause(); video.currentTime = .5; }')
    page.wait_for_function('() => !document.querySelector("#offline-video").seeking', polling=100)
    assert page.evaluate('() => document.querySelector("#offline-video").error') is None
    assert page.evaluate('() => document.querySelector("#offline-video").currentTime') >= .5
    page.wait_for_function('() => document.querySelector("#offline-video").textTracks[0].cues?.length === 1', polling=100)
    page.locator('#sv-sub-btn').click()
    page.locator('#sv-sub-menu').get_by_role('button', name='ES · Fixture' if provider else 'Español', exact=True).click()
    assert page.evaluate('() => document.querySelector("#offline-video").textTracks[1].mode') == 'showing'
    page.locator('#offline-subtitle-size').fill('150')
    page.locator('#offline-subtitle-offset').fill('0.5')
    page.locator('#offline-subtitle-offset').dispatch_event('change')
    assert page.evaluate('document.querySelector("#offline-video").textTracks[1].cues[0].startTime') == .5
    page.wait_for_function('''async () => {
        const [entry] = await SVOffline.list();
        return entry.playback?.subtitle_language === 'es' && entry.playback?.subtitle_size === 150;
    }''')
    page.locator('#sv-back').click()
    expect(page.locator('#offline-player-panel')).to_be_hidden()
    page.reload()
    page.get_by_role('button', name='Continue', exact=True).evaluate('button => button.click()')
    expect(page.locator('#offline-subtitle-select')).to_have_value('1')
    expect(page.locator('#offline-subtitle-size')).to_have_value('150')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_offline_library_search_groups_series_and_preserves_existing_downloads(offline_page):
    page, context, origin = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    page.evaluate('''async () => {
        for (const episode of [1, 2]) await SVOffline.save({title: 'Example series',
            relative_path: `Example.S01E0${episode}.mp4`, media_type: 'series',
            season_number: 1, episode_number: episode, audio_tracks: [], subtitles: []});
        await SVOffline.save({title: 'Other movie', relative_path: 'Other.mp4', audio_tracks: [], subtitles: []});
    }''')
    context.set_offline(True)
    page.goto(origin + '/assets/offline.html')
    expect(page.locator('#offline-list .offline-item')).to_have_count(2)
    page.get_by_role('searchbox', name='Search downloads').fill('Example')
    expect(page.locator('#offline-list .offline-item')).to_have_count(1)
    page.get_by_role('button', name='Details · Example series').click()
    expect(page.locator('#offline-episode-select option')).to_have_count(2)
    page.locator('#offline-episode-select').select_option('2')
    expect(page.locator('#offline-player-meta')).to_contain_text('S01E02')
    # Opening a detail screen must not start playback or contact a provider.
    assert not page.locator('#offline-video').get_attribute('src')
    page.locator('#offline-close').click()
    expect(page.get_by_role('searchbox')).to_have_value('Example')


def test_individual_episode_can_be_removed_without_deleting_the_series(offline_page):
    page, _, _ = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    page.evaluate('''async () => {
        for (const episode of [1, 2]) await SVOffline.save({title: 'Keep the series', overview: 'A series',
            relative_path: `Series.S01E0${episode}.mp4`, media_type: 'series',
            season_number: 1, episode_number: episode, audio_tracks: [], subtitles: []});
        await SVOfflineUI.refresh();
    }''')
    page.get_by_role('button', name='Details · Keep the series').click()
    page.locator('#offline-episode-select').select_option('2')
    page.locator('#offline-detail-delete').click()
    page.get_by_role('dialog').get_by_role('button', name='Delete', exact=True).click()
    expect(page.locator('#offline-player-panel')).to_be_hidden()
    page.wait_for_function('async () => (await SVOffline.list()).length === 1')
    assert page.evaluate('async () => (await SVOffline.list())[0].episode_number') == 1


def test_saved_artwork_and_metadata_are_local_bounded_and_credential_free(offline_page):
    page, context, origin = offline_page
    artwork = Path(__file__).resolve().parents[2] / 'assets/icon.png'
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    context.add_cookies([{'name': 'private_session', 'value': 'must-not-be-copied', 'url': origin}])
    result = page.evaluate('''async () => {
        const nativeFetch = window.fetch;
        const credentials = [];
        window.fetch = (url, options) => {
            if (String(url).includes('/assets/icon.png')) credentials.push(options.credentials);
            return nativeFetch(url, options);
        };
        const file = {title: 'Artwork fixture', overview: 'Saved synopsis', year: '2026', tmdb_id: 7,
            poster_path: '/assets/icon.png', backdrop_path: '/assets/icon.png',
            relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []};
        const entry = await SVOffline.save(file);
        window.fetch = nativeFetch;
        return {poster: entry.poster.size, backdrop: entry.backdrop.size, used: entry.storage_bytes,
            synopsis: entry.overview, keys: Object.keys(entry), credentials};
    }''')
    assert result['poster'] == result['backdrop'] == artwork.stat().st_size
    assert result['used'] == 5 + 2 * artwork.stat().st_size
    assert 'relative_path' not in result['keys'] and '_poster' not in result['keys']
    assert result['credentials'] == ['omit', 'omit']
    context.set_offline(True)
    page.goto(origin + '/assets/offline.html')
    expect(page.locator('.offline-cover img')).to_have_attribute('src', re.compile('^blob:'))
    page.wait_for_function('() => document.querySelector(".offline-cover img").naturalWidth > 0')
    page.get_by_role('button', name='Details · Artwork fixture').click()
    expect(page.locator('#offline-player-description')).to_have_text('Saved synopsis')
    assert 'blob:' in page.locator('#offline-backdrop').evaluate('el => el.style.backgroundImage')
    if directory := os.getenv('STREAMVAULT_UI_SCREENSHOTS'):
        width = page.viewport_size['width']
        page.screenshot(path=str(Path(directory) / f'offline-detail-{width}.png'), full_page=True)
        page.locator('#offline-close').click()
        page.screenshot(path=str(Path(directory) / f'offline-library-{width}.png'), full_page=True)


def test_oversized_or_untrusted_artwork_does_not_break_the_download(offline_page):
    page, _, _ = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    result = page.evaluate('''async () => {
        const nativeFetch = window.fetch, calls = [];
        window.fetch = (url, options) => {
            calls.push(String(url));
            if (String(url).startsWith('https://image.tmdb.org/')) return Promise.resolve(
                new Response(new Uint8Array(2 * 1024 * 1024), {headers: {'Content-Type': 'image/png'}}));
            return nativeFetch(url, options);
        };
        const entry = await SVOffline.save({title: 'Bounded artwork', overview: 'A saved movie',
            poster_path: 'https://untrusted.example/image.png?token=do-not-forward', backdrop_path: '/large.png',
            relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []});
        window.fetch = nativeFetch;
        return {poster: entry.poster, backdrop: entry.backdrop, used: entry.storage_bytes, status: entry.status, calls};
    }''')
    assert result['status'] == 'complete' and result['used'] == 5
    assert result['poster'] is None and result['backdrop'] is None
    assert not any('untrusted.example' in call for call in result['calls'])


def test_device_playback_preferences_do_not_resurrect_deleted_entries(offline_page):
    page, _, _ = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    result = page.evaluate('''async () => {
        const entry = await SVOffline.save({title: 'Preferences', relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []});
        await SVOffline.updatePlayback(entry.id, {progress_seconds: 42, subtitle_language: 'es', subtitle_size: 150,
            subtitle_offset: .5, rate: 1.25, password: 'must-not-be-stored'});
        const saved = (await SVOffline.list())[0].playback;
        await SVOffline.remove(entry.id);
        await SVOffline.updatePlayback(entry.id, {progress_seconds: 99});
        return {saved, entries: await SVOffline.list()};
    }''')
    assert result['saved'] == dict(progress_seconds=42, subtitle_language='es', subtitle_size=150,
                                 subtitle_offset=.5, rate=1.25)
    assert result['entries'] == []


@pytest.mark.parametrize('existing', [False, True], ids=['new-copy', 'existing-copy'])
def test_active_provider_subtitle_is_saved_through_the_authenticated_proxy(offline_page, existing):
    page, context, origin = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    page.route('**/api/subtitles/fetch?*', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nSaved provider caption\n', content_type='text/vtt'))
    entry = page.evaluate('''async existing => {
        const file = {title: 'Provider subtitles', overview: 'Offline copy',
            relative_path: 'Fixture.mp4', audio_tracks: [{stream_index: 1, language: 'eng'}], subtitles: []};
        if (existing) {
            const old = await SVOffline.save(file);
            // Pre-update clients used a null key for the default audio and
            // did not record its language/index. Preserve that copy as-is.
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify([file.relative_path, null])));
            old.source_key = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
            delete old.audio_index; delete old.audio_label;
            const db = await SVOffline.database();
            await new Promise((resolve, reject) => {
                const tx = db.transaction('entries', 'readwrite');
                tx.objectStore('entries').put(old); tx.oncomplete = resolve; tx.onerror = reject;
            });
        }
        const entry = await SVOffline.save(file, {
            subtitles: [{url: '/api/subtitles/fetch?provider=opensubtitles&file_id=1', language: 'es', label: 'Español'}]
        });
        return {entry, count: (await SVOffline.list()).length};
    }''', existing)
    assert entry['count'] == 1
    entry = entry['entry']
    assert len(entry['subtitles']) == 1
    assert entry['subtitles'][0]['language'] == 'es'
    assert 'Saved provider caption' in entry['subtitles'][0]['text']
    assert 'file_id' not in str(entry) and 'opensubtitles' not in str(entry)


@pytest.mark.parametrize('series', [False, True], ids=['movie', 'episode'])
def test_available_provider_subtitles_are_downloaded_automatically_and_deleted_with_video(offline_page, series):
    page, context, origin = offline_page
    searches, transfers = [], []
    base = '/api/federation/peers/1/media/' + 'a' * 64 if series else '/api/downloads/files'
    page.route('**' + base + '/playback?*', lambda route: route.fulfill(json={
        'url': base + '/stream', 'mode': 'file'}))
    page.route('**' + base + '/stream', lambda route: (transfers.append(1), route.fulfill(body=b'video', content_type='video/mp4'))[-1])

    def search(route):
        params = parse_qs(urlsplit(route.request.url).query)
        searches.append(params)
        lang = params['language'][0]
        route.fulfill(json={'items': [
            {'language': lang, 'provider': 'Fixture', 'play_url': f'/api/subtitles/fetch?provider=remote&url=https%3A%2F%2Fsub.wyzie.ru%2F{lang}'}]})

    page.route('**/api/subtitles/search?*', search)
    page.route('**/api/subtitles/fetch?*', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nAutomatic saved caption\n', content_type='text/vtt'))
    file = dict(title='Automatic captions', overview='Known film', tmdb_id=7,
                relative_path='Fixture.mp4', audio_tracks=[], subtitles=[])
    if series:
        file.update(playback_url=base + '/playback', media_type='tv', season_number=2, episode_number=3)
    entry = page.evaluate('file => SVOffline.save(file, {languages: ["es", "en"]})', file)
    assert {track['language'] for track in entry['subtitles']} == {'es', 'en'}
    assert {params['language'][0] for params in searches} == {'es', 'en'}
    assert all(params['tmdb_id'] == ['7'] and params['media_type'] == ['tv' if series else 'movie'] for params in searches)
    if series:
        assert all(params['season'] == ['2'] and params['episode'] == ['3'] for params in searches)
    assert 'wyzie' not in str(entry) and 'playback_url' not in entry
    assert entry['storage_bytes'] == 5 + sum(len(track['text'].encode()) for track in entry['subtitles'])
    context.set_offline(True)
    page.goto(origin + '/assets/offline.html')
    page.locator('.offline-cover').first.click()
    expect(page.locator('#offline-subtitle-select option')).to_have_count(3)
    page.locator('#offline-detail-delete').click()
    page.get_by_role('dialog').get_by_role('button', name='Delete', exact=True).click()
    expect(page.locator('#offline-empty')).to_be_visible()
    assert page.evaluate('SVOffline.list()') == []
    assert page.evaluate('''async () => {
        const db = await SVOffline.database();
        return await new Promise(resolve => {
            const request = db.transaction('chunks').objectStore('chunks').count();
            request.onsuccess = () => resolve(request.result);
        });
    }''') == 0
    assert transfers == [1]


def test_existing_offline_copy_can_download_missing_languages_without_video_redownload(offline_page):
    page, context, origin = offline_page
    transfers = []
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream', lambda route: (transfers.append(1), route.fulfill(body=b'video', content_type='video/mp4'))[-1])
    page.route('**/api/subtitles/search?*', lambda route: route.fulfill(json={'items': []}))
    entry = page.evaluate('''() => SVOffline.save({title: 'Missing captions', tmdb_id: 7, overview: 'Known film',
        relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []}, {languages: []})''')
    page.route('**/api/subtitles/search?*', lambda route: route.fulfill(json={'items': [
        {'language': 'spa', 'provider': 'Fixture', 'play_url': '/api/subtitles/fetch?provider=opensubtitles&file_id=2'},
        {'language': 'eng', 'provider': 'Fixture', 'play_url': '/api/subtitles/fetch?provider=opensubtitles&file_id=3'}]}))
    page.route('**/api/subtitles/fetch?*', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nRefreshed subtitle\n', content_type='text/vtt'))
    page.evaluate('id => SVOffline.updatePlayback(id, {progress_seconds: 12})', entry['id'])
    page.evaluate('() => SVOfflineUI.refresh()')
    page.locator('.offline-cover').click()
    page.locator('#offline-refresh-subtitles').click()
    panel = page.get_by_role('dialog')
    panel.get_by_label('Español', exact=True).check()
    panel.get_by_role('button', name='Download', exact=True).click()
    expect(panel).to_contain_text('2 subtitle tracks saved')
    updated = page.evaluate('(id) => SVOffline.list().then(items => items.find(item => item.id === id))', entry['id'])
    assert updated['id'] == entry['id'] and updated['size_bytes'] == entry['size_bytes']
    assert updated['playback']['progress_seconds'] == 12
    assert {track['language'] for track in updated['subtitles']} == {'es', 'en'}
    again = page.evaluate('id => SVOffline.refreshSubtitles(id, {languages: ["es", "en"]})', entry['id'])
    assert len(again['subtitles']) == 2
    assert again['storage_bytes'] == updated['storage_bytes']
    assert transfers == [1]
    context.set_offline(True)
    page.goto(origin + '/assets/offline.html')
    page.locator('.offline-cover').first.click()
    expect(page.locator('#offline-subtitle-select option')).to_have_count(3)


def test_automatic_subtitles_try_another_result_and_never_fetch_external_links(offline_page):
    page, _, _ = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream', lambda route: route.fulfill(body=b'video', content_type='video/mp4'))
    page.route('**/api/subtitles/search?*', lambda route: route.fulfill(json={'items': [
        {'language': 'es', 'play_url': 'https://untrusted.test/leak'},
        {'language': 'es', 'play_url': '/api/subtitles/fetch?file_id=1'},
        {'language': 'es', 'play_url': '/api/subtitles/fetch?file_id=2'},
        {'language': 'en', 'play_url': '/api/subtitles/fetch?file_id=3'}]}))
    external = []
    page.route('https://untrusted.test/**', lambda route: (external.append(route.request.url), route.abort())[-1])
    page.route('**/api/subtitles/fetch?file_id=1', lambda route: route.fulfill(status=502))
    page.route('**/api/subtitles/fetch?file_id=2', lambda route: route.fulfill(
        body='WEBVTT\n\n00:00.000 --> 00:02.000\nFallback subtitle\n', content_type='text/vtt'))
    page.route('**/api/subtitles/fetch?file_id=3', lambda route: route.fulfill(
        body='<html>Cloudflare verification</html>', content_type='text/html'))
    entry = page.evaluate('''() => SVOffline.save({title: 'Fallback', tmdb_id: 7, overview: 'Known film',
        relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []}, {languages: ['es', 'en']})''')
    assert entry['status'] == 'complete' and len(entry['subtitles']) == 1
    assert entry['subtitles'][0]['language'] == 'es'
    assert external == []


def test_slow_subtitle_provider_does_not_discard_captions_already_saved(offline_page):
    page, _, _ = offline_page
    result = page.evaluate('''async () => {
        const originalFetch = window.fetch, timeout = window.setTimeout;
        window.setTimeout = (callback, delay, ...args) => timeout(callback, delay === 30000 ? 100 : delay, ...args);
        window.fetch = async (url, options = {}) => {
            if (String(url).includes('/playback')) return new Response(JSON.stringify({mode:'file',url:'/api/downloads/files/stream'}));
            if (String(url).includes('/stream')) return new Response('video', {headers:{'Content-Type':'video/mp4'}});
            if (String(url).includes('fast.vtt')) return new Response('WEBVTT\\n\\n00:00.000 --> 00:02.000\\nFast caption\\n', {headers:{'Content-Type':'text/vtt'}});
            if (String(url).includes('slow.vtt')) return new Promise((resolve,reject) => {
                options.signal.addEventListener('abort', () => reject(new DOMException('Timed out','AbortError')), {once:true});
            });
            return originalFetch(url, options);
        };
        try {
            return await SVOffline.save({title:'Bounded captions',overview:'Known film',relative_path:'Fixture.mp4',audio_tracks:[],
                subtitles:[{url:'/api/downloads/files/subtitle?path=fast.vtt',language:'en',label:'English'},
                           {url:'/api/downloads/files/subtitle?path=slow.vtt',language:'es',label:'Español'}]}, {languages:[]});
        } finally { window.fetch = originalFetch; window.setTimeout = timeout; }
    }''')
    assert result['status'] == 'complete'
    assert len(result['subtitles']) == 1 and result['subtitles'][0]['language'] == 'en'


def test_device_download_audio_choice_and_offline_audio_variants(offline_page):
    page, context, origin = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    file = {'title': 'Two languages', 'overview': 'Audio variants', 'relative_path': 'Fixture.mp4',
            'audio_tracks': [{'stream_index': 1, 'language': 'eng'}, {'stream_index': 2, 'language': 'spa'}], 'subtitles': []}
    page.evaluate('file => SVOfflineUI.download(file)', file)
    page.get_by_role('dialog').get_by_label('Audio to save').select_option('2')
    page.get_by_role('dialog').get_by_role('button', name='Download', exact=True).click()
    expect(page.get_by_role('dialog')).to_contain_text('Download complete')
    page.get_by_role('dialog').get_by_role('button', name='Close player', exact=True).click()
    spanish = page.evaluate('() => SVOffline.list()')[0]
    assert spanish['audio_index'] == 2 and spanish['audio_label'] == 'spa'
    english = page.evaluate('file => SVOffline.save(file, {audio: 1})', file)
    context.set_offline(True)
    page.goto(origin + '/assets/offline.html')
    expect(page.locator('#offline-list .offline-item')).to_have_count(1)
    page.get_by_role('button', name='Details · Two languages').click()
    expect(page.locator('#audio-select option')).to_have_count(2)
    page.locator('#audio-select').select_option(english['id'])
    expect(page.locator('#offline-audio-label')).to_contain_text('ENG')
    page.locator('#audio-select').select_option(spanish['id'])
    expect(page.locator('#offline-audio-label')).to_contain_text('SPA')


def test_old_device_copies_can_refresh_artwork_without_redownloading_video(offline_page):
    page, _, _ = offline_page
    page.route('**/api/downloads/files/playback?*', lambda route: route.fulfill(json={
        'url': '/api/downloads/files/stream?path=Fixture.mp4', 'mode': 'file'}))
    page.route('**/api/downloads/files/stream?*', lambda route: route.fulfill(body=b'media', content_type='video/mp4'))
    result = page.evaluate('''async () => {
        const entry = await SVOffline.save({title: 'Legacy artwork', overview: 'Old synopsis',
            relative_path: 'Fixture.mp4', audio_tracks: [], subtitles: []});
        const nativeFetch = window.fetch;
        const calls = [];
        window.fetch = (url, options) => {
            calls.push(String(url));
            if (String(url).includes('/api/tmdb/search/movie')) return Promise.resolve(new Response(JSON.stringify([
                {id: 7, title: 'Legacy artwork', overview: 'Updated synopsis', poster_path: '/assets/icon.png'}
            ]), {headers: {'Content-Type': 'application/json'}}));
            return nativeFetch(url, options);
        };
        await SVOffline.refreshPresentation(entry.id);
        window.fetch = nativeFetch;
        const saved = (await SVOffline.list())[0];
        return {calls, id: saved.id, sameId: saved.id === entry.id, poster: saved.poster.size,
            overview: saved.overview, bytes: await (await fetch(SVOffline.mediaUrl(saved.id))).text()};
    }''')
    assert result['sameId'] and result['bytes'] == 'media'
    assert result['poster'] > 0 and result['overview'] == 'Updated synopsis'
    assert not any('/files/playback' in call or '/files/stream' in call for call in result['calls'])


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
            args=CHROMIUM_ARGS)

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
