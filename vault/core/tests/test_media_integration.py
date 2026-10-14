"""Exercise real media tools when available, using only generated local data."""
import asyncio
import hashlib
import os
import shutil
import socket
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import pytest

from routers import downloads
from services import torrent_service as torrents
from services.media_library import find_matching_media, is_incomplete_media, scan_media_library


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools required")
def test_real_video_conversion_and_seekable_cache(tmp_path, monkeypatch):
    source = tmp_path / "sample.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "4", "-c:v", "mpeg4", "-c:a", "ac3", str(source),
    ], check=True, capture_output=True, timeout=15)
    monkeypatch.setattr(downloads.settings, "MEDIA_STORAGE_PATH", str(tmp_path / "cache"))
    cached = downloads._remux_cache_path(source, "0:1")
    asyncio.run(downloads._build_remux_cache(source, "0:1", cached))
    assert cached.is_file()
    tracks = downloads._probe_media_tracks(cached)
    assert tracks["video_codec"] == "h264"
    assert tracks["audio"][0]["codec"] == "aac"
    assert 3.9 < tracks["duration_seconds"] < 4.2
    content = cached.read_bytes()
    assert content.index(b"moov") < content.index(b"mdat")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(cached), "-f", "null", "-"],
                   check=True, capture_output=True, timeout=15)


def _bencode(value):
    if isinstance(value, int):
        return f"i{value}e".encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in sorted(value)) + b"e"


@pytest.mark.skipif(not shutil.which("aria2c"), reason="aria2 required")
@pytest.mark.parametrize("file_idx", [0, 1, None])
def test_real_magnet_selects_episode_or_whole_season(tmp_path, monkeypatch, file_idx):
    seed_dir = tmp_path / "seed"
    season = seed_dir / "Show.S01"
    season.mkdir(parents=True)
    payloads = [os.urandom(180_000), os.urandom(220_000)]
    names = ["Show.S01E01.mkv", "Show.S01E02.mkv"]
    for name, data in zip(names, payloads):
        (season / name).write_bytes(data)
    combined = b"".join(payloads)
    info = {
        b"name": b"Show.S01", b"piece length": 16384,
        b"pieces": b"".join(hashlib.sha1(combined[i:i + 16384]).digest() for i in range(0, len(combined), 16384)),
        b"files": [{b"length": len(data), b"path": [name.encode()]} for name, data in zip(names, payloads)],
    }
    info_hash = hashlib.sha1(_bencode(info)).hexdigest()
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        seed_port = port_socket.getsockname()[1]

    class Tracker(BaseHTTPRequestHandler):
        def do_GET(self):
            data = _bencode({b"interval": 1, b"peers": socket.inet_aton("127.0.0.1") + struct.pack("!H", seed_port)})
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Tracker)
    tracker = f"http://127.0.0.1:{server.server_port}/announce"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    metadata = tmp_path / "sample.torrent"
    metadata.write_bytes(_bencode({b"announce": tracker.encode(), b"info": info}))
    seeder = subprocess.Popen([
        "aria2c", "--no-conf=true", f"--dir={seed_dir}", f"--listen-port={seed_port}",
        "--seed-time=60", "--bt-seed-unverified=true", "--enable-dht=false",
        "--enable-dht6=false", "--enable-peer-exchange=false", "--bt-enable-lpd=false", str(metadata),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    original_command = torrents.build_aria2_command

    def local_command(*args):
        command = original_command(*args)
        command.insert(-1, "--enable-dht=false")
        command.insert(-1, "--enable-peer-exchange=false")
        return command

    monkeypatch.setattr(torrents, "build_aria2_command", local_command)
    monkeypatch.setattr(torrents, "DOWNLOADS_DIR", str(tmp_path / "downloads"))

    async def transfer():
        monkeypatch.setattr(torrents, "download_slots", asyncio.Semaphore(1))
        job = torrents.TorrentJob("Show", f"magnet:?xt=urn:btih:{info_hash}&tr={quote(tracker, safe='')}", info_hash, file_idx)
        service = torrents.TorrentService()
        await asyncio.wait_for(service._run(job), timeout=45)
        assert job.status == "completed", job.error_msg
        expected = range(2) if file_idx is None else [file_idx]
        assert len(job.local_paths) == len(expected)
        for index in expected:
            path = next(path for path in job.local_paths if path.endswith(names[index]))
            from pathlib import Path
            assert Path(path).read_bytes() == payloads[index]
        assert not (tmp_path / "downloads" / job.id / ".streamvault-incomplete").exists()
        library_root = tmp_path / "downloads"
        library = scan_media_library(library_root)
        assert {item["name"] for item in library["series"]} == {names[index] for index in expected}
        for index in expected:
            match = find_matching_media(library_root, "Show", "tv", season=1, episode=index + 1)
            assert match is not None
            assert not is_incomplete_media(library_root / match["relative_path"], library_root)

    try:
        asyncio.run(transfer())
    finally:
        seeder.terminate()
        try:
            seeder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            seeder.kill()
            seeder.wait()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
