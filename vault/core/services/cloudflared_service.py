"""Admin-managed Cloudflare Tunnel lifecycle and configuration."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    class InvalidToken(Exception):
        pass

    class Fernet:
        def __init__(self, key):
            self.key = key

        def encrypt(self, data: bytes) -> bytes:
            return b"enc:" + base64.b64encode(data)

        def decrypt(self, token: bytes) -> bytes:
            if not token.startswith(b"enc:"):
                raise InvalidToken("Invalid token")
            return base64.b64decode(token[4:])

logger = logging.getLogger(__name__)

CF_API = "https://api.cloudflare.com/client/v4"
QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _write_json(path: Path, payload: dict) -> None:
    """Publish complete private files for the other container to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, indent=2)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class CloudflaredManager:
    def __init__(
        self,
        data_root: str | Path,
        secret_key: str,
        binary: str = "cloudflared",
        *,
        external: bool = False,
        tunnel_url: str = "",
    ):
        self.config_path = Path(data_root) / "config" / "cloudflare_tunnel.json"
        self.status_path = self.config_path.with_name("cloudflare_tunnel_status.json")
        self.external = external
        self.tunnel_url = tunnel_url.rstrip("/")
        self.binary = binary
        digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(digest))
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._should_run: bool = False
        self.logs: deque[str] = deque(maxlen=80)
        self.public_url: str | None = None
        self.connected = False

    @staticmethod
    def defaults() -> dict[str, Any]:
        return {
            "enabled": False,
            "mode": "quick",
            "origin": "http://nginx:80",
            "hostname": "",
            "tunnel_id": "",
            "tunnel_name": "streamvault",
            "account_id": "",
            "zone_id": "",
        }

    def is_running(self) -> bool:
        return bool(self.process and self.process.returncode is None)

    async def check_ready(self) -> bool:
        """Check edge connectivity, not just whether the child process exists."""
        self.connected = False
        if self.is_running():
            if httpx is not None:
                try:
                    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                        response = await client.get("http://127.0.0.1:2000/ready")
                        self.connected = response.status_code == 200
                except Exception:
                    self.connected = True
            else:
                self.connected = True
        return self.connected

    def load_config(self, include_token: bool = False) -> dict[str, Any]:
        config = self.defaults()
        if self.config_path.exists():
            try:
                config.update(json.loads(self.config_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        encrypted = config.pop("token_encrypted", "")
        token = ""
        if encrypted:
            try:
                token = self._cipher.decrypt(encrypted.encode("ascii")).decode("utf-8")
            except (InvalidToken, ValueError):
                token = ""
        config["token_set"] = bool(token)
        if include_token:
            config["token"] = token
        return config

    def save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        patch = dict(patch)
        current = self.load_config(include_token=True)
        token = patch.pop("token", current.pop("token", ""))
        current.pop("token_set", None)
        current.update({key: value for key, value in patch.items() if key in self.defaults()})
        if current["mode"] not in {"quick", "managed"}:
            raise ValueError("Tunnel mode must be quick or managed")
        if not str(current["origin"]).startswith(("http://", "https://")):
            raise ValueError("Origin must be an HTTP or HTTPS URL")
        if token:
            current["token_encrypted"] = self._cipher.encrypt(token.encode("utf-8")).decode("ascii")
        _write_json(self.config_path, current)
        return self.load_config()

    def build_command(self) -> tuple[list[str], dict[str, str]]:
        config = self.load_config(include_token=True)
        env = dict(os.environ)
        # Use http2 over TCP by default to eliminate QUIC/UDP packet loss disconnects during torrent bursts
        protocol = os.environ.get("TUNNEL_PROTOCOL", "http2").strip() or "http2"
        base_flags = [
            self.binary, "tunnel",
            "--no-autoupdate",
            "--protocol", protocol,
            "--metrics", "127.0.0.1:2000",
        ]
        if config["mode"] == "managed":
            if not config.get("token"):
                raise ValueError("A managed tunnel token is required")
            token = config["token"]
            env["TUNNEL_TOKEN"] = token
            return base_flags + ["run"], env
        return base_flags + ["--url", config["origin"]], env

    async def _tunnel_req(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        if not self.tunnel_url:
            raise RuntimeError("Tunnel URL not configured")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, f"{self.tunnel_url}{path}", json=payload)
            if not resp.is_success:
                try:
                    data = resp.json()
                    err = data.get("error") or resp.text
                except Exception:
                    err = resp.text
                raise RuntimeError(err or f"Tunnel container returned HTTP {resp.status_code}")
            return resp.json()

    async def provision(
        self,
        *,
        account_id: str,
        zone_id: str,
        api_token: str,
        tunnel_name: str,
        hostname: str,
        origin: str,
    ) -> dict[str, Any]:
        """Create a remotely-managed tunnel, ingress config and DNS route."""
        if self.tunnel_url:
            return await self._tunnel_req("POST", "/api/tunnel/create", {
                "account_id": account_id,
                "zone_id": zone_id,
                "api_token": api_token,
                "tunnel_name": tunnel_name,
                "hostname": hostname,
                "origin": origin,
            })
        headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        secret = base64.b64encode(os.urandom(32)).decode("ascii")
        async with httpx.AsyncClient(timeout=20) as client:
            created = await self._cf_request(
                client, "POST", f"/accounts/{account_id}/cfd_tunnel", headers,
                {"name": tunnel_name, "config_src": "cloudflare", "tunnel_secret": secret},
            )
            tunnel_id = created["id"]
            await self._cf_request(
                client, "PUT",
                f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
                headers,
                {"config": {"ingress": [
                    {"hostname": hostname, "service": origin},
                    {"service": "http_status:404"},
                ]}},
            )
            await self._cf_request(
                client, "POST", f"/zones/{zone_id}/dns_records", headers,
                {"type": "CNAME", "name": hostname,
                 "content": f"{tunnel_id}.cfargotunnel.com", "proxied": True, "ttl": 1},
            )
            token = await self._cf_request(
                client, "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token", headers,
            )
        self.save_config({
            "mode": "managed", "account_id": account_id, "zone_id": zone_id,
            "tunnel_id": tunnel_id, "tunnel_name": tunnel_name,
            "hostname": hostname, "origin": origin, "token": token,
        })
        return self.status()

    async def _cf_request(self, client, method, path, headers, payload=None):
        response = await client.request(method, f"{CF_API}{path}", headers=headers, json=payload)
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Cloudflare returned HTTP {response.status_code}") from exc
        if not response.is_success or not body.get("success"):
            errors = "; ".join(item.get("message", "Unknown error") for item in body.get("errors", []))
            raise RuntimeError(errors or f"Cloudflare returned HTTP {response.status_code}")
        return body.get("result")

    async def start(self, *, persist: bool = True) -> dict[str, Any]:
        if self.tunnel_url:
            return await self._tunnel_req("POST", "/api/tunnel/start")
        if self.external:
            self.build_command()  # Validate the managed token before enabling.
            self.save_config({"enabled": True})
            return self.status()
        self._should_run = True
        if self.process and self.process.returncode is None:
            if not self._supervisor_task or self._supervisor_task.done():
                self._supervisor_task = asyncio.create_task(self._supervisor_loop())
            return self.status()
        await self._spawn_process()
        if persist:
            self.save_config({"enabled": True})
        if not self._supervisor_task or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(self._supervisor_loop())
        return self.status()

    async def _spawn_process(self):
        executable = self.binary if Path(self.binary).is_file() else shutil.which(self.binary)
        if not executable:
            raise RuntimeError("cloudflared is not installed in the StreamVault container")
        command, env = self.build_command()
        command[0] = executable
        self.public_url = None
        self.connected = False
        self.logs.clear()
        self.process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        self._reader_task = asyncio.create_task(self._read_output())
        await asyncio.sleep(0.25)
        if self.process.returncode is not None:
            raise RuntimeError(self.logs[-1] if self.logs else "cloudflared exited during startup")

    async def _supervisor_loop(self):
        """Monitor the cloudflared process and auto-reconnect if it exits unexpectedly."""
        backoff = 2.0
        while self._should_run:
            try:
                if self.process:
                    await self.process.wait()
                if not self._should_run:
                    break
                # Process exited unexpectedly while enabled
                self.logs.append(f"[supervisor] cloudflared exited (code {self.process.returncode if self.process else 'unknown'}), restarting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                if not self._should_run:
                    break
                await self._spawn_process()
                backoff = 2.0  # Reset backoff on successful start
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logs.append(f"[supervisor] restart error: {e}")
                backoff = min(backoff * 2, 30.0)

    async def _read_output(self):
        if not self.process or not self.process.stdout:
            return
        while line := await self.process.stdout.readline():
            text = line.decode("utf-8", errors="replace").strip()
            self.logs.append(text)
            logger.info("[cloudflared] %s", text)
            if match := QUICK_URL.search(text):
                self.public_url = match.group(0)

    async def stop(self, disable: bool = True) -> dict[str, Any]:
        if self.tunnel_url:
            return await self._tunnel_req("POST", "/api/tunnel/stop")
        if self.external:
            if disable:
                self.save_config({"enabled": False})
            return self.status()
        self._should_run = False
        if self._supervisor_task and not self._supervisor_task.done():
            self._supervisor_task.cancel()
            self._supervisor_task = None
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            self._reader_task = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        self.connected = False
        if disable:
            self.save_config({"enabled": False})
        return self.status()

    async def async_status(self) -> dict[str, Any]:
        if self.tunnel_url:
            try:
                return await self._tunnel_req("GET", "/api/tunnel/status")
            except Exception as exc:
                logger.warning("Tunnel container query failed (%s), falling back to snapshot: %s", self.tunnel_url, exc)
        elif not self.external:
            await self.check_ready()
        return self.status()

    async def async_save_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        if self.tunnel_url:
            return await self._tunnel_req("PUT", "/api/tunnel/config", patch)
        return self.save_config(patch)

    def status(self) -> dict[str, Any]:
        config = self.load_config()
        running = bool(self.process and self.process.returncode is None)
        configured_url = f"https://{config['hostname']}" if config.get("hostname") else None
        if self.external or self.tunnel_url:
            snapshot = {}
            try:
                snapshot = json.loads(self.status_path.read_text(encoding="utf-8"))
                if not 0 <= time.time() - float(snapshot.get("updated_at", 0)) < 15:
                    snapshot = {}
            except (OSError, ValueError, TypeError, AttributeError):
                snapshot = {}
            return {
                **config,
                "external": True,
                "installed": snapshot.get("installed", True),
                "running": bool(snapshot.get("running", False)),
                "connected": bool(snapshot.get("connected", False)),
                "public_url": snapshot.get("public_url") or configured_url,
                "recent_logs": snapshot.get("recent_logs", ["Waiting for the cloudflared service."]),
            }
        return {
            **config,
            "installed": bool(Path(self.binary).is_file() or shutil.which(self.binary)),
            "running": running,
            "connected": running and self.connected,
            "public_url": self.public_url or configured_url,
            "recent_logs": list(self.logs)[-12:],
        }
