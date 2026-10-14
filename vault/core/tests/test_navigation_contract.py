from pathlib import Path


VAULT_ROOT = Path(__file__).resolve().parents[2]


def test_shared_navigation_uses_top_header_not_icon_rail():
    navigation = (VAULT_ROOT / "core/templates/_sidebar.html").read_text()

    assert '<header class="top-nav"' in navigation
    assert '<aside class="sidebar"' not in navigation
    assert "Movies" in navigation
    assert "Series" in navigation
    assert "Live TV" in navigation


def test_anime_navigation_uses_the_existing_redirect_route():
    navigation = (VAULT_ROOT / "core/templates/_sidebar.html").read_text()

    assert 'href="/anime"' in navigation
