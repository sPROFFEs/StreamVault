#!/usr/bin/env python3
"""Repeatable frontend measurements using real templates/assets and offline APIs.

This isolates rendering/transfer costs, not database/provider or media latency.
Pass a source snapshot with --root to compare revisions without switching Git.
"""
import argparse
from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright
from starlette.requests import Request


ROOT = Path(__file__).resolve().parents[2]
VIEWPORTS = {"mobile": (390, 844), "desktop": (1440, 900), "tv": (1920, 1080)}
ROUTES = {
    "login": ("/login", "login.html", "input[name=username]", 1),
    "home": ("/portal", "portal.html", "#genre-rows .section:first-child .poster-card", 18),
    "catalog": ("/browse?type=movie", "browse.html", "#browse-grid .poster-card", 20),
    "large_catalog": ("/browse?type=movie&fixture_size=240", "browse.html", "#browse-grid .poster-card", 240),
    "settings": ("/settings", "settings.html", ".accent-swatch", 6),
    "admin": ("/admin?tab=overview", "admin.html", "input[name=storage_limit]", 1),
    "detail": ("/media/7?type=movie", "media_detail.html", "#detail-title", 1),
}
MOVIES = [{"id": i, "title": f"Fixture movie {i}", "name": f"Fixture series {i}",
           "media_type": "movie", "release_date": "2024-01-01", "vote_average": 8,
           "poster_path": None, "backdrop_path": None, "overview": "Offline fixture."}
          for i in range(1, 241)]
PREFS = {"accent": "red", "font_size": "normal", "home_visibility": {},
         "auto_source_failover": True, "subtitle_lang": "en"}


@contextmanager
def fixture_server(root, vendor):
    environment = Environment(loader=FileSystemLoader(root / "vault/core/templates"),
                              autoescape=select_autoescape(["html"]))
    environment.globals["_"] = lambda value: value

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            url = urlsplit(self.path)
            mime = "application/json"
            status = 200
            if url.path.startswith(("/assets/", "/vendor/")):
                base = (root / "vault/assets") if url.path.startswith("/assets/") else vendor
                relative = url.path.split("/", 2)[2]
                path = (base / relative).resolve()
                if not path.is_relative_to(base.resolve()) or not path.is_file():
                    status, body = 404, b""
                else:
                    body = path.read_bytes()
                mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            elif url.path in {urlsplit(route[0]).path for route in ROUTES.values()}:
                name = next(route[1] for route in ROUTES.values() if urlsplit(route[0]).path == url.path)
                user = SimpleNamespace(username="fixture-admin", is_admin=True, can_download=True)
                request = Request({"type": "http", "headers": [(b"cookie", b"lang=en")],
                                   "state": {"user": user}, "query_string": url.query.encode()})
                html = environment.get_template(name).render(request=request, username=user.username,
                            tmdb_id=7, media_type="movie", users=[], is_admin=True,
                            sys_stats={"cpu_usage": 12, "ram_percent": 34, "disk_total": 100,
                                       "disk_used": 10, "disk_percent": 10})
                html = re.sub(r"https://cdn\.jsdelivr\.net/npm/[^\"]+/dist/hls\.min\.js", "/vendor/hls.min.js", html)
                html = re.sub(r"https://cdn\.jsdelivr\.net/npm/[^\"]+/webtorrent\.min\.js", "/vendor/webtorrent.min.js", html)
                mime, body = "text/html", html.encode()
            else:
                count = int(parse_qs(urlsplit(self.headers.get("Referer", "")).query).get("fixture_size", [20])[0])
                payload = {}
                if url.path == "/api/preferences":
                    payload = PREFS
                elif url.path.startswith("/api/tmdb/genres/"):
                    payload = [{"id": 18, "name": "Drama"}]
                elif any(token in url.path for token in ("/discover/", "/trending/", "/recommendations", "/similar")):
                    payload = MOVIES[:count]
                elif url.path == "/api/tmdb/movie/7":
                    payload = MOVIES[6]
                elif url.path in {"/api/library/history", "/api/library/favorites", "/api/local/browse"}:
                    payload = {"items": []}
                elif url.path == "/api/downloads/library":
                    payload = {"movies": [], "series": []}
                elif url.path == "/admin/system/update/status":
                    payload = {"state": "current", "message": "Fixture is current"}
                elif url.path.startswith("/api/streams/"):
                    payload = {"streams": []}
                elif url.path.startswith("/api/subtitles/"):
                    payload = []
                elif url.path == "/api/health":
                    payload = {"status": "ok"}
                body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            # Vendored CDN bytes keep the encoding ordinarily supplied by CDN.
            if (url.path.startswith('/vendor/') and len(body) >= 1024 and "gzip" in self.headers.get("Accept-Encoding", "")
                    and (mime.startswith("text/") or mime in {"application/javascript", "application/json"})):
                body = gzip.compress(body, compresslevel=5, mtime=0)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def deployment_proxy(root, upstream, binary):
    """Use the real nginx rules; substitute only ports and deployment paths."""
    import socket
    import time
    with tempfile.TemporaryDirectory(prefix='streamvault-perf-proxy-') as directory:
        base = Path(directory)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        config = (root / 'vault/nginx.conf').read_text()
        config = config.replace('include       mime.types;', 'types { text/css css; application/javascript js; image/png png; font/woff2 woff2; }')
        config = config.replace('listen 80;', f'listen 127.0.0.1:{port};')
        config = config.replace('http://core:8000', upstream)
        config = config.replace('alias /app/assets/;', f'alias {root / "vault/assets"}/;')
        paths = '\n'.join(f'{kind}_temp_path {base}/{kind};' for kind in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
        config = config.replace('http {', f'http {{\naccess_log off;\n{paths}\n')
        path = base / 'nginx.conf'
        path.write_text(f'daemon off;\npid {base}/nginx.pid;\nerror_log stderr;\n' + config)
        process = subprocess.Popen([str(binary), '-p', str(base), '-c', str(path)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.05):
                        break
                except OSError:
                    time.sleep(.02)
            else:
                raise RuntimeError('nginx did not start')
            yield f'http://127.0.0.1:{port}'
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stderr.close()


def measure(browser, origin, name, viewport, args):
    route, _, selector, count = ROUTES[name]
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                  service_workers="block")
    page = context.new_page()
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(urlsplit(request.url).path))
    session = context.new_cdp_session(page)
    session.send("Network.enable")
    session.send("Network.setCacheDisabled", {"cacheDisabled": True})
    session.send("Network.emulateNetworkConditions", {"offline": False, "latency": args.rtt,
                 "downloadThroughput": args.mbps * 1_000_000 / 8,
                 "uploadThroughput": 750_000 / 8})
    session.send("Emulation.setCPUThrottlingRate", {"rate": args.cpu})
    page.add_init_script("""(() => {
      window.measurements = {lcp_ms: 0, cls: 0, blocking_ms: 0};
      for (const [type, record] of [
        ['largest-contentful-paint', e => measurements.lcp_ms = e.startTime],
        ['layout-shift', e => {if (!e.hadRecentInput) measurements.cls += e.value}],
        ['longtask', e => measurements.blocking_ms += Math.max(0, e.duration - 50)]
      ]) new PerformanceObserver(list => list.getEntries().forEach(record)).observe({type, buffered: true});
    })()""")
    # Record readiness inside the page, avoiding polling latency in the timing.
    page.add_init_script(f"""(() => {{
      const ready = () => {{
        if (document.querySelectorAll({json.dumps(selector)}).length < {count}) return;
        if ({json.dumps(name)} === 'detail' && document.querySelector('#detail-title').textContent !== 'Fixture movie 7') return;
        window.contentReady = performance.now(); observer.disconnect();
      }};
      const observer = new MutationObserver(ready);
      observer.observe(document, {{childList: true, subtree: true, characterData: true}});
    }})()""")
    try:
        page.goto(origin + route, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("window.contentReady !== undefined", timeout=30000)
        page.wait_for_timeout(1200)
        values = page.evaluate("""() => ({...measurements, ready_ms: contentReady,
          fcp_ms: performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0,
          asset_bytes: performance.getEntriesByType('resource').filter(e => /\\.(css|js)(\\?|$)/.test(e.name)).reduce((n,e) => n + e.encodedBodySize, 0),
          nodes: document.querySelectorAll('*').length
        })""")
        return {**values, "api_requests": sum(path.startswith(("/api/", "/admin/api/", "/admin/system/")) for path in requests),
                "preference_reads": requests.count("/api/preferences"), "errors": errors}
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--vendor", type=Path, required=True, help="Directory with pinned Hls.js bundle (missing vendor files return 404)")
    parser.add_argument("--nginx", type=Path, required=True, help="Path to nginx executable")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="Keep completed routes with identical conditions")
    parser.add_argument("--rtt", type=float, default=150)
    parser.add_argument("--mbps", type=float, default=1.6)
    parser.add_argument("--cpu", type=float, default=4)
    parser.add_argument("--routes", nargs="+", choices=list(ROUTES), default=list(ROUTES))
    args = parser.parse_args()
    report = {"label": args.label, "conditions": {"runs": args.runs, "rtt_ms": args.rtt,
              "download_mbps": args.mbps, "cpu_slowdown": args.cpu, "artwork": "absent",
              "cache": "cold", "apis": "offline fixtures", "compression": "real nginx; vendored CDN gzip"},
              "results": {}}
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text())
        if previous['label'] != report['label'] or previous['conditions'] != report['conditions']:
            parser.error('Existing report uses a different revision or conditions')
        report = previous
    with fixture_server(args.root.resolve(), args.vendor.resolve()) as upstream, deployment_proxy(args.root.resolve(), upstream, args.nginx.resolve()) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        report["chromium"] = browser.version
        try:
            for viewport, dimensions in VIEWPORTS.items():
                for name in args.routes:
                    if f'{viewport}/{name}' in report['results']:
                        continue
                    samples = [measure(browser, origin, name, dimensions, args) for _ in range(args.runs)]
                    medians = {key: round(statistics.median(sample[key] for sample in samples), 2)
                               for key in samples[0] if key != "errors"}
                    report["results"][f"{viewport}/{name}"] = {"median": medians, "samples": samples}
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    print(viewport, name, json.dumps(medians), flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
