"""Check the deployed proxy rules with real nginx (when installed)."""
import json
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest


def test_proxy_refreshes_docker_dns_after_backend_recreation():
    config = (Path(__file__).resolve().parents[2] / "nginx.conf").read_text()
    assert "resolver 127.0.0.11 valid=10s ipv6=off;" in config
    assert "set $core_backend http://core:8000;" in config
    assert config.count("proxy_pass $core_backend;") == 4


def test_federation_relays_are_unbuffered_and_do_not_spool_video_to_disk():
    config = (Path(__file__).resolve().parents[2] / "nginx.conf").read_text()
    assert "location /api/federation/ {" in config
    block = config.split("location /api/federation/ {", 1)[1].split("}", 1)[0]
    assert "proxy_buffering off;" in block
    assert "proxy_max_temp_file_size 0;" in block
    assert "gzip off;" in block
    assert "proxy_read_timeout 6h;" in block


@pytest.fixture
def proxy(tmp_path, request):
    binary = shutil.which("nginx")
    if not binary:
        pytest.skip("nginx required for proxy integration check")

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/downloads/files/?"):
                self.send_response(307)
                self.send_header("Location", "http://watch.example.test" + self.path.replace("files/?", "files?"))
                self.end_headers()
                return
            body = json.dumps({"path": self.path, "proto": self.headers.get("X-Forwarded-Proto"),
                               "range": self.headers.get("Range")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # Keep the real location/proxy rules; substitute only deployment paths/ports.
    source = Path(__file__).resolve().parents[2] / "nginx.conf"
    config = source.read_text().replace("include       mime.types;", "types { text/css css; application/javascript js; }")
    config = config.replace("alias /app/assets/;", f"alias {source.parent / 'assets'}/;")
    if getattr(request, "param", None) == "legacy":
        config = config.replace("location /api/downloads/files {", "location /api/downloads/files/ {")
        config = config.replace("absolute_redirect off;", "absolute_redirect on;")
    config = config.replace("listen 80;", f"listen 127.0.0.1:{port};")
    config = config.replace("http://core:8000", f"http://127.0.0.1:{upstream.server_port}")
    temp_paths = "\n".join(f"{kind}_temp_path {tmp_path}/{kind};" for kind in
                           ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
    config = config.replace("http {", f"http {{\n    access_log off;\n{temp_paths}\n")
    config = f"daemon off;\npid {tmp_path}/nginx.pid;\nerror_log stderr;\n" + config
    config_path = tmp_path / "nginx.conf"
    config_path.write_text(config)
    process = subprocess.Popen([binary, "-p", str(tmp_path), "-c", str(config_path)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.05):
                    break
            except OSError:
                time.sleep(.02)
        else:
            pytest.fail("nginx did not start")
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2,
                          headers={"Host": "watch.example.test", "X-Forwarded-Proto": "https"}) as client:
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def test_library_request_is_proxied_without_redirect(proxy):
    response = proxy.get("/api/downloads/files?page=1")
    assert response.status_code == 200, response.headers.get("location")
    assert response.json() == {"path": "/api/downloads/files?page=1", "proto": "https", "range": None}


def test_static_styles_compress_and_vary_without_changing_cache_policy(proxy):
    response = proxy.get('/assets/css/app.css', headers={'Accept-Encoding': 'gzip'})
    assert response.status_code == 200
    assert response.headers.get('content-encoding') == 'gzip'
    assert response.headers.get('vary') == 'Accept-Encoding'
    assert 'no-cache, must-revalidate' in response.headers['cache-control']
    original = (Path(__file__).resolve().parents[2] / 'assets/css/app.css').read_bytes()
    assert response.content == original
    plain = proxy.get('/assets/css/app.css', headers={'Accept-Encoding': 'identity'})
    assert plain.content == original
    assert 'content-encoding' not in plain.headers


def test_upstream_slash_redirect_does_not_downgrade_to_http(proxy):
    response = proxy.get("/api/downloads/files/?page=1")
    assert response.status_code == 307
    assert response.headers["location"] == "/api/downloads/files?page=1"


def test_streaming_keeps_ranges_and_forwarded_https(proxy):
    response = proxy.get("/api/downloads/files/stream?path=video.mp4", headers={"Range": "bytes=3-6"})
    assert response.status_code == 200
    assert response.json()["range"] == "bytes=3-6"
    assert response.json()["proto"] == "https"


def test_direct_http_access_still_works(proxy):
    proxy.headers.pop("X-Forwarded-Proto")
    response = proxy.get("/api/downloads/files?page=1")
    assert response.status_code == 200
    assert response.json()["proto"] == "http"


@pytest.mark.parametrize("proxy", ["legacy"], indirect=True)
def test_library_alias_bypasses_legacy_nginx_redirect(proxy):
    old_response = proxy.get("/api/downloads/files?page=1")
    assert old_response.status_code == 301
    assert old_response.headers["location"].startswith("http://")
    response = proxy.get("/api/downloads/library?page=1")
    assert response.status_code == 200
    assert response.json()["path"] == "/api/downloads/library?page=1"
