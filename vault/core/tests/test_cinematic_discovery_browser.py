"""Cinematic layouts keep artwork, metadata and existing navigation intact."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from test_discovery_routes_browser import discovery_page


pytestmark = pytest.mark.browser


def artwork(page):
    # Inline SVG fixtures exercise intrinsic dimensions without remote services.
    def image(route):
        if os.getenv('STREAMVAULT_SCREENSHOTS'):
            fixture = Path(os.environ['STREAMVAULT_SCREENSHOTS']) / ('fixture-backdrop.jpg' if 'backdrop' in route.request.url else 'fixture-poster.jpg')
            if fixture.is_file():
                return route.fulfill(path=fixture, content_type='image/jpeg')
        return route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600"><rect width="400" height="600" fill="#234452"/></svg>')
    page.route("https://image.tmdb.org/**", image)
    page.route("https://artwork.example/**", image)


def screenshot(page, name, full_page=False):
    if os.getenv('STREAMVAULT_SCREENSHOTS'):
        page.evaluate('window.scrollTo({top: 0, behavior: "instant"})')
        page.screenshot(path=str(Path(os.environ['STREAMVAULT_SCREENSHOTS']) / f'{name}-{page.viewport_size["width"]}.png'), full_page=full_page, animations='disabled')


def test_fullscreen_library_portrait_keeps_ratio_metadata_and_keyboard(discovery_page, browser_viewport):
    ui, page = discovery_page, discovery_page.page
    artwork(page)
    ui.payloads['/api/local/browse'] = {'items': [
        {'id': 1, 'tmdb_id': 7, 'title': 'Example', 'type': 'movie', 'year': 2024, 'rating': 8.5,
         'duration': 110, 'poster_path': 'https://artwork.example/portrait.svg', 'backdrop_path': '/backdrop.jpg',
         'overview': 'A cinematic fixture overview.'}]}
    page.goto('https://streamvault.test/catalog')
    card = page.locator('#media-grid .catalog-card')
    expect(card.locator('img')).to_have_attribute('src', 'https://artwork.example/portrait.svg')
    card.click()
    panel = page.locator('#detail-modal [role="dialog"]')
    expect(panel).to_be_visible()
    expect(panel).to_have_css('background-color', 'rgb(20, 20, 20)')
    box = panel.bounding_box()
    assert abs(box['width'] - browser_viewport['width']) < 1
    assert abs(box['height'] - browser_viewport['height']) < 1
    expect(page.locator('#modal-img')).to_have_css('object-fit', 'contain')
    image = page.locator('#modal-img').bounding_box()
    assert abs(image['height'] / image['width'] - 1.5) < .01
    expect(page.locator('#modal-img')).to_have_css('max-height', 'none')
    expect(page.locator('#modal-backdrop-bg')).to_have_css('filter', 'none')
    assert 'w1280/backdrop.jpg' in page.locator('#modal-backdrop-bg').evaluate('e => e.style.backgroundImage')
    expect(page.locator('#modal-badges')).to_contain_text('8.5/10')
    expect(page.locator('#modal-badges')).to_contain_text('2024')
    expect(page.locator('#modal-badges')).to_contain_text('110 min')
    assert '98%' not in page.locator('#modal-badges').inner_text()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    screenshot(page, 'library-modal')
    page.keyboard.press('Tab')
    assert page.locator('#detail-modal').evaluate('e => e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(panel).to_be_hidden()
    expect(card).to_be_focused()


def test_library_failed_artwork_resets_on_next_item(discovery_page):
    ui, page = discovery_page, discovery_page.page
    artwork(page)
    page.route('https://artwork.example/broken.svg', lambda route: route.fulfill(status=404))
    ui.payloads['/api/local/browse'] = {'items': [
        {'id': 1, 'title': 'Broken', 'type': 'movie', 'poster_path': 'https://artwork.example/broken.svg'},
        {'id': 2, 'title': 'Working', 'type': 'series', 'poster_path': 'https://artwork.example/working.svg'}]}
    page.goto('https://streamvault.test/catalog')
    cards = page.locator('#media-grid .catalog-card')
    cards.first.click()
    expect(page.locator('#modal-artwork-placeholder')).to_be_visible()
    expect(page.locator('#modal-img')).to_be_hidden()
    page.keyboard.press('Escape')
    expect(cards.first).to_be_focused()
    cards.nth(1).click()
    expect(page.locator('#modal-img')).to_be_visible()
    expect(page.locator('#modal-artwork-placeholder')).to_be_hidden()
    expect(page.locator('#modal-type-badge')).to_have_text('Series')
    expect(page.locator('#modal-match')).to_be_hidden()


def test_movie_and_series_with_same_local_id_open_the_correct_title(discovery_page):
    ui, page = discovery_page, discovery_page.page
    artwork(page)
    ui.payloads['/api/local/browse'] = {'items': [
        {'id': 1, 'tmdb_id': 9, 'title': 'Movie fixture', 'type': 'movie', 'poster_path': '/movie.jpg'},
        {'id': 1, 'tmdb_id': 7, 'title': 'Series fixture', 'type': 'series', 'poster_path': '/series.jpg'}]}
    page.goto('https://streamvault.test/catalog')
    page.locator('#media-grid .catalog-card').nth(1).click()
    expect(page.locator('#modal-title')).to_have_text('Series fixture')
    expect(page.locator('#modal-img')).to_have_attribute('src', 'https://image.tmdb.org/t/p/w500/series.jpg')
    expect(page.locator('#modal-type-badge')).to_have_text('Series')
    page.get_by_role('button', name='Play', exact=True).click()
    expect(page).to_have_url('https://streamvault.test/media/7?type=tv')
    expect(page.locator('#season-select')).to_have_value('1')


def test_detail_has_cinematic_artwork_without_moving_playback_contracts(discovery_page):
    ui, page = discovery_page, discovery_page.page
    artwork(page)
    ui.payloads['/api/tmdb/movie/7'] = {**ui.movie, 'poster_path': 'https://artwork.example/portrait.svg', 'backdrop_path': '/backdrop.jpg', 'overview': 'A cinematic fixture overview.', 'genres': [{'name': 'Drama'}], 'runtime': 110}
    page.goto('https://streamvault.test/media/7?type=movie')
    expect(page.locator('#detail-title')).to_have_text('Example')
    expect(page.locator('#detail-poster img')).to_have_attribute('src', 'https://artwork.example/portrait.svg')
    page.wait_for_function("document.querySelector('#detail-poster img').complete && document.querySelector('#detail-poster img').naturalWidth > 0")
    expect(page.locator('.detail-feature')).to_be_visible()
    poster = page.locator('#detail-poster').bounding_box()
    assert poster['width'] <= 180.01  # Firefox may report fractional layout rounding.
    assert abs(poster['height'] / poster['width'] - 1.5) < .01
    expect(page.locator('#detail-genres')).to_have_text('Drama')
    expect(page.locator('#detail-meta')).to_contain_text('110 min')
    for selector in ['#play-button', '#favorite-button', '#download-button', '#source-select', '#fullscreen-button', '#subtitle-lang-select', '#subtitle-size-slider']:
        expect(page.locator(selector)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert sum(path == '/api/tmdb/movie/7' for path, _ in ui.calls) == 1
    assert not any('hls.min.js' in path or 'webtorrent.min.js' in path for path, _ in ui.calls)
    page.locator('#favorite-button').click()
    expect(page.locator('#favorite-button')).to_have_attribute('aria-pressed', 'true')
    screenshot(page, 'detail', full_page=True)


def test_home_uses_real_metadata_and_keeps_existing_rail_links(discovery_page):
    ui, page = discovery_page, discovery_page.page
    artwork(page)
    ui.payloads['/api/tmdb/trending/movie'] = [{**ui.movie, 'vote_average': '8.5', 'backdrop_path': '/backdrop.jpg', 'poster_path': '/portrait.jpg'}]
    page.goto('https://streamvault.test/portal')
    expect(page.locator('#hero h1')).to_have_text('Example')
    expect(page.locator('#hero .hero-rating')).to_contain_text('8.5')
    assert 'Match' not in page.locator('#hero').inner_text()
    assert 'HD' not in page.locator('#hero').inner_text()
    expect(page.locator('#hero .hero-actions a').first).to_have_attribute('href', '/media/7?type=movie')
    expect(page.locator('#trending-movies-row .poster-card')).to_have_attribute('href', '/media/7?type=movie')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    screenshot(page, 'home')
