"""Production-served metadata and icon contracts, without a frontend build."""
import json
import re
import struct
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]


def test_login_has_one_consistent_metadata_set_and_public_assets(regression_client):
    response = regression_client.get("/login")
    assert response.status_code == 200
    head = BeautifulSoup(response.text, "html.parser").head
    assert len(head.select('meta[name="theme-color"]')) == 1
    assert head.select_one('meta[name="theme-color"]')["content"] == "#0a0a0a"
    assert head.select_one('meta[name="description"]')["content"]
    assert len(head.select('link[rel="apple-touch-icon"]')) == 1
    for link in head.select('link[href^="/assets/"]'):
        assert regression_client.get(link["href"]).status_code == 200, link["href"]
    for name in ["app.css", "ui-extras.css"]:
        source = (ROOT / "assets/css" / name).read_text()
        for asset in set(re.findall(r'url\("(/assets/[^\"]+)"\)', source)):
            assert regression_client.get(asset).status_code == 200, asset


def test_all_pages_share_head_metadata():
    for path in (ROOT / "core/templates").glob("*.html"):
        source = path.read_text()
        if "<head>" in source:
            assert '{% include "_metadata.html" %}' in source, path.name
    assert 'name="theme-color"' not in (ROOT / "core/templates/_sidebar.html").read_text()


def test_install_icons_have_declared_dimensions_and_maskable_variant():
    manifest = json.loads((ROOT / "assets/site.webmanifest").read_text())
    assert manifest["theme_color"] == manifest["background_color"] == "#0a0a0a"
    assert manifest["start_url"] == "/portal"
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        content = (ROOT / urlsplit(icon["src"]).path.removeprefix("/")).read_bytes()
        assert content[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", content[16:24])
        assert icon["sizes"] == f"{width}x{height}"
    ico = (ROOT / "assets/favicon.ico").read_bytes()
    assert struct.unpack("<HHH", ico[:6]) == (0, 1, 3)


@pytest.mark.parametrize("language,word", [("en", "private"), ("es", "privado")])
def test_metadata_description_is_localized(regression_client, language, word):
    regression_client.cookies.set("lang", language)
    head = BeautifulSoup(regression_client.get("/login").text, "html.parser").head
    assert word in head.select_one('meta[name="description"]')["content"].lower()
