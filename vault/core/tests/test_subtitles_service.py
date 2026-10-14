import asyncio

from services.subtitles_service import SubtitlesService


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": [{
                "attributes": {
                    "language": "es",
                    "release": "Obsession.WEB-DL",
                    "format": "srt",
                    "files": [{"file_id": 44}],
                    "ratings": 4.5,
                    "download_count": 1200,
                }
            }]
        }


class FakeClient:
    async def get(self, url, params, headers):
        assert url.endswith("/subtitles")
        assert params["imdb_id"] == "1234567"
        assert headers["Api-Key"] == "key"
        return FakeResponse()


def test_opensubtitles_search_returns_playable_file_reference():
    items = asyncio.run(SubtitlesService()._search_opensubtitles(
        FakeClient(), "Obsession", "tt1234567", None, None, ["es"], "key",
    ))

    assert items == [{
        "provider": "OpenSubtitles",
        "server": "Obsession.WEB-DL",
        "language": "es",
        "file_id": 44,
        "type": "srt",
        "hearing_impaired": False,
        "rating": 4.5,
        "download_count": 1200,
    }]


class FakeYearClient:
    async def get(self, url, params, headers):
        assert url.endswith("/subtitles")
        assert params["query"] == "Obsession"
        assert params["year"] == 2024
        assert headers["Api-Key"] == "key"
        return FakeResponse()


def test_opensubtitles_search_passes_year_for_title_only_queries():
    items = asyncio.run(SubtitlesService()._search_opensubtitles(
        FakeYearClient(), "Obsession", None, None, None, ["es"], "key",
        year=2024,
    ))
    assert items and items[0]["file_id"] == 44
