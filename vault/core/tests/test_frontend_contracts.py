import json
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _search_payload(payload):
    script_path = PROJECT_ROOT / "assets/js/search-modal.js"
    script = script_path.read_text()
    probe = f"""
global.window = {{}};
eval({json.dumps(script)});
console.log(JSON.stringify(window.SVSearchModal.normalizeResults({json.dumps(payload)})));
"""
    result = subprocess.run(
        ["node", "-e", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_global_search_accepts_tmdb_array_response():
    payload = [{"id": 933260, "title": "The Substance"}]

    assert _search_payload(payload) == payload


def test_global_search_remains_compatible_with_wrapped_response():
    payload = [{"id": 933260, "title": "The Substance"}]

    assert _search_payload({"results": payload}) == payload


def test_media_detail_asset_is_cache_busted():
    template = (PROJECT_ROOT / "core/templates/media_detail.html").read_text()

    assert '/assets/js/media_detail.js?v=' in template


def test_media_detail_uses_the_shared_toast_without_shadowing_it():
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    assert "function showToast(" not in script
    assert "components/toast.js?v=" in (PROJECT_ROOT / "core/templates/_header.html").read_text()


def test_nginx_does_not_cache_mutable_assets_for_thirty_days():
    nginx = (PROJECT_ROOT / "nginx.conf").read_text()

    assert "expires 30d" not in nginx
    assert 'Cache-Control "no-cache, must-revalidate"' in nginx


def test_browse_honors_favorites_deep_link():
    script = (PROJECT_ROOT / "assets/js/browse.js").read_text()

    assert 'state.view = params.get("view") || "discover"' in script
    assert 'params.set("view", state.view)' in script


def test_catalog_uses_flat_1flex_style_layout():
    template = (PROJECT_ROOT / "core/templates/browse.html").read_text()
    script = (PROJECT_ROOT / "assets/js/browse.js").read_text()

    assert 'class="browse-hero"' in template
    assert '<section class="panel">' not in template
    assert 'id="load-more-button"' in template
    assert "page=${state.page}" in script
    assert "appendCatalog" in script
    assert '/assets/js/browse.js?v=' in template
    assert 'value="downloads"' in template
    assert "loadDownloadedItems" in script


def test_home_uses_content_rails_without_duplicate_carousel():
    script = (PROJECT_ROOT / "assets/js/portal.js").read_text()
    template = (PROJECT_ROOT / "core/templates/portal.html").read_text()

    assert "row-trending-carousel" not in script
    assert 'class="scroll-row" id="trending-movies-row"' in script
    assert 'class="scroll-row" id="trending-tv-row"' in script
    assert "recommendations" in script
    assert "genre-rows" in script
    assert '/assets/js/portal.js?v=' in template


def test_download_empty_states_are_not_inside_mutated_lists():
    template = (PROJECT_ROOT / "core/templates/downloads.html").read_text()

    assert re.search(r'id="active-list"[^>]*></div>\s*<div[^>]+id="active-empty"', template)
    assert re.search(r'id="library-list"[^>]*></div>\s*<div[^>]+id="library-empty"', template)


def test_download_ui_blocks_duplicate_submissions_and_hides_terminal_jobs():
    detail_script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    manager_script = (PROJECT_ROOT / "assets/js/download-manager.js").read_text()

    assert "downloadSubmitting" in detail_script
    assert 'includes(j.status)' in manager_script
    assert '"pending", "connecting", "downloading"' in manager_script


def test_media_detail_prefers_matching_local_source():
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    downloads_script = (PROJECT_ROOT / "assets/js/download-manager.js").read_text()
    nginx = (PROJECT_ROOT / "nginx.conf").read_text()

    assert "findLocalSource" in script
    assert "localSource ? [localSource" in script
    assert 'source.type === "local"' in script
    assert "SVLocalPlayback.open" in script
    assert "embedded-subtitle" in (PROJECT_ROOT / "core/routers/downloads.py").read_text()
    assert "playerAudio" in downloads_script
    assert "proxy_buffering off" in nginx


def test_live_tv_surfaces_bootstrap_errors_and_refreshes_its_bundle():
    template = (PROJECT_ROOT / "core/templates/iptv.html").read_text()
    script = (PROJECT_ROOT / "assets/js/iptv.js").read_text()

    assert 'id="iptv-empty-message"' in template
    assert '/assets/js/iptv.js?v=' in template
    assert "bootstrapResult.errors" in script
    assert 'params.set("limit", String(pageSize))' in script
    assert "le=5000" in (PROJECT_ROOT / "core/routers/iptv.py").read_text()
    assert "data.working" in script


def test_local_and_remote_subtitles_share_the_player_controls():
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    router = (PROJECT_ROOT / "core/routers/downloads.py").read_text()

    assert "renderAvailableSubtitles" in script
    assert "activateSubtitle" in script
    assert "source.subtitles" in script
    assert "play_url" in script
    assert "source.duration_seconds" in script
    assert "await fetch(item.play_url" in script
    # Subtitles render via a custom overlay driven by video.currentTime
    # (not the flaky <track>+blob approach) so the overlay stays in sync
    # even when the remux seeks to a resume position.
    assert "video-subtitle-overlay" in script
    assert "parseVttCues" in script
    assert "findActiveCue" in script
    assert "syncSubtitleSelection" in script
    assert "TEXT_SUBTITLE_CODECS" in router
    assert 'track["codec"] in TEXT_SUBTITLE_CODECS' in router
    assert "subtitle_cache_path" in router
    assert "SUBTITLE_EXTRACTION_TIMEOUT" in router
    assert "Subtitle extraction timed out" in router


def test_local_remux_hybrid_streaming_and_background_cache():
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    router = (PROJECT_ROOT / "core/routers/downloads.py").read_text()

    # The remux endpoint uses a hybrid approach:
    # 1. If a cached faststart MP4 exists → FileResponse (seekable via Range)
    # 2. If no cache → stream fragmented MP4 immediately + background remux
    assert "+faststart" in router
    assert "FileResponse" in router
    assert "Accept-Ranges" in router
    assert "_ensure_background_remux" in router
    assert "_run_background_remux" in router
    assert "frag_keyframe" in router  # fragmented MP4 for immediate playback
    player = (PROJECT_ROOT / "assets/js/local-player.js").read_text()
    assert 'playback.mode === "file"' in player
    assert 'playback.mode === "stream"' in player
    assert "video.duration > resumeAt" not in script
    assert "offset + video.currentTime" in script


def test_downloads_poll_once_for_all_jobs_and_offer_library_pagination():
    script = (PROJECT_ROOT / "assets/js/download-manager.js").read_text()
    template = (PROJECT_ROOT / "core/templates/downloads.html").read_text()
    assert "state.jobsLoading" in script
    assert "attachSse(job.id)" not in script
    assert 'id="library-next"' in template
    assert "page=${state.libraryPage}" in script


def test_pseudo_fullscreen_keeps_subtitle_overlay_visible():
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    css_extras = (PROJECT_ROOT / "assets/css/ui-extras.css").read_text()

    # Pseudo-fullscreen uses CSS position:fixed instead of the native
    # Fullscreen API, so the subtitle overlay (a sibling of <video>)
    # stays in the DOM and is always visible.
    assert "player-box--pseudo-fs" in script
    assert "enterPseudoFullscreen" in script
    assert "exitPseudoFullscreen" in script
    assert "togglePseudoFullscreen" in script
    # The custom player removes native controls; the fullscreen interceptor
    # also covers browsers that still expose a native fullscreen action.
    custom_player = (PROJECT_ROOT / "assets/js/custom-player.js").read_text()
    assert "SVInitCustomPlayer" in script
    assert 'video.removeAttribute("controls")' in custom_player
    assert 'video.controlsList = "nofullscreen"' in script
    # When native video fullscreen IS triggered anyway, we inject a native
    # <track> element and switch it to "showing" so the browser renders
    # subtitles natively inside its own fullscreen view.
    assert 'track[data-sub-track]' in script
    assert 'track.track.mode = "showing"' in script
    assert 'track.track.mode = "hidden"' in script
    # CSS class for pseudo-fullscreen fills the screen.
    assert ".player-box--pseudo-fs" in css_extras
    assert "position: fixed" in css_extras
    assert "z-index: 2147483647" in css_extras
    # Exit button exists for pseudo-fullscreen.
    assert "#pseudo-fs-exit" in css_extras


def test_subtitle_size_slider_exists():
    template = (PROJECT_ROOT / "core/templates/media_detail.html").read_text()
    script = (PROJECT_ROOT / "assets/js/media_detail.js").read_text()
    css_extras = (PROJECT_ROOT / "assets/css/ui-extras.css").read_text()

    # Slider in the subtitle section of the media detail page.
    assert 'id="subtitle-size-slider"' in template
    assert "subtitle-size-slider" in script
    # Slider persists to localStorage and applies a CSS custom property.
    assert 'localStorage.getItem("subtitleSize")' in script
    assert "--sub-scale" in script
    # CSS uses the custom property to scale subtitle font size.
    assert "var(--sub-scale" in css_extras
    assert ".subtitle-size-slider" in css_extras


def test_settings_exposes_opensubtitles_key():
    template = (PROJECT_ROOT / "core/templates/settings.html").read_text()
    script = (PROJECT_ROOT / "assets/js/settings.js").read_text()

    assert 'id="key-opensubtitles"' in template
    assert 'id="save-opensubtitles"' in template
    assert "opensubtitles_api_key" in script


def test_admin_exposes_cloudflare_tunnel_controls():
    template = (PROJECT_ROOT / "core/templates/admin.html").read_text()

    assert 'id="tunnel-enabled"' in template
    assert 'id="tunnel-create"' in template
    assert 'id="tunnel-start"' in template
    assert 'id="tunnel-stop"' in template
    assert '/assets/js/admin.js?v=' in template


def test_admin_download_inventory_has_search_filters_summary_and_pagination():
    template = (PROJECT_ROOT / "core/templates/admin.html").read_text()
    explorer = (PROJECT_ROOT / "core/templates/_admin_files.html").read_text()
    script = (PROJECT_ROOT / "assets/js/admin-files.js").read_text()
    stylesheet = (PROJECT_ROOT / "assets/css/admin.css").read_text()

    assert "_admin_files.html" in template
    assert '/assets/js/admin-files.js?v=' in template
    for control in ("fm-search", "fm-kind", "fm-summary", "fm-list", "fm-previous", "fm-next", "fm-breadcrumbs", "fm-cleaner", "fm-cleaner-overlay"):
        assert f'id="{control}"' in explorer
    assert "/admin/api/downloads/cleanup" in script
    assert "download_q" in script and "download_kind" in script
    assert "data.total_files" in script
    assert "data.total_bytes_label" in script
    assert "data.items" in script and "data.pages" in script
    assert ".admin-explorer__toolbar" in stylesheet
    assert ".admin-explorer__entry" in stylesheet


def test_portal_progress_is_clamped_to_a_valid_percentage():
    source = (PROJECT_ROOT / "assets/js/portal.js").read_text()
    assert "Math.max(0, Math.min(100" in source


def test_fixed_navigation_reserves_space_on_application_pages():
    stylesheet = (PROJECT_ROOT / "assets/css/app.css").read_text()

    assert "--nav-height: 68px" in stylesheet
    assert "padding-top: var(--nav-height)" in stylesheet


def test_login_uses_the_application_artwork_and_professional_shell():
    template = (PROJECT_ROOT / "core/templates/login.html").read_text()

    assert 'class="auth-brand"' in template
    assert re.search(r'src="/assets/icon\.png(?:\?v=[^\"]+)?"', template)
    assert 'class="auth-backdrop"' in template
