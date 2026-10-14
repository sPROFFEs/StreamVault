"""Keep every HTTP resource of an HLS playlist on the same-origin proxy."""
import re
from urllib.parse import urlencode, urljoin, urlsplit


MAX_MANIFEST_BYTES = 1024 * 1024
URI_ATTRIBUTE = re.compile(r'(?<![\w-])(URI|SERVER-URI)="([^"]+)"')
PLAYLIST_TAGS = ("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:", "#EXT-X-RENDITION-REPORT:")


def rewrite_hls_manifest(content: str, base_url: str) -> str:
    def proxied(target: str, playlist: bool = False) -> str:
        # Embedded keys do not make network requests and must remain embedded.
        if target.startswith("data:"):
            return target
        absolute = urljoin(base_url, target)
        if urlsplit(absolute).scheme not in ("http", "https"):
            raise ValueError("Unsupported HLS resource URL")
        params = {"url": absolute}
        if playlist:
            params["playlist"] = "true"
        return f"/api/local/proxy-stream?{urlencode(params)}"

    lines = []
    next_is_playlist = False
    for line in content.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            is_playlist = stripped.startswith(PLAYLIST_TAGS)
            line = URI_ATTRIBUTE.sub(lambda match: f'{match[1]}="{proxied(match[2], is_playlist)}"', line)
            if stripped.startswith("#EXT-X-STREAM-INF:"):
                next_is_playlist = True
        elif stripped:
            line = proxied(stripped, next_is_playlist)
            next_is_playlist = False
        lines.append(line)
    return "\n".join(lines) + "\n"
