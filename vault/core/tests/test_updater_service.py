"""Updater regressions use disposable Git repositories and a mocked daemon."""
import subprocess

import pytest

from services.updater_service import UpdateError, UpdateManager


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "release")
    git(upstream, "config", "user.email", "fixture@example.test")
    git(upstream, "config", "user.name", "Fixture")
    (upstream / "vault").mkdir()
    (upstream / "vault/.env").write_text("SECRET_KEY=persistent-fixture\n")
    (upstream / "code.py").write_text("old\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "initial")
    local = tmp_path / "checkout"
    git(tmp_path, "clone", "--origin", "configured", str(upstream), str(local))
    manager = UpdateManager(local, tmp_path / "data")
    return manager, local, upstream


def advance(upstream, path="code.py"):
    (upstream / path).write_text("new\n")
    git(upstream, "add", path)
    git(upstream, "commit", "-m", "update")
    return git(upstream, "rev-parse", "HEAD")


def test_uses_configured_tracking_remote_and_full_commit(checkout):
    manager, local, upstream = checkout
    result = manager.check()
    assert result["state"] == "current"
    assert result["remote"] == "configured"
    assert result["branch"] == "release"
    assert result["local_commit"] == result["remote_commit"] == git(local, "rev-parse", "HEAD")
    assert len(result["local_commit"]) == 40


def test_available_check_does_not_update_checkout(checkout):
    manager, local, upstream = checkout
    before = git(local, "rev-parse", "HEAD")
    target = advance(upstream)
    result = manager.check()
    assert result["state"] == "available"
    assert result["remote_commit"] == target
    assert git(local, "rev-parse", "HEAD") == before
    assert (local / "code.py").read_text() == "old\n"


def test_no_remote_or_branch_is_guessed(checkout):
    manager, local, _ = checkout
    git(local, "branch", "--unset-upstream")
    with pytest.raises(UpdateError, match="upstream|UPDATE_REMOTE"):
        manager.check()
    assert manager.status()["state"] == "failure"


def test_explicit_remote_and_branch(checkout):
    manager, local, upstream = checkout
    git(local, "branch", "--unset-upstream")
    manager.remote, manager.branch = "configured", "release"
    assert manager.check()["state"] == "current"
    manager.branch = "missing-branch"
    with pytest.raises(UpdateError, match="fetch|branch"):
        manager.check()


def test_partial_configuration_and_detached_checkout_are_rejected(checkout):
    manager, local, _ = checkout
    manager.remote = "configured"
    with pytest.raises(UpdateError, match="both"):
        manager.check()
    manager.remote = ""
    git(local, "checkout", "--detach", "HEAD")
    with pytest.raises(UpdateError, match="Detached"):
        manager.check()


def test_repository_identity_comes_from_remote_and_credentials_are_redacted(checkout):
    manager, local, _ = checkout
    git(local, "remote", "set-url", "configured", "https://fixture:secret@github.com/custom-owner/custom-repo.git?token=private")
    assert manager.selection()["repository"] == "https://github.com/custom-owner/custom-repo.git"


def test_network_failure_is_not_current(checkout):
    manager, local, _ = checkout
    git(local, "remote", "set-url", "configured", str(local / "missing-repository"))
    with pytest.raises(UpdateError, match="fetch|network"):
        manager.check()
    assert manager.status()["state"] == "failure"


def test_dirty_code_and_diverged_checkout_are_rejected(checkout):
    manager, local, upstream = checkout
    advance(upstream)
    (local / "code.py").write_text("local edits\n")
    with pytest.raises(UpdateError, match="uncommitted"):
        manager.check()
    git(local, "config", "user.email", "fixture@example.test")
    git(local, "config", "user.name", "Fixture")
    git(local, "add", "code.py")
    git(local, "commit", "-m", "local-only")
    with pytest.raises(UpdateError, match="fast-forward"):
        manager.check()


def test_persistent_configuration_and_data_survive_code_update(checkout):
    manager, local, upstream = checkout
    target = advance(upstream)
    env = local / "vault/.env"
    env.write_text("SECRET_KEY=local-custom-value\n")
    user_data = manager.data_root / "users.db"
    user_data.parent.mkdir(parents=True)
    user_data.write_bytes(b"persistent user database")
    assert manager.check()["state"] == "available"
    manager.apply_code(target)
    assert git(local, "rev-parse", "HEAD") == target
    assert env.read_text() == "SECRET_KEY=local-custom-value\n"
    assert user_data.read_bytes() == b"persistent user database"


def test_upstream_cannot_replace_persistent_configuration(checkout):
    manager, _, upstream = checkout
    advance(upstream, "vault/.env")
    with pytest.raises(UpdateError, match="persistent"):
        manager.check()


def test_upstream_cannot_rename_persistent_configuration(checkout):
    manager, _, upstream = checkout
    git(upstream, "mv", "vault/.env", "vault/example-config")
    git(upstream, "commit", "-m", "rename config")
    with pytest.raises(UpdateError, match="persistent"):
        manager.check()


@pytest.mark.parametrize("operation", ["restart", "rebuild"])
def test_maintenance_does_not_need_network_access(checkout, monkeypatch, operation):
    manager, local, _ = checkout
    git(local, "remote", "set-url", "configured", str(local / "missing-repository"))
    monkeypatch.setattr(manager, "deployment", lambda: {
        "workspace": "/srv/checkout", "data": "/srv/data", "project": "fixture", "image": "sha256:fixture",
    })
    monkeypatch.setattr(manager, "docker", lambda *args, **kwargs: "worker-id")
    assert manager.start(operation)["state"] == "updating"


def test_deployment_uses_inspected_bind_sources_not_container_paths(checkout, monkeypatch):
    import json
    manager, local, _ = checkout
    info = {"Config": {"Labels": {"com.docker.compose.service": "core",
            "com.docker.compose.project": "custom", "com.docker.compose.project.config_files": "/srv/repo/vault/docker-compose.yaml"}},
            "Image": "sha256:fixture", "Mounts": [
                {"Destination": str(local), "Source": "/srv/repo", "Type": "bind"},
                {"Destination": str(manager.data_root), "Source": "/srv/data", "Type": "bind"}]}
    monkeypatch.setenv("HOSTNAME", "actual-core-id")
    calls = []
    monkeypatch.setattr(manager, "docker", lambda *args, **kwargs: calls.append(args) or json.dumps([info]))
    assert manager.deployment() == {"workspace": "/srv/repo", "data": "/srv/data", "project": "custom", "image": "sha256:fixture", "environment": []}
    assert ("inspect", "actual-core-id") in calls


def test_compose_uses_plugin_host_paths_and_persistent_env(checkout, monkeypatch):
    manager, local, _ = checkout
    manager.project, manager.host_data = "actual-project", "/srv/persistent-data"
    calls = []
    monkeypatch.setattr(manager, "command", lambda args, **kwargs: calls.append((args, kwargs)) or "")
    manager.compose("up", "-d", "--force-recreate")
    args, options = calls[-1]
    assert args[:2] == ["docker", "compose"]
    assert args[args.index("--project-name") + 1] == "actual-project"
    assert args[args.index("--env-file") + 1] == str(local / "vault/.env")
    assert options["env"]["HOST_DATA_ROOT"] == "/srv/persistent-data"


def test_shallow_github_style_clone_can_fast_forward(checkout, tmp_path):
    manager, _, upstream = checkout
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "--depth", "1", "--origin", "github", upstream.as_uri(), str(shallow))
    target = advance(upstream)
    manager = UpdateManager(shallow, tmp_path / "shallow-data")
    assert manager.check()["state"] == "available"
    manager.apply_code(target)
    assert git(shallow, "rev-parse", "HEAD") == target


def test_overlap_is_rejected_across_manager_instances(checkout):
    manager, local, _ = checkout
    other = UpdateManager(local, manager.data_root)
    with manager.lock():
        with pytest.raises(UpdateError) as failure:
            other.check()
    assert failure.value.status_code == 409


def test_interrupted_check_is_reported_and_can_be_retried(checkout):
    manager, _, _ = checkout
    manager.save({"state": "checking", "message": "Checking"})
    assert manager.status()["state"] == "failure"
    assert manager.check()["state"] == "current"


def test_interrupted_worker_does_not_report_success(checkout, monkeypatch):
    manager, _, _ = checkout
    manager.save({"state": "updating", "worker": "fixture-worker", "requires_rebuild": True})
    monkeypatch.setattr(manager, "worker_running", lambda name: False)
    result = manager.status()
    assert result["state"] == "failure"
    assert result["requires_rebuild"] is True


def test_current_update_is_noop_without_docker(checkout, monkeypatch):
    manager, _, _ = checkout
    monkeypatch.setattr(manager, "deployment", lambda: pytest.fail("Current checkout must not invoke Docker"))
    assert manager.start()["state"] == "current"


def test_detached_worker_uses_host_paths_project_and_current_image(checkout, monkeypatch):
    manager, local, upstream = checkout
    advance(upstream)
    monkeypatch.setattr(manager, "deployment", lambda: {
        "workspace": "/srv/custom-checkout", "data": "/srv/persistent-data",
        "project": "custom-deployment", "image": "sha256:current-core-image",
    })
    calls = []
    monkeypatch.setattr(manager, "docker", lambda *args, **kwargs: calls.append(args) or "worker-id")
    result = manager.start()
    assert result["state"] == "updating"
    assert git(local, "rev-parse", "HEAD") != result["remote_commit"]
    args = calls[-1]
    assert args[:2] == ("run", "--detach")
    assert "/srv/custom-checkout:/srv/custom-checkout" in args
    assert "/srv/persistent-data:/saas-data" in args
    assert "sha256:current-core-image" in args
    assert "custom-deployment" in args


def test_detached_worker_preserves_runtime_configuration(checkout, monkeypatch):
    manager, _, upstream = checkout
    advance(upstream)
    monkeypatch.setattr(manager, "deployment", lambda: {
        "workspace": "/srv/repo", "data": "/srv/data", "project": "fixture", "image": "sha256:fixture",
        "environment": ["SECRET_KEY=runtime-fixture-secret", "DOMAIN=custom.test", "HOST_DATA_ROOT=/old/value", "PATH=/container/bin"],
    })
    calls = []
    monkeypatch.setattr(manager, "docker", lambda *args, **kwargs: calls.append(args) or "worker-id")
    result = manager.start()
    assert "SECRET_KEY=runtime-fixture-secret" in calls[-1]
    assert "DOMAIN=custom.test" in calls[-1]
    assert "PATH=/container/bin" not in calls[-1]
    assert "runtime-fixture-secret" not in str(result)
    assert "runtime-fixture-secret" not in manager.status_path.read_text()


def test_worker_rejects_checkout_changed_after_queueing(checkout, monkeypatch):
    manager, local, upstream = checkout
    target = advance(upstream)
    manager.check()
    manager.save({**manager.read(), "state": "updating", "job_id": "fixture", "operation": "update"})
    git(local, "switch", "-c", "unexpected-branch")
    monkeypatch.setattr(manager, "compose", lambda *args, **kwargs: pytest.fail("Changed checkout must not deploy"))
    manager.run_worker("fixture", target, "update", "fixture", str(manager.data_root))
    assert manager.status()["state"] == "failure"
    assert git(local, "rev-parse", "HEAD") != target


def test_lost_launch_response_keeps_running_worker_locked(checkout, monkeypatch):
    manager, _, upstream = checkout
    advance(upstream)
    monkeypatch.setattr(manager, "deployment", lambda: {
        "workspace": "/srv/repo", "data": "/srv/data", "project": "fixture", "image": "sha256:fixture",
    })
    def launch(*args, **kwargs):
        raise UpdateError("Docker response timed out", 504)
    monkeypatch.setattr(manager, "docker", launch)
    monkeypatch.setattr(manager, "worker_running", lambda name: True)
    assert manager.start()["state"] == "updating"
    with pytest.raises(UpdateError) as failure:
        manager.check()
    assert failure.value.status_code == 409


def test_failed_queued_worker_cannot_apply_later(checkout, monkeypatch):
    manager, local, upstream = checkout
    target = advance(upstream)
    manager.check()
    manager.save({**manager.read(), "state": "failure", "job_id": "fixture", "operation": "update"})
    monkeypatch.setattr(manager, "compose", lambda *args, **kwargs: pytest.fail("Cancelled worker must not deploy"))
    manager.run_worker("fixture", target, "update", "fixture", str(manager.data_root))
    assert git(local, "rev-parse", "HEAD") != target
    assert manager.status()["state"] == "failure"


@pytest.mark.parametrize("fails", [False, True])
def test_worker_reports_real_completion_and_rebuild_failures(checkout, monkeypatch, fails):
    manager, local, upstream = checkout
    target = advance(upstream)
    manager.check()
    manager.save({**manager.read(), "state": "updating", "job_id": "fixture", "operation": "update"})
    calls = []
    def compose(*args, **kwargs):
        calls.append(args)
        if fails and args == ("build",):
            raise UpdateError("Fixture build failed", 500)
        return ""
    monkeypatch.setattr(manager, "compose", compose)
    monkeypatch.setattr(manager, "wait_ready", lambda: None)
    manager.run_worker("fixture", target, "update", "fixture-project", str(manager.data_root))
    result = manager.status()
    assert result["state"] == ("failure" if fails else "success")
    assert result["requires_rebuild"] is fails
    assert git(local, "rev-parse", "HEAD") == target
    assert ("build",) in calls
    assert (("up", "-d", "--force-recreate") in calls) is not fails
