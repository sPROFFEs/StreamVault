"""Public Git and release assets exclude local state, even when force-added."""
import re
import subprocess
import zipfile

import pytest

from test_release_contract import ROOT, release_tools


PRIVATE_PATHS = (
    "vault/.env", "vault/.env.production", "vault/core/.env.local",
    "vault/data/config/updater/backups/snapshot.zip", "vault/downloads/video.mkv",
    "vault/core/cache.sqlite3", "vault/core/cache.db-wal", "vault/core/cache.sqlite-shm",
    "vault/core/server.log", "vault/core/secrets/token.json", "vault/core/.cache/items.json",
    "android/release.pfx", "android/release.jks", "android/signing.properties",
    "android/local.properties", "android/app/build/outputs/apk/release/app-release.apk",
    "confs/private.conf", "tmp_cookies2.txt", ".mcp.json", "opencode.json",
    "AGENTS.md", "PLAN.md", "docs/FINAL_AUDIT.md", "docs/CLEANUP.md",
    "docs/VERIFICATION.md", "android/VERIFICATION.md",
    "vault/core/tests/performance-results.json", "vault/core/tests/PERFORMANCE.md",
    "vault/core/private.pem", "vault/core/dump.sql", "vault/core/season.torrent",
    "vault/core/season.aria2", "vault/core/episode.mp4", "vault/core/credentials.json",
)


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_local_and_sensitive_files_are_ignored(path):
    result = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT)
    assert result.returncode == 0, path


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_release_excludes_private_files_even_if_tracked(path):
    assert not release_tools().public_source(path), path


@pytest.mark.parametrize("path", (
    "vault/.env.example", "confs/wireguard.conf.example", ".github/workflows/release.yml",
    "vault/core/requirements.lock", "android/gradle/wrapper/gradle-wrapper.jar",
    "vault/core/templates/admin.html", "vault/assets/icon.png", "docs/RELEASES.md",
    "vault/core/.dockerignore",
))
def test_documentation_examples_and_build_inputs_remain_addable(path):
    assert subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT).returncode == 1


def test_docker_context_allows_only_inputs_copied_by_the_images():
    rules = (ROOT / "vault/core/.dockerignore").read_text().splitlines()
    assert rules[0] == "*"
    for path in ("!Dockerfile*", "!requirements*.lock", "!entrypoint.sh", "!services/",
                 "services/*", "!services/cloudflared_service.py", "!services/cloudflared_worker.py"):
        assert path in rules


def test_release_checks_and_signs_the_validated_commit_not_a_mutable_tag():
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "commit: ${{ steps.version.outputs.commit }}" in workflow
    assert 'test "$RELEASE_COMMIT" = "$(git rev-parse HEAD)"' in workflow
    assert workflow.count("ref: ${{ needs.contract.outputs.commit }}") == 2
    assert "--draft" in workflow and "--verify-tag" in workflow
    assert "environment: release" in workflow
    assert "unset KEYSTORE_BASE64" in workflow
    assert workflow.count("contents: write") == 1
    assert "pull_request_target" not in workflow
    for action in re.findall(r"uses: ([^\s]+)", workflow):
        assert action.startswith("./") or re.search(r"@[a-f0-9]{40}$", action), action


def test_github_checks_reject_secrets_in_the_history_without_signing_secrets():
    workflow = (ROOT / ".github/workflows/build.yml").read_text()
    assert 'gitleaks" git --redact' in workflow
    assert "--log-opts=HEAD" in workflow
    assert "sha256sum --check" in workflow
    assert "secrets." not in workflow


def test_release_output_cannot_reuse_a_directory_containing_private_files(tmp_path):
    (tmp_path / "release.jks").write_bytes(b"private fixture")
    with pytest.raises(ValueError, match="empty"):
        release_tools().package(ROOT, tmp_path, "fixture/StreamVault", "main")


def test_ignored_local_files_are_not_tracked():
    result = subprocess.check_output(
        ["git", "ls-files", "--cached", "--ignored", "--exclude-standard"], cwd=ROOT, text=True)
    assert not result, "Ignored local files must also be removed from the Git index: " + result


def test_release_apk_disables_checkout_metadata_and_has_no_default_server():
    gradle = (ROOT / "android/app/build.gradle").read_text()
    assert "vcsInfo.include = false" in gradle
    assert "gradleProperty('STREAMVAULT_SERVER_URL').getOrElse('')" in gradle


def test_android_connection_error_offers_same_origin_device_library():
    import xml.etree.ElementTree as ET
    namespace = '{http://schemas.android.com/apk/res/android}'
    layout = ET.parse(ROOT / 'android/app/src/main/res/layout/activity_main.xml')
    buttons = {item.get(namespace + 'id') for item in layout.iter('Button')}
    assert '@+id/offline_button' in buttons
    activity = (ROOT / 'android/app/src/main/java/com/streamvault/app/MainActivity.java').read_text()
    assert 'findViewById(R.id.offline_button).setOnClickListener' in activity
    # Use the installed alias so an older SW cache cannot replace the new
    # offline screen before its first online visit. Media stays in the same IDB.
    assert 'webView.loadUrl(server + "/_streamvault/offline")' in activity
    frontend = (ROOT / 'android/app/src/main/java/com/streamvault/app/MobileFrontend.java').read_text()
    assert 'path.equals("/_streamvault/offline")' in frontend
    assert 'return asset("assets/offline.html")' in frontend
    assert 'addJavascriptInterface' not in activity


@pytest.mark.parametrize("private_entry", (
    "assets/.env", "assets/private.jks", "assets/database.db",
    "assets/signing.properties", "META-INF/version-control-info.textproto",
))
def test_packaging_rejects_apks_containing_private_material(tmp_path, private_entry):
    apk = tmp_path / "private.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"fixture manifest")
        archive.writestr("classes.dex", b"fixture dex")
        archive.writestr(private_entry, b"fixture private material")
    with pytest.raises(ValueError, match="private"):
        release_tools().validate_apk(apk)


@pytest.mark.parametrize("filename", ("build.yml", "release.yml"))
def test_sdk_setup_does_not_request_the_removed_legacy_tools_package(filename):
    workflow = (ROOT / ".github/workflows" / filename).read_text()
    setup = workflow.split("- uses: android-actions/setup-android@", 1)[1].split("- ", 1)[0]
    assert "packages: ''" in setup
    assert "sdkmanager 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'" in workflow
