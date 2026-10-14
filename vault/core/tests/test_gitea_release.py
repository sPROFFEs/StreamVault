"""Gitea publishing is explicit, drafts only, and validates packaged checksums."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def publisher():
    spec = importlib.util.spec_from_file_location("gitea_release", Path(__file__).resolve().parents[2] / "scripts/publish_gitea_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assets(tmp_path):
    web = tmp_path / "streamvault-1.0.0-web.tar.gz"
    web.write_bytes(b"fixture-source")
    manifest = {"version": "1.0.0", "tag": "v1.0.0", "repository": "owner/repo", "commit": "a" * 40,
                "assets": [{"name": web.name, "sha256": hashlib.sha256(web.read_bytes()).hexdigest(), "size": web.stat().st_size}]}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest))
    (tmp_path / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n" for file in (web, path)))
    return tmp_path


def test_gitea_draft_and_assets_are_only_published_when_requested(tmp_path, monkeypatch):
    module = publisher()
    folder = assets(tmp_path)
    calls = []
    def api(url, token, *, body=None, file=None):
        calls.append((url, body, file))
        return {"id": 12} if file is None else {"id": 100}
    monkeypatch.setattr(module, "api", api)
    result = module.publish("https://forge.test", "owner/repo", "fixture-token", folder)
    assert result == {"release_id": 12, "tag": "v1.0.0", "draft": True}
    assert calls[0][1]["draft"] is True
    assert calls[0][1]["tag_name"] == "v1.0.0"
    assert len([call for call in calls if call[2] is not None]) == 3


def test_tampered_release_asset_cannot_be_published(tmp_path, monkeypatch):
    module = publisher()
    folder = assets(tmp_path)
    (folder / "streamvault-1.0.0-web.tar.gz").write_bytes(b"changed")
    monkeypatch.setattr(module, "api", lambda *args, **kwargs: pytest.fail("Tampered assets must not reach Gitea"))
    with pytest.raises(ValueError, match="checksum|size"):
        module.publish("https://forge.test", "owner/repo", "fixture-token", folder)


@pytest.mark.parametrize("url", ["https://token@forge.test", "file:///tmp", "https://forge.test/?token=private"])
def test_publisher_rejects_credential_urls(tmp_path, url):
    with pytest.raises(ValueError):
        publisher().publish(url, "owner/repo", "fixture-token", assets(tmp_path))
