"""Distribution fixtures exercise the real branch updater without network/Docker."""
import importlib.util
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import textwrap
import zipfile

import pytest

from services.updater_service import UpdateManager


ROOT = Path(__file__).resolve().parents[3]


def release_tools():
    spec = importlib.util.spec_from_file_location("release_tools", ROOT / "vault/scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True,
                          text=True, check=True).stdout.strip()


def test_version_metadata_matches_server():
    import main

    metadata = json.loads((ROOT / "vault/core/version.json").read_text())
    assert main.app.version == metadata["version"]
    assert asyncio.run(main.health_check())["version"] == metadata["version"]
    assert metadata["android_version_code"] > 0


@pytest.mark.parametrize("publish", ["true", "false"])
def test_github_release_attaches_verified_assets_and_supports_draft_preview(tmp_path, publish):
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    script = textwrap.dedent(workflow.rsplit("run: |\n", 1)[1])
    dist = tmp_path / "dist"
    dist.mkdir()
    payload = b"fixture-only-signed-apk"
    (dist / "streamvault-1.0.0-android.apk").write_bytes(payload)
    (dist / "SHA256SUMS").write_text(hashlib.sha256(payload).hexdigest() + "  streamvault-1.0.0-android.apk\n")
    cli = tmp_path / "gh"
    cli.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_TEST_ARGS"\n')
    cli.chmod(0o755)
    arguments = tmp_path / "arguments"
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, check=True, capture_output=True,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "GH_TEST_ARGS": str(arguments), "RELEASE_TAG": "v1.0.0", "PUBLISH_RELEASE": publish})
    flags = arguments.read_text().splitlines()
    assert flags[:3] == ["release", "create", "v1.0.0"]
    assert "--verify-tag" in flags
    assert ("--draft" in flags) == (publish == "false")
    assert "./streamvault-1.0.0-android.apk" in flags
    assert "github.event_name == 'push' || inputs.publish" in workflow


@pytest.mark.parametrize("tag", ["1.0.0", "v1.0.1", "v1.0.0-beta", "v01.0.0"])
def test_mismatched_release_tags_are_rejected(tag):
    with pytest.raises(ValueError):
        release_tools().validate_metadata(ROOT, tag)


def test_release_assets_and_branch_are_compatible_with_updater(tmp_path):
    tools = release_tools()
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "stable")
    git(upstream, "config", "user.email", "fixture@example.test")
    git(upstream, "config", "user.name", "Release fixture")
    (upstream / "vault/core").mkdir(parents=True)
    (upstream / "vault/core/version.json").write_text(json.dumps({"version": "0.9.0", "android_version_code": 1}))
    (upstream / "vault/.env").write_text("SECRET_KEY=must-not-ship\n")
    (upstream / "vault/.env.example").write_text("SECRET_KEY=\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "previous version")
    local = tmp_path / "checkout"
    git(tmp_path, "clone", "--origin", "distribution", str(upstream), str(local))
    (local / "vault/.env").write_text("SECRET_KEY=local-setting\n")
    (upstream / "vault/core/version.json").write_text(json.dumps({"version": "1.0.0", "android_version_code": 2}))
    long_name = "vault/" + "artwork-" * 20 + "ñ.txt"
    (upstream / long_name).write_text("release fixture\n")
    git(upstream, "add", "vault/core/version.json")
    git(upstream, "add", long_name)
    git(upstream, "commit", "-m", "release version")
    git(upstream, "tag", "v1.0.0")
    output = tmp_path / "assets"
    apk = tmp_path / "fixture.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"fixture manifest")
        archive.writestr("classes.dex", b"fixture-only-dex")
    manifest = tools.package(upstream, output, "fixture/StreamVault", "stable", tag="v1.0.0", apk=apk)
    assert manifest["tag"] == "v1.0.0"
    assert manifest["update"]["branch"] == "stable"
    assert manifest["commit"] == git(upstream, "rev-parse", "v1.0.0^{commit}")
    assert json.loads((output / "release.json").read_text()) == manifest
    assert (output / "streamvault-1.0.0-android.apk").read_bytes() == apk.read_bytes()
    for entry in (output / "SHA256SUMS").read_text().splitlines():
        digest, name = entry.split("  ")
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    with tarfile.open(output / "streamvault-1.0.0-web.tar.gz") as archive:
        names = archive.getnames()
        assert "streamvault-1.0.0/vault/core/version.json" in names
        assert "streamvault-1.0.0/vault/.env.example" in names
        assert "streamvault-1.0.0/vault/.env" not in names
        assert "streamvault-1.0.0/" + long_name in names
    first = (output / "streamvault-1.0.0-web.tar.gz").read_bytes()
    tools.package(upstream, tmp_path / "repeat", "fixture/StreamVault", "stable", tag="v1.0.0")
    assert first == (tmp_path / "repeat/streamvault-1.0.0-web.tar.gz").read_bytes()
    manager = UpdateManager(local, tmp_path / "data")
    assert manager.check()["remote_commit"] == manifest["commit"]
    manager.apply_code(manifest["commit"])
    assert manager.check()["state"] == "current"
    assert (local / "vault/.env").read_text() == "SECRET_KEY=local-setting\n"


def test_signing_and_data_files_never_enter_source_assets():
    safe = release_tools().public_source
    for path in ("vault/.env", "vault/data/users.db", "vault/core/cache.sqlite3",
                 "android/key.jks", "android/signing.properties", "confs/private.conf",
                 "vault/core/__pycache__/main.pyc"):
        assert not safe(path), path
    assert safe("vault/.env.example")
    assert safe("android/app/src/main/AndroidManifest.xml")
