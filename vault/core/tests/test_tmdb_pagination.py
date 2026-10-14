import services.tmdb_service as tmdb_module
from services.tmdb_service import TMDBService
from types import SimpleNamespace


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
