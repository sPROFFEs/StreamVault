import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import admin


def test_admin_inventory_scan_does_not_block_event_loop(monkeypatch):
    def scan(**kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"items": []}
        raise AssertionError("Filesystem scan is blocking the application event loop")

    monkeypatch.setattr(admin, "scan_download_library", scan)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        assert client.get("/admin/api/downloads/list").json() == {"items": []}


def test_file_browser_api_download_delete_and_path_boundary(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    folder = root / "A series"
    folder.mkdir(parents=True)
    (folder / "notes & credits.txt").write_text("credits")
    monkeypatch.setattr(admin.settings, "DOWNLOADS_ROOT", str(root))
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        response = client.get("/admin/api/downloads/list", params={"download_path": "A series"})
        assert response.status_code == 200
        assert response.json()["items"][0]["name"] == "notes & credits.txt"
        params = {"relative_path": "A series/notes & credits.txt"}
        response = client.get("/admin/api/downloads/file", params=params)
        assert response.content == b"credits"
        assert "attachment" in response.headers["content-disposition"]
        assert client.get("/admin/api/downloads/list", params={"download_path": "../"}).status_code == 403
        assert client.get("/admin/api/downloads/list", params={"download_path": "missing"}).status_code == 404
        assert client.get("/admin/api/downloads/file", params={"relative_path": "../secret"}).status_code == 403
        assert client.delete("/admin/downloads/delete", params=params).status_code == 200
        assert not (folder / "notes & credits.txt").exists()


def test_admin_file_endpoints_require_authentication():
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        assert client.get("/admin/api/downloads/list").status_code == 401
        assert client.get("/admin/api/downloads/file?relative_path=video.mp4").status_code == 401


def test_cleanup_endpoint_previews_and_removes_only_safe_candidates(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    (root / "stale.part").write_text("partial")
    (root / "keep.txt").write_text("metadata")
    (root / "empty").mkdir()
    monkeypatch.setattr(admin.settings, "DOWNLOADS_ROOT", str(root))
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        preview = client.get("/admin/api/downloads/cleanup")
        assert preview.status_code == 200
        paths = {item["relative_path"] for item in preview.json()["items"]}
        assert paths == {"stale.part", "empty"}
        cleaned = client.post("/admin/api/downloads/cleanup", json={"paths": ["stale.part", "empty"]})
        assert cleaned.status_code == 200
        assert not (root / "stale.part").exists()
        assert not (root / "empty").exists()
        assert (root / "keep.txt").exists()


def test_cleanup_endpoints_require_authentication():
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        assert client.get("/admin/api/downloads/cleanup").status_code == 401
        assert client.post("/admin/api/downloads/cleanup", json={}).status_code == 401


def test_cleaner_includes_idle_playback_copies_and_reports_disk_budget(tmp_path, monkeypatch):
    import os
    import time
    root = tmp_path / 'downloads'
    root.mkdir()
    video = root / 'keep.mp4'
    video.write_bytes(b'original')
    cache = tmp_path / 'cache' / 'remux'
    cache.mkdir(parents=True)
    copy = cache / ('a' * 64 + '.mp4')
    copy.write_bytes(b'disposable')
    old = time.time() - 7200
    os.utime(copy, (old, old))
    monkeypatch.setattr(admin.settings, 'DOWNLOADS_ROOT', str(root))
    monkeypatch.setattr(admin.settings, 'MEDIA_STORAGE_PATH', str(cache.parent))
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        preview = client.get('/admin/api/downloads/cleanup').json()
        assert preview['storage']['disk_free_bytes'] > 0
        candidate = '@cache/remux/' + copy.name
        assert candidate in {item['relative_path'] for item in preview['items']}
        result = client.post('/admin/api/downloads/cleanup', json={'paths': [candidate]}).json()
        assert result['freed_bytes'] == len(b'disposable')
        assert not copy.exists() and video.exists()


def test_legacy_purge_opens_preview_instead_of_erasing_global_tmp(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A maintenance request must not traverse global temporary directories')
    monkeypatch.setattr('os.walk', forbidden)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[admin.get_current_admin] = lambda: SimpleNamespace(is_admin=True)
    with TestClient(app) as client:
        response = client.post('/admin/system/purge-storage', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == '/admin/?tab=files&clean=1'


def test_large_cleanup_batches_remove_children_before_offering_parent_folders(tmp_path, monkeypatch):
    root = tmp_path / 'downloads'
    for index in range(501):
        folder = root / f'job-{index:04d}'
        folder.mkdir(parents=True)
        (folder / 'abandoned.part').write_bytes(b'partial')
    monkeypatch.setattr(admin.settings, 'DOWNLOADS_ROOT', str(root))
    monkeypatch.setattr(admin.settings, 'MEDIA_STORAGE_PATH', str(tmp_path / 'cache'))
    preview = admin._storage_cleanup_preview(set())
    assert preview['total_candidates'] == 1002
    assert len(preview['items']) == 500
    selected = [item['relative_path'] for item in preview['items']]
    result = admin._cleanup_storage(selected, set())
    assert len(result['deleted']) == 500
    assert not result['errors']
    assert result['freed_bytes'] == 500 * len(b'partial')
    assert result['remaining']['total_candidates'] == 502
