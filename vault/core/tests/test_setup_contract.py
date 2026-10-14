from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_setup_supports_a_clean_machine_non_interactive_install():
    script = (PROJECT_ROOT / "setup.sh").read_text()

    assert "--non-interactive" in script
    assert "--repo-url" in script
    assert "docker-compose.yaml" in script
    assert "git clone" in script
    assert "STREAMVAULT_ADMIN_USERNAME" in script
    assert "STREAMVAULT_ADMIN_PASSWORD" in script
    assert "/api/health" in script
    assert "docker compose up -d --build" in script
    assert "create_admin.py" in script
    assert "--reset" not in script
    assert "existing_secret" in script
    assert 'streamvault-dev-secret-change-me' in script


def test_admin_seed_has_no_known_default_password():
    source = (PROJECT_ROOT / "core/create_admin.py").read_text()

    assert 'STREAMVAULT_ADMIN_PASSWORD", "admin"' not in source
    assert "is_password_strong" in source


def test_readme_documents_the_single_command_install():
    readme = (PROJECT_ROOT / "README.md").read_text()

    assert "One-command install" in readme
    assert "--non-interactive" in readme
