"""Release discovery never applies an unverified branch tip or exposes tokens."""
import json
import sqlite3
import subprocess
import zipfile

import pytest

from services.updater_service import UpdateError, UpdateManager


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def release_checkout(tmp_path):
    upstream = tmp_path / "origin"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Fixture")
    git(upstream, "config", "user.email", "fixture@example.test")
    (upstream / "vault/core").mkdir(parents=True)
    metadata = upstream / "vault/core/version.json"
    metadata.write_text(json.dumps({"version": "1.0.0", "android_version_code": 1}))
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "initial")
    local = tmp_path / "local"
    git(tmp_path, "clone", str(upstream), str(local))
    metadata.write_text(json.dumps({"version": "1.1.0", "android_version_code": 2}))
    git(upstream, "commit", "-am", "release")
    git(upstream, "tag", "v1.1.0")
    target = git(upstream, "rev-parse", "HEAD")
    (upstream / "unreleased.txt").write_text("Do not install this branch tip")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "unreleased")
    manager = UpdateManager(local, tmp_path / "data")
    manager.configure({"mode": "release", "provider": "gitea", "api_url": "https://forge.example.test",
                       "repository": "owner/StreamVault", "api_token": "private-fixture-token"})
    return manager, local, upstream, target


def test_gitea_stable_release_selects_tag_not_branch_tip(release_checkout, monkeypatch):
    manager, local, upstream, target = release_checkout
    calls = []
    def releases(url, token):
        calls.append((url, token))
        return [{"tag_name": "v9.0.0-beta", "prerelease": True}, {"tag_name": "v8.0.0", "draft": True},
                {"tag_name": "v1.1.0", "body": "Release notes"}]
    monkeypatch.setattr(manager, "release_json", releases)
    result = manager.check()
    assert result["state"] == "available"
    assert result["remote_commit"] == target != git(upstream, "rev-parse", "HEAD")
    assert result["release_tag"] == "v1.1.0"
    assert result["installed_version"] == "1.0.0"
    assert calls[0][0].startswith("https://forge.example.test/api/v1/repos/owner/StreamVault/releases")
    assert "private-fixture-token" not in str(result) + str(manager.configuration())
    assert git(local, "rev-parse", "HEAD") != target
    assert manager.config_path.stat().st_mode & 0o077 == 0


def test_github_adapter_and_numeric_version_order(release_checkout, monkeypatch):
    manager, _, _, _ = release_checkout
    manager.configure({"provider": "github", "api_url": "https://api.github.com", "api_token": ""})
    calls = []
    monkeypatch.setattr(manager, "release_json", lambda url, token: calls.append(url) or [
        {"tag_name": "v1.1.0"}, {"tag_name": "v1.0.9"}])
    assert manager.check()["release_tag"] == "v1.1.0"
    assert calls[0].startswith("https://api.github.com/repos/owner/StreamVault/releases")


def test_failed_release_api_is_not_reported_as_current(release_checkout, monkeypatch):
    manager, _, _, _ = release_checkout
    monkeypatch.setattr(manager, "release_json", lambda *args: (_ for _ in ()).throw(UpdateError("Release API unavailable", 502)))
    with pytest.raises(UpdateError):
        manager.check()
    assert manager.status()["state"] == "failure"


def test_changing_forge_does_not_forward_the_previous_credential(release_checkout):
    manager, _, _, _ = release_checkout
    manager.configure({"api_url": "https://another-forge.test"})
    assert manager.configuration()["has_token"] is False
    assert manager._configuration()["api_token"] == ""


def test_private_release_credential_requires_an_explicit_destination(release_checkout):
    manager, _, _, _ = release_checkout
    with pytest.raises(UpdateError, match="URL"):
        manager.configure({"api_url": "", "api_token": "new-private-token"})


def test_release_tag_must_match_committed_version(release_checkout, monkeypatch):
    manager, _, upstream, _ = release_checkout
    git(upstream, "tag", "v2.0.0")
    monkeypatch.setattr(manager, "release_json", lambda *args: [{"tag_name": "v2.0.0"}])
    with pytest.raises(UpdateError, match="version"):
        manager.check()


def test_older_release_never_downgrades_checkout(release_checkout, monkeypatch):
    manager, local, _, target = release_checkout
    monkeypatch.setattr(manager, "release_json", lambda *args: [{"tag_name": "v1.1.0"}])
    manager.check()
    manager.apply_code(target)
    assert manager.check()["state"] == "current"


@pytest.mark.parametrize("patch", [{"mode": "unsafe"}, {"api_url": "https://user:password@example.test"},
                                   {"repository": "../owner/repo"}, {"check_interval_hours": 0}])
def test_update_configuration_is_validated(release_checkout, patch):
    manager, _, _, _ = release_checkout
    with pytest.raises(UpdateError):
        manager.configure(patch)


def test_backup_uses_sqlite_snapshot_and_keeps_media_outside_archive(release_checkout):
    manager, local, _, _ = release_checkout
    manager.data_root.mkdir(exist_ok=True)
    db_path = manager.data_root / "core.db"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('keep-me')")
        db.commit()
        (manager.data_root / "downloads").mkdir()
        (manager.data_root / "downloads/movie.mp4").write_bytes(b"do not duplicate media")
        (local / "vault/.env").write_text("SECRET_KEY=fixture-secret")
        result = manager.backup()
    archive_path = manager.directory / "backups" / result["name"]
    with zipfile.ZipFile(archive_path) as archive:
        assert "data/core.db" in archive.namelist()
        assert "deployment/vault.env" in archive.namelist()
        assert not any("downloads" in name for name in archive.namelist())
        snapshot = local / "snapshot.db"
        snapshot.write_bytes(archive.read("data/core.db"))
    with sqlite3.connect(snapshot) as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "keep-me"
    assert archive_path.stat().st_mode & 0o077 == 0
    assert manager.backups()[0]["name"] == result["name"]
