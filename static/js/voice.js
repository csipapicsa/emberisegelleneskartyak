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

    try {
      this._stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch { return; }

    this._ready = true;
    this._applyMute();
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
    const pc = this._newPC(remoteSid);
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

    const tracksAlive = this._stream?.getAudioTracks().some(t => t.readyState === 'live');
    if (!tracksAlive) {
      try {
        this._stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        this._applyMute();
        for (const pc of Object.values(this._peers)) {
          const sender = pc.getSenders().find(s => s.track?.kind === 'audio');
          if (sender) await sender.replaceTrack(this._stream.getAudioTracks()[0]).catch(() => {});
        }
      } catch { return; }
    }

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

  _applyMute() {
    this._stream?.getAudioTracks().forEach(t => { t.enabled = !this._muted; });
  }

  _applySpeaker() {
    document.querySelectorAll('[id^="audio-peer-"]').forEach(el => {
      el.muted = this._speakerMuted;
    });
  }

  _newPC(remoteSid) {
    if (this._peers[remoteSid]) return this._peers[remoteSid];
    const pc = new RTCPeerConnection(this._iceConfig);
    this._peers[remoteSid] = pc;

    this._stream.getTracks().forEach(t => pc.addTrack(t, this._stream));

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
        audio.muted = this._speakerMuted;
        document.body.appendChild(audio);
      }
      audio.srcObject = streams[0];
    };

    return pc;
  }

  toggleMute() {
    this._muted = !this._muted;
    this._applyMute();
    this._ws.send('voice_mute_state', { muted: this._muted });
    localStorage.setItem('eek_mic_muted', this._muted ? 'true' : 'false');
    return this._muted;
  }

  toggleSpeaker() {
    this._speakerMuted = !this._speakerMuted;
    this._applySpeaker();
    localStorage.setItem('eek_speaker_muted', this._speakerMuted ? 'true' : 'false');
    return this._speakerMuted;
  }

  get muted()        { return this._muted; }
  get speakerMuted() { return this._speakerMuted; }
  get ready()        { return this._ready; }

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
  }
}
