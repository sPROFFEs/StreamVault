import asyncio
from concurrent.futures import ThreadPoolExecutor

import main
from sqlalchemy import create_engine, text


def test_download_recovery_does_not_block_application_startup(monkeypatch):
    scheduled = []

    class ScheduledTask:
        def add_done_callback(self, callback):
            callback(self)

        def __hash__(self):
            return id(self)

    def schedule(coroutine):
        scheduled.append(coroutine)
        return ScheduledTask()

    monkeypatch.setattr(main.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough")
    monkeypatch.setattr(main.i18n, "load_translations", lambda: None)
    monkeypatch.setattr(main.database, "run_migrations", lambda: None)
    monkeypatch.setattr(main.admin.cloudflared_manager, "load_config", lambda: {})
    monkeypatch.setattr(main.asyncio, "create_task", schedule)

    asyncio.run(main.startup_event())

    try:
        assert [coroutine.cr_code.co_name for coroutine in scheduled] == [
            "_recover_downloads_background", "run_periodic",
        ]
    finally:
        for coroutine in scheduled:
            coroutine.close()


def test_storage_maintenance_yields_before_touching_disk(monkeypatch):
    from unittest.mock import Mock
    scan = Mock()
    monkeypatch.setattr(main.storage_maintenance, 'maintain_cache', scan)
    async def scenario():
        task = asyncio.create_task(main.storage_maintenance.background_loop())
        await asyncio.sleep(0)
        try:
            scan.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_parallel_additive_migrations_preserve_legacy_users(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(main.database, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"))
        conn.execute(text("CREATE TABLE download_jobs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE user_preferences (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE federated_media (peer_id INT, media_type TEXT, tmdb_id INT, season_number INT, episode_number INT)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'legacy')"))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: main.database.run_migrations(), range(2)))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, username, can_download FROM users")).one() == (1, "legacy", 0)
        plan = conn.execute(text("EXPLAIN QUERY PLAN SELECT * FROM federated_media WHERE media_type='tv' AND tmdb_id=7 AND season_number=1 AND episode_number=2")).all()
        assert any("ix_federated_lookup" in row[-1] for row in plan)
    engine.dispose()


def test_periodic_tasks_fail_over_after_the_first_worker_closes(tmp_path):
    from services.periodic_tasks import run_periodic

    async def scenario():
        entered = asyncio.Event()
        successor = asyncio.Event()

        async def work(signal):
            signal.set()
            await asyncio.Event().wait()

        first = asyncio.create_task(run_periodic(tmp_path, lambda: work(entered), retry_seconds=.01))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(run_periodic(tmp_path, lambda: work(successor), retry_seconds=.01))
        try:
            await asyncio.sleep(.04)
            assert not successor.is_set()
            first.cancel()
            await asyncio.wait_for(successor.wait(), 1)
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(scenario())
