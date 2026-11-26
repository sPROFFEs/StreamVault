"""Streaming responses that release resources even before the first body byte."""
import anyio
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect


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
        async def send_chunk(message):
            try:
                await send(message)
            except OSError as exc:
                raise ClientDisconnect() from exc

        failure = None
        try:
            async with anyio.create_task_group() as tasks:
                async def watch_disconnect():
                    await self.listen_for_disconnect(receive)
                    tasks.cancel_scope.cancel()

                # ASGI 2.4 send errors only detect disconnects on a write.
                # An upstream read or FFmpeg pipe can be blocked indefinitely.
                tasks.start_soon(watch_disconnect)
                try:
                    await self.stream_response(send_chunk)
                except Exception as exc:
                    failure = exc
                finally:
                    tasks.cancel_scope.cancel()
            if failure is not None:
                raise failure
            if self.background is not None:
                await self.background()
        finally:
            try:
                await self.body_iterator.aclose()
            finally:
                await self.aclose()
