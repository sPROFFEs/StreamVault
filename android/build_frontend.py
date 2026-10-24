#!/usr/bin/env python3
"""Compile public, canonical UI templates for Android without importing the server/database."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parent


def build(output=None):
    output = Path(output) if output is not None else PROJECT / 'app/build/generated/frontend/assets/web'
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'vault'
    public_types = {'.js', '.css', '.html', '.png', '.ico', '.svg', '.woff', '.woff2', '.ttf', '.json', '.webmanifest', '.md'}
    for path in (source / 'assets').rglob('*'):
        if (path.is_symlink() or path.name.startswith('.') or path.is_file() and
                (path.suffix.lower() not in public_types or path.name in {'credentials.json', 'secrets.json'})):
            raise ValueError('Not a public asset: ' + path.name)
    # Only the public static tree is copied: no checkout, database, env or user data.
    shutil.copytree(source / 'assets', output / 'assets', dirs_exist_ok=True)
    shutil.copytree(PROJECT / 'frontend', output / 'mobile', dirs_exist_ok=True)
    shutil.copyfile(source / 'assets/sw.js', output / 'sw.js')
    fingerprint = hashlib.sha256()
    for path in sorted((source / 'assets').rglob('*')):
        if path.is_file():
            fingerprint.update(path.relative_to(source).as_posix().encode())
            fingerprint.update(path.read_bytes())
    version = json.loads((source / 'core/version.json').read_text())
    stamp = f'{version["android_version_code"]}-{fingerprint.hexdigest()[:12]}'
    templates = Environment(loader=FileSystemLoader(source / 'core/templates'), autoescape=select_autoescape())
    pages = [('portal', '/portal', ''), ('browse-movie', '/browse', 'movie'), ('browse-tv', '/browse', 'tv'),
             ('browse-favorites', '/browse', 'favorites'), ('downloads', '/downloads', ''),
             ('media-movie', '/media/0', 'movie'), ('media-tv', '/media/0', 'tv'),
             ('iptv', '/iptv', ''), ('anime', '/anime', ''), ('youtube', '/youtube', ''), ('about', '/about', '')]
    (output / 'pages').mkdir(exist_ok=True)
    for lang in ('en', 'es'):
        for role in ('user', 'downloader', 'admin'):
            user = SimpleNamespace(username='SV_MOBILE_USERNAME', is_admin=role == 'admin', can_download=role != 'user')
            for name, path, media_type in pages:
                query = {'type': media_type} if media_type else {}
                if media_type == 'favorites':
                    query = {'view': 'favorites'}
                request = SimpleNamespace(cookies={'lang': lang}, state=SimpleNamespace(user=user),
                                          scope={'path': path}, query_params=query)
                if name == 'about' and role == 'user':
                    # The installed public About screen must also work when
                    # the backend/session is unavailable; it has no account context.
                    request.state.user = None
                template = 'media_detail' if name.startswith('media-') else 'browse' if name.startswith('browse-') else name
                html = templates.get_template(template + '.html').render(request=request, username=user.username,
                    is_admin=user.is_admin, tmdb_id='SV_MOBILE_TMDB_ID', media_type=media_type)
                html = html.replace('<html ', '<html data-client="android" ', 1)
                html = html.replace('href="/assets/offline.html"', 'href="/_streamvault/offline"')
                # Distinct APK asset URLs avoid an older service-worker cache overriding installed code.
                html = re.sub(r'(["\'])(/assets/[^"\'?#]+)(?:\?[^"\']*)?\1',
                              lambda match: f'{match[1]}{match[2]}?svapk={stamp}{match[1]}', html)
                html = html.replace('</head>', '<link rel="stylesheet" href="/_streamvault/mobile/mobile.css">'
                    '<script defer src="/_streamvault/mobile/runtime.js"></script></head>', 1)
                (output / f'pages/{lang}-{role}-{name}.html').write_text(html, encoding='utf-8')
    files = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(output.rglob('*')) if path.is_file() and path.name != 'manifest.json'}
    (output / 'manifest.json').write_text(json.dumps({'protocol': 1, 'version': version, 'files': files}, sort_keys=True), encoding='utf-8')
    print(f'Bundled {len(files)} public frontend files for Android')
    return output


if __name__ == '__main__':
    build()
