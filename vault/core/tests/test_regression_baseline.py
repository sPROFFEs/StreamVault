"""Current auth and updater contracts, without external services."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.baseline
UPDATES = ("git-pull", "docker-restart", "docker-recreate")


def sign_in(client, username="baseline-viewer", password="Baseline123!", next="/browse?type=movie"):
    return client.post("/login", data={"username": username, "password": password, "next": next})


def test_auth_session_redirect_cookie_and_logout(regression_client):
    client = regression_client
    assert client.get("/browse?type=tv").status_code == 303
    assert client.get("/api/library/favorites").status_code == 401
    response = sign_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/browse?type=movie"
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax"))
    assert client.get("/browse?type=movie").status_code == 200
    assert client.post("/logout").status_code == 303
    assert client.get("/api/library/favorites").status_code == 401


@pytest.mark.parametrize("username,password", [("baseline-viewer", "wrong"), ("baseline-disabled", "Baseline123!")])
def test_rejected_login_does_not_create_session(regression_client, username, password):
    response = sign_in(regression_client, username, password)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "access_token" not in response.cookies


def test_login_external_redirect_and_cross_origin_boundary(regression_client):
    response = sign_in(regression_client, next="https://outside.test/")
    assert response.headers["location"] == "/portal"
    assert regression_client.post("/logout", headers={"Origin": "https://outside.test"}).status_code == 403
    assert regression_client.get("/browse").status_code == 200


@pytest.mark.parametrize("operation", UPDATES)
def test_updater_requires_admin_and_post(regression_client, monkeypatch, operation):
    runner = Mock(side_effect=AssertionError("Updater subprocess must not run"))
    monkeypatch.setattr(subprocess, "run", runner)
    path = f"/admin/system/update/{operation}"
    assert regression_client.post(path).status_code == 303
    sign_in(regression_client)
    assert regression_client.post(path).status_code == 403
    sign_in(regression_client, "baseline-admin")
    assert regression_client.get(path).status_code == 405
    runner.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_git_update_reports_persisted_result_without_running_installation(monkeypatch, fails):
    from routers import admin
    from services.updater_service import UpdateError
    manager = Mock()
    if fails:
        manager.start.side_effect = UpdateError("Fixture network failure", 502)
        manager.status.return_value = {"state": "failure", "message": "Fixture network failure"}
    else:
        manager.start.return_value = {"state": "updating", "message": "Fixture worker queued"}
    monkeypatch.setattr(admin, "updater", manager)
    response = admin.system_git_pull(db=None, admin=SimpleNamespace(is_admin=True))
    assert response.status_code == (502 if fails else 202)
    assert json.loads(response.body)["state"] == ("failure" if fails else "updating")
    manager.start.assert_called_once_with("update")


@pytest.mark.parametrize("operation", ["docker-restart", "docker-recreate"])
def test_container_update_queues_detached_worker(monkeypatch, operation):
    from routers import admin
    manager = Mock()
    manager.start.return_value = {"state": "updating", "message": "Fixture worker queued"}
    monkeypatch.setattr(admin, "updater", manager)
    handler = admin.system_docker_restart if operation == "docker-restart" else admin.system_docker_recreate
    response = handler(db=None, admin=SimpleNamespace(is_admin=True))
    assert response.status_code == 202
    manager.start.assert_called_once_with("restart" if operation == "docker-restart" else "rebuild")


def test_update_check_and_status_require_admin(regression_client, monkeypatch):
    from routers import admin
    manager = Mock()
    monkeypatch.setattr(admin, "updater", manager)
    for method, path in (("get", "status"), ("post", "check")):
        assert getattr(regression_client, method)(f"/admin/system/update/{path}").status_code == 303
    sign_in(regression_client)
    for method, path in (("get", "status"), ("post", "check")):
        assert getattr(regression_client, method)(f"/admin/system/update/{path}").status_code == 403
    manager.assert_not_called()
    manager.check.assert_not_called()
    manager.status.assert_not_called()
