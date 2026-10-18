"""Route interactions exercised against real templates and offline API responses."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from starlette.requests import Request
from shared_templates import templates

pytestmark = pytest.mark.browser
ASSETS = Path(__file__).resolve().parents[2] / 'assets'


@pytest.fixture
def discovery_page(regression_browser, browser_viewport):
    page = regression_browser.new_page(viewport=browser_viewport)
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    movie = {'id': 7, 'title': 'Example', 'media_type': 'movie', 'poster_path': None}
    payloads = {
        '/api/preferences': {}, '/api/library/favorites': {'items': []},
        '/api/library/history': {'items': []}, '/api/library/progress': {},
        '/api/tmdb/genres/movie': [{'id': 18, 'name': 'Drama'}],
        '/api/tmdb/genres/tv': [], '/api/tmdb/discover/movie': [movie],
        '/api/tmdb/discover/tv': [], '/api/tmdb/search/movie': [],
        '/api/tmdb/movie/7': movie, '/api/tmdb/tv/7': {**movie, 'seasons': [{'season_number': 1}]},
        '/api/tmdb/tv/7/season/1': {'episodes': [{'episode_number': 1, 'name': 'Pilot'}]},
        '/api/downloads/library': {'movies': [], 'series': []},
        '/api/streams/7': {'streams': []}, '/api/local/browse': {'items': []},
    }
    def respond(route):
        url = urlsplit(route.request.url)
        calls.append((url.path, url.query))
        if url.path.startswith('/assets/'):
            asset = ASSETS / url.path.removeprefix('/assets/')
            return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)
        if url.path in {'/browse', '/portal', '/catalog', '/media/7'}:
            name = {'/browse': 'browse.html', '/portal': 'portal.html', '/catalog': 'catalog.html', '/media/7': 'media_detail.html'}[url.path]
            user = SimpleNamespace(is_admin=True, can_download=True)
            request = Request({'type': 'http', 'headers': [(b'cookie', b'lang=en')], 'state': {'user': user}, 'query_string': url.query.encode()})
            html = templates.get_template(name).render(request=request, tmdb_id=7, media_type=parse_qs(url.query).get('type', ['movie'])[0])
            return route.fulfill(content_type='text/html', body=html)
        payload = payloads.get((url.path, parse_qs(url.query).get("page", ["1"])[0]), payloads.get(url.path, {}))
        status, data = payload if isinstance(payload, tuple) else (200, payload)
        return route.fulfill(status=status, json=data)
    page.route('**/*', respond)
    yield SimpleNamespace(page=page, calls=calls, payloads=payloads, movie=movie)
    page.close()
    assert not errors, errors


def test_favorites_search_filter_empty_and_retry(discovery_page):
    ui = discovery_page
    ui.payloads['/api/library/favorites'] = {'items': [
        {'tmdb_id': 7, 'title': 'Example', 'media_type': 'movie', 'rating': 5},
        {'tmdb_id': 9, 'title': 'Other', 'media_type': 'movie', 'rating': 9}]}
    page = ui.page
    page.goto('https://streamvault.test/browse?view=favorites')
    expect(page.locator('#browse-grid .poster-card')).to_have_count(2)
    page.locator('#browse-search').fill('Example')
    expect(page.locator('#browse-grid .poster-card')).to_have_count(1)
    expect(page.locator('#view-filter')).to_have_value('favorites')
    page.locator('#sort-filter').select_option('vote_average.desc')
    expect(page.locator('#browse-grid')).to_contain_text('Example')
    page.locator('#browse-search').fill('Nothing')
    expect(page.locator('#browse-empty')).to_be_visible()
    page.locator('#reset-filters').click()
    expect(page.locator('#browse-grid .poster-card')).to_have_count(2)
    assert sum(path == '/api/library/favorites' for path, _ in ui.calls) == 1
    ui.payloads['/api/library/favorites'] = (503, {'detail': 'offline'})
    page.locator('#refresh-button').click()
    expect(page.locator('#browse-error')).to_be_visible()
    expect(page.locator('#browse-empty')).not_to_be_visible()
    ui.payloads['/api/library/favorites'] = {'items': []}
    page.locator('#retry-catalog').click()
    expect(page.locator('#browse-error')).not_to_be_visible()
    expect(page.locator('#browse-empty')).to_be_visible()
    expect(page.locator('#browse-search')).to_be_focused()


def test_home_requests_preferences_once(discovery_page):
    ui = discovery_page
    ui.payloads['/api/tmdb/trending/movie'] = [ui.movie]
    ui.page.goto('https://streamvault.test/portal')
    expect(ui.page.locator('#hero h1')).to_have_text('Example')
    assert ui.page.locator('script[src*="appearance.js"]').count() == 1
    assert sum(path == '/api/preferences' for path, _ in ui.calls) == 1


def test_detail_defers_decoder_and_reuses_preferences(discovery_page):
    ui = discovery_page
    page = ui.page
    page.goto('https://streamvault.test/media/7?type=movie')
    expect(page.locator('#detail-title')).to_have_text('Example')
    page.wait_for_function("document.querySelector('#source-select').options.length > 0")
    assert sum(path == '/api/preferences' for path, _ in ui.calls) == 1
    assert not any('hls.min.js' in path or 'webtorrent.min.js' in path for path, _ in ui.calls)
    loaded = []
    def decoder(route):
        loaded.append(route.request.url)
        route.fulfill(content_type='application/javascript', body='''
          window.hlsLoads = 0;
          window.Hls = class {
            static isSupported() {return true;}
            static Events = {}; static ErrorTypes = {};
            on() {} attachMedia() {} destroy() {}
            loadSource() {window.hlsLoads++;}
          };
        ''')
    page.route('**/hls.min.js', decoder)
    for _ in range(2):
        page.evaluate("loadSource({type: 'direct', url: '/fixture.m3u8'})")
    assert len(loaded) == 1
    assert page.evaluate('hlsLoads') == 2


def test_closed_player_ignores_a_late_decoder(discovery_page):
    page = discovery_page.page
    page.goto('https://streamvault.test/media/7?type=movie')
    expect(page.locator('#detail-title')).to_have_text('Example')
    pending = []
    page.route('**/hls.min.js', lambda route: pending.append(route))
    page.evaluate("window.opening = loadSource({type: 'direct', url: '/fixture.m3u8'}); undefined")
    page.wait_for_function("!!document.querySelector('script[data-playback-library=Hls]')")
    page.evaluate('destroyPlayer()')
    assert len(pending) == 1
    pending[0].fulfill(content_type='application/javascript', body='window.Hls = class {constructor() {window.unexpectedDecoder = true;} static isSupported() {return true;}}')
    page.evaluate('opening')
    assert page.evaluate('!window.unexpectedDecoder && !document.querySelector("video")')


def test_native_hls_does_not_fetch_a_decoder(discovery_page):
    ui = discovery_page
    ui.page.goto('https://streamvault.test/media/7?type=movie')
    expect(ui.page.locator('#detail-title')).to_have_text('Example')
    ui.page.evaluate("HTMLMediaElement.prototype.canPlayType = () => 'probably'; HTMLMediaElement.prototype.play = async () => {}")
    ui.page.evaluate("loadSource({type: 'direct', url: '/fixture.m3u8'})")
    expect(ui.page.locator('video')).to_have_attribute('src', '/fixture.m3u8')
    assert not any('hls.min.js' in path for path, _ in ui.calls)


def test_decoder_failure_is_retryable(discovery_page):
    ui = discovery_page
    ui.page.goto('https://streamvault.test/media/7?type=movie')
    expect(ui.page.locator('#detail-title')).to_have_text('Example')
    ui.page.evaluate("HTMLMediaElement.prototype.canPlayType = () => ''")
    ui.page.route('**/hls.min.js', lambda route: route.fulfill(status=503))
    ui.page.evaluate("loadSource({type: 'direct', url: '/fixture.m3u8'})")
    assert ui.page.locator('video').count() == 0
    ui.page.route('**/hls.min.js', lambda route: route.fulfill(content_type='application/javascript', body='''
        window.Hls = class {static isSupported() {return true;} static Events = {};
          on() {} attachMedia() {} destroy() {} loadSource() {window.retriedDecoder = true;}};
    '''))
    ui.page.evaluate("loadSource({type: 'direct', url: '/fixture.m3u8'})")
    assert ui.page.evaluate('retriedDecoder')


@pytest.mark.parametrize('missing', ['Notification', 'EventSource', 'notification-permission'])
def test_detail_loads_without_optional_webview_notification_apis(discovery_page, missing):
    ui, page = discovery_page, discovery_page.page
    if missing == 'notification-permission':
        page.add_init_script("Notification.requestPermission = () => { throw new Error('Unavailable in WebView'); };")
    else:
        page.add_init_script(f'delete window.{missing};')
    ui.payloads['/api/stream/resolve/7'] = [{'source': 'Local fixture', 'type': 'direct', 'url': '/fixture.mp4'}]
    page.goto('https://streamvault.test/media/7?type=movie')
    expect(page.locator('#detail-title')).to_have_text('Example')
    expect(page.locator('#source-select')).to_contain_text('Local fixture')
    expect(page.locator('#detail-error')).to_be_hidden()


def test_large_catalog_return_focus_and_dpad(discovery_page):
    ui = discovery_page
    ui.payloads['/api/tmdb/discover/movie'] = [
        {**ui.movie, 'id': i, 'title': ('LongTitle' * 35) if i == 7 else f'Movie {i}'} for i in range(1, 241)]
    ui.payloads[('/api/tmdb/discover/movie', '2')] = [{**ui.movie, 'id': i, 'title': f'Movie {i}'} for i in range(241, 261)]
    page = ui.page
    page.goto('https://streamvault.test/browse?type=movie&genre=18')
    expect(page.locator('#browse-grid .poster-card')).to_have_count(240)
    expect(page.locator('.media-card__placeholder').first).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('#type-filter').bounding_box()['height'] >= 44
    cards = page.locator('#browse-grid .poster-card')
    cards.first.focus()
    page.keyboard.press('ArrowRight')
    expect(cards.nth(1)).to_be_focused()
    page.locator('#load-more-button').click()
    expect(page.locator('#browse-grid .poster-card')).to_have_count(260)
    expect(page.locator('#browse-grid')).to_have_attribute('aria-busy', 'false')
    target = page.locator('#browse-grid a[data-id="7"]')
    target.focus()
    saved_scroll = page.evaluate('scrollY')
    target.press('Enter')
    expect(page.locator('#detail-title')).to_have_text('Example')
    expect(page.locator('#detail-back')).to_have_attribute('href', '/browse?type=movie&genre=18')
    page.locator('#detail-back').click()
    expect(page.locator('#browse-grid a[data-id="7"]')).to_be_focused()
    assert sum(path == '/api/tmdb/discover/movie' for path, _ in ui.calls) == 2
    assert abs(page.evaluate('scrollY') - saved_scroll) <= 1
    page.locator('#browse-grid a[data-id="7"]').press('Enter')
    page.go_back()
    expect(page.locator('#browse-grid a[data-id="7"]')).to_be_focused()
    assert sum(path == '/api/tmdb/discover/movie' for path, _ in ui.calls) == 2
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_detail_error_retry_and_metadata_request_count(discovery_page):
    ui = discovery_page
    page = ui.page
    ui.payloads['/api/tmdb/tv/7'] = (503, {})
    page.goto('https://streamvault.test/media/7?type=tv')
    expect(page.locator('#detail-error')).to_be_visible()
    ui.payloads['/api/tmdb/tv/7'] = {**ui.movie, 'title': 'LongTitle' * 35, 'seasons': [{'season_number': 1}]}
    page.locator('#retry-detail').click()
    expect(page.locator('#detail-title')).to_have_text('LongTitle' * 35)
    expect(page.locator('#season-select')).to_have_value('1')
    expect(page.locator('#favorite-button')).to_be_focused()
    assert sum(path == '/api/tmdb/tv/7' for path, _ in ui.calls) == 2
    expect(page.locator('#detail-poster')).to_contain_text('No artwork')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_long_playback_labels_stay_inside_their_fields(discovery_page):
    ui, page = discovery_page, discovery_page.page
    episode_name = "LetYouDown_" * 30
    source_name = "Torrentio_CompleteSeason_1080p_" * 20
    ui.payloads['/api/tmdb/tv/7/season/1'] = {'episodes': [{'episode_number': 1, 'name': episode_name}]}
    ui.payloads['/api/stream/resolve/7'] = [{'source': source_name, 'type': 'direct', 'url': '/fixture.mp4'}]
    page.goto('https://streamvault.test/media/7?type=tv')
    expect(page.locator('#episode-select')).to_contain_text(episode_name)
    expect(page.locator('#source-select')).to_contain_text(source_name)
    for selector in ['#season-select', '#episode-select', '#source-select']:
        assert page.locator(selector).evaluate('''select => {
            const box = select.getBoundingClientRect();
            const field = select.closest('.field').getBoundingClientRect();
            const panel = select.closest('.media-info-col').getBoundingClientRect();
            return box.width > 0 && box.left >= field.left - 1 && box.right <= field.right + 1
                && box.left >= panel.left && box.right <= panel.right;
        }'''), f'{selector} overflows its field'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_local_library_combines_search_and_type_and_restores_modal_focus(discovery_page):
    ui = discovery_page
    ui.payloads['/api/local/browse'] = {'items': [
        {'id': 1, 'title': '<Example>', 'type': 'movie'},
        {'id': 2, 'title': 'Other', 'type': 'series'}]}
    page = ui.page
    page.goto('https://streamvault.test/catalog')
    page.locator('#catalog-search').fill('Example')
    expect(page.locator('#media-grid .catalog-card')).to_have_count(1)
    page.locator('#btn-tv').click()
    expect(page.locator('#no-results')).to_contain_text('No matches')
    page.locator('#btn-all').click()
    expect(page.locator('#media-grid .catalog-card')).to_have_count(1)
    card = page.locator('#media-grid .catalog-card')
    expect(card).to_contain_text('<Example>')
    card.click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(card).to_be_focused()


@pytest.mark.parametrize("media_type,expected", [("movie", "movie"), ("series", "tv")])
def test_local_library_play_opens_shared_detail_and_keeps_back_history(discovery_page, media_type, expected):
    ui = discovery_page
    ui.payloads['/api/local/browse'] = {'items': [
        {'id': 1, 'tmdb_id': 7, 'title': 'Example', 'type': media_type}]}
    page = ui.page
    page.goto('https://streamvault.test/catalog')
    page.locator('#media-grid .catalog-card').click()
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button', name='Play', exact=True).click()
    expect(page).to_have_url(f'https://streamvault.test/media/7?type={expected}')
    page.go_back()
    expect(page).to_have_url('https://streamvault.test/catalog')
    expect(page.get_by_role('dialog')).to_be_hidden()


def test_local_library_loads_complete_shared_navigation_styles(discovery_page, browser_viewport):
    page = discovery_page.page
    page.goto('https://streamvault.test/catalog')
    navigation = page.locator('.mobile-tabbar')
    if browser_viewport['width'] > 640:
        expect(navigation).to_be_hidden()
    else:
        expect(navigation).to_have_css('position', 'fixed')
        assert navigation.locator('svg').first.bounding_box()['width'] <= 24


def test_home_feedback_preferences_and_return_focus(discovery_page):
    ui = discovery_page
    page = ui.page
    page.add_init_script('window.SVPrefs = {home_visibility: {continue: false, similar: false, top_rated: false}};')
    ui.payloads['/api/preferences'] = {'home_visibility': {'continue': False, 'similar': False, 'top_rated': False}}
    ui.payloads['/api/tmdb/trending/movie'] = (503, {})
    ui.payloads['/api/tmdb/trending/tv'] = (503, {})
    page.goto('https://streamvault.test/portal')
    expect(page.locator('#home-feedback')).to_contain_text('Could not load')
    ui.payloads['/api/tmdb/trending/movie'] = [{**ui.movie, 'title': 'LongTitle' * 35}]
    page.locator('#retry-home').click()
    expect(page.locator('#trending-movies-row .poster-card')).to_have_count(1)
    assert page.evaluate("document.querySelector('.hero-title').getBoundingClientRect().top >= document.querySelector('#hero').getBoundingClientRect().top")
    assert page.evaluate("document.querySelector('.hero-actions').getBoundingClientRect().bottom <= document.querySelector('#hero').getBoundingClientRect().bottom")
    assert not any(path == '/api/library/history' or 'vote_average.desc' in query for path, query in ui.calls)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    target = page.locator('#trending-movies-row .poster-card')
    target.focus()
    target.press('Enter')
    expect(page.locator('#detail-back')).to_have_attribute('href', '/portal')
    page.locator('#detail-back').click()
    expect(page.locator('#trending-movies-row .poster-card')).to_be_focused()


def test_search_empty_error_and_expired_response(discovery_page):
    ui = discovery_page
    page = ui.page
    pending = []
    def search(route):
        query = parse_qs(urlsplit(route.request.url).query)['q'][0]
        if query == 'slow':
            pending.append(route)
        elif query == 'offline':
            route.fulfill(status=503, json={})
        else:
            route.fulfill(json=[] if query == 'missing' else [{**ui.movie, 'title': query}])
    page.route('**/api/tmdb/search/movie?*', search)
    page.goto('https://streamvault.test/browse')
    expect(page.locator('#browse-grid .poster-card')).to_have_count(1)
    page.locator('#browse-search').fill('missing')
    expect(page.locator('#browse-empty')).to_be_visible()
    page.locator('#browse-search').fill('offline')
    expect(page.locator('#browse-error')).to_be_visible()
    expect(page.locator('#browse-empty')).not_to_be_visible()
    with page.expect_request('**/api/tmdb/search/movie?*q=slow*'):
        page.locator('#browse-search').fill('slow')
    page.locator('#browse-search').fill('current')
    page.locator('#browse-search').press('Tab')
    expect(page.locator('#browse-grid')).to_contain_text('current')
    pending[0].fulfill(json=[{**ui.movie, 'title': 'Expired response'}])
    expect(page.locator('#browse-grid')).not_to_contain_text('Expired response')
    assert 'q=current' in page.url
    assert not any(path == '/api/library/favorites' for path, _ in ui.calls)


def test_downloaded_filter_reuses_library_and_metadata(discovery_page):
    ui = discovery_page
    ui.payloads['/api/downloads/library'] = {'movies': [{'title': 'Example', 'year': 2024, 'relative_path': 'Example.mp4'}], 'series': []}
    ui.payloads['/api/tmdb/search/movie'] = [ui.movie]
    page = ui.page
    page.goto('https://streamvault.test/browse?view=downloads')
    expect(page.locator('#browse-grid')).to_contain_text('Example')
    page.locator('#browse-search').fill('Example')
    expect(page.locator('#results-count')).to_have_text('1 results')
    page.locator('#sort-filter').select_option('primary_release_date.desc')
    expect(page.locator('#browse-grid')).to_contain_text('Example')
    assert sum(path == '/api/downloads/library' for path, _ in ui.calls) == 1
    assert sum(path == '/api/tmdb/search/movie' for path, _ in ui.calls) == 1


def test_downloaded_filter_includes_federated_movies_and_series_on_later_pages(discovery_page):
    ui = discovery_page
    page = ui.page
    calls = []
    local_movie = {'title': 'Local movie', 'tmdb_id': 7, 'relative_path': 'Local.mp4'}
    local_episode = {'title': 'Local show', 'tmdb_id': 8, 'relative_path': 'Local.S01E01.mkv'}
    remote_movie = {'title': 'Remote movie', 'tmdb_id': 9, 'origin': 'federation',
                    'relative_path': 'federation:1:movie'}
    remote_episodes = [{'title': 'Remote show', 'tmdb_id': 10, 'origin': 'federation',
                        'relative_path': f'federation:1:episode{episode}',
                        'season_number': 1, 'episode_number': episode} for episode in (1, 2)]

    def library(route):
        params = parse_qs(urlsplit(route.request.url).query)
        number = int(params.get('page', ['1'])[0])
        calls.append(params)
        if params.get('origin') != ['all']:
            return route.fulfill(json={'movies': [local_movie], 'series': [local_episode], 'pages': 1})
        route.fulfill(json={'page': number, 'pages': 2,
                            'movies': [local_movie] if number == 1 else [remote_movie],
                            'series': [local_episode] if number == 1 else remote_episodes})

    page.route('**/api/downloads/library*', library)
    page.goto('https://streamvault.test/browse?view=downloads')
    cards = page.locator('#browse-grid .poster-card')
    expect(cards).to_have_count(2)
    expect(cards.filter(has_text='Local movie')).to_have_attribute('href', '/media/7?type=movie')
    expect(cards.filter(has_text='Remote movie')).to_have_attribute('href', '/media/9?type=movie')
    page.locator('#type-filter').select_option('tv')
    expect(cards).to_have_count(2)
    expect(cards.filter(has_text='Local show')).to_have_attribute('href', '/media/8?type=tv')
    remote_show = cards.filter(has_text='Remote show')
    expect(remote_show).to_have_attribute('href', '/media/10?type=tv')
    expect(remote_show).to_contain_text('2 episodes')
    page.locator('#browse-search').fill('Remote')
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text('Remote show')
    page.locator('#sort-filter').select_option('vote_average.desc')
    expect(cards).to_have_count(1)
    assert [params['page'] for params in calls] == [['1'], ['2']]
    assert all(params['origin'] == ['all'] for params in calls)

    # Reload must not retain media from a node that has gone offline.
    page.unroute('**/api/downloads/library*', library)
    ui.payloads['/api/downloads/library'] = {'movies': [local_movie], 'series': [local_episode]}
    page.locator('#browse-search').fill('')
    expect(cards).to_have_count(2)
    page.locator('#refresh-button').click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text('Local show')


def test_downloaded_filter_retries_failed_library_pages_without_caching_partial_results(discovery_page):
    page = discovery_page.page
    calls = []
    second_page_available = False

    def library(route):
        params = parse_qs(urlsplit(route.request.url).query)
        number = int(params.get('page', ['1'])[0])
        calls.append(number)
        if number == 2 and not second_page_available:
            return route.fulfill(status=503, json={})
        route.fulfill(json={'page': number, 'pages': 2, 'series': [], 'movies': [{
            'title': f'Movie {number}', 'tmdb_id': number,
            'relative_path': f'federation:1:movie{number}'}]})

    page.route('**/api/downloads/library*', library)
    page.goto('https://streamvault.test/browse?view=downloads')
    expect(page.locator('#browse-error')).to_be_visible()
    expect(page.locator('#browse-grid .poster-card')).to_have_count(0)
    second_page_available = True
    page.locator('#retry-catalog').click()
    expect(page.locator('#browse-grid .poster-card')).to_have_count(2)
    expect(page.locator('#browse-error')).not_to_be_visible()
    assert calls == [1, 2, 1, 2]


def test_favorite_failure_and_removal_keep_returned_list_consistent(discovery_page):
    ui = discovery_page
    ui.payloads['/api/library/favorites'] = {'items': [{'tmdb_id': 7, 'title': 'Example', 'media_type': 'movie'}]}
    page = ui.page
    page.goto('https://streamvault.test/browse?view=favorites')
    page.locator('#browse-grid .poster-card').click()
    button = page.locator('#favorite-button')
    expect(button).to_have_attribute('aria-pressed', 'true')
    ui.payloads['/api/library/favorites/movie/7'] = (503, {})
    button.click()
    expect(page.locator('#detail-status')).to_contain_text('Could not update favorites')
    expect(button).to_have_attribute('aria-pressed', 'true')
    ui.payloads['/api/library/favorites/movie/7'] = {}
    button.click()
    expect(button).to_have_attribute('aria-pressed', 'false')
    page.locator('#detail-back').click()
    expect(page.locator('#browse-empty')).to_be_visible()
    expect(page.locator('#browse-search')).to_be_focused()


def test_local_library_error_retry(discovery_page):
    ui = discovery_page
    ui.payloads['/api/local/browse'] = (503, {})
    page = ui.page
    page.goto('https://streamvault.test/catalog')
    expect(page.locator('#catalog-error')).to_be_visible()
    expect(page.locator('#no-results')).not_to_be_visible()
    ui.payloads['/api/local/browse'] = {'items': []}
    page.locator('#catalog-error button').click()
    expect(page.locator('#catalog-error')).not_to_be_visible()
    expect(page.locator('#no-results')).to_contain_text('Your library is empty')
    expect(page.locator('#catalog-search')).to_be_focused()
