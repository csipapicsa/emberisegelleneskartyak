const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC = path.join(__dirname, '..', '..', 'static', 'js', 'umami-normalize.js');
const ORIGIN = 'https://emberisegelleneskartyak.hu';

function load() {
  const sandbox = { URL, location: { origin: ORIGIN } };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SRC, 'utf8') +
    '\nglobalThis.normalizeAnalyticsUrl = normalizeAnalyticsUrl;' +
    '\nglobalThis.umamiBeforeSend = umamiBeforeSend;', sandbox);
  return sandbox;
}

test('room and game codes collapse into one analytics path', () => {
  const { normalizeAnalyticsUrl: n } = load();
  assert.strictEqual(n(`${ORIGIN}/room/6454`, ORIGIN), `${ORIGIN}/room/:code`);
  assert.strictEqual(n(`${ORIGIN}/game/6920/`, ORIGIN), `${ORIGIN}/game/:code`);
  assert.strictEqual(n('/game/0001', ORIGIN), '/game/:code');
});

test('invite code is masked, other params survive', () => {
  const { normalizeAnalyticsUrl: n } = load();
  assert.strictEqual(n(`${ORIGIN}/join?code=6454`, ORIGIN), `${ORIGIN}/join?code=xxxx`);
  assert.strictEqual(n(`${ORIGIN}/join?code=6454&utm_source=fb`, ORIGIN),
                     `${ORIGIN}/join?code=xxxx&utm_source=fb`);
  assert.strictEqual(n(`${ORIGIN}/kartyak?edition=5`, ORIGIN), `${ORIGIN}/kartyak?edition=5`);
  assert.strictEqual(n(`${ORIGIN}/?fbclid=abc`, ORIGIN), `${ORIGIN}/?fbclid=abc`);
  assert.strictEqual(n(`${ORIGIN}/room/1234?session_id=x#y`, ORIGIN), `${ORIGIN}/room/:code`);
});

test('other pages and external referrers are untouched', () => {
  const { normalizeAnalyticsUrl: n } = load();
  for (const p of ['/', '/join', '/create', '/kartyaotlet.html', '/roomy/1234', '/room/1/2']) {
    assert.strictEqual(n(`${ORIGIN}${p}`, ORIGIN), `${ORIGIN}${p}`, p);
  }
  assert.strictEqual(n('https://www.google.com/search?q=room/1234', ORIGIN),
                     'https://www.google.com/search?q=room/1234');
  assert.strictEqual(n('not a url ::', ORIGIN), `${ORIGIN}/not%20a%20url%20::`);
});

test('before-send hook rewrites url and referrer and keeps the payload', () => {
  const { umamiBeforeSend } = load();
  const out = umamiBeforeSend('event', {
    website: 'w', title: 'Játék', url: `${ORIGIN}/game/6920`, referrer: `${ORIGIN}/room/6920`,
  });
  assert.strictEqual(out.url, `${ORIGIN}/game/:code`);
  assert.strictEqual(out.referrer, `${ORIGIN}/room/:code`);
  assert.strictEqual(out.website, 'w');
  assert.strictEqual(umamiBeforeSend('event', undefined), undefined);
});
