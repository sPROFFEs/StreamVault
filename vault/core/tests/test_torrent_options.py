from providers.torrent_options import prepare_torrent_options


def test_torrent_options_rank_by_seeders_and_label_health():
    options = prepare_torrent_options([
        {"type": "torrent", "seeders": 3, "size_bytes": 2_000, "title": "weak"},
        {"type": "torrent", "seeders": 48, "size_bytes": 4_000, "title": "fast"},
        {"type": "torrent", "seeders": 12, "size_bytes": 3_000, "title": "okay"},
    ])

    assert [item["title"] for item in options] == ["fast", "okay", "weak"]
    assert [item["health"] for item in options] == ["healthy", "limited", "weak"]


def test_torrent_options_drop_non_torrents_and_limit_results():
    streams = [
        {"type": "torrent", "seeders": i, "title": str(i)}
        for i in range(20)
    ]
    streams.append({"type": "embed", "seeders": 999, "title": "not a download"})

    options = prepare_torrent_options(streams, limit=5)

    assert len(options) == 5
    assert [item["seeders"] for item in options] == [19, 18, 17, 16, 15]


def test_missing_seeders_are_reported_as_unknown_not_fast():
    [option] = prepare_torrent_options([
        {"type": "torrent", "seeders": 0, "title": "unknown"},
    ])

    assert option["health"] == "unknown"
