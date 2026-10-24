"""Installed clients expose public context, never cookies or administrator configuration."""
import asyncio
from unittest.mock import AsyncMock

import auth
from version import VERSION


def test_mobile_bootstrap_requires_auth_and_returns_minimal_context(regression_client):
    assert regression_client.get('/api/mobile/bootstrap').status_code == 401
    regression_client.cookies.set('access_token', auth.create_access_token({'sub': 'baseline-viewer'}))
    response = regression_client.get('/api/mobile/bootstrap')
    assert response.status_code == 200
    payload = response.json()
    assert payload == {'protocol': 1, 'username': 'baseline-viewer', 'is_admin': False,
                       'can_download': False, 'language': 'en', 'server_version': VERSION}
    assert 'no-store' in response.headers['cache-control']


def test_about_is_available_to_regular_users_without_admin_secrets(regression_client, monkeypatch):
    from routers import client
    assert regression_client.get('/api/about').status_code == 401
    regression_client.cookies.set('access_token', auth.create_access_token({'sub': 'baseline-viewer'}))
    monkeypatch.setattr(client, 'latest_release', AsyncMock(return_value={'state': 'current', 'version': VERSION, 'android': None}))
    response = regression_client.get('/api/about')
    assert response.status_code == 200
    assert response.json()['server_version'] == VERSION
    assert response.json()['repository'] == 'https://github.com/sPROFFEs/StreamVault'
    assert set(response.json()) == {'server_version', 'repository', 'release'}
    assert 'no-store' in response.headers['cache-control']
    assert regression_client.get('/about').status_code == 200


def test_public_apk_release_validation_rejects_unsafe_or_unverifiable_downloads():
    from services.client_release import parse_release
    apk = {'name': 'streamvault-1.0.2-android.apk', 'size': 1234,
           'browser_download_url': 'https://github.com/sPROFFEs/StreamVault/releases/download/v1.0.2/streamvault-1.0.2-android.apk'}
    release = {'tag_name': 'v1.0.2', 'draft': False, 'prerelease': False, 'body': 'New Android client', 'assets': [apk]}
    manifest = {'schema_version': 1, 'version': '1.0.2', 'android_version_code': 5,
                'repository': 'sPROFFEs/StreamVault', 'tag': 'v1.0.2',
                'assets': [{'name': apk['name'], 'size': 1234, 'sha256': 'a' * 64}]}
    result = parse_release(release, manifest)
    assert result['android'] == dict(version='1.0.2', version_code=5, url=apk['browser_download_url'], sha256='a' * 64, size=1234)
    for changed in [{**apk, 'browser_download_url': 'https://untrusted.test/app.apk'},
                    {**apk, 'browser_download_url': apk['browser_download_url'] + '?token=private'},
                    {**apk, 'size': 999}]:
        assert parse_release({**release, 'assets': [changed]}, manifest)['android'] is None
    assert parse_release(release, {**manifest, 'android_version_code': True})['android'] is None
    assert parse_release(release, {**manifest, 'repository': 'someone/else'})['android'] is None
    assert parse_release(release, {**manifest, 'version': '1.0.1'})['android'] is None


def test_apk_checks_cache_failures_and_do_not_retry_on_every_page(monkeypatch):
    from services import client_release
    monkeypatch.setattr(client_release, '_cached', None)
    monkeypatch.setattr(client_release, '_checked_at', 0)
    fetch = AsyncMock(side_effect=RuntimeError('Provider unavailable'))
    monkeypatch.setattr(client_release, '_fetch_release', fetch)
    async def check():
        first = await client_release.latest_release()
        second = await client_release.latest_release()
        assert first['state'] == second['state'] == 'unavailable'
    asyncio.run(check())
    assert fetch.await_count == 1
