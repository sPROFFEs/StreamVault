import asyncio
from pathlib import Path

from services.iptv_service import load_sources_concurrently
from services.iptv_service import IPTVService, PUBLIC_M3U_SOURCES


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_channel_limit_accepts_frontend_batch_size():
    router = (PROJECT_ROOT / "core/routers/iptv.py").read_text()

    assert "limit: int = Query(100, le=5000)" in router


def test_worldwide_catalog_is_the_default_public_source():
    router = (PROJECT_ROOT / "core/routers/iptv.py").read_text()
    service = (PROJECT_ROOT / "core/services/iptv_service.py").read_text()

    assert 'DEFAULT_SOURCE_KEYS = ["iptv-org", "tdtchannels", "free-tv"]' in router
    assert '"url": "https://iptv-org.github.io/iptv/index.m3u"' in service


def test_complementary_public_lists_are_configured():
    assert PUBLIC_M3U_SOURCES["tdtchannels"]["url"] == "https://www.tdtchannels.com/lists/tv.m3u8"
    assert PUBLIC_M3U_SOURCES["free-tv"]["url"] == "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"


def test_m3u_names_do_not_include_commas_from_quoted_metadata():
    service = IPTVService()
    try:
        channels = service._parse_m3u('''#EXTM3U
#EXTINF:-1 tvg-language="English,Spanish" tvg-country="US,ES" group-title="News, International",News "24", live
https://example.com/live.m3u8
''', "test")
        assert channels[0]["name"] == 'News "24", live'
        assert channels[0]["languages"] == ["English", "Spanish"]
        assert channels[0]["countries"] == ["US", "ES"]
    finally:
        asyncio.run(service.close())


def test_merge_public_lists_keeps_ids_and_deduplicates_identical_streams(monkeypatch):
    from routers import iptv
    existing = {"id": "old-id", "name": "News", "stream_url": "https://example.com/live.m3u8", "source": "iptv-org"}
    monkeypatch.setattr(iptv, "channels_cache", [existing])
    imported = iptv._merge_channels([
        {**existing, "id": "new-id", "source": "free-tv"},
        {**existing, "id": "alternative", "source": "free-tv", "stream_url": "https://example.com/other.m3u8"},
    ])
    assert imported == 1
    assert [channel["id"] for channel in iptv.channels_cache] == ["old-id", "alternative"]
    assert existing["sources"] == ["iptv-org", "free-tv"]


def test_simultaneous_page_loads_fetch_each_default_source_once(monkeypatch):
    from routers import iptv
    calls = []
    monkeypatch.setattr(iptv, "channels_cache", [])

    async def scenario():
        monkeypatch.setattr(iptv, "_bootstrap_lock", asyncio.Lock())
        async def load(source_key):
            calls.append(source_key)
            await asyncio.sleep(.01)
            imported = iptv._merge_channels([{"id": source_key, "name": source_key, "source": source_key, "stream_url": f"https://example.com/{source_key}.m3u8"}])
            return {"imported": imported}
        monkeypatch.setattr(iptv, "_load_public_source", load)
        results = await asyncio.gather(iptv.bootstrap_sources(), iptv.bootstrap_sources())
        assert all(result["success"] for result in results)
        assert [result["imported"] for result in results] == [3, 0]
    asyncio.run(scenario())
    assert sorted(calls) == sorted(iptv.DEFAULT_SOURCE_KEYS)


def test_bootstrap_recognizes_duplicate_source_aliases(monkeypatch):
    from routers import iptv
    monkeypatch.setattr(iptv, "channels_cache", [{"id": "one", "source": "iptv-org", "sources": ["iptv-org", "tdtchannels", "free-tv"]}])
    async def unexpected_load(source_key):
        raise AssertionError(f"Already imported source fetched again: {source_key}")
    monkeypatch.setattr(iptv, "_load_public_source", unexpected_load)
    result = asyncio.run(iptv.bootstrap_sources())
    assert result["imported"] == 0
    assert result["sources_loaded"] == []


def test_frontend_loads_every_channel_page():
    script = (PROJECT_ROOT / "assets/js/iptv.js").read_text()

    assert 'params.set("offset", String(offset))' in script
    assert "while (offset < total)" in script


def test_public_sources_bootstrap_concurrently(monkeypatch):
    started = []

    async def fake_load(source_key):
        started.append(source_key)
        await asyncio.sleep(0.01)
        return {"source": source_key, "imported": 2, "total_in_cache": 4}

    result = asyncio.run(load_sources_concurrently(["one", "two"], fake_load, timeout_s=1))

    assert set(started) == {"one", "two"}
    assert result["imported"] == 4
    assert set(result["sources_loaded"]) == {"one", "two"}
    assert result["errors"] == []


def test_public_sources_bootstrap_reports_timeout(monkeypatch):
    async def slow_load(source_key):
        await asyncio.sleep(0.05)
        return {"source": source_key, "imported": 1, "total_in_cache": 1}

    result = asyncio.run(load_sources_concurrently(["slow"], slow_load, timeout_s=0.001))

    assert result["success"] is False
    assert result["errors"] == [{"source": "slow", "error": "timeout"}]
