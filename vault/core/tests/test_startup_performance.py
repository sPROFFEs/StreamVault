import asyncio

import main


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
            "_recover_downloads_background", "_update_checks_background", "background_loop", "background_loop",
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
