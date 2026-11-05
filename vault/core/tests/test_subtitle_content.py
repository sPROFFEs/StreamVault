import gzip
import io
import zipfile

import httpx
import pytest

from services import subtitle_content


@pytest.mark.parametrize('packing', ['gzip', 'zip', 'plain'])
def test_subtitle_decoding_bounds_decompressed_size(monkeypatch, packing):
    monkeypatch.setattr(subtitle_content, 'MAX_SUBTITLE_BYTES', 1024)
    raw = b'WEBVTT\n\n' + b'x' * 2048
    if packing == 'gzip':
        content = gzip.compress(raw)
    elif packing == 'zip':
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('video.srt', raw)
        content = buffer.getvalue()
    else:
        content = raw
    with pytest.raises(subtitle_content.SubtitleTooLarge):
        subtitle_content.subtitle_bytes_to_vtt(content)


def test_valid_compressed_subtitles_are_converted_to_vtt():
    raw = b'1\n00:00:01,000 --> 00:00:02,000\nHello\n'
    result = subtitle_content.subtitle_bytes_to_vtt(gzip.compress(raw))
    assert result.startswith('WEBVTT')
    assert '00:00:01.000 --> 00:00:02.000' in result


def test_download_limits_chunked_response_before_collecting_its_body(monkeypatch):
    import asyncio
    monkeypatch.setattr(subtitle_content, 'MAX_SUBTITLE_BYTES', 1024)
    read = []
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                read.append(index)
                yield b'x' * 700
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Chunks()))) as client:
            with pytest.raises(subtitle_content.SubtitleTooLarge):
                await subtitle_content.download_subtitle(client, 'https://subtitles.example/video.vtt')
    asyncio.run(scenario())
    assert read == [0, 1]
