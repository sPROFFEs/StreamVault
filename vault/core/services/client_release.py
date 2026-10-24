"""Public Android release discovery, independent of private deployment update settings."""
import asyncio
import json
import re
import time

import httpx

from version import VERSION

REPOSITORY = 'sPROFFEs/StreamVault'
REPOSITORY_URL = f'https://github.com/{REPOSITORY}'
_cached = None
_checked_at = 0
_lock = asyncio.Lock()


def parse_release(release, manifest):
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('No stable public release')
    version = tag[1:]
    result = {'state': 'available' if tuple(map(int, version.split('.'))) > tuple(map(int, VERSION.split('.'))) else 'current',
              'version': version, 'url': f'{REPOSITORY_URL}/releases/tag/{tag}',
              'notes': str(release.get('body') or '')[:12000], 'android': None}
    code = manifest.get('android_version_code')
    if (manifest.get('schema_version') != 1 or manifest.get('repository') != REPOSITORY
            or manifest.get('version') != version or manifest.get('tag') != tag
            or type(code) is not int or not 1 <= code <= 2100000000):
        return result
    name = f'streamvault-{version}-android.apk'
    url = f'{REPOSITORY_URL}/releases/download/{tag}/{name}'
    for asset in release.get('assets', []):
        if asset.get('name') != name or asset.get('browser_download_url') != url:
            continue
        for item in manifest.get('assets', []):
            size = item.get('size')
            if (item.get('name') == name and type(size) is int and 0 < size <= 500 * 1024 ** 2
                    and asset.get('size') == size and re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256', '')))):
                result['android'] = {'version': version, 'version_code': code, 'url': url,
                                     'sha256': item['sha256'], 'size': size}
    return result


async def _json(client, url):
    async with client.stream('GET', url) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > 512 * 1024:
                raise ValueError('Release metadata is too large')
        return json.loads(data)


async def _fetch_release():
    async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                 headers={'User-Agent': 'StreamVault-release-check'}) as client:
        release = await _json(client, f'https://api.github.com/repos/{REPOSITORY}/releases/latest')
        tag = release.get('tag_name', '')
        if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
            raise ValueError('Invalid release tag')
        manifest = await _json(client, f'{REPOSITORY_URL}/releases/download/{tag}/release.json')
    return parse_release(release, manifest)


async def latest_release():
    global _cached, _checked_at
    async with _lock:
        if _cached is not None and time.monotonic() - _checked_at < 300:
            return _cached
        try:
            _cached = await _fetch_release()
        except Exception:
            _cached = {'state': 'unavailable', 'android': None}
        _checked_at = time.monotonic()
        return _cached
