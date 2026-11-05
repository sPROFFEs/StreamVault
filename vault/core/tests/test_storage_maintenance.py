import os
import time
from pathlib import Path

import pytest

from services import storage_maintenance as maintenance


def cache_file(root, name=None, *, age=7200, size=16):
    path = root / 'remux' / (name or ('a' * 64 + '.mp4'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    os.utime(path, (time.time() - age, time.time() - age))
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance.settings, 'MEDIA_STORAGE_PATH', str(tmp_path / 'cache'))
    monkeypatch.setattr(maintenance.settings, 'MIN_FREE_DISK_GB', 0)
    return tmp_path / 'cache'


def test_cache_cleaner_only_offers_owned_idle_files_and_rechecks_before_delete(cache, tmp_path):
    old = cache_file(cache)
    recent = cache_file(cache, 'b' * 64 + '.mp4', age=0)
    external = tmp_path / 'private.mp4'
    external.write_bytes(b'private')
    (old.parent / ('c' * 64 + '.mp4')).symlink_to(external)
    unknown = cache_file(cache, 'my-original.mp4')
    preview = maintenance.cache_inventory()
    assert [item['relative_path'] for item in preview['items']] == ['remux/' + old.name]
    # A video can start after the preview. It must then survive the cleanup.
    with maintenance.protect_cache(old):
        assert maintenance.cleanup_cache(['remux/' + old.name])['deleted'] == []
    result = maintenance.cleanup_cache(['remux/' + old.name, '../private.mp4'])
    assert result['freed_bytes'] == 16
    assert len(result['errors']) == 1
    assert not old.exists()
    assert recent.exists() and unknown.exists() and external.exists()


def test_cache_maintenance_expires_old_copies_but_retains_recent_and_playing(cache):
    old = cache_file(cache, age=8 * 86400)
    recent = cache_file(cache, 'b' * 64 + '.mp4', age=7200)
    playing = cache_file(cache, 'c' * 64 + '.mp4', age=9 * 86400)
    with maintenance.protect_cache(playing):
        result = maintenance.maintain_cache()
    assert result['freed_bytes'] == 16
    assert not old.exists()
    assert recent.exists() and playing.exists()


def test_low_disk_space_reclaims_oldest_idle_copy_only(cache, monkeypatch):
    old = cache_file(cache, age=7200, size=100)
    newer = cache_file(cache, 'b' * 64 + '.mp4', age=3600, size=100)
    monkeypatch.setattr(maintenance, 'disk_free_bytes', lambda _: 50)
    result = maintenance.maintain_cache(required_bytes=100)
    assert result['freed_bytes'] == 100
    assert not old.exists() and newer.exists()


def test_abandoned_conversion_outputs_are_reclaimed_but_live_writer_is_protected(cache):
    partial = cache_file(cache, 'd' * 64 + '.' + 'e' * 32 + '.mp4', age=7200)
    with maintenance.protect_cache(partial):
        assert maintenance.maintain_cache()['freed_bytes'] == 0
    assert maintenance.maintain_cache()['freed_bytes'] == 16
    assert not partial.exists()


def test_download_budget_preserves_physical_disk_reserve(tmp_path, monkeypatch):
    from services import storage_policy
    monkeypatch.setattr(storage_policy.settings, 'MIN_FREE_DISK_GB', 1)
    monkeypatch.setattr(storage_policy, 'disk_free_bytes', lambda _: 1024**3 + 100)
    monkeypatch.setattr(storage_policy, 'get_global_download_quota_gb', lambda: 100)
    storage_policy.ensure_download_space(tmp_path, 50)
    with pytest.raises(ValueError, match='disk'):
        storage_policy.ensure_download_space(tmp_path, 101)


def test_size_accounting_does_not_follow_external_symlinks(tmp_path):
    from services.storage_policy import get_path_size_bytes
    root = tmp_path / 'downloads'
    root.mkdir()
    (root / 'video.mp4').write_bytes(b'video')
    external = tmp_path / 'private'
    external.write_bytes(b'x' * 100)
    (root / 'link.mp4').symlink_to(external)
    assert get_path_size_bytes(str(root)) == 5


def test_cached_range_requests_retain_the_same_etag_and_release_file_lease(cache):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    path = cache_file(cache)
    original_mtime = path.stat().st_mtime_ns
    app = FastAPI()
    @app.get('/video')
    def video():
        return maintenance.PlaybackFileResponse(path, media_type='video/mp4')
    with TestClient(app) as client:
        first = client.get('/video', headers={'Range': 'bytes=0-3'})
        second = client.get('/video', headers={'Range': 'bytes=4-7', 'If-Range': first.headers['etag']})
    assert first.status_code == second.status_code == 206
    assert first.headers['etag'] == second.headers['etag']
    assert path.stat().st_mtime_ns == original_mtime
    assert str(path.resolve()) not in maintenance._ACTIVE


def test_library_invalidation_does_not_wait_for_a_slow_disk_scan(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    from services import storage_policy
    entered, release = threading.Event(), threading.Event()
    original = os.walk
    def slow_walk(*args, **kwargs):
        entered.set()
        release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(storage_policy.os, 'walk', slow_walk)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
        scan = workers.submit(storage_policy.get_path_size_bytes, str(tmp_path))
        try:
            assert entered.wait(2)
            invalidation = workers.submit(storage_policy.invalidate_path_size, tmp_path)
            invalidation.result(timeout=1)
        finally:
            release.set()
            scan.result(timeout=2)
