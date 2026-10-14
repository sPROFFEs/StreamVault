#!/usr/bin/env python3
"""Validate and package public release assets; never publish or update a checkout."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True).stdout


def validate_metadata(root, tag=None):
    metadata = json.loads((Path(root) / "vault/core/version.json").read_text())
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", metadata["version"]):
        raise ValueError("version must be a stable MAJOR.MINOR.PATCH version")
    code = metadata["android_version_code"]
    if type(code) is not int or not 1 <= code <= 2100000000:
        raise ValueError("android_version_code must be a positive Android version code")
    if tag is not None and tag != "v" + metadata["version"]:
        raise ValueError("release tag must equal v plus vault/core/version.json version")
    return metadata


def public_source(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or ".." in path.parts
            or path.parts[0] not in {"vault", "android", "docs", ".github", ".gitea", "README.md", ".gitignore"}):
        return False
    if any(part in {"data", "downloads", "backups", "secrets", "build", ".gradle", "__pycache__",
                    ".cache", ".git", ".idea", ".vscode", "node_modules", "dist", "output",
                    "test-results", "playwright-report"} for part in path.parts):
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    return (path.suffix.lower() not in {
                ".db", ".sqlite", ".sqlite3", ".pyc", ".jks", ".keystore", ".p12", ".pem", ".key", ".pfx",
                ".sql", ".log", ".pid", ".torrent", ".aria2", ".part", ".apk", ".aab",
                ".mkv", ".mp4", ".avi", ".mov", ".webm", ".ts"}
            and not re.search(r"\.(?:db|sqlite3?)-(?:wal|shm|journal)$", path.name)
            and path.name not in {"local.properties", "signing.properties", "credentials.json", "secrets.json",
                                  "cookies.txt", "FINAL_AUDIT.md", "CLEANUP.md", "VERIFICATION.md",
                                  "PERFORMANCE.md", "performance-results.json"})


def validate_apk(path):
    """Check distribution hygiene; cryptographic signing is verified by apksigner."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not {"AndroidManifest.xml", "classes.dex"}.issubset(names):
                raise ValueError("APK must contain an Android manifest and compiled code")
            for name in names:
                entry = PurePosixPath(name)
                if (entry.is_absolute() or ".." in entry.parts or entry.name.startswith(".env")
                        or entry.suffix.lower() in {".jks", ".keystore", ".p12", ".pfx", ".pem", ".key",
                                                  ".db", ".sqlite", ".sqlite3"}
                        or entry.name in {"signing.properties", "local.properties", "credentials.json",
                                          "secrets.json", "version-control-info.textproto"}):
                    raise ValueError("APK contains private files or checkout metadata")
    except zipfile.BadZipFile as error:
        raise ValueError("APK must be a valid ZIP package") from error


def package(root, output, repository, branch, *, tag=None, apk=None):
    root, output = Path(root), Path(output)
    # Workflows upload the whole output directory. Never silently include stale
    # APKs, signing material or local files from an earlier/manual packaging run.
    if output.exists() and any(output.iterdir()):
        raise ValueError("release output directory must be empty")
    if apk:
        validate_apk(apk)
    metadata = validate_metadata(root, tag)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be OWNER/REPOSITORY")
    git(root, "check-ref-format", "refs/heads/" + branch)
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    if tag and git(root, "rev-parse", tag + "^{commit}").decode().strip() != commit:
        raise ValueError("release tag must point to the checked out commit")
    committed_metadata = json.loads(git(root, "show", "HEAD:vault/core/version.json"))
    if metadata != committed_metadata:
        raise ValueError("version metadata must be committed before packaging")
    output.mkdir(parents=True, exist_ok=True)
    version = metadata["version"]
    source = output / f"streamvault-{version}-web.tar.gz"
    snapshot = git(root, "archive", "--format=tar", "HEAD")
    # Fixed gzip headers and Git's commit timestamps make repeat archives identical.
    with source.open("wb") as target, gzip.GzipFile(fileobj=target, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            with tarfile.open(fileobj=io.BytesIO(snapshot), mode="r:") as original:
                for member in original:
                    if not public_source(member.name) or not (member.isfile() or member.isdir()):
                        continue
                    payload = original.extractfile(member) if member.isfile() else None
                    member.name = f"streamvault-{version}/" + member.name
                    # Git PAX records can otherwise overwrite the prefixed name.
                    member.pax_headers.pop("path", None)
                    archive.addfile(member, payload)
    assets = [source]
    if apk:
        destination = output / f"streamvault-{version}-android.apk"
        shutil.copyfile(apk, destination)
        assets.append(destination)
    manifest = {"schema_version": 1, **metadata, "tag": "v" + version,
                "repository": repository, "commit": commit,
                "update": {"strategy": "git-fast-forward", "branch": branch},
                "assets": [{"name": asset.name, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                            "size": asset.stat().st_size} for asset in assets]}
    manifest_path = output / "release.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    assets.append(manifest_path)
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n" for asset in assets))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--tag")
    check.add_argument("--apk", type=Path)
    build = subparsers.add_parser("package")
    build.add_argument("--tag")
    build.add_argument("--repository", required=True)
    build.add_argument("--branch", required=True)
    build.add_argument("--output", type=Path, default=ROOT / "dist")
    build.add_argument("--apk", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        if args.apk:
            validate_apk(args.apk)
        print(json.dumps(validate_metadata(ROOT, args.tag)))
    else:
        print(json.dumps(package(ROOT, args.output, args.repository, args.branch, tag=args.tag, apk=args.apk)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Release validation failed: {error}")
