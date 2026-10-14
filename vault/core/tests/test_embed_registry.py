import pytest

from providers.embed_registry import build_embed_streams, normalize_source_preferences


def test_movie_providers_follow_preferred_personal_order():
    streams = build_embed_streams(
        tmdb_id="550",
        media_type="movie",
        preferred_source="vidlink",
        source_order=["vidrock", "videasy", "vidlink", "vidfast"],
    )

    assert [stream["provider_id"] for stream in streams] == [
        "vidlink",
        "vidrock",
        "videasy",
        "vidfast",
    ]
    assert streams[0]["url"] == "https://vidlink.pro/movie/550"
    assert all(stream["type"] == "embed" for stream in streams)
    assert all(stream["download_mode"] == "sniff" for stream in streams)


def test_tv_provider_urls_include_season_and_episode():
    streams = build_embed_streams(
        tmdb_id="1399",
        media_type="tv",
        season=2,
        episode=3,
    )

    assert streams[0]["url"] == "https://player.videasy.to/tv/1399/2/3"
    assert streams[1]["url"] == "https://vidlink.pro/tv/1399/2/3"


def test_tv_sources_require_season_and_episode():
    with pytest.raises(ValueError, match="season and episode"):
        build_embed_streams(tmdb_id="1399", media_type="tv", season=1)


def test_unknown_preference_is_ignored_without_losing_sources():
    streams = build_embed_streams(
        tmdb_id="550",
        media_type="movie",
        preferred_source="removed-provider",
        source_order=["removed-provider"],
    )

    assert [stream["provider_id"] for stream in streams] == [
        "videasy",
        "vidlink",
        "vidfast",
        "vidrock",
    ]


def test_removed_provider_preferences_are_migrated_to_registry_defaults():
    preferred, order = normalize_source_preferences(
        "vidsrc",
        ["vidsrc", "vidrock", "missing"],
    )

    assert preferred == "videasy"
    assert order == ["vidrock", "videasy", "vidlink", "vidfast"]
