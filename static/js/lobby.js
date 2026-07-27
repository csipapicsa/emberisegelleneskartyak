// Lobby page logic
// Expects: roomCode, sessionId, username to be set before loading

let socket;
let mySessionId;
let currentRoomCode;
let isAdmin = false;
let roomState = null;
let voiceManager = null;
let usernameChangeRequested = false;
let roomReplacedHandled = false;

async function initLobby(code, sid, uname) {
  currentRoomCode = code;
  mySessionId = sid;

  const canEnterLobby = await ensureLobbyAccess();
  if (!canEnterLobby) return false;

  socket = new GameSocket(code, sid);

  socket
    .on('room_state',     onRoomState)
    .on('pending_approval', onPendingApproval)
    .on('game_started',   () => { location.href = `/game/${code}`; })
    .on('room_closed',    onRoomClosed)
    .on('room_replaced',  onRoomReplaced)
    .on('denied',         onDenied)
    .on('username_taken', onUsernameTaken)
    .on('fatal_close',    ({ code: c }) => {
      if (c === 4002) onRoomClosed({});
      if (c === 4004 && !roomReplacedHandled) redirectToJoin();
      if (c === 4005 && !usernameChangeRequested) onUsernameTaken({});
      if (c === 4006) showGameAlreadyStarted();
    })
    .on('error',          msg => showToast(msg.message));

  socket.connect(uname);
  initChat(socket);
  setupSegControls();

  window.addEventListener('pagehide', () => { socket.close(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) ensureLobbyAccess();
  });
  return true;
}

async function ensureLobbyAccess() {
  try {
    const res = await fetch(`/api/room/${encodeURIComponent(currentRoomCode)}?session_id=${encodeURIComponent(mySessionId)}`);
    if (res.status === 404) {
      const body = await res.json().catch(() => null);
      const newCode = body?.detail?.reason === 'admin_opened_new_room' ? body.detail.new_code : null;
      if (newCode) {
        onRoomReplaced({ new_code: newCode });
      } else {
        redirectToJoin();
      }
      return false;
    }
    if (!res.ok) return true;
    const info = await res.json();
    if (!info.started) return true;
    if (info.is_player) {
      location.replace(`/game/${encodeURIComponent(currentRoomCode)}`);
      return false;
    }
    showGameAlreadyStarted();
    return false;
  } catch {
    // The WebSocket reconnect path below will handle transient network failures.
    return true;
  }
}

function redirectToJoin() {
  location.replace(`/join?code=${encodeURIComponent(currentRoomCode)}`);
}

function setupSegControls() {
  const defs = [
    { id: 'roundSeg',  key: 'round_total',   parse: v => parseInt(v) },
    { id: 'timerSeg',  key: 'timer_seconds', parse: v => parseInt(v) },
    { id: 'votingSeg', key: 'voting_mode',   parse: v => v },
    { id: 'selfVoteSeg', key: 'prevent_self_vote', parse: v => v === 'true' },
  ];
  defs.forEach(({ id, key, parse }) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', e => {
      const btn = e.target.closest('button[data-val]');
      if (!btn) return;
      el.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
      socket.send('configure_room', { [key]: parse(btn.dataset.val) });
    });
  });
}

function onRoomState(state) {
  roomState = state;
  isAdmin = state.admin === mySessionId;

  if (state.started) {
    location.replace(`/game/${encodeURIComponent(currentRoomCode)}`);
    return;
  }

  const amApproved = state.players.some(p => p.session_id === mySessionId);

  // Move approved player from pending screen to main lobby
  if (amApproved) {
    document.getElementById('pendingScreen').classList.add('hidden');
    document.getElementById('mainContent').classList.remove('hidden');

    // Init voice on first approval
    if (!voiceManager) {
      voiceManager = new VoiceManager(socket, mySessionId);

      // Show buttons immediately for every approved user — state is already
      // read from localStorage in the constructor, no need to wait for init()
      const micBtn = document.getElementById('micBtn');
      const spkBtn = document.getElementById('speakerBtn');
      micBtn.classList.remove('hidden');
      spkBtn.classList.remove('hidden');
      micBtn.classList.toggle('muted', voiceManager.muted);
      micBtn.textContent = voiceManager.muted ? '🔇' : '🎤';
      spkBtn.classList.toggle('muted', voiceManager.speakerMuted);
      spkBtn.textContent = voiceManager.speakerMuted ? '🔕' : '🔊';
      micBtn.addEventListener('click', _toggleMic);
      spkBtn.addEventListener('click', _toggleSpeaker);

      voiceManager.init(); // mic + TURN handshake runs in background
    }
    if (voiceManager) {
      voiceManager.connectToPeers(state.players.map(p => p.session_id));
    }
  }

  renderPlayers(state.players);
  renderPending(state.pending);

  if (isAdmin) {
    document.getElementById('adminPanel').classList.remove('hidden');
    document.getElementById('waitingMsg').classList.add('hidden');
    renderEditions(state.edition_ids);
    renderConfig(state);
    const hasPending = state.pending.length > 0;
    document.getElementById('startBtn').disabled = state.players.length < 1 || hasPending;
    document.getElementById('pendingBlockHint').classList.toggle('hidden', !hasPending);
  } else {
    document.getElementById('adminPanel').classList.add('hidden');
    document.getElementById('waitingMsg').classList.remove('hidden');
  }

  document.getElementById('roomCodeDisplay').textContent = state.code;
}

function onPendingApproval() {
  document.getElementById('mainContent').classList.add('hidden');
  document.getElementById('pendingScreen').classList.remove('hidden');
}

function onRoomClosed() {
  showToast('A szobát bezárták.');
  setTimeout(() => location.replace('/join'), 1200);
}

function onRoomReplaced(msg) {
  roomReplacedHandled = true;
  socket?.close();
  showRoomReplaced(msg.new_code);
}

function onDenied(msg) {
  showToast(msg.message || 'A belépési kérelmet elutasították.');
  setTimeout(redirectToJoin, 1200);
}

function onUsernameTaken(msg) {
  usernameChangeRequested = true;
  const taken = msg.username || getSavedUsername();
  const next = prompt(`A(z) "${taken}" név már foglalt ebben a szobában. Adj meg egy másik nevet:`, '');

  if (next && next.trim() && next.trim().toLocaleLowerCase('hu-HU') !== String(taken).trim().toLocaleLowerCase('hu-HU')) {
    saveUsername(next.trim());
    location.reload();
    return;
  }

  clearSavedUsername();
  location.replace(`/join?code=${encodeURIComponent(currentRoomCode)}`);
}

function renderPlayers(players) {
  const list = document.getElementById('playerList');
  list.innerHTML = players.map(p => `
    <div class="player-row ${p.session_id === mySessionId ? 'you' : ''}">
      <span class="name">${esc(p.username)}${p.is_admin ? ' 👑' : ''}</span>
      <span>
        <span class="voice-indicator" data-sid="${p.session_id}"></span>
        <span class="score">${p.connected ? '' : '(kilépett)'}</span>
      </span>
    </div>
  `).join('');
}

function _toggleMic() {
  if (!voiceManager) return;
  const muted = voiceManager.toggleMute();
  const btn = document.getElementById('micBtn');
  btn.classList.toggle('muted', muted);
  btn.textContent = muted ? '🔇' : '🎤';
}

function _toggleSpeaker() {
  if (!voiceManager) return;
  const muted = voiceManager.toggleSpeaker();
  const btn = document.getElementById('speakerBtn');
  btn.classList.toggle('muted', muted);
  btn.textContent = muted ? '🔕' : '🔊';
}

function renderPending(pending) {
  const section = document.getElementById('pendingSection');
  const list    = document.getElementById('pendingList');
  if (!isAdmin || pending.length === 0) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');
  list.innerHTML = pending.map(p => `
    <div class="player-row" style="justify-content:space-between">
      <span class="name">${esc(p.username)}</span>
      <div style="display:flex;gap:8px">
        <button class="btn btn-sm btn-primary" onclick="approve('${p.session_id}')">Enged</button>
        <button class="btn btn-sm btn-danger"  onclick="deny('${p.session_id}')">Tilt</button>
      </div>
    </div>
  `).join('');
}

function renderEditions(selectedIds) {
  const container = document.getElementById('editionList');
  if (!container._loaded) return; // wait for fetch
  container.querySelectorAll('.select-option').forEach(el => {
    const id = parseInt(el.dataset.id);
    el.classList.toggle('selected', selectedIds.includes(id));
    el.querySelector('input').checked = selectedIds.includes(id);
  });
}

async function loadEditions(currentIds) {
  const res  = await fetch('/api/editions');
  const list = await res.json();
  const container = document.getElementById('editionList');
  container._loaded = true;

  // Default: select every edition except Bōks if none are configured yet.
  const selected = currentIds.length > 0
    ? currentIds
    : list.filter(e => e.name !== 'Bōks').map(e => e.id);

  container.innerHTML = list.map(e => `
    <div class="select-option ${selected.includes(e.id) ? 'selected' : ''}" data-id="${e.id}"${e.community ? ' data-community="1"' : ''}>
      <label class="edition-choice">
        <input type="checkbox" ${selected.includes(e.id) ? 'checked' : ''}>
        <span>${esc(e.name)} <span class="edition-hint">(${e.community ? 'csak másik csomaggal &middot; ' : ''}${e.black_count} fekete, ${e.white_count} fehér)</span></span>
      </label>
      <a class="edition-cards-link" href="/kartyak?edition=${encodeURIComponent(e.id)}" title="${esc(e.name)} kártyáinak megtekintése" aria-label="${esc(e.name)} kártyáinak megtekintése">Kártyák</a>
    </div>
  `).join('');

  // Send initial selection to server
  if (currentIds.length === 0 && list.length > 0) {
    socket.send('configure_room', { edition_ids: selected });
  }

  container.addEventListener('change', () => {
    const ids = [...container.querySelectorAll('input:checked')]
      .map(el => parseInt(el.closest('.select-option').dataset.id));
    socket.send('configure_room', { edition_ids: ids });
    container.querySelectorAll('.select-option').forEach(el => {
      el.classList.toggle('selected', el.querySelector('input').checked);
    });
  });
}

// The community "Ti küldtétek" edition can't be played on its own. Returns true
// when it's selected and no normal edition is — server enforces this too.
function onlyCommunitySelected() {
  const checked = [...document.querySelectorAll('#editionList .select-option')]
    .filter(el => el.querySelector('input').checked);
  return checked.length > 0 && checked.every(el => el.dataset.community === '1');
}

function renderConfig(state) {
  segSetActive('roundSeg',  btn => parseInt(btn.dataset.val) === state.round_total);
  segSetActive('timerSeg',  btn => parseInt(btn.dataset.val) === state.timer_seconds);
  segSetActive('votingSeg', btn => btn.dataset.val === state.voting_mode);
  segSetActive('selfVoteSeg', btn => (btn.dataset.val === 'true') === (state.prevent_self_vote !== false));
}

function segSetActive(id, matchFn) {
  document.getElementById(id)?.querySelectorAll('button').forEach(btn => {
    btn.classList.toggle('active', matchFn(btn));
  });
}

function showRoomNotFound() {
  document.getElementById('pendingScreen').classList.add('hidden');
  document.getElementById('mainContent').classList.add('hidden');
  document.getElementById('gameAlreadyStarted')?.classList.add('hidden');
  document.getElementById('roomNotFound').classList.remove('hidden');
  hideChatUi();
}

function showGameAlreadyStarted() {
  document.getElementById('pendingScreen').classList.add('hidden');
  document.getElementById('mainContent').classList.add('hidden');
  document.getElementById('roomNotFound')?.classList.add('hidden');
  document.getElementById('gameAlreadyStarted')?.classList.remove('hidden');
  voiceManager?.cleanup();
  hideChatUi();
}

function showRoomReplaced(newCode) {
  document.getElementById('pendingScreen').classList.add('hidden');
  document.getElementById('mainContent').classList.add('hidden');
  document.getElementById('roomNotFound')?.classList.add('hidden');
  document.getElementById('gameAlreadyStarted')?.classList.add('hidden');
  document.getElementById('roomReplacedCode').textContent = newCode;
  document.getElementById('roomReplacedLink').href = `/room/${encodeURIComponent(newCode)}`;
  document.getElementById('roomReplaced').classList.remove('hidden');
  voiceManager?.cleanup();
  hideChatUi();
}

function approve(sid) { socket.send('approve_player', { session_id: sid }); }
function deny(sid)    { socket.send('deny_player',    { session_id: sid }); }

function esc(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}
