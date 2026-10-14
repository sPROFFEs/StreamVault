from services.user_library_service import UserLibraryService


def _movie_progress(seconds: float, duration: float = 6500) -> dict:
    return {
        "tmdb_id": 123,
        "media_type": "movie",
        "title": "Obsession",
        "progress_seconds": seconds,
        "duration_seconds": duration,
        "completed": False,
    }


def test_movie_progress_updates_one_continue_watching_row(tmp_path):
    service = UserLibraryService(str(tmp_path))

    service.upsert_progress("viewer", _movie_progress(10))
    service.upsert_progress("viewer", _movie_progress(20))

    items = service.list_continue_watching("viewer")
    assert len(items) == 1
    assert items[0]["progress_seconds"] == 20
    assert items[0]["season_number"] is None


def test_existing_duplicate_movie_progress_is_deduplicated(tmp_path):
    service = UserLibraryService(str(tmp_path))
    with service._connection("viewer") as connection:
        for progress in (10, 30, 20):
            connection.execute(
                """INSERT INTO history
                   (tmdb_id, media_type, title, season_number, episode_number, progress_seconds)
                   VALUES (123, 'movie', 'Obsession', NULL, NULL, ?)""",
                (progress,),
            )

    service.upsert_progress("viewer", _movie_progress(40))

    items = service.list_continue_watching("viewer")
    assert len(items) == 1
    assert items[0]["progress_seconds"] == 40
