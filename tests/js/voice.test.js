// Client-side regression tests for the "first game has no sound" bug.
//
// Three independent causes produced the same symptom (silent first room, sound
// in the second): the autoplay policy leaving the <audio> element parked in a
// paused state, unmuting the speaker not restarting playback, and a refused or
// unanswered microphone prompt killing the channel in BOTH directions.
const test = require('node:test');
const assert = require('node:assert');
const { loadVoice, makeStream, flush } = require('./harness');

const SID = 'aaa';           // ours sorts first, so we are the offering side
const PEER = 'zzz';

async function readyManager(opts = {}) {
  const env = loadVoice(opts);
  const mgr = new env.VoiceManager(env.ws, SID);
  await mgr.init();
  return Object.assign(env, { mgr });
}

async function withRemoteAudio(opts = {}) {
  const env = await readyManager(opts);
  env.mgr.connectToPeers([SID, PEER]);
  env.pc = env.pcs[0];
  env.pc.fireTrack(makeStream('remote'));
  await flush();
  env.el = env.doc.audioEls()[0];
  return env;
}

// ── autoplay policy ───────────────────────────────────────────────────────────

test('remote audio is explicitly told to play, not just marked autoplay', async () => {
  const { el } = await withRemoteAudio();
  assert.ok(el.autoplay, 'element still carries the autoplay hint');
  assert.ok(el.playCalls >= 1, 'play() must be called explicitly — autoplay alone is only a request');
});

test('a refused play() arms a gesture retry that recovers the audio', async () => {
  const env = await readyManager();
  env.doc.autoplayBlocked = true;               // browser refuses without a gesture
  env.mgr.connectToPeers([SID, PEER]);
  env.pcs[0].fireTrack(makeStream('remote'));
  await flush();

  const el = env.doc.audioEls()[0];
  assert.equal(el.paused, true, 'blocked element stays paused — this was the silent first game');
  assert.equal(env.doc.listenerCount('click'), 1, 'a gesture listener is armed');
  assert.equal(env.doc.listenerCount('touchend'), 1);

  el.playRejects = false;                       // the tap makes playback allowed
  env.doc.fire('click');
  await flush();

  assert.equal(el.paused, false, 'audio recovers on the next tap');
  assert.equal(env.doc.listenerCount('click'), 0, 'listener is removed once it worked');
});

test('a still-refused retry stays armed for the following gesture', async () => {
  const env = await readyManager();
  env.doc.autoplayBlocked = true;
  env.mgr.connectToPeers([SID, PEER]);
  env.pcs[0].fireTrack(makeStream('remote'));
  await flush();

  env.doc.fire('click');                        // retry, still refused
  await flush();
  assert.equal(env.doc.listenerCount('click'), 1, 're-armed after a failed retry');

  env.doc.audioEls()[0].playRejects = false;
  env.doc.fire('click');
  await flush();
  assert.equal(env.doc.audioEls()[0].paused, false);
});

test('unmuting the speaker restarts playback instead of only clearing .muted', async () => {
  const { mgr, el } = await withRemoteAudio();
  assert.equal(el.muted, true, 'speaker starts muted (unchanged default)');

  const before = el.playCalls;
  mgr.toggleSpeaker();

  assert.equal(el.muted, false);
  assert.ok(el.playCalls > before,
    'unmuting must re-issue play(): a parked element does not resume on its own');
});

test('returning to the tab resumes audio the browser paused', async () => {
  const { doc, el } = await withRemoteAudio();
  el.paused = true;                             // backgrounded tab paused it
  const before = el.playCalls;

  doc.fire('visibilitychange');
  await flush();

  assert.ok(el.playCalls > before, 'visibility recovery re-issues play()');
});

// ── microphone permission no longer kills the whole channel ───────────────────

test('a denied microphone still joins the room in listen-only mode', async () => {
  const { mgr, ws } = await readyManager({
    getUserMedia: async () => { throw new Error('NotAllowedError'); },
  });

  assert.equal(mgr.ready, true, 'a denied mic used to leave the channel dead forever');
  assert.equal(mgr.micAvailable, false);
  assert.equal(ws.sentOf('voice_ready').length, 1, 'we still announce ourselves so peers connect');
});

test('without a microphone the peer connection is receive-only, so we can still hear', async () => {
  const env = await readyManager({ getUserMedia: async () => { throw new Error('denied'); } });
  env.mgr.connectToPeers([SID, PEER]);

  const tx = env.pcs[0].getTransceivers();
  assert.equal(tx.length, 1);
  assert.equal(tx[0].direction, 'recvonly');
  assert.equal(env.pcs[0].getSenders().filter(s => s.track).length, 0, 'nothing to send');
});

test('an unanswered permission prompt does not block the channel forever', async () => {
  let resolveMic;
  const env = loadVoice({ getUserMedia: () => new Promise(r => { resolveMic = r; }) });
  const mgr = new env.VoiceManager(env.ws, SID);

  const pending = mgr.init();
  await flush();
  assert.equal(mgr.ready, false, 'still waiting on the prompt');

  env.timers.advance(6000);                     // grace period elapses
  await pending;

  assert.equal(mgr.ready, true, 'joins listen-only rather than staying silent all game');
  assert.equal(env.ws.sentOf('voice_ready').length, 1);
  resolveMic(null);
});

test('a microphone granted late is adopted and renegotiated into live peers', async () => {
  let resolveMic;
  const env = loadVoice({ getUserMedia: () => new Promise(r => { resolveMic = r; }) });
  const mgr = new env.VoiceManager(env.ws, SID);

  const pending = mgr.init();
  await flush();
  env.timers.advance(6000);
  await pending;

  mgr.connectToPeers([SID, PEER]);
  const pc = env.pcs[0];
  assert.equal(pc.getTransceivers()[0].direction, 'recvonly');
  const offersBefore = pc.offersCreated;

  resolveMic(makeStream('late-mic'));            // user finally hits Allow
  await flush(12);

  const tx = pc.getTransceivers()[0];
  assert.equal(tx.direction, 'sendrecv', 'direction opened up for sending');
  assert.equal(tx.sender.track.id, 'late-mic', 'the new mic track is attached');
  assert.ok(pc.offersCreated > offersBefore, 'a fresh offer renegotiates the existing peer');
});

test('we never advertise an open mic we do not have', async () => {
  const { mgr, ws } = await readyManager({
    localStorage: { eek_mic_muted: 'false' },    // remembered preference: unmuted
    getUserMedia: async () => { throw new Error('denied'); },
  });

  assert.equal(mgr.muted, true, 'forced muted — there is no track to send');
  assert.equal(ws.sentOf('voice_mute_state')[0].muted, true);
});

test('tapping the mic button asks for permission again and stays muted if refused', async () => {
  let calls = 0;
  const { mgr } = await readyManager({
    getUserMedia: async () => { calls++; throw new Error('denied'); },
  });
  assert.equal(calls, 1);

  const muted = await mgr.toggleMute();

  assert.equal(calls, 2, 'the button press re-prompts instead of being a dead control');
  assert.equal(muted, true, 'still muted — nothing to send');
  assert.equal(mgr.muted, true);
});

test('tapping the mic button unmutes once permission is finally granted', async () => {
  let calls = 0;
  const { mgr } = await readyManager({
    getUserMedia: async () => {
      calls++;
      if (calls === 1) throw new Error('denied');
      return makeStream('granted-on-tap');
    },
  });

  const muted = await mgr.toggleMute();

  assert.equal(muted, false, 'unmuted after the retry succeeded');
  assert.equal(mgr.micAvailable, true);
});

// ── the ordinary path must keep working ───────────────────────────────────────

test('with a working microphone the track is sent as before', async () => {
  const env = await readyManager();
  env.mgr.connectToPeers([SID, PEER]);

  const pc = env.pcs[0];
  assert.equal(pc.getSenders().filter(s => s.track && s.track.kind === 'audio').length, 1);
  assert.equal(pc.getTransceivers()[0].direction, 'sendrecv');
  assert.ok(pc.offersCreated >= 1, 'an offer went out to the peer');
});

test('the remembered speaker preference still decides the initial mute state', async () => {
  const muted = await withRemoteAudio();
  assert.equal(muted.el.muted, true, 'default stays muted');

  const unmuted = await withRemoteAudio({ localStorage: { eek_speaker_muted: 'false' } });
  assert.equal(unmuted.el.muted, false, 'remembered preference is honoured');
  assert.ok(unmuted.el.playCalls >= 1);
});

test('the signalling payloads are unchanged, so no server change is required', async () => {
  const { ws } = await readyManager();
  assert.deepEqual(ws.sentOf('voice_ready'), [{ event: 'voice_ready' }]);
  assert.deepEqual(Object.keys(ws.sentOf('voice_mute_state')[0]).sort(), ['event', 'muted']);
});
