// How long to wait for the browser's microphone permission prompt before
// joining the voice channel anyway, in listen-only mode. An unanswered prompt
// used to leave a first-time player deaf for the whole room.
const VOICE_MIC_PROMPT_GRACE_MS = 6000;

// Events that count as a user gesture for the autoplay policy.
const VOICE_GESTURE_EVENTS = ['pointerdown', 'touchend', 'click', 'keydown'];

class VoiceManager {
  constructor(ws, sessionId) {
    this._ws = ws;
    this._sid = sessionId;
    this._peers = {};
    this._stream = null;
    this._muted = localStorage.getItem('eek_mic_muted') !== 'false';         // default: muted
    this._speakerMuted = localStorage.getItem('eek_speaker_muted') !== 'false'; // default: muted
    this._iceConfig = null;
    this._ready = false;
    this._pendingPeers = [];
    this._queuedSignals = [];
    this._knownPeers = [];
    this._unlockHandler = null;

    // Optional hook so the page can re-render the mic/speaker buttons after a
    // state change that did not come from a button press (mic denied at start,
    // or granted late).
    this.onStateChange = null;

    this._ws
      .on('voice_offer',      msg => this._ready ? this._handleOffer(msg)  : this._queuedSignals.push(msg))
      .on('voice_answer',     msg => this._ready ? this._handleAnswer(msg) : this._queuedSignals.push(msg))
      .on('voice_ice',        msg => this._ready ? this._handleIce(msg)    : this._queuedSignals.push(msg))
      .on('voice_mute_state', msg => this._onPeerMuteState(msg))
      .on('voice_ready',      msg => this._ready ? this._handleVoiceReady(msg) : this._queuedSignals.push(msg))
      .on('reconnected',      ()  => this._recover());

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') this._recover();
    });
  }

  async init() {
    try {
      const res = await fetch(`/api/voice/credentials?session_id=${encodeURIComponent(this._sid)}`);
      if (!res.ok) return;
      const data = await res.json();
      this._iceConfig = {
        iceServers: [{
          urls: data.uris,
          username: data.username,
          credential: data.credential,
        }],
      };
    } catch { return; }

    // A first-time player meets the permission prompt right here. Blocking on
    // it means an ignored (or denied) prompt kills the channel in both
    // directions for the rest of the room — you need no microphone to *hear*
    // the others, so give the prompt a few seconds and then carry on
    // listen-only, adopting the mic later if it does arrive.
    const micPromise = navigator.mediaDevices
      ? navigator.mediaDevices.getUserMedia({ audio: true, video: false }).catch(() => null)
      : Promise.resolve(null);
    const stream = await Promise.race([
      micPromise,
      new Promise(resolve => setTimeout(() => resolve(null), VOICE_MIC_PROMPT_GRACE_MS)),
    ]);
    if (stream) this._stream = stream;
    else micPromise.then(late => { if (late) this._adoptMic(late); });

    this._ready = true;
    if (!this._micLive()) this._muted = true;  // nothing to send without a mic
    this._applyMute();
    this.onStateChange?.();
    this._ws.send('voice_mute_state', { muted: this._muted });
    this._ws.send('voice_ready', {});

    const queued = this._queuedSignals.splice(0);
    for (const msg of queued) {
      if (msg.event === 'voice_offer')       await this._handleOffer(msg);
      else if (msg.event === 'voice_answer') await this._handleAnswer(msg);
      else if (msg.event === 'voice_ice')    await this._handleIce(msg);
      else if (msg.event === 'voice_ready')  this._handleVoiceReady(msg);
    }

    if (this._pendingPeers.length) {
      this._connectToAll(this._pendingPeers);
      this._pendingPeers = [];
    }
  }

  connectToPeers(peerSids) {
    this._knownPeers = peerSids.filter(s => s !== this._sid);
    if (!this._ready) {
      this._pendingPeers = this._knownPeers;
      return;
    }
    this._connectToAll(peerSids);
  }

  _connectToAll(peerSids) {
    for (const sid of peerSids) {
      if (sid === this._sid || this._peers[sid]) continue;
      if (this._sid < sid) this._createOffer(sid);
    }
  }

  async _createOffer(remoteSid) {
    await this._sendOffer(remoteSid, this._newPC(remoteSid));
  }

  async _sendOffer(remoteSid, pc) {
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      this._ws.send('voice_offer', { to: remoteSid, sdp: pc.localDescription.sdp });
    } catch {}
  }

  async _handleOffer(msg) {
    if (!this._ready) return;
    const { from, sdp } = msg;
    const pc = this._newPC(from);
    try {
      await pc.setRemoteDescription({ type: 'offer', sdp });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      this._ws.send('voice_answer', { to: from, sdp: pc.localDescription.sdp });
    } catch {}
  }

  async _handleAnswer(msg) {
    const pc = this._peers[msg.from];
    if (!pc) return;
    try { await pc.setRemoteDescription({ type: 'answer', sdp: msg.sdp }); } catch {}
  }

  async _handleIce(msg) {
    const pc = this._peers[msg.from];
    if (!pc || !msg.candidate) return;
    try {
      await pc.addIceCandidate({
        candidate: msg.candidate,
        sdpMid: msg.sdpMid,
        sdpMLineIndex: msg.sdpMLineIndex,
      });
    } catch {}
  }

  _onPeerMuteState(msg) {
    document.querySelectorAll(`.voice-indicator[data-sid="${msg.session_id}"]`)
      .forEach(el => { el.textContent = msg.muted ? '🔇' : ''; });
  }

  _handleVoiceReady(msg) {
    if (!this._ready) return;
    const { from } = msg;
    if (this._sid >= from) return;
    const existing = this._peers[from];
    if (existing) {
      // Only rebuild if the existing connection is clearly dead. Tearing down
      // a 'new'/'checking' peer just because a late voice_ready arrived costs
      // seconds of setup time — the offer/answer in flight will complete.
      const s = existing.iceConnectionState;
      if (s !== 'failed' && s !== 'closed' && s !== 'disconnected') return;
      existing.close();
      delete this._peers[from];
      document.getElementById(`audio-peer-${from}`)?.remove();
    }
    this._createOffer(from);
  }

  async _recover() {
    if (!this._ready) return;

    // Coming back to a backgrounded tab can leave the remote audio paused, and
    // a paused element never resumes on its own.
    this._resumeAll();

    // Only re-ask for a mic we already had and lost (mobile tab switch); a
    // listen-only player must not get a permission prompt on every tab focus.
    // Losing this is not fatal — we can still hear everyone else.
    if (this._stream && !this._micLive()) await this._acquireMic();

    for (const [sid, pc] of Object.entries(this._peers)) {
      const s = pc.iceConnectionState;
      if (s === 'failed' || s === 'closed' || s === 'disconnected') {
        pc.close();
        delete this._peers[sid];
        document.getElementById(`audio-peer-${sid}`)?.remove();
      }
    }

    this._ws.send('voice_ready', {});
    this._connectToAll(this._knownPeers);
  }

  _micLive() {
    return !!this._stream?.getAudioTracks().some(t => t.readyState === 'live');
  }

  async _acquireMic() {
    if (this._micLive()) return true;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch { return false; }
    await this._adoptMic(stream);
    return true;
  }

  // Wire a freshly obtained mic into the connections we already have. Peers
  // built while we had no mic negotiated receive-only, so they need a new
  // offer before our audio can travel over them.
  async _adoptMic(stream) {
    if (this._micLive()) { stream.getTracks().forEach(t => t.stop()); return; }
    this._stream = stream;
    this._applyMute();
    const track = stream.getAudioTracks()[0];
    if (!track) return;

    for (const [sid, pc] of Object.entries(this._peers)) {
      const sender = pc.getSenders().find(s => s.track?.kind === 'audio');
      if (sender) {
        await sender.replaceTrack(track).catch(() => {});
        continue;
      }
      let renegotiate = false;
      const tx = pc.getTransceivers().find(t => !t.sender.track);
      try {
        if (tx) {
          await tx.sender.replaceTrack(track);
          tx.direction = 'sendrecv';
        } else {
          pc.addTrack(track, stream);
        }
        renegotiate = true;
      } catch {}
      if (renegotiate) await this._sendOffer(sid, pc);
    }
  }

  _applyMute() {
    this._stream?.getAudioTracks().forEach(t => { t.enabled = !this._muted; });
  }

  _applySpeaker() {
    document.querySelectorAll('[id^="audio-peer-"]').forEach(el => {
      el.muted = this._speakerMuted;
      // Unmuting an element that the autoplay policy parked in a paused state
      // is not enough on its own — it has to be told to play again.
      this._resume(el);
    });
  }

  _resumeAll() {
    document.querySelectorAll('[id^="audio-peer-"]').forEach(el => this._resume(el));
  }

  // `autoplay` is a request, not a guarantee: a browser that has not yet seen
  // this visitor interact with the site leaves the element paused and the
  // player hears nothing. Ask explicitly, and if we are refused, try again on
  // the next tap — by then the gesture makes it allowed.
  _resume(audio) {
    // No `paused` guard: unmuting an element the browser was only tolerating
    // because it was silent can pause it again, and not always synchronously.
    // play() on an already-playing element is a no-op, so just always ask.
    audio.play?.().catch(() => this._armGestureUnlock());
  }

  _armGestureUnlock() {
    if (this._unlockHandler) return;
    this._unlockHandler = () => {
      this._disarmGestureUnlock();
      this._resumeAll();
    };
    VOICE_GESTURE_EVENTS.forEach(ev =>
      document.addEventListener(ev, this._unlockHandler, true));
  }

  _disarmGestureUnlock() {
    if (!this._unlockHandler) return;
    VOICE_GESTURE_EVENTS.forEach(ev =>
      document.removeEventListener(ev, this._unlockHandler, true));
    this._unlockHandler = null;
  }

  _newPC(remoteSid) {
    if (this._peers[remoteSid]) return this._peers[remoteSid];
    const pc = new RTCPeerConnection(this._iceConfig);
    this._peers[remoteSid] = pc;

    if (this._stream) this._stream.getTracks().forEach(t => pc.addTrack(t, this._stream));
    else pc.addTransceiver('audio', { direction: 'recvonly' });

    pc.onicecandidate = ({ candidate }) => {
      if (!candidate) return;
      this._ws.send('voice_ice', {
        to: remoteSid,
        candidate: candidate.candidate,
        sdpMid: candidate.sdpMid,
        sdpMLineIndex: candidate.sdpMLineIndex,
      });
    };

    pc.ontrack = ({ streams }) => {
      let audio = document.getElementById(`audio-peer-${remoteSid}`);
      if (!audio) {
        audio = document.createElement('audio');
        audio.id = `audio-peer-${remoteSid}`;
        audio.autoplay = true;
        audio.playsInline = true;
        document.body.appendChild(audio);
      }
      audio.muted = this._speakerMuted;
      if (audio.srcObject !== streams[0]) audio.srcObject = streams[0];
      this._resume(audio);
    };

    return pc;
  }

  async toggleMute() {
    if (this._muted && !this._micLive()) {
      // Turning the mic on without having one: a button press is the best
      // moment to ask for permission, so the player who ignored or missed the
      // prompt at join time gets a second chance instead of a dead button.
      if (!await this._acquireMic()) {
        this.onStateChange?.();
        return true;
      }
    }
    this._muted = !this._muted;
    this._applyMute();
    this._ws.send('voice_mute_state', { muted: this._muted });
    localStorage.setItem('eek_mic_muted', this._muted ? 'true' : 'false');
    this.onStateChange?.();
    return this._muted;
  }

  toggleSpeaker() {
    this._speakerMuted = !this._speakerMuted;
    this._applySpeaker();
    localStorage.setItem('eek_speaker_muted', this._speakerMuted ? 'true' : 'false');
    this.onStateChange?.();
    return this._speakerMuted;
  }

  get muted()        { return this._muted; }
  get speakerMuted() { return this._speakerMuted; }
  get ready()        { return this._ready; }
  get micAvailable() { return this._micLive(); }

  removePeer(sid) {
    this._peers[sid]?.close();
    delete this._peers[sid];
    document.getElementById(`audio-peer-${sid}`)?.remove();
  }

  cleanup() {
    Object.keys(this._peers).forEach(sid => {
      this._peers[sid].close();
      document.getElementById(`audio-peer-${sid}`)?.remove();
    });
    this._peers = {};
    this._stream?.getTracks().forEach(t => t.stop());
    this._stream = null;
    this._ready = false;
    this._disarmGestureUnlock();
  }
}
