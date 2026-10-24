"""Run the actual APK's compiled frontend in Chromium without a remote HTML dependency."""
import importlib.util
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.browser
def test_chromium_delivers_animation_frames_for_real_clicks(regression_browser):
    page = regression_browser.new_page()
    page.set_content('<button onclick="this.textContent=\'Clicked\'">Ready</button>')
    page.get_by_role('button', name='Ready').click(timeout=5000)
    expect(page.get_by_role('button', name='Clicked')).to_be_visible()
    page.close()


@pytest.fixture
def installed_frontend(tmp_path):
    spec = importlib.util.spec_from_file_location('mobile_build', ROOT / 'android/build_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / 'installed'
    module.build(output)
    return output


def test_packaged_frontend_contains_canonical_screens_and_no_server_state(installed_frontend):
    output = installed_frontend
    assert (output / 'assets/offline.html').exists()
    assert (output / 'assets/js/offline-store.js').exists()
    assert (output / 'sw.js').exists()
    assert len(list((output / 'pages').glob('*.html'))) == 66
    media = (output / 'pages/es-admin-media-tv.html').read_text()
    assert 'SV_MOBILE_TMDB_ID' in media and 'SV_MOBILE_USERNAME' in media
    assert 'Descargar' in media and 'device-download-button' in media
    assert 'data-client="android"' in media
    assert 'svapk=' in media
    assert not list(output.rglob('*.db')) and not list(output.rglob('.env*'))


@pytest.mark.parametrize('symlink', [False, True], ids=['private-file', 'symlink'])
def test_frontend_compiler_rejects_private_files_and_symlinks(tmp_path, symlink):
    spec = importlib.util.spec_from_file_location('mobile_build', ROOT / 'android/build_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / 'source/vault/assets'
    source.mkdir(parents=True)
    if symlink:
        (source / 'secret.png').symlink_to(ROOT / 'vault/core/version.json')
    else:
        (source / '.env').write_text('test-private-data')
    module.ROOT = tmp_path / 'source'
    with pytest.raises(ValueError, match='public asset'):
        module.build(tmp_path / 'output')


@pytest.mark.browser
def test_installed_detail_bootstraps_identity_then_loads_sources(regression_browser, regression_server, installed_frontend, browser_viewport):
    page = regression_browser.new_page(viewport=browser_viewport)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def respond(route):
        path = urlsplit(route.request.url).path
        if path == '/media/7':
            return route.fulfill(path=installed_frontend / 'mobile/boot.html')
        if path.startswith('/_streamvault/'):
            target = installed_frontend / path.removeprefix('/_streamvault/')
            return route.fulfill(path=target) if target.is_file() else route.fulfill(status=404)
        if path.startswith('/assets/'):
            return route.fulfill(path=installed_frontend / path.removeprefix('/'))
        if path == '/api/mobile/bootstrap':
            return route.fulfill(json={'protocol': 1, 'username': 'test-viewer', 'is_admin': False,
                                      'can_download': True, 'language': 'es', 'server_version': '1.0.2'})
        if path == '/api/tmdb/movie/7':
            return route.fulfill(json={'id': 7, 'title': 'Installed movie', 'genres': []})
        if path == '/api/stream/resolve/7':
            return route.fulfill(json={'streams': [{'type': 'embed', 'name': 'Fixture source', 'url': 'https://example.test/player'}]})
        return route.fulfill(json={})
    page.route('**/*', respond)
    page.goto(regression_server + '/media/7?type=movie')
    expect(page.locator('#detail-title')).to_have_text('Installed movie')
    expect(page.locator('#source-select option')).to_have_count(1)
    expect(page.locator('#download-button')).to_be_visible()
    assert page.evaluate('window.MEDIA_TMDB_ID') == '7'
    assert page.evaluate('document.documentElement.lang') == 'es'
    assert not errors, errors
    page.close()


@pytest.mark.browser
@pytest.mark.parametrize('status', [401, 403, 503])
def test_installed_boot_does_not_bypass_access_or_auth(regression_browser, regression_server, installed_frontend, status):
    page = regression_browser.new_page()
    def respond(route):
        path = urlsplit(route.request.url).path
        if path == '/portal':
            return route.fulfill(path=installed_frontend / 'mobile/boot.html')
        if path == '/api/mobile/bootstrap':
            return route.fulfill(status=status, json={'detail': 'Unavailable'})
        if path == '/login':
            return route.fulfill(body='<h1>Server authentication / Cloudflare</h1>', content_type='text/html')
        if path.startswith('/_streamvault/'):
            return route.fulfill(path=installed_frontend / path.removeprefix('/_streamvault/'))
        return route.fulfill(status=404)
    page.route('**/*', respond)
    page.goto(regression_server + '/portal')
    if status in (401, 403):
        expect(page).to_have_url(re.compile('/login\\?next='))
        expect(page.locator('h1')).to_contain_text('authentication')
    else:
        expect(page.locator('#mobile-error')).to_be_visible()
        expect(page.locator('a[href="/_streamvault/offline"]')).to_be_visible()
    page.close()


@pytest.mark.browser
@pytest.mark.parametrize('native', [False, True], ids=['web-download', 'android-install'])
def test_about_shows_official_updates_without_executing_release_notes(regression_browser, regression_server, installed_frontend, native):
    page = regression_browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    if native:
        page.add_init_script('''window.SV_ANDROID={version:'1.0.2',version_code:5};window.nativeMessages=[];
            window.StreamVaultAndroid={postMessage: message => nativeMessages.push(JSON.parse(message))};''')
    release = {'state': 'available', 'version': '1.0.3', 'url': 'https://github.com/sPROFFEs/StreamVault/releases/tag/v1.0.3',
               'notes': '<script>window.injection=true</script> Release notes',
               'android': {'version': '1.0.3', 'version_code': 6,
                           'url': 'https://github.com/sPROFFEs/StreamVault/releases/download/v1.0.3/streamvault-1.0.3-android.apk'}}
    def respond(route):
        path = urlsplit(route.request.url).path
        if path == '/about':
            return route.fulfill(path=installed_frontend / 'pages/en-user-about.html')
        if path.startswith('/assets/'):
            return route.fulfill(path=installed_frontend / path.removeprefix('/'))
        if path.startswith('/_streamvault/'):
            return route.fulfill(path=installed_frontend / path.removeprefix('/_streamvault/'))
        if path == '/api/about':
            return route.fulfill(json={'server_version': '1.0.2', 'release': release})
        return route.fulfill(json={})
    page.route('**/*', respond)
    page.goto(regression_server + '/about')
    expect(page.locator('#about-release-title')).to_have_text('StreamVault 1.0.3')
    expect(page.locator('#about-notes')).to_contain_text('<script>')
    assert not page.evaluate('!!window.injection')
    if native:
        expect(page.locator('#about-client')).to_contain_text('1.0.2 (5)')
        expect(page.locator('#about-apk')).to_be_hidden()
        page.locator('#about-install').click()
        assert {'type': 'install-update'} in page.evaluate('nativeMessages')
    else:
        expect(page.locator('#about-install')).to_be_hidden()
        expect(page.locator('#about-apk')).to_have_attribute('href', release['android']['url'])
    page.close()
    assert not errors, errors
