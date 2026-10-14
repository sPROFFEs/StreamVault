"""Keep imports and API tests away from the server's real database/downloads."""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import sqlalchemy


_data = tempfile.TemporaryDirectory(prefix="streamvault-tests-")
os.environ["DOMAIN"] = "localhost"
os.environ["SECRET_KEY"] = "streamvault-regression-secret-for-tests-only"
for name, directory in {
    "DATA_ROOT": "", "DOWNLOADS_ROOT": "downloads", "MEDIA_STORAGE_PATH": "cache",
}.items():
    os.environ[name] = str(Path(_data.name) / directory)

_create_engine = sqlalchemy.create_engine
with patch("sqlalchemy.create_engine", side_effect=lambda url, **kwargs: _create_engine(
    f"sqlite:///{_data.name}/core.db", **kwargs,
)):
    import database  # The legacy database module creates its tables on import.


import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name.endswith("_browser.py"):
            item.add_marker(pytest.mark.browser)
        if item.path.name in {"test_playback_browser.py", "test_iptv_playback_browser.py", "test_media_integration.py"}:
            item.add_marker(pytest.mark.media)
        if item.get_closest_marker("browser") and os.getenv("STREAMVAULT_BROWSER_TESTS") != "1":
            item.add_marker(pytest.mark.skip(reason="Enable with --browser or STREAMVAULT_BROWSER_TESTS=1"))
        if item.path.name in {"test_playback_browser.py", "test_iptv_playback_browser.py"}:
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                item.add_marker(pytest.mark.skip(reason="FFmpeg/FFprobe required for real browser media playback"))


VIEWPORTS = {
    "mobile": {"width": 390, "height": 844},
    "desktop": {"width": 1440, "height": 900},
    "tv": {"width": 1920, "height": 1080},
}


def pytest_generate_tests(metafunc):
    if "browser_viewport" in metafunc.fixturenames:
        selected = metafunc.config.getoption("--viewport")
        names = list(VIEWPORTS) if selected == "all" else [selected]
        metafunc.parametrize("browser_viewport", [VIEWPORTS[name] for name in names], ids=names)


@pytest.fixture(scope="session")
def regression_password_hash():
    import auth
    return auth.get_password_hash("Baseline123!")


@pytest.fixture
def regression_client(monkeypatch, regression_password_hash):
    """Real auth/security middleware, temporary users, and no startup services."""
    import main
    import security
    from fastapi.testclient import TestClient

    monkeypatch.setattr(security, "login_attempts", {})
    names = {"baseline-viewer": (False, True), "baseline-admin": (True, True),
             "baseline-disabled": (False, False)}
    with database.SessionLocal() as db:
        for user in db.query(database.User).filter(database.User.username.in_(names)).all():
            db.delete(user)
        db.commit()
        for username, (is_admin, is_active) in names.items():
            db.add(database.User(username=username, hashed_password=regression_password_hash,
                                 is_admin=is_admin, is_active=is_active))
        db.commit()
    # Do not enter TestClient's lifespan: no recovery, tunnel or downloader jobs.
    client = TestClient(main.app, base_url="https://streamvault.test", follow_redirects=False)
    try:
        yield client
    finally:
        client.close()
        with database.SessionLocal() as db:
            for user in db.query(database.User).filter(database.User.username.in_(names)).all():
                db.delete(user)
            db.commit()


from regression_fixtures import baseline_page, baseline_payloads, regression_browser, regression_server
