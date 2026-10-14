import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from routers.media_local import proxy_stream


@pytest.mark.parametrize("content_type", ["application/vnd.apple.mpegurl", "application/octet-stream"])
def test_hls_proxy_rewrites_playlists_segments_audio_subtitles_and_keys(content_type):
    async def scenario():
        manifest = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="audio/index.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",URI="//subtitles.example/en/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1000000
video/main
#EXT-X-KEY:METHOD=AES-128,URI="../key.bin?token=a&b=2"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6,
segment.ts
https://cdn.example/absolute.ts?token=abc
'''
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"Content-Type": content_type, "ETag": '"old"'}, content=manifest)))
        request = Request({"type": "http", "headers": []})
        with patch("routers.media_local.httpx.AsyncClient", return_value=client), patch("routers.media_local.is_safe_external_url", return_value=True):
            response = await proxy_stream("https://origin.example/live/master.m3u8", request)
        from urllib.parse import parse_qs, urlsplit
        import re
        body = response.body.decode()
        urls = [line for line in body.splitlines() if line and not line.startswith("#")]
        urls += re.findall(r'URI="([^"]+)"', body)
        assert len(urls) == 7
        assert all(url.startswith("/api/local/proxy-stream?") for url in urls)
        targets = [parse_qs(urlsplit(url).query)["url"][0] for url in urls]
        assert "https://origin.example/live/video/main" in targets
        assert "https://origin.example/key.bin?token=a&b=2" in targets
        assert "https://subtitles.example/en/index.m3u8" in targets
        assert "https://cdn.example/absolute.ts?token=abc" in targets
        variant = next(url for url in urls if "video%2Fmain" in url)
        assert parse_qs(urlsplit(variant).query)["playlist"] == ["true"]
        assert response.headers["content-type"].startswith("application/vnd.apple.mpegurl")
        assert "etag" not in response.headers
        assert int(response.headers["content-length"]) == len(response.body)
        assert client.is_closed
    asyncio.run(scenario())


def test_hls_proxy_uses_the_final_redirect_url_and_validates_each_hop():
    async def scenario():
        visited = []
        def upstream(request):
            visited.append(str(request.url))
            if request.url.host == "origin.example":
                return httpx.Response(302, headers={"Location": "https://cdn.example/new/playlist.m3u8"})
            return httpx.Response(200, content="#EXTM3U\n#EXTINF:6,\nsegment.ts\n")
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False)
        with patch("routers.media_local.httpx.AsyncClient", return_value=client), patch("routers.media_local.is_safe_external_url", return_value=True) as safe:
            response = await proxy_stream("https://origin.example/start.m3u8", Request({"type": "http", "headers": []}))
        assert "https%3A%2F%2Fcdn.example%2Fnew%2Fsegment.ts" in response.body.decode()
        assert safe.call_count == 2
        assert len(visited) == 2
    asyncio.run(scenario())


def test_proxy_refuses_redirects_to_private_targets():
    async def scenario():
        calls = []
        def upstream(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False)
        with patch("routers.media_local.httpx.AsyncClient", return_value=client), patch("routers.media_local.is_safe_external_url", side_effect=[True, False]):
            with pytest.raises(HTTPException) as error:
                await proxy_stream("https://origin.example/playlist.m3u8", Request({"type": "http", "headers": []}))
        assert error.value.status_code == 400
        assert len(calls) == 1
        assert client.is_closed
    asyncio.run(scenario())


def test_proxy_caps_manifest_memory_and_releases_connections():
    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"#EXTM3U\n" + b"a" * (1024 * 1024))))
        with patch("routers.media_local.httpx.AsyncClient", return_value=client), patch("routers.media_local.is_safe_external_url", return_value=True):
            with pytest.raises(HTTPException) as error:
                await proxy_stream("https://origin.example/playlist.m3u8", Request({"type": "http", "headers": []}))
        assert error.value.status_code == 502
        assert client.is_closed
    asyncio.run(scenario())


def test_proxy_stream_forwards_ranges_without_buffering_or_fetching_twice():
    async def scenario():
        read = False
        requests = []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal read
                read = True
                yield b"3456"

        def upstream(request):
            requests.append(request)
            return httpx.Response(206, headers={"Content-Range": "bytes 3-6/10", "Content-Length": "4"}, stream=Body())

        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        request = Request({"type": "http", "headers": [(b"range", b"bytes=3-6")]})
        with patch("routers.media_local.httpx.AsyncClient", return_value=client):
            response = await proxy_stream("https://example.com/video.mp4", request)
            assert not read
            assert response.status_code == 206
            assert response.headers["content-range"] == "bytes 3-6/10"
            assert b"".join([chunk async for chunk in response.body_iterator]) == b"3456"
        assert len(requests) == 1
        assert requests[0].headers["range"] == "bytes=3-6"
        assert client.is_closed
    asyncio.run(scenario())
