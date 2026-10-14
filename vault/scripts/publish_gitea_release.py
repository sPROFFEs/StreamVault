#!/usr/bin/env python3
"""Explicitly create a Gitea draft from verified release.py artifacts.

No import side effects, no overwrite/delete of existing releases, no automatic
publication. Credentials are read from the environment, never command arguments.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def api(url, token, *, body=None, file=None):
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if file is not None:
        boundary = "StreamVault" + uuid.uuid4().hex
        prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; filename="{file.name}"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n').encode()
        content = prefix + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    else:
        content = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(Request(url, data=content, headers=headers, method="POST"), timeout=120) as response:
            payload = response.read(2 * 1024**2 + 1)
        if len(payload) > 2 * 1024**2:
            raise ValueError("Oversized forge response")
        return json.loads(payload)
    except Exception as exc:
        # urllib errors may contain credential-bearing URLs/server messages.
        raise RuntimeError("Gitea request failed. Check permissions, connectivity and whether the release already exists.") from exc


def publish(server, repository, token, folder):
    parsed = urlsplit(server)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not token or any(ord(c) < 32 for c in token)):
        raise ValueError("Use a forge URL without credentials and set GITEA_RELEASE_TOKEN.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(p in {".", ".."} for p in repository.split("/")):
        raise ValueError("Repository must be OWNER/REPOSITORY.")
    folder = Path(folder)
    manifest = json.loads((folder / "release.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version) or manifest["tag"] != "v" + version:
        raise ValueError("Invalid stable release metadata.")
    if manifest["repository"] != repository or not re.fullmatch(r"[a-f0-9]{40,64}", manifest["commit"]):
        raise ValueError("Release metadata does not match the target repository/commit.")
    files = []
    for asset in manifest["assets"]:
        name = asset["name"]
        if not re.fullmatch(r"streamvault-" + re.escape(version) + r"-(web\.tar\.gz|android\.apk)", name):
            raise ValueError("Unexpected release asset.")
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024**2:
            raise ValueError("Invalid or oversized release asset.")
        if path.stat().st_size != asset["size"] or digest(path) != asset["sha256"]:
            raise ValueError("Release asset size/checksum mismatch.")
        files.append(path)
    files.extend([folder / "release.json", folder / "SHA256SUMS"])
    checksums = dict(line.split("  ", 1)[::-1] for line in (folder / "SHA256SUMS").read_text().splitlines())
    if any(checksums.get(path.name) != digest(path) for path in files if path.name != "SHA256SUMS"):
        raise ValueError("Release checksum manifest mismatch.")
    base = server.rstrip("/") + "/api/v1/repos/" + repository + "/releases"
    release = api(base, token, body={"tag_name": manifest["tag"], "name": "StreamVault " + version, "draft": True,
        "prerelease": False, "body": f"Reviewed snapshot {manifest['commit']}.\nVerify the attached SHA256SUMS before installing."})
    release_id = release.get("id")
    if type(release_id) is not int or release_id <= 0:
        raise RuntimeError("Gitea did not return a valid draft identifier.")
    for path in files:
        api(f"{base}/{release_id}/assets?name={quote(path.name)}", token, file=path)
    return {"release_id": release_id, "tag": manifest["tag"], "draft": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--assets", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.server, args.repository, os.environ.get("GITEA_RELEASE_TOKEN", ""), args.assets)))
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
