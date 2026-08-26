// Minimal DOM / WebRTC / media stubs so static/js/voice.js can be exercised in
// Node. Only the surface voice.js actually touches is implemented — anything
// else is deliberately absent so an untested code path fails loudly.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const VOICE_SRC = path.join(__dirname, '..', '..', 'static', 'js', 'voice.js');

class FakeMediaElement {
  constructor() {
    this.id = '';
    this.autoplay = false;
    this.playsInline = false;
    this.muted = false;
    this.srcObject = null;
    this.paused = true;
    this.playCalls = 0;
    this.playRejects = false;   // simulate the autoplay policy refusing us
    this._doc = null;
  }
  play() {
    this.playCalls++;
    if (this.playRejects) return Promise.reject(new Error('NotAllowedError'));
    this.paused = false;
    return Promise.resolve();
  }
  remove() {
    if (!this._doc) return;
    const i = this._doc._els.indexOf(this);
    if (i >= 0) this._doc._els.splice(i, 1);
  }
}

function makeTrack(kind = 'audio', id = 'track') {
  return {
    kind, id, readyState: 'live', enabled: true,
    stop() { this.readyState = 'ended'; },
  };
}

function makeStream(id = 'mic') {
  const track = makeTrack('audio', id);
  return { id, _track: track, getTracks: () => [track], getAudioTracks: () => [track] };
}

class FakePeerConnection {
  constructor(config) {
    this.config = config;
    this.iceConnectionState = 'new';
    this.closed = false;
    this.offersCreated = 0;
    this.localDescription = null;
    this.remoteDescription = null;
    this.iceCandidates = [];
    this._senders = [];
    this._transceivers = [];
    this.onicecandidate = null;
    this.ontrack = null;
    FakePeerConnection.instances.push(this);
  }
  _mkSender(track) {
    const sender = {
      track,
      replaceTrack: async t => { sender.track = t; sender.replaceCalls = (sender.replaceCalls || 0) + 1; },
    };
    return sender;
  }
  addTrack(track) {
    const sender = this._mkSender(track);
    this._senders.push(sender);
    this._transceivers.push({ sender, direction: 'sendrecv', receiver: { track: null } });
    return sender;
  }
  addTransceiver(kind, opts = {}) {
    const sender = this._mkSender(null);
    const tx = { sender, direction: opts.direction || 'sendrecv', receiver: { track: null }, kind };
    this._senders.push(sender);
    this._transceivers.push(tx);
    return tx;
  }
  getSenders() { return this._senders; }
  getTransceivers() { return this._transceivers; }
  async createOffer() { this.offersCreated++; return { type: 'offer', sdp: `offer-${this.offersCreated}` }; }
  async createAnswer() { return { type: 'answer', sdp: 'answer-1' }; }
  async setLocalDescription(d) { this.localDescription = d; }
  async setRemoteDescription(d) { this.remoteDescription = d; }
  async addIceCandidate(c) { this.iceCandidates.push(c); }
  close() { this.closed = true; this.iceConnectionState = 'closed'; }
  // test helper: pretend the remote sent us audio
  fireTrack(stream) { this.ontrack({ streams: [stream] }); }
}

class FakeWs {
  constructor() { this.handlers = {}; this.sent = []; }
  on(event, fn) { this.handlers[event] = fn; return this; }
  send(event, payload) { this.sent.push({ event, ...(payload || {}) }); }
  emit(event, msg) { this.handlers[event] && this.handlers[event]({ event, ...msg }); }
  sentOf(event) { return this.sent.filter(m => m.event === event); }
}

function makeDocument() {
  const doc = {
    visibilityState: 'visible',
    _els: [],
    _listeners: {},
    body: { appendChild(el) { el._doc = doc; doc._els.push(el); } },
    autoplayBlocked: false,   // flip to simulate the browser autoplay policy
    createElement() {
      const el = new FakeMediaElement();
      el.playRejects = doc.autoplayBlocked;
      return el;
    },
    getElementById(id) { return doc._els.find(e => e.id === id) || null; },
    querySelectorAll(sel) {
      if (sel.indexOf('audio-peer-') !== -1) {
        return doc._els.filter(e => e.id.startsWith('audio-peer-'));
      }
      return [];   // .voice-indicator lookups — no UI in these tests
    },
    addEventListener(ev, fn) { (doc._listeners[ev] = doc._listeners[ev] || []).push(fn); },
    removeEventListener(ev, fn) {
      const a = doc._listeners[ev] || [];
      const i = a.indexOf(fn);
      if (i >= 0) a.splice(i, 1);
    },
    fire(ev) { (doc._listeners[ev] || []).slice().forEach(fn => fn()); },
    listenerCount(ev) { return (doc._listeners[ev] || []).length; },
    audioEls() { return doc._els.filter(e => e.id.startsWith('audio-peer-')); },
  };
  return doc;
}

// Controllable clock so the microphone-prompt grace period does not cost the
// test suite six real seconds.
function makeTimers() {
  let pending = [];
  let now = 0;
  return {
    setTimeout(fn, delay) { pending.push({ fn, at: now + (delay || 0) }); return pending.length; },
    clearTimeout() {},
    advance(ms) {
      now += ms;
      const due = pending.filter(t => t.at <= now);
      pending = pending.filter(t => t.at > now);
      due.forEach(t => t.fn());
    },
  };
}

/** Build a fresh sandbox and return the VoiceManager class plus the stubs. */
function loadVoice(opts = {}) {
  FakePeerConnection.instances = [];
  const doc = makeDocument();
  const timers = makeTimers();
  const store = Object.assign({}, opts.localStorage || {});

  const context = {
    document: doc,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    navigator: {
      mediaDevices: opts.noMediaDevices ? undefined : {
        getUserMedia: opts.getUserMedia || (async () => makeStream()),
      },
    },
    RTCPeerConnection: FakePeerConnection,
    fetch: opts.fetch || (async () => ({
      ok: true,
      json: async () => ({ uris: ['turn:example'], username: 'u', credential: 'c' }),
    })),
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    console,
    Promise,
  };
  vm.createContext(context);
  const src = fs.readFileSync(VOICE_SRC, 'utf8')
    + '\n;globalThis.VoiceManager = VoiceManager;\n';
  vm.runInContext(src, context, { filename: 'voice.js' });

  return {
    VoiceManager: context.VoiceManager,
    doc, timers, store,
    pcs: FakePeerConnection.instances,
    ws: new FakeWs(),
  };
}

const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };

module.exports = { loadVoice, makeStream, makeTrack, flush, FakePeerConnection };
