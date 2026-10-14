"""Authentication recovery keeps the existing form and cookie contract."""
import logging
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def empty_accounts(regression_client):
    import database

    with database.SessionLocal() as db:
        db.query(database.User).filter(database.User.username.like("baseline-%")).delete()
        db.commit()
    try:
        yield regression_client
    finally:
        with database.SessionLocal() as db:
            db.query(database.User).filter_by(username="auth-created-admin").delete()
            db.commit()


def test_rejected_login_preserves_destination_without_credentials(regression_client, caplog):
    caplog.set_level(logging.INFO)
    secret = "NeverRenderThisPassword!"
    response = regression_client.post("/login", data={
        "username": "baseline-viewer", "password": secret, "next": "/browse?type=tv&sort=popular",
    })
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["next"] == ["/browse?type=tv&sort=popular"]
    html = regression_client.get(response.headers["location"]).text
    assert "Invalid username or password" in html
    assert secret not in html + caplog.text + response.headers["location"]


def test_untrusted_error_query_is_never_rendered(regression_client):
    response = regression_client.get("/login", params={"error": "password=SensitiveSecret! token=private-token"})
    assert "SensitiveSecret" not in response.text
    assert "private-token" not in response.text


def test_enhanced_login_preserves_cookie_and_redirect_contract(regression_client):
    response = regression_client.post("/login", headers={"Accept": "application/json"}, data={
        "username": "baseline-viewer", "password": "Baseline123!", "next": "/browse?type=tv",
    })
    assert response.status_code == 200
    assert response.json() == {"redirect": "/browse?type=tv"}
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "secure", "samesite=lax", "max-age=86400"))
    assert regression_client.get("/browse?type=tv").status_code == 200
    assert regression_client.post("/logout").headers["location"] == "/login"
    assert regression_client.get("/api/library/favorites").status_code == 401


@pytest.mark.parametrize("password,status,code", [("wrong", 401, "invalid_credentials"), ("", 401, "invalid_credentials")])
def test_enhanced_login_errors_are_fixed_codes(regression_client, password, status, code):
    response = regression_client.post("/login", headers={"Accept": "application/json"}, data={
        "username": "baseline-viewer", "password": password,
    })
    assert response.status_code == status
    assert response.json() == {"code": code}
    assert "access_token" not in response.cookies


def test_lockout_keeps_destination_and_does_not_echo_exception_details(regression_client, monkeypatch):
    import security
    from fastapi import HTTPException

    def blocked(request):
        raise HTTPException(429, "SensitiveServerDetail!")

    monkeypatch.setattr(security, "check_brute_force", blocked)
    data = {"username": "baseline-viewer", "password": "Baseline123!", "next": "/browse?type=movie"}
    response = regression_client.post("/login", data=data)
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == [data["next"]]
    assert "SensitiveServerDetail" not in response.headers["location"]
    response = regression_client.post("/login", data=data, headers={"Accept": "application/json"})
    assert response.status_code == 429
    assert response.json() == {"code": "blocked"}


def test_login_localizes_known_errors_and_created_notice(regression_client):
    regression_client.cookies.set("lang", "es")
    response = regression_client.get("/login?error=invalid_credentials")
    assert "Usuario o contraseña incorrectos" in response.text
    response = regression_client.get("/login?notice=created")
    assert "Cuenta de administrador creada" in response.text
    assert 'role="status"' in response.text


def test_persistent_session_restores_in_another_client(regression_client):
    import main
    from fastapi.testclient import TestClient

    regression_client.post("/login", data={"username": "baseline-viewer", "password": "Baseline123!"})
    restored = TestClient(main.app, base_url="https://streamvault.test", follow_redirects=False,
                          cookies=regression_client.cookies)
    try:
        assert restored.get("/browse?type=tv").status_code == 200
        assert restored.post("/logout").status_code == 303
        assert restored.get("/api/library/favorites").status_code == 401
    finally:
        restored.close()


@pytest.mark.parametrize("token", ["invalid-token", "expired"])
def test_invalid_sessions_return_to_login(regression_client, token):
    import auth
    from datetime import timedelta

    if token == "expired":
        token = auth.create_access_token({"sub": "baseline-viewer"}, timedelta(seconds=-10))
    regression_client.cookies.set("access_token", token)
    response = regression_client.get("/browse?type=tv")
    assert response.status_code == 303
    assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == ["/browse?type=tv"]


def test_setup_validation_creation_and_sign_in(empty_accounts, caplog):
    caplog.set_level(logging.INFO)
    client = empty_accounts
    response = client.get("/setup")
    assert response.status_code == 200
    assert 'autocomplete="new-password"' in response.text
    headers = {"Accept": "application/json"}
    assert client.post("/setup", headers=headers, data={"username": "../bad", "password": "Baseline123!"}).json() == {"code": "invalid_username"}
    assert client.post("/setup", headers=headers, data={"username": "auth-created-admin", "password": "weak"}).json() == {"code": "weak_password"}
    response = client.post("/setup", headers=headers, data={"username": "auth-created-admin", "password": "Baseline123!"})
    assert response.json() == {"redirect": "/login?notice=created"}
    assert "Baseline123!" not in response.text + caplog.text
    response = client.post("/login", data={"username": "auth-created-admin", "password": "Baseline123!", "next": "/browse?type=tv"})
    assert response.headers["location"] == "/browse?type=tv"
    assert client.get("/browse?type=tv").status_code == 200
    assert client.post("/setup", headers=headers, data={"username": "second-admin", "password": "Baseline123!"}).status_code == 403
