from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_application_has_a_global_session_boundary():
    source = (PROJECT_ROOT / "core/main.py").read_text()

    assert "async def enforce_security_boundary" in source
    assert 'path.startswith("/api/")' in source
    assert 'status_code=401' in source
    assert "is_active" in source


def test_public_user_creation_requires_an_administrator():
    source = (PROJECT_ROOT / "core/main.py").read_text()

    assert "admin_user: database.User = Depends(admin.get_current_admin)" in source


def test_session_cookie_is_secure_when_request_uses_https():
    source = (PROJECT_ROOT / "core/main.py").read_text()

    assert "request_is_https(request)" in source
    assert "secure=request_is_https(request)" in source


def test_security_headers_and_csrf_origin_check_are_global():
    source = (PROJECT_ROOT / "core/main.py").read_text()

    assert 'response.headers["Content-Security-Policy"]' in source
    assert 'response.headers["X-Content-Type-Options"] = "nosniff"' in source
    assert "origin_is_allowed" in source


def test_logout_requires_post():
    source = (PROJECT_ROOT / "core/main.py").read_text()
    navigation = (PROJECT_ROOT / "core/templates/_sidebar.html").read_text()

    assert '@app.post("/logout")' in source
    assert 'action="/logout" method="post"' in navigation


def test_deployment_has_no_known_default_session_secret():
    compose = (PROJECT_ROOT / "docker-compose.yaml").read_text()

    assert "streamvault-dev-secret-change-me" not in compose
    assert "SECRET_KEY=${SECRET_KEY:?" in compose


def test_login_throttling_does_not_trust_spoofable_forwarded_for():
    source = (PROJECT_ROOT / "core/security.py").read_text()

    assert 'request.headers.get("X-Forwarded-For")' not in source
    assert 'request.headers.get("CF-Connecting-IP")' in source


def test_cross_origin_api_access_is_not_enabled():
    source = (PROJECT_ROOT / "core/main.py").read_text()

    assert "CORSMiddleware" not in source


def test_browser_media_dependencies_are_version_pinned():
    template = (PROJECT_ROOT / "core/templates/media_detail.html").read_text()

    assert "@latest" not in template
    assert "hls.js@1.6.16" in template
    assert "webtorrent@3.0.21" in template


def test_account_names_and_admin_file_deletes_cannot_escape_storage_roots():
    auth_source = (PROJECT_ROOT / "core/auth.py").read_text()
    main_source = (PROJECT_ROOT / "core/main.py").read_text()
    admin_source = (PROJECT_ROOT / "core/routers/admin.py").read_text()

    assert "def is_valid_username" in auth_source
    assert "auth.is_valid_username(username)" in main_source
    assert "auth.is_valid_username(user.username)" in main_source
    assert "auth.is_valid_username(username)" in admin_source
    assert "resolve_library_path" in admin_source
