"""Artwork URLs and recovery work without unsafe HTML or remote dependencies."""
import json
from pathlib import Path
import subprocess


def test_portal_loads_artwork_before_the_home_controller():
    from shared_templates import templates
    from starlette.requests import Request
    request = Request({'type': 'http', 'headers': [], 'state': {}})
    html = templates.get_template('portal.html').render(request=request)
    assert html.index('src="/assets/js/components/artwork.js') < html.index('src="/assets/js/portal.js')


def test_artwork_urls_and_failed_image_recovery():
    script = Path(__file__).resolve().parents[2] / 'assets/js/components/artwork.js'
    probe = '''
        global.window = {};
        eval(SOURCE);
        const art = window.SVArtwork;
        const placeholder = {hidden: false};
        const image = {removeAttribute() {delete this.src;}};
        art.setImage(image, '/poster.jpg', {placeholder, alt: '<Title>'});
        const first = [image.src, image.hidden, placeholder.hidden, image.alt];
        image.onerror();
        const failed = [image.hidden, placeholder.hidden];
        art.setImage(image, 'https://images.example/next.jpg', {placeholder});
        const next = [image.src, image.hidden, placeholder.hidden];
        art.setImage(image, '', {placeholder});
        const empty = [image.src || '', image.hidden, placeholder.hidden];
        console.log(JSON.stringify({first, failed, next, empty,
            urls: [null, '/poster.jpg', 'https://images.example/a.jpg', '//images.example/a.jpg',
                '/assets/poster.jpg', 'javascript:alert(1)'].map(path => art.url(path, 'w342'))}));
    '''.replace('SOURCE', json.dumps(script.read_text()))
    result = subprocess.run(['node', '-e', probe], check=True, capture_output=True, text=True)
    actual = json.loads(result.stdout)
    assert actual['urls'] == ['', 'https://image.tmdb.org/t/p/w342/poster.jpg',
                              'https://images.example/a.jpg', 'https://images.example/a.jpg', '/assets/poster.jpg', '']
    assert actual['first'] == ['https://image.tmdb.org/t/p/w500/poster.jpg', False, True, '<Title>']
    assert actual['failed'] == [True, False]
    assert actual['next'] == ['https://images.example/next.jpg', False, True]
    assert actual['empty'] == ['', True, False]
