import json
import subprocess
from pathlib import Path


ASSETS = Path(__file__).resolve().parents[2] / "assets/js"


def test_local_player_uses_server_offset_even_with_infinite_duration():
    script = (ASSETS / "local-player.js").read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {};
global.document = {createElement: () => ({append() {}, addEventListener() {}, remove() {}})};
const video = {
  dataset: {}, duration: Infinity, currentTime: 0,
  pause() {}, load() {}, removeAttribute() {},
  addEventListener(name, fn) { this[name] = fn; }, removeEventListener() {},
  insertAdjacentElement() {}, play: async () => {},
};
let descriptor = {mode: 'stream', offset: 30, url: '/stream?start=30', duration_seconds: 100};
global.fetch = async () => ({ok: true, json: async () => descriptor});
__SCRIPT__
(async () => {
  await window.SVLocalPlayback.open(video, {relative_path: 'test.mkv', duration_seconds: 100}, {start: 30});
  video.loadedmetadata();
  assert.equal(video.currentTime, 0);
  video.currentTime = 4;
  assert.equal(window.SVLocalPlayback.currentTime(video), 34);
  descriptor = {mode: 'file', offset: 0, url: '/cached', duration_seconds: 100};
  video.duration = 100;
  await window.SVLocalPlayback.open(video, {relative_path: 'test.mkv'}, {start: 34});
  video.loadedmetadata();
  assert.equal(window.SVLocalPlayback.currentTime(video), 34);
  window.SVLocalPlayback.close(video);
  assert.equal(video.dataset.playbackOffset, '0');
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("__SCRIPT__", script)
    subprocess.run(["node", "-e", probe], check=True, capture_output=True, text=True)


def test_library_refresh_installs_only_one_click_handler():
    script = (ASSETS / "download-manager.js").read_text().replace("boot();", "")
    probe = r"""
global.window = {APP_LANG: 'en'};
const elements = new Map();
global.document = {addEventListener() {}, getElementById(id) {
  if (!elements.has(id)) elements.set(id, {style: {}, addEventListener() {this.handlers = (this.handlers || 0) + 1;}});
  return elements.get(id);
}};
__SCRIPT__
const data = {files: [{name: 'Movie.mp4', relative_path: 'Movie.mp4'}], movies: [], series: [], used_bytes: 1, quota_bytes: 10};
renderLibrary(data);
renderLibrary(data);
renderLibrary(data);
const library = elements.get('library-list');
require('node:assert/strict').equal((library.handlers || 0) + (library.onclick ? 1 : 0), 1);
""".replace("__SCRIPT__", script)
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_closed_local_player_ignores_late_descriptor_errors():
    script = (ASSETS / "local-player.js").read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {};
let rejectDescriptor;
const video = {
  dataset: {}, pause() {}, load() {}, removeAttribute() {},
  removeEventListener() {}, addEventListener() {},
};
global.fetch = async () => ({ok: true, json: () => new Promise((_, reject) => {rejectDescriptor = reject;})});
__SCRIPT__
(async () => {
  const pending = window.SVLocalPlayback.open(video, {relative_path: 'fixture.mkv'});
  await new Promise(resolve => setImmediate(resolve));
  window.SVLocalPlayback.close(video);
  rejectDescriptor(new Error('Late network failure'));
  await pending;
  assert.equal(video._playbackRequest, null);
  assert.equal(video.src, undefined);
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("__SCRIPT__", script)
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_seek_reuses_native_file_and_buffered_fragments_without_resuming_paused_video():
    script = (ASSETS / 'local-player.js').read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {};
let requests = 0, plays = 0;
let descriptor = {mode: 'file', offset: 0, url: '/video', duration_seconds: 300};
const video = {
  dataset: {}, currentTime: 0, readyState: 4, paused: true, playbackRate: 1.5,
  buffered: {length: 1, start: () => 0, end: () => 50},
  pause() {this.paused = true;}, load() {}, removeAttribute() {}, removeEventListener() {},
  addEventListener(name, callback) {if (name === 'loadedmetadata') this.loadedmetadata = callback;},
  play: async function() {this.paused = false; plays++;},
};
global.fetch = async () => { requests++; return {ok: true, json: async () => descriptor}; };
__SCRIPT__
(async () => {
  const source = {relative_path: 'video.mkv'};
  await window.SVLocalPlayback.open(video, source);
  video.loadedmetadata();
  video.pause();
  await window.SVLocalPlayback.seek(video, source, {start: 35});
  assert.equal(video.currentTime, 35);
  assert.equal(requests, 1);
  assert.equal(plays, 1);
  descriptor = {mode: 'stream', offset: 30, url: '/stream', duration_seconds: 300};
  await window.SVLocalPlayback.open(video, source, {start: 30});
  video.loadedmetadata();
  video.pause();
  await window.SVLocalPlayback.seek(video, source, {start: 60});
  assert.equal(video.currentTime, 30);
  assert.equal(requests, 2, 'Buffered seeks must not restart FFmpeg');
  const before = plays;
  descriptor = {...descriptor, offset: 200};
  await window.SVLocalPlayback.seek(video, source, {start: 200});
  video.loadedmetadata();
  assert.equal(requests, 3);
  assert.equal(plays, before, 'A paused seek must remain paused');
  assert.equal(video.playbackRate, 1.5);
  window.SVLocalPlayback.close(video);
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace('__SCRIPT__', script)
    result = subprocess.run(['node', '-e', probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stream_waits_for_buffer_and_close_cancels_delayed_play():
    script = (ASSETS / "local-player.js").read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {};
global.document = {createElement: () => ({append() {}, addEventListener() {}, remove() {}})};
let pending, plays = 0, bufferedEnd = 1;
global.setTimeout = fn => { pending = fn; return 42; };
global.clearTimeout = () => { pending = null; };
const video = {
  dataset: {}, duration: Infinity, currentTime: 0,
  buffered: {length: 1, start: () => 0, end: () => bufferedEnd},
  pause() {}, load() {}, removeAttribute() {},
  addEventListener(name, fn) {this[name] = fn;}, removeEventListener() {},
  insertAdjacentElement() {}, play: async () => { plays++; },
};
global.fetch = async () => ({ok: true, json: async () => ({
  mode: 'stream', offset: 0, url: '/stream', duration_seconds: 100,
})});
__SCRIPT__
(async () => {
  await window.SVLocalPlayback.open(video, {relative_path: 'video.mkv'});
  video.loadedmetadata();
  assert.equal(plays, 0, 'Do not start at the first tiny fragment');
  assert.equal(video.preload, 'auto');
  assert.equal(typeof pending, 'function');
  bufferedEnd = 6;
  pending();
  assert.equal(plays, 1);
  bufferedEnd = 1;
  await window.SVLocalPlayback.open(video, {relative_path: 'video.mkv'});
  video.loadedmetadata();
  const staleCallback = pending;
  window.SVLocalPlayback.close(video);
  assert.equal(pending, null);
  bufferedEnd = 6;
  staleCallback();
  assert.equal(plays, 1, 'Closing a player must not restart it later');
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace("__SCRIPT__", script)
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_stalled_playback_reconnects_at_same_position_and_pause_cancels_watchdog():
    script = (ASSETS / 'local-player.js').read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {};
const timers = new Map(); let timerId = 0, requests = [];
global.setTimeout = (fn, ms) => {timers.set(++timerId, {fn, ms}); return timerId;};
global.clearTimeout = id => timers.delete(id);
class Video extends EventTarget {
  dataset = {}; currentTime = 0; readyState = 2; paused = true; ended = false;
  duration = Infinity; buffered = {length: 1, start: () => 0, end: () => 90};
  pause() {this.paused = true; this.dispatchEvent(new Event('pause'));}
  async play() {this.paused = false;}
  load() {} removeAttribute() {}
}
const video = new Video();
global.fetch = async url => {requests.push(url); return {ok: true, json: async () => ({
  mode: 'stream', offset: Number(new URL(url, 'https://example.test').searchParams.get('start')),
  url: '/stream', duration_seconds: 300,
})};};
function fire(ms) {
  const entry = [...timers].find(([id, item]) => item.ms === ms);
  assert.ok(entry, `Missing ${ms}ms watchdog`);
  timers.delete(entry[0]); entry[1].fn();
}
__SCRIPT__
(async () => {
  await SVLocalPlayback.open(video, {relative_path: 'video.mkv'}, {start: 30});
  video.dispatchEvent(new Event('loadedmetadata'));
  video.currentTime = 12;
  video.dispatchEvent(new Event('waiting'));
  video.pause();
  assert.ok(![...timers.values()].some(item => item.ms === 15000));
  await video.play(); video.dispatchEvent(new Event('waiting'));
  fire(15000); fire(1500);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.equal(new URL(requests[1], 'https://example.test').searchParams.get('start'), '42');
  SVLocalPlayback.close(video);
  assert.equal(timers.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
""".replace('__SCRIPT__', script).replace('SVLocalPlayback.', 'window.SVLocalPlayback.')
    result = subprocess.run(['node', '-e', probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_tunnel_badge_requires_edge_connectivity():
    script = (ASSETS / "admin.js").read_text().replace(
        'document.addEventListener("DOMContentLoaded", boot);', 'window.renderTunnel = render;')
    probe = r"""
global.window = {APP_LANG: 'en'};
const elements = new Map();
global.document = {querySelector(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    classList: {remove() {}}, setAttribute(name, value) { this[name] = value; },
  });
  return elements.get(selector);
}};
__SCRIPT__
const assert = require('node:assert/strict');
window.renderTunnel({running: true, connected: false, installed: true});
assert.equal(elements.get('#tunnel-status').textContent, 'Connecting');
assert.ok(!elements.get('#tunnel-status').className.includes('badge--ok'));
window.renderTunnel({running: true, connected: true, installed: true});
assert.equal(elements.get('#tunnel-status').textContent, 'Connected');
assert.equal(elements.get('#tunnel-result').role, 'status');
""".replace("__SCRIPT__", script)
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_player_reports_persistent_media_errors_and_cancels_retries():
    script = (ASSETS / "local-player.js").read_text()
    probe = r"""
const assert = require('node:assert/strict');
global.window = {APP_LANG: 'es'};
let pending, requests = 0, errors = [];
global.setTimeout = fn => { pending = fn; return 42; };
global.clearTimeout = () => { pending = null; };
const video = {
  dataset: {}, currentTime: 0, error: {code: 4},
  pause() {}, load() {}, removeAttribute() {},
  addEventListener(name, fn) { this['on_' + name] = fn; },
  removeEventListener(name) { this['on_' + name] = null; },
  play: async () => {},
};
global.fetch = async () => {
  requests++;
  return {ok: true, json: async () => ({mode: 'stream', offset: 0, url: '/stream', duration_seconds: 100})};
};
__SCRIPT__
(async () => {
  await window.SVLocalPlayback.open(video, {relative_path: 'test.mkv'}, {onError: e => errors.push(e.message)});
  for (let i = 0; i < 2; i++) {
    video.on_error();
    assert.equal(typeof pending, 'function');
    pending();
    await new Promise(resolve => setImmediate(resolve));
  }
  video.on_error();
  assert.equal(requests, 3);
  assert.equal(errors.length, 1);
  assert.ok(errors[0].includes('No se pudo cargar'));
  await window.SVLocalPlayback.open(video, {relative_path: 'test.mkv'});
  video.on_error();
  const stale = pending;
  window.SVLocalPlayback.close(video);
  assert.equal(pending, null);
  stale();
  assert.equal(requests, 4, 'A closed player must not retry later');
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace("__SCRIPT__", script)
    result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
