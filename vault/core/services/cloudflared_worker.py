"""Independent tunnel process; exposes HTTP API and manages the cloudflared process directly in the tunnel container."""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from services.cloudflared_service import CloudflaredManager, _write_json


logger = logging.getLogger(__name__)


class TunnelWorker:
    def __init__(self, manager: CloudflaredManager, port: int = 8080):
        self.manager = manager
        self.port = port
        self._active_config = None
        self._server = None
        self._lock = asyncio.Lock()
        self._disconnected_since = None

    def publish_status(self):
        try:
            status_data = self.manager.status()
            if asyncio.iscoroutine(status_data):
                return
            _write_json(self.manager.status_path, {**status_data, "updated_at": time.time()})
        except Exception as exc:
            logger.debug("Failed to publish status snapshot: %s", exc)

    async def tick(self):
        async with self._lock:
            await self._reconcile()

    async def _reconcile(self):
        config = self.manager.load_config(include_token=True)
        signature = (config["mode"], config["origin"], config.get("token", ""))
        state_changed = False
        if config["enabled"]:
            if signature != self._active_config:
                await self.manager.stop(disable=False)
                await self.manager.start(persist=False)
                self._active_config = signature
                self._disconnected_since = None
                state_changed = True
            if await self.manager.check_ready():
                self._disconnected_since = None
            elif self._disconnected_since is None:
                self._disconnected_since = time.monotonic()
            elif time.monotonic() - self._disconnected_since >= 120:
                logger.warning("Tunnel disconnected for 120s; restarting connector")
                await self.manager.stop(disable=False)
                await self.manager.start(persist=False)
                self._disconnected_since = time.monotonic()
                state_changed = True
        elif self._active_config is not None:
            await self.manager.stop(disable=False)
            self._active_config = None
            state_changed = True

        if state_changed:
            self.publish_status()

    async def handle_http_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            req_line = line.decode("utf-8", errors="replace").strip()
            parts = req_line.split()
            if len(parts) < 2:
                writer.close()
                return
            method, raw_path = parts[0].upper(), parts[1]
            path = urlparse(raw_path).path

            headers = {}
            while True:
                header_line = await reader.readline()
                if not header_line or header_line in (b"\r\n", b"\n"):
                    break
                h_str = header_line.decode("utf-8", errors="replace").strip()
                if ":" in h_str:
                    k, v = h_str.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_length = int(headers.get("content-length", 0))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            payload = {}
            if body:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    pass

            status_code = 200
            resp_body = {}

            # Route handling
            if path in ("/health", "/api/tunnel/health"):
                enabled = self.manager.load_config()["enabled"]
                connected = await self.manager.check_ready() if enabled else False
                ok = not enabled or connected
                status_code = 200 if ok else 503
                resp_body = {"status": "ok" if ok else "disconnected",
                             "running": self.manager.is_running(), "connected": connected}
            elif path in ("/status", "/api/tunnel/status") and method == "GET":
                resp_body = self.manager.status()
                if asyncio.iscoroutine(resp_body):
                    resp_body = await resp_body
            elif path in ("/config", "/api/tunnel/config") and method == "PUT":
                async with self._lock:
                    self.manager.save_config(payload)
                    await self._reconcile()
                    resp_body = self.manager.status()
            elif path in ("/start", "/api/tunnel/start") and method == "POST":
                async with self._lock:
                    self.manager.build_command()  # Validate before persisting enabled.
                    self.manager.save_config({"enabled": True})
                    await self._reconcile()
                    resp_body = self.manager.status()
            elif path in ("/stop", "/api/tunnel/stop") and method == "POST":
                async with self._lock:
                    resp_body = await self.manager.stop()
                    self._active_config = None
                    self._disconnected_since = None
                    self.publish_status()
            elif path in ("/create", "/api/tunnel/create") and method == "POST":
                async with self._lock:
                    resp_body = await self.manager.provision(**payload)
                    await self._reconcile()
            else:
                status_code = 404
                resp_body = {"error": "Not Found", "path": path}

            resp_bytes = json.dumps(resp_body).encode("utf-8")
            status_text = "OK" if status_code == 200 else ("Not Found" if status_code == 404 else "Error")
            response = (
                f"HTTP/1.1 {status_code} {status_text}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(resp_bytes)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii") + resp_bytes
            writer.write(response)
            await writer.drain()
        except Exception as exc:
            logger.error("Error handling HTTP request: %s", exc)
            try:
                err_bytes = json.dumps({"error": str(exc)}).encode("utf-8")
                writer.write(
                    f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: {len(err_bytes)}\r\nConnection: close\r\n\r\n".encode("ascii")
                    + err_bytes
                )
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def run(self, stop: asyncio.Event):
        try:
            self._server = await asyncio.start_server(
                self.handle_http_client, "0.0.0.0", self.port
            )
            logger.info("Tunnel worker API listening on port %s", self.port)
        except Exception as exc:
            logger.error("Failed to start tunnel API server on port %s: %s", self.port, exc)

        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception as exc:
                    logger.error("Tunnel reconcile failed: %s", exc, exc_info=True)
                    self.manager.logs.append(f"[worker] {exc}")
                    try:
                        self.publish_status()
                    except OSError:
                        pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._server:
                self._server.close()
                await self._server.wait_closed()
            await self.manager.stop(disable=False)
            self.publish_status()


async def main():
    secret = os.environ.get("SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters")
    data_root = os.environ.get("DATA_ROOT", "/saas-data")
    port = int(os.environ.get("TUNNEL_API_PORT", "8080"))
    manager = CloudflaredManager(data_root, secret, external=False)

    # Bootstrap config from environment variables if provided
    env_token = os.environ.get("TUNNEL_TOKEN", "").strip()
    env_hostname = os.environ.get("TUNNEL_HOSTNAME", "").strip()
    env_origin = os.environ.get("TUNNEL_ORIGIN", "").strip()
    env_mode = os.environ.get("TUNNEL_MODE", "").strip()
    if env_token:
        patch = {
            "mode": env_mode or "managed",
            "token": env_token,
            "enabled": True,
        }
        if env_hostname:
            patch["hostname"] = env_hostname
        if env_origin:
            patch["origin"] = env_origin
        manager.save_config(patch)
        logger.info("[bootstrap] Configured Cloudflare tunnel token from environment (enabled=True)")
    elif env_mode == "quick" and os.environ.get("TUNNEL_ENABLED", "").lower() in ("true", "1", "yes"):
        patch = {
            "mode": "quick",
            "enabled": True,
        }
        if env_origin:
            patch["origin"] = env_origin
        manager.save_config(patch)
        logger.info("[bootstrap] Configured Quick Tunnel from environment (enabled=True)")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await TunnelWorker(manager, port=port).run(stop)


def healthy() -> bool:
    try:
        path = Path(os.environ.get("DATA_ROOT", "/saas-data")) / "config/cloudflare_tunnel_status.json"
        if path.is_file():
            status = json.loads(path.read_text())
            return bool(0 <= time.time() - status.get("updated_at", 0) < 15
                        and (not status.get("enabled") or
                             (status.get("installed") and status.get("running") and status.get("connected"))))
        return False
    except Exception:
        return False


if __name__ == "__main__":
    if "--healthcheck" in sys.argv:
        sys.exit(0 if healthy() else 1)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
