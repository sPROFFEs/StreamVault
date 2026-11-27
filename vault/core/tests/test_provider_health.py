import asyncio

import httpx

from providers.sources.torrentio import TorrentioProvider


def test_torrentio_coalesces_requests_and_respects_retry_after():
    calls = []
    now = [0]

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0)
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "45"})
        return httpx.Response(200, json={"streams": [{"infoHash": "a" * 40, "name": "1080p"}]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = TorrentioProvider(client=client, clock=lambda: now[0])
            first = await asyncio.gather(*(provider.get_streams("7", imdb_id="tt1234567") for _ in range(4)))
            assert all(items and items == first[0] for items in first)
            assert len(calls) == 1
            assert not await provider.get_streams("8", imdb_id="tt2345678")
            now[0] = 44
            assert not await provider.get_streams("9", imdb_id="tt3456789")
            assert len(calls) == 2
            now[0] = 46
            assert await provider.get_streams("9", imdb_id="tt3456789")

    asyncio.run(scenario())
