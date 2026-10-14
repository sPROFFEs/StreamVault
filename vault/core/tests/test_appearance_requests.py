"""Preference reads share one document request and stay retryable."""
from pathlib import Path
import subprocess


def test_preferences_share_reads_retry_failures_and_use_saved_values():
    script = (Path(__file__).resolve().parents[2] / 'assets/js/appearance.js').read_text()
    probe = r'''
const assert = require('node:assert/strict');
global.window = {dispatchEvent() {}};
global.document = {readyState: 'loading', addEventListener() {},
  documentElement: {style: {setProperty() {}}}, body: {classList: {toggle() {}}}};
global.localStorage = {setItem() {}, getItem() {return null;}};
global.CustomEvent = function() {};
let reads = 0, fail = true, release;
global.fetch = async (_, options) => {
  if (options?.method === 'PUT') return {ok: true, json: async () => ({accent: 'blue'})};
  reads++;
  await new Promise(resolve => {release = resolve;});
  return {ok: !fail, json: async () => ({accent: 'red'})};
};
__SCRIPT__
(async () => {
  const api = window.SVAppearance;
  const first = api.loadPrefs(), duplicate = api.loadPrefs();
  assert.equal(reads, 1);
  release();
  assert.deepEqual(await Promise.all([first, duplicate]), [null, null]);
  fail = false;
  const retry = api.loadPrefs();
  assert.equal(reads, 2);
  release();
  assert.equal((await retry).accent, 'red');
  assert.equal((await api.loadPrefs()).accent, 'red');
  assert.equal(reads, 2);
  await api.savePrefs({accent: 'blue'});
  assert.equal((await api.loadPrefs()).accent, 'blue');
  assert.equal(reads, 2);
  __SCRIPT__
  const replacement = window.SVAppearance;
  const olderRead = replacement.loadPrefs();
  await replacement.savePrefs({accent: 'blue'});
  release();
  assert.equal((await olderRead).accent, 'blue');
  assert.equal((await replacement.loadPrefs()).accent, 'blue');
})().catch(error => {console.error(error); process.exitCode = 1;});
'''.replace('__SCRIPT__', script)
    result = subprocess.run(['node', '-e', probe], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
