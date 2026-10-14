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
            "_recover_downloads_background", "_update_checks_background", "background_loop",
        ]
    finally:
        for coroutine in scheduled:
            coroutine.close()
