from collections import OrderedDict
from types import SimpleNamespace

import services.tmdb_service as tmdb_module
from services.tmdb_service import TMDBService


class _FakeDiscover:
    def __init__(self):
        self.kwargs = None

    def movie(self, **kwargs):
        self.kwargs = kwargs
        return {"results": [{"id": 1}]}


def test_discover_media_forwards_page(monkeypatch):
    fake = _FakeDiscover()
    monkeypatch.setattr(tmdb_module, "tmdb", SimpleNamespace(Discover=lambda: fake), raising=False)
    service = TMDBService()
    service.enabled = True

    assert service.discover_media("movie", page=3) == [{"id": 1}]
    assert fake.kwargs["page"] == 3


def test_tmdb_memory_cache_evicts_least_used_entry_while_all_entries_are_fresh(monkeypatch):
    monkeypatch.setattr(TMDBService, "_cache", OrderedDict())
    monkeypatch.setattr(TMDBService, "_CACHE_MAX", 2)
    now = 1000.0
    monkeypatch.setattr(tmdb_module, "time", SimpleNamespace(monotonic=lambda: now))

    TMDBService._cache_set(("popular",), [1])
    TMDBService._cache_set(("other",), [2])
    assert TMDBService._cache_get(("popular",)) == [1]
    TMDBService._cache_set(("new",), [])

    assert len(TMDBService._cache) == 2
    assert TMDBService._cache_get(("other",)) is None
    assert TMDBService._cache_get(("popular",)) == [1]
    assert TMDBService._cache_get(("new",)) == []
    now += TMDBService._CACHE_TTL
    assert TMDBService._cache_get(("popular",)) is None
