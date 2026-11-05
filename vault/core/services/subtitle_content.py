"""Bound subtitle transfers and archive expansion before they enter RAM."""
import asyncio
import gzip
import io
import zipfile

from services.media_library import srt_to_vtt

MAX_SUBTITLE_BYTES = 8 * 1024**2


class SubtitleTooLarge(ValueError):
    pass


class SubtitleDownloadError(ValueError):
    pass


def subtitle_bytes_to_vtt(content: bytes, content_type: str = '') -> str:
    if len(content) > MAX_SUBTITLE_BYTES:
        raise SubtitleTooLarge('Subtitle file is too large')
    try:
        if content.startswith(b'PK') or 'zip' in content_type and 'gzip' not in content_type:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                candidates = [entry for entry in archive.infolist() if entry.filename.lower().endswith(('.srt', '.vtt'))]
                if not candidates:
                    raise ValueError('Archive contains no supported subtitle')
                if candidates[0].file_size > MAX_SUBTITLE_BYTES:
                    raise SubtitleTooLarge('Expanded subtitle file is too large')
                with archive.open(candidates[0]) as source:
                    content = source.read(MAX_SUBTITLE_BYTES + 1)
        elif content.startswith(b'\x1f\x8b') or 'gzip' in content_type:
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as source:
                content = source.read(MAX_SUBTITLE_BYTES + 1)
    except (zipfile.BadZipFile, OSError, EOFError) as error:
        raise ValueError('Invalid subtitle archive') from error
    if len(content) > MAX_SUBTITLE_BYTES:
        raise SubtitleTooLarge('Expanded subtitle file is too large')
    text = content.decode('utf-8-sig', errors='replace')
    text = text if text.lstrip().startswith('WEBVTT') else srt_to_vtt(text)
    if len(text.encode('utf-8')) > MAX_SUBTITLE_BYTES:
        raise SubtitleTooLarge('Converted subtitle file is too large')
    return text


async def download_subtitle(client, url) -> tuple[bytes, str]:
    async with asyncio.timeout(20):
        async with client.stream('GET', url) as response:
            if not response.is_success:
                raise SubtitleDownloadError('Subtitle provider download failed')
            try:
                declared = int(response.headers.get('content-length', '0'))
            except ValueError:
                declared = 0
            if declared > MAX_SUBTITLE_BYTES:
                raise SubtitleTooLarge('Subtitle file is too large')
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > MAX_SUBTITLE_BYTES:
                    raise SubtitleTooLarge('Subtitle file is too large')
                content.extend(chunk)
            return bytes(content), response.headers.get('content-type', '')
