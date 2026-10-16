const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = path.join(__dirname, '..', '..', 'static', 'js', 'ads.js');

function loadAds() {
  const sandbox = { URLSearchParams };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SRC, 'utf8') +
    '\nglobalThis.adSlotMode = adSlotMode; globalThis.ADS_ENABLED = ADS_ENABLED;', sandbox);
  return sandbox;
}

test('ads are off by default', () => {
  assert.strictEqual(loadAds().ADS_ENABLED, false);
});

test('only /, /join and /create can ever show the banner', () => {
  const { adSlotMode } = loadAds();
  for (const p of ['/', '/join', '/join/', '/create']) {
    assert.strictEqual(adSlotMode(p, '', true), 'placeholder', p);
  }
  for (const p of ['/room/1234', '/game/1234', '/kartyak', '/kartyaotlet.html',
                   '/kartyaszavazas', '/adatkezeles', '/joinx']) {
    assert.strictEqual(adSlotMode(p, '', true), 'off', p);
    assert.strictEqual(adSlotMode(p, '?ads=preview', true), 'off', p);
  }
});

test('disabled → off, unless this page load asks for a preview', () => {
  const { adSlotMode } = loadAds();
  assert.strictEqual(adSlotMode('/', '', false), 'off');
  assert.strictEqual(adSlotMode('/', '?ads=1', false), 'off');
  assert.strictEqual(adSlotMode('/', '?ads=preview', false), 'preview');
  assert.strictEqual(adSlotMode('/join', '?code=1234&ads=preview', false), 'preview');
});
