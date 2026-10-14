import contextlib
import re
from pathlib import Path
import asyncio
import json
import time
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

try:
    import pytest
except ImportError:
    class _MockPytest:
        mark = SimpleNamespace(parametrize=lambda *a, **k: (lambda f: f))

        @contextlib.contextmanager
        def raises(self, exc_type, match=None):
            try:
                yield
            except exc_type as exc:
                if match and not re.search(match, str(exc)):
                    raise AssertionError(f"Pattern {match!r} not in {str(exc)!r}") from exc
            else:
                raise AssertionError(f"Expected {exc_type} but nothing was raised")
    pytest = _MockPytest()

from services.cloudflared_service import CloudflaredManager


def test_tunnel_config_encrypts_managed_token(tmp_path: Path):
    manager = CloudflaredManager(tmp_path, secret_key="test-secret", binary="cloudflared")

    manager.save_config({"mode": "managed", "token": "super-secret", "hostname": "watch.example.com"})

    raw = manager.config_path.read_text()
    loaded = manager.load_config(include_token=True)
    public = manager.load_config()
    assert "super-secret" not in raw
    assert loaded["token"] == "super-secret"
    assert public["token_set"] is True
    assert "token" not in public


def test_quick_tunnel_command_uses_configured_origin(tmp_path: Path):
    manager = CloudflaredManager(tmp_path, secret_key="test-secret", binary="/usr/bin/cloudflared")
    manager.save_config({"mode": "quick", "origin": "http://nginx:80"})

    command, env = manager.build_command()

    assert command[:3] == ["/usr/bin/cloudflared", "tunnel", "--no-autoupdate"]
    assert "--url" in command and "http://nginx:80" in command
    assert "TUNNEL_TOKEN" not in env


def test_managed_tunnel_passes_token_outside_process_arguments(tmp_path: Path):
    manager = CloudflaredManager(tmp_path, secret_key="test-secret", binary="cloudflared")
    manager.save_config({"mode": "managed", "token": "super-secret"})

    command, env = manager.build_command()

    assert "super-secret" not in command
    assert env["TUNNEL_TOKEN"] == "super-secret"


def test_external_manager_never_launches_or_stops_connector_on_core_shutdown(tmp_path, monkeypatch):
    manager = CloudflaredManager(tmp_path, "test-secret", external=True)
    manager.save_config({"mode": "managed", "token": "super-secret"})
    spawn = AsyncMock()
    monkeypatch.setattr(manager, "_spawn_process", spawn)
    asyncio.run(manager.start())
    assert manager.load_config()["enabled"]
    asyncio.run(manager.stop(disable=False))
    assert manager.load_config()["enabled"]
    spawn.assert_not_called()
    assert manager._supervisor_task is None
    asyncio.run(manager.stop())
    assert not manager.load_config()["enabled"]


def test_external_status_reads_worker_heartbeat_and_never_exposes_token(tmp_path):
    manager = CloudflaredManager(tmp_path, "test-secret", external=True)
    manager.save_config({"enabled": True, "mode": "managed", "token": "super-secret"})
    status = {"updated_at": time.time(), "running": True, "installed": True,
              "public_url": "https://watch.example.com", "recent_logs": ["Connected"]}
    manager.status_path.write_text(json.dumps(status))
    assert manager.status()["running"]
    assert manager.status()["public_url"] == "https://watch.example.com"
    assert "super-secret" not in json.dumps(manager.status())
    status["updated_at"] -= 60
    manager.status_path.write_text(json.dumps(status))
    assert not manager.status()["running"]


def test_worker_preserves_connector_across_core_restart_and_applies_config(tmp_path):
    from services.cloudflared_worker import TunnelWorker

    async def scenario():
        manager = CloudflaredManager(tmp_path, "test-secret")
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        worker = TunnelWorker(manager)
        control = CloudflaredManager(tmp_path, "test-secret", external=True)
        control.save_config({"enabled": True, "mode": "managed", "token": "old-token"})
        await worker.tick()
        manager.start.assert_awaited_once_with(persist=False)
        manager.start.reset_mock()
        manager.stop.reset_mock()
        await control.stop(disable=False)  # core shuts down
        control = CloudflaredManager(tmp_path, "test-secret", external=True)  # new core
        await worker.tick()
        manager.start.assert_not_awaited()
        manager.stop.assert_not_awaited()
        control.save_config({"token": "new-token"})
        await worker.tick()
        manager.stop.assert_awaited_once_with(disable=False)
        manager.start.assert_awaited_once_with(persist=False)
        manager.stop.reset_mock()
        await control.stop()  # explicit admin action
        await worker.tick()
        manager.stop.assert_awaited_once_with(disable=False)
        assert not control.load_config()["enabled"]
        assert "new-token" not in manager.status_path.read_text()

    asyncio.run(scenario())


def test_real_connector_process_survives_core_restart_and_stops_on_admin_request(tmp_path):
    from services.cloudflared_worker import TunnelWorker

    binary = tmp_path / "fake-cloudflared"
    binary.write_text(f"#!{sys.executable}\nimport time\nprint('https://test.trycloudflare.com', flush=True)\ntime.sleep(60)\n")
    binary.chmod(0o700)

    async def scenario():
        connector = CloudflaredManager(tmp_path, "test-secret", binary=str(binary))
        worker = TunnelWorker(connector)
        control = CloudflaredManager(tmp_path, "test-secret", external=True)
        await control.start()
        try:
            await worker.tick()
            process = connector.process
            assert process and process.returncode is None
            assert control.status()["running"]
            await control.stop(disable=False)
            restarted_core = CloudflaredManager(tmp_path, "test-secret", external=True)
            await worker.tick()
            assert connector.process is process
            assert restarted_core.status()["running"]
            assert restarted_core.status()["public_url"] == "https://test.trycloudflare.com"
            await restarted_core.stop()
            await worker.tick()
            assert process.returncode is not None
            assert not restarted_core.status()["running"]
        finally:
            await connector.stop(disable=False)

    asyncio.run(scenario())


def test_worker_start_failure_retries_without_changing_admin_configuration(tmp_path, monkeypatch):
    from services.cloudflared_worker import TunnelWorker

    async def scenario():
        manager = CloudflaredManager(tmp_path, "test-secret")
        manager.save_config({"enabled": True, "mode": "managed", "token": "super-secret"})
        manager.start = AsyncMock(side_effect=[RuntimeError("connector unavailable"), None])
        manager.stop = AsyncMock()
        worker = TunnelWorker(manager)
        stop = asyncio.Event()
        original_tick = worker.tick

        async def tick():
            if manager.start.await_count == 1:
                stop.set()
            await original_tick()

        monkeypatch.setattr(worker, "tick", tick)
        await worker.run(stop)
        assert manager.start.await_count == 2
        assert manager.load_config()["enabled"]
        assert "super-secret" not in manager.status_path.read_text()

    asyncio.run(scenario())


def test_tunnel_worker_http_api(tmp_path):
    import urllib.request
    from services.cloudflared_worker import TunnelWorker

    async def scenario():
        manager = CloudflaredManager(tmp_path, "test-secret-long-enough-32-chars-ok", external=False)
        worker = TunnelWorker(manager, port=8899)
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await asyncio.sleep(0.3)
        try:
            def req(path, method="GET", data=None):
                r = urllib.request.Request(f"http://127.0.0.1:8899{path}", method=method)
                if data is not None:
                    r.data = json.dumps(data).encode("utf-8")
                    r.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(r) as resp:
                    return json.loads(resp.read().decode())

            loop = asyncio.get_running_loop()
            h = await loop.run_in_executor(None, req, "/health")
            assert h["status"] == "ok"
            s = await loop.run_in_executor(None, req, "/api/tunnel/status")
            assert "enabled" in s
            c = await loop.run_in_executor(None, req, "/api/tunnel/config", "PUT", {"mode": "quick", "origin": "http://nginx:80"})
            assert c["origin"] == "http://nginx:80"
        finally:
            stop.set()
            await task

    asyncio.run(scenario())


async def worker_request(worker, method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    reader = asyncio.StreamReader()
    reader.feed_data(f"{method} {path} HTTP/1.1\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
    reader.feed_eof()
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
    await worker.handle_http_client(reader, writer)
    return b"".join(call.args[0] for call in writer.write.call_args_list)


@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/api/tunnel/start", None),
    ("PUT", "/api/tunnel/config", {"origin": "http://nginx:80"}),
])
def test_tunnel_controls_do_not_restart_unchanged_connector(tmp_path, method, path, payload):
    from services.cloudflared_worker import TunnelWorker

    async def scenario():
        manager = CloudflaredManager(tmp_path, "test-secret")
        manager.save_config({"enabled": True})
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        worker = TunnelWorker(manager)
        await worker.tick()
        manager.start.reset_mock()
        manager.stop.reset_mock()
        response = await worker_request(worker, method, path, payload)
        assert b"200 OK" in response
        await worker.tick()
        manager.stop.assert_not_awaited()
        manager.start.assert_not_awaited()
    asyncio.run(scenario())


def test_health_rejects_enabled_but_disconnected_connector(tmp_path, monkeypatch):
    from services.cloudflared_worker import TunnelWorker, healthy

    manager = CloudflaredManager(tmp_path, "test-secret")
    manager.save_config({"enabled": True})
    response = asyncio.run(worker_request(TunnelWorker(manager), "GET", "/health"))
    assert b"503" in response.splitlines()[0]
    manager.status_path.write_text(json.dumps({
        "updated_at": time.time(), "installed": True, "enabled": True,
        "running": True, "connected": False,
    }))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert healthy() is False


def test_worker_recovers_process_that_stays_alive_but_disconnected(tmp_path, monkeypatch):
    from services.cloudflared_worker import TunnelWorker

    async def scenario():
        manager = CloudflaredManager(tmp_path, "test-secret")
        manager.save_config({"enabled": True})
        manager.start = AsyncMock()
        manager.stop = AsyncMock()
        manager.check_ready = AsyncMock(return_value=False)
        now = [1000.0]
        monkeypatch.setattr("services.cloudflared_worker.time", SimpleNamespace(
            time=time.time, monotonic=lambda: now[0]))
        worker = TunnelWorker(manager)
        await worker.tick()
        manager.stop.reset_mock()
        manager.start.reset_mock()
        now[0] += 30
        await worker.tick()
        manager.stop.assert_not_awaited()
        now[0] += 100
        await worker.tick()
        manager.stop.assert_awaited_once_with(disable=False)
        manager.start.assert_awaited_once_with(persist=False)
    asyncio.run(scenario())


@pytest.mark.parametrize("status_code,connected", [(200, True), (503, False)])
def test_connector_readiness_uses_private_metrics_endpoint(tmp_path, monkeypatch, status_code, connected):
    import httpx

    manager = CloudflaredManager(tmp_path, "test-secret")
    manager.process = SimpleNamespace(returncode=None)
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code))
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr("services.cloudflared_service.httpx.AsyncClient", lambda **_: client)
    assert asyncio.run(manager.check_ready()) is connected
    assert manager.status()["connected"] is connected
    command, _ = manager.build_command()
    assert command[command.index("--metrics") + 1] == "127.0.0.1:2000"


@pytest.mark.parametrize("enabled,running,connected,age,expected", [
    (True, True, True, 0, True),
    (True, True, False, 0, False),
    (True, False, True, 0, False),
    (False, False, False, 0, True),
    (True, True, True, 30, False),
])
def test_health_requires_fresh_connected_snapshot(tmp_path, monkeypatch, enabled, running, connected, age, expected):
    from services.cloudflared_worker import healthy
    manager = CloudflaredManager(tmp_path, "test-secret")
    manager.save_config({"enabled": enabled})
    manager.status_path.write_text(json.dumps({
        "updated_at": time.time() - age, "installed": True, "enabled": enabled,
        "running": running, "connected": connected,
    }))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    assert healthy() is expected
