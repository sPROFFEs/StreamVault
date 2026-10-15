"""Real template checks for the shared cards, toasts and library filters."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright
from starlette.requests import Request

from shared_templates import templates


pytestmark = pytest.mark.skipif(os.getenv("STREAMVAULT_BROWSER_TESTS") != "1", reason="Opt-in browser check")
ASSETS = Path(__file__).resolve().parents[2] / "assets"


@pytest.fixture
def ui_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page, errors
        browser.close()
        assert not errors


def render_template(name, query=""):
    request = Request({"type": "http", "headers": [(b"cookie", b"lang=es")], "state": {}, "query_string": query.encode()})
    return templates.get_template(name).render(request=request, is_admin=True)


def serve_asset(route):
    asset = ASSETS / urlsplit(route.request.url).path.removeprefix("/assets/")
    return route.fulfill(path=asset) if asset.is_file() else route.fulfill(status=404)


def test_federated_downloads_filter_play_audio_and_never_offer_local_delete(ui_page):
    page, _ = ui_page
    html = render_template("downloads.html")
    base = '/api/federation/peers/7/media/' + 'a' * 64
    local = {"title": "Local movie", "name": "Local.mp4", "media_type": "movie", "relative_path": "Local.mp4", "size_bytes": 42, "modified_at": 1}
    remote = {"title": "Remote show", "name": "Remote show S01E02", "media_type": "series",
              "relative_path": "federation:7:" + 'a' * 64, "origin": "federation", "peer_id": 7,
              "peer_name": "Node <Cinema>", "size_bytes": 9000, "modified_at": 2,
              "season_number": 1, "episode_number": 2, "metadata_url": base + '/metadata', "playback_url": base + '/playback'}
    calls = []
    def respond(route):
        url = urlsplit(route.request.url)
        calls.append((url.path, parse_qs(url.query)))
        if url.path == '/downloads':
            return route.fulfill(content_type='text/html', body=html)
        if url.path.startswith('/assets/'):
            return serve_asset(route)
        if url.path == '/api/downloads/library':
            origin = parse_qs(url.query).get('origin', ['local'])[0]
            files = [local] if origin == 'local' else [local, remote] if origin == 'all' else [remote]
            return route.fulfill(json={"files": files, "movies": [f for f in files if f['media_type'] == 'movie'],
                "series": [f for f in files if f['media_type'] == 'series'], "total": len(files), "page": 1, "pages": 1,
                "used_bytes": 42, "quota_bytes": 1024, "nodes": [{"id": 7, "name": "Node <Cinema>"}]})
        if url.path == base + '/metadata':
            return route.fulfill(json={"duration_seconds": 100, "subtitles": [], "audio_tracks": [
                {"stream_index": 1, "language": "eng", "codec": "aac"}, {"stream_index": 2, "language": "spa", "codec": "aac"}]})
        if url.path == base + '/playback':
            return route.fulfill(json={"mode": "file", "offset": 0, "url": '/remote.mp4', "duration_seconds": 100})
        if url.path == '/api/downloads/notifications':
            return route.fulfill(content_type='text/event-stream', body=': connected\n\n')
        return route.fulfill(json={})
    page.add_init_script('HTMLMediaElement.prototype.play = async () => {}; HTMLMediaElement.prototype.load = () => {};')
    page.route('**/*', respond)
    page.goto('https://streamvault.test/downloads')
    expect(page.locator('#library-list')).to_contain_text('Remote show')
    page.locator('[data-toggle-show]').click()
    row = page.locator('[data-file-card]').filter(has_text='Node <Cinema>')
    expect(row).to_contain_text('S01E02')
    expect(row.locator('[data-delete]')).to_have_count(0)
    row.locator('[data-play]').click()
    expect(page.locator('#local-player')).to_have_attribute('src', '/remote.mp4')
    with page.expect_response('**' + base + '/playback?*audio=2*'):
        page.locator('#local-player-audio').select_option('2')
    expect(page.locator('#local-player-audio')).to_have_value('2')
    page.wait_for_function("document.querySelector('#local-player').src.endsWith('/remote.mp4')")
    assert any(path == base + '/playback' and query.get('audio') == ['2'] for path, query in calls)
    assert not any(path == '/api/downloads/files/match' for path, _ in calls)
    page.locator('#local-player-close').click()
    page.locator('#library-origin').select_option('local')
    expect(page.locator('#library-list')).not_to_contain_text('Remote show')
    expect(page.locator('#library-list [data-delete]')).to_have_count(1)
    page.locator('#library-origin').select_option('peer:7')
    expect(page.locator('#library-list')).to_contain_text('Remote show')
    expect(page.locator('#library-list')).not_to_contain_text('Local movie')
    assert 'origin=peer%3A7' in page.url


def test_download_filters_search_url_reset_empty_and_expand(ui_page):
    page, _ = ui_page
    html = render_template("downloads.html")
    queries = []
    delayed = []
    movie = {"title": "Example", "name": "Example.mp4", "media_type": "movie", "relative_path": "Example.mp4", "size_bytes": 42, "modified_at": 1}
    episode = {"title": "The Bear", "name": "01.mkv", "media_type": "series", "relative_path": "The Bear/Season 03/01.mkv", "size_bytes": 80, "modified_at": 2, "season_number": 3, "episode_number": 1}

    def route_request(route):
        url = urlsplit(route.request.url)
        if url.path == "/downloads":
            return route.fulfill(content_type="text/html", body=html)
        if url.path.startswith("/assets/"):
            return serve_asset(route)
        if url.path == "/api/downloads/jobs":
            return route.fulfill(json={"torrent": [], "yt_dlp": []})
        if url.path == "/api/downloads/library":
            params = {key: values[0] for key, values in parse_qs(url.query).items()}
            queries.append(params)
            if params.get("q") == "delayed":
                delayed.append(route)
                return
            files = [movie, episode]
            if params.get("media_type", "all") != "all":
                files = [item for item in files if item["media_type"] == params["media_type"]]
            files = [item for item in files if params.get("q", "").lower() in item["title"].lower()]
            return route.fulfill(json={"files": files, "movies": [item for item in files if item["media_type"] == "movie"], "series": [item for item in files if item["media_type"] == "series"], "total": len(files), "page": 1, "pages": 1, "used_bytes": 122, "quota_bytes": 1024})
        if url.path == "/api/downloads/notifications":
            return route.fulfill(content_type="text/event-stream", body=": connected\n\n")
        return route.fulfill(json={})

    page.route("**/*", route_request)
    page.goto("https://streamvault.test/downloads?q=Bear&type=series&sort=title&page=2")
    expect(page.locator("#library-search")).to_have_value("Bear")
    expect(page.locator("#library-list")).to_contain_text("The Bear")
    expect(page.locator("#library-list")).not_to_contain_text("Example")
    assert queries[0] == {"page": "2", "q": "Bear", "media_type": "series", "sort": "title", "origin": "all"}
    show = page.locator("[data-toggle-show]")
    show.click()
    expect(show).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".series-group__body")).to_be_visible()
    page.locator("#refresh-btn").click()
    expect(show).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".series-group__body")).to_be_visible()

    page.locator("#library-search").fill("nothing")
    expect(page.locator("#library-empty")).to_contain_text("Sin resultados")
    assert queries[-1]["page"] == "1"
    assert "q=nothing" in page.url
    page.locator("#library-reset").click()
    expect(page.locator("#library-search")).to_have_value("")
    expect(page.locator("#library-list")).to_contain_text("Example")
    expect(page.locator("#library-type")).to_have_value("all")
    expect(page.locator("#library-sort")).to_have_value("recent")
    page.locator("#library-type").select_option("movie")
    expect(page.locator("#library-list")).not_to_contain_text("The Bear")
    page.locator("#library-sort").select_option("size")
    expect(page).to_have_url("https://streamvault.test/downloads?type=movie&sort=size")
    for width in (1366, 390):
        page.set_viewport_size({"width": width, "height": 844})
        # Compare initial layout; resizing can preserve the prior control scroll position.
        page.evaluate("window.scrollTo(0, 0)")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("document.querySelector('.downloads-overview').getBoundingClientRect().top >= document.querySelector('.top-nav').getBoundingClientRect().bottom")

    # Numeric show titles must keep the order selected on the server rather
    # than being reordered as JavaScript object property names.
    page.evaluate("""() => {
        const series = ['1883', '24'].map(title => ({title, name: `${title}.S01E01.mkv`, relative_path: `${title}.mkv`, media_type: 'series', season_number: 1, episode_number: 1, size_bytes: 1, modified_at: 1}));
        renderLibrary({files: series, series, used_bytes: 2, quota_bytes: 1024, total: 2});
    }""")
    assert page.locator(".series-group__title").all_text_contents() == ["1883", "24"]

    page.evaluate("""() => {
        window.libraryRenders = [];
        new MutationObserver(() => libraryRenders.push(document.getElementById('library-list').textContent))
            .observe(document.getElementById('library-list'), {childList: true});
    }""")
    with page.expect_request("**/api/downloads/library?*q=delayed*"):
        page.locator("#library-search").fill("delayed")
    page.locator("#library-search").fill("Example")
    page.locator("#library-search").press("Tab")
    expect(page.locator("#library-summary")).to_contain_text("Buscando")
    page.wait_for_timeout(300)  # Let the debounced refresh queue behind the held request.
    expired = {**movie, "title": "EXPIRED RESULTS"}
    delayed[0].fulfill(json={"files": [expired], "movies": [expired], "series": [], "total": 1, "page": 1, "pages": 1, "used_bytes": 42, "quota_bytes": 1024})
    expect(page.locator("#library-summary")).to_have_text("1 archivo descargado")
    expect(page.locator("#library-list")).to_contain_text("Example")
    assert all("EXPIRED RESULTS" not in value for value in page.evaluate("libraryRenders"))


def test_catalog_cards_are_localized_and_keep_navigation(ui_page):
    page, _ = ui_page
    html = render_template("browse.html", "type=tv")

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path == "/browse":
            return route.fulfill(content_type="text/html", body=html)
        if path.startswith("/assets/"):
            return serve_asset(route)
        if path == "/api/tmdb/discover/tv":
            return route.fulfill(json=[{"id": 7, "name": "The Bear", "media_type": "tv", "first_air_date": "2022-06-23", "vote_average": 8.5, "poster_path": "/missing.jpg"}])
        if path == "/api/library/favorites":
            return route.fulfill(json={"items": []})
        return route.fulfill(json={})

    page.route("**/*", route_request)
    page.goto("https://streamvault.test/browse?type=tv")
    card = page.locator("#browse-grid .poster-card")
    expect(card).to_have_attribute("href", "/media/7?type=tv")
    expect(card).to_contain_text("2022")
    expect(card).to_contain_text("Serie")
    expect(card).to_contain_text("8.5")
    expect(card.locator(".media-card__placeholder")).to_be_visible()
    card.focus()
    expect(card.locator(".card-overlay")).to_have_css("opacity", "1")
    for width in (1366, 390):
        page.set_viewport_size({"width": width, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_toasts_are_accessible_safe_and_dismissible(ui_page):
    page, _ = ui_page
    page.route("**/*", lambda route: serve_asset(route) if "/assets/" in route.request.url else route.fulfill(content_type="text/html", body='<html lang="es"><body><script src="/assets/js/components/toast.js"></script></body></html>'))
    page.goto("https://streamvault.test/toasts")
    page.evaluate('showToast("Descarga añadida", "<script>bad</script>", "ok", 60000)')
    toast = page.locator(".toast")
    expect(toast).to_have_attribute("role", "status")
    expect(toast).to_contain_text("<script>bad</script>")
    assert toast.locator("script").count() == 0
    page.get_by_role("button", name="Cerrar aviso").click()
    expect(toast).to_have_count(0)
    page.evaluate('showToast("Error", "No se pudo cargar", "error", 60000)')
    expect(page.locator(".toast")).to_have_attribute("role", "alert")
