"""Streaming responses that release resources even before the first body byte."""
import anyio
from fastapi.responses import StreamingResponse


class ClosingStreamingResponse(StreamingResponse):
    def __init__(self, content, *, cleanup, **kwargs):
        self._cleanup = cleanup
        self._closed = False
        super().__init__(self._iterate(content), **kwargs)

    async def _iterate(self, content):
        try:
            async for chunk in content:
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self):
        if not self._closed:
            self._closed = True
            with anyio.CancelScope(shield=True):
                await self._cleanup()

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()
            await self.aclose()
