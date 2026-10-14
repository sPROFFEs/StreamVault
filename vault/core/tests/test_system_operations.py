"""Read-only monitor and source settings are restricted to administrators."""
import pytest


def sign_in(client, name):
    client.post("/login", data={"username": name, "password": "Baseline123!"})


@pytest.mark.parametrize("path", ["/admin/api/system/monitor", "/admin/system/update/config", "/admin/system/update/history", "/admin/system/update/backups"])
def test_system_endpoints_require_admin(regression_client, path):
    sign_in(regression_client, "baseline-viewer")
    assert regression_client.get(path).status_code == 403


def test_update_source_is_persistent_and_masked(regression_client, monkeypatch, tmp_path):
    from routers import admin
    from services.updater_service import UpdateManager
    manager = UpdateManager(tmp_path, tmp_path / "data")
    monkeypatch.setattr(admin, "updater", manager)
    sign_in(regression_client, "baseline-admin")
    response = regression_client.put("/admin/system/update/config", json={"mode": "release", "provider": "gitea",
        "api_url": "http://forge.lan:3000", "repository": "owner/repo", "api_token": "secret-test-token"})
    assert response.status_code == 200
    assert response.json()["has_token"] is True
    assert "secret-test-token" not in response.text
    assert regression_client.get("/admin/system/update/config").json()["repository"] == "owner/repo"
    assert regression_client.put("/admin/system/update/config", json={"mode": "other"}).status_code == 422


def test_monitor_does_not_run_filesystem_scan_or_block_the_event_loop(regression_client, monkeypatch):
    from services import system_monitor
    monkeypatch.setattr(system_monitor, "service_status", lambda: [{"name": "core", "state": "running"}])
    sign_in(regression_client, "baseline-admin")
    data = regression_client.get("/admin/api/system/monitor").json()
    assert 0 <= data["cpu_percent"] <= 100
    assert 0 <= data["memory"]["percent"] <= 100
    assert data["services"][0]["name"] == "core"
    assert data["disk"]["total_bytes"] > 0
    assert "version" in data


def test_system_page_keeps_explicit_monitor_tab(regression_client):
    sign_in(regression_client, "baseline-admin")
    response = regression_client.get("/admin/system")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/?tab=system"
