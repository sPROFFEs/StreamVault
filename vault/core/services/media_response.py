"""Authenticated local media responses, with optional nginx sendfile delivery."""
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse, Response

from config import settings


class MediaFileResponse(FileResponse):
    chunk_size = 512 * 1024


def media_file_response(path, request=None, *, media_type=None):
    headers = {'Cache-Control': 'private, no-store', 'Accept-Ranges': 'bytes', 'X-Accel-Buffering': 'no'}
    media_type = media_type or mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
    if settings.MEDIA_ACCEL_REDIRECT and request is not None and request.headers.get('x-streamvault-sendfile') == '1':
        relative = Path(path).resolve().relative_to(Path(settings.DOWNLOADS_ROOT).resolve())
        headers['X-Accel-Redirect'] = '/_streamvault_media/downloads/' + quote(relative.as_posix(), safe='/')
        return Response(media_type=media_type, headers=headers)
    return MediaFileResponse(path, media_type=media_type, headers=headers)
