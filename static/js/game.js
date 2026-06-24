let socket, mySessionId, roomCode;
let hand;
let voiceManager = null;
let currentBlack = null;
let isCzar = false;
let currentCzarSid  = null;
let currentCzarName = '';
let votingMode = 'czar';
let playersMap = {};  // sid → username
let revealedSubmissions = [];
let pendingSubmissions = [];
let lastWinnerCardId = null;
let hasSubmitted = false;
let currentPhase = 'lobby';
let preventSelfVote = true;
let currentPlayerCount = 0;
let config = { card_hold_duration_ms: 2500 };

async function initGame(code, sid) {
  roomCode    = code;
  mySessionId = sid;

  let roomInfo;
  try {
    const roomRes = await fetch(`/api/room/${encodeURIComponent(code)}?session_id=${encodeURIComponent(sid)}`);
    if (roomRes.status === 404) {
      showRoomNotFound();
      return;
    }
    if (!roomRes.ok) throw new Error();
    roomInfo = await roomRes.json();
  } catch {
    showRoomNotFound();
    return;
  }

  if (!roomInfo.started) {
    location.replace(`/room/${encodeURIComponent(code)}`);
    return;
  }
  if (roomInfo.is_player === false) {
    showGameAlreadyStarted();
    return;
  }

  const cfgRes = await fetch('/api/config');
  config = await cfgRes.json();

  hand = new Hand(
    document.getElementById('handContainer'),
    onCardsSelected,
    config.card_hold_duration_ms,
    requestNewHand
  );

  socket = new GameSocket(code, sid);
  socket
    .on('room_state',         onRoomState)
    .on('round_start',        onRoundStart)
    .on('hand_update',        onHandUpdate)
    .on('timer_tick',         onTimerTick)
    .on('player_submitted',   onPlayerSubmitted)
    .on('submission_withdrawn', onSubmissionWithdrawn)
    .on('cards_revealed',     onCardsRevealed)
    .on('submission_accepted',onSubmissionAccepted)
    .on('round_end',          onRoundEnd)
    .on('game_end',           onGameEnd)
    .on('room_closed',        onRoomClosed)
    .on('player_disconnected',onPlayerDisconnected)
    .on('vote_cast',          onVoteCast)
    .on('round_skipped',      () => showToast('Senki sem küldött kártyát — következő kör!'))
    .on('new_hand_dealt',     () => showToast('Új kártyákat kaptál!'))
    .on('kicked',             () => showToast('Inaktivitás miatt kiestél a játékból.'))
    .on('player_kicked',      msg => showToast(`${esc(msg.username || 'Egy játékos')} inaktivitás miatt kiesett.`))
    .on('game_aborted',       () => showToast('A játék véget ért, mert nem maradt aktív játékos.'))
    .on('admin_promoted',     () => {})
    .on('error',              msg => showToast(msg.message));
  socket.on('fatal_close', ({ code: c }) => {
    if (c === 4002) onRoomClosed({});
    if (c === 4004) showRoomNotFound();
    if (c === 4006) showGameAlreadyStarted();
  });

  socket.connect();

  voiceManager = new VoiceManager(socket, sid);

  // Show buttons immediately for every player — state from localStorage
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

  voiceManager.init();

  _initHandDrawer();

  window.addEventListener('pagehide', () => {
    socket.close();
    voiceManager?.cleanup();
  });
}

// ── Event handlers ────────────────────────────────────────────────────────────

function onRoomState(state) {
  playersMap = {};
  (state.players || []).forEach(p => { playersMap[p.session_id] = p.username; });
  currentPlayerCount = (state.players || []).length;
  votingMode = state.voting_mode || 'czar';
  preventSelfVote = state.prevent_self_vote !== false;
  renderPlayers(state.players, state.czar);
  voiceManager?.connectToPeers((state.players || []).map(p => p.session_id));
  document.getElementById('roomCodeEl').textContent = state.code;
  if (state.started && state.black_card) {
    renderBlackCard(state.black_card);
    currentBlack    = state.black_card;
    currentCzarSid  = state.czar;
    currentCzarName = playersMap[state.czar] || '';
    isCzar = state.czar === mySessionId;
    currentPhase = state.phase || currentPhase;
    document.getElementById('roundEl').textContent = `${state.round_num || 1} / ${state.round_total}`;
    setTimer(state.timer_seconds);
    updateStatus();
    const me = (state.players || []).find(p => p.session_id === mySessionId);
    hasSubmitted = Boolean(me && me.submitted);
    if (state.phase === 'submitting') {
      pendingSubmissions = (state.players || [])
        .filter(p => p.submitted)
        .map(p => ({ session_id: p.session_id, username: p.username }));
      renderPendingSubmissions();
    } else if (state.phase === 'revealed' && Array.isArray(state.submissions)) {
      onCardsRevealed({
        submissions: state.submissions,
        timer_seconds: state.timer_seconds,
        prevent_self_vote: state.prevent_self_vote,
        player_count: (state.players || []).length,
      });
    }
    hand.setDisabled(hasSubmitted || state.phase !== 'submitting');
    showNewHandBtn(state.phase === 'submitting' && !hasSubmitted);
  }
}

function onRoundStart(msg) {
  currentBlack    = msg.black_card;
  currentCzarSid  = msg.czar;
  currentCzarName = msg.czar_username || playersMap[msg.czar] || '';
  votingMode      = msg.voting_mode || 'czar';
  preventSelfVote = msg.prevent_self_vote !== false;
  isCzar          = msg.czar === mySessionId;
  revealedSubmissions = [];
  pendingSubmissions  = [];
  hasSubmitted = false;
  currentPhase = 'submitting';

  document.getElementById('roundEl').textContent = `${msg.round_num} / ${msg.round_total}`;
  renderBlackCard(msg.black_card);
  renderPlayers(null, msg.czar);
  clearMiddle();
  updateStatus();

  hand.setPickCount(msg.black_card.pick || 1);
  hand.setDisabled(false);

  setTimer(msg.timer_seconds);
  hideFeedbackBtn();
  document.getElementById('winnerOverlay').classList.add('hidden');
  showNewHandBtn(true);
}

function onPlayerSubmitted(msg) {
  if (!pendingSubmissions.find(p => p.session_id === msg.session_id)) {
    pendingSubmissions.push(msg);
  }
  renderPendingSubmissions();
}

function renderPendingSubmissions() {
  const el = document.getElementById('gameMiddle');
  el.innerHTML = `<div class="submissions-grid">${
    pendingSubmissions.map(p => `
      <div class="submission-pile" data-sid="${p.session_id}">
        <div class="sub-card face-down">
          <span style="opacity:.4;font-size:1.4rem">🂠</span>
          <span style="margin-left:8px;font-size:.8rem;font-weight:600">${esc(p.username)}</span>
        </div>
        ${p.session_id === mySessionId ? '<button class="change-submission-btn" onclick="withdrawSubmission()">Kártya cseréje</button>' : ''}
      </div>`).join('')
  }</div>`;
}

function onHandUpdate(msg) {
  hand.setCards(msg.hand);
  hand.setDisabled(hasSubmitted || currentPhase !== 'submitting');
}

function onTimerTick(msg) {
  const t = document.getElementById('timerEl');
  t.textContent = msg.seconds_remaining;
  t.classList.toggle('urgent', msg.seconds_remaining <= 10);
}

function onCardsRevealed(msg) {
  currentPhase = 'revealed';
  revealedSubmissions = msg.submissions;
  preventSelfVote = msg.prevent_self_vote !== false;
  currentPlayerCount = Number(msg.player_count || currentPlayerCount || 0);
  setTimer(msg.timer_seconds);
  hand.setDisabled(true);
  showNewHandBtn(false);

  if (votingMode === 'vote') {
    renderSubmissionsVote(msg.submissions);
    setStatus('Szavazz a kedvenced kártyájára!');
  } else if (isCzar) {
    renderSubmissionsCzar(msg.submissions);
    setStatus('Válaszd ki a legjobb kártyát!');
    showCzarHint(true);
  } else {
    renderSubmissionsViewer(msg.submissions);
    setStatus(`Kártyák felfedve. ${esc(currentCzarName)} választ...`);
  }
}

function onVoteCast(msg) {
  const el = document.getElementById('voteCounter');
  if (el) el.textContent = `${msg.votes_in} / ${msg.total} szavazat beérkezett`;
}

function onSubmissionAccepted() {
  hasSubmitted = true;
  hand.setDisabled(true);
  showNewHandBtn(false);
  setStatus('Beküldve! Várakozás a többiekre...');
}

function onSubmissionWithdrawn(msg) {
  pendingSubmissions = pendingSubmissions.filter(p => p.session_id !== msg.session_id);
  renderPendingSubmissions();
  if (msg.session_id === mySessionId) {
    hasSubmitted = false;
    hand.setDisabled(false);
    showNewHandBtn(true);
    updateStatus();
  }
}

function onRoundEnd(msg) {
  currentPhase = 'round_ending';
  showCzarHint(false);
  highlightWinner(msg.winner_session_id);
  updateScores(msg.scores);
  lastWinnerCardId = getWinnerCardId(msg.winner_session_id);

  if (msg.authors && msg.authors.length) {
    revealAuthors(msg.authors);
  }

  showWinnerOverlay(msg);
}

function showWinnerOverlay(msg) {
  const winners = msg.winners || [msg.winner_session_id];
  const authors = msg.authors || [];

  const names = winners.map(sid => {
    const a = authors.find(x => x.session_id === sid);
    return a ? esc(a.username) : esc(playersMap[sid] || sid);
  });

  const labelEl = document.getElementById('winnerLabel');
  const cardsEl = document.getElementById('winnerCards');

  labelEl.innerHTML = winners.length > 1
    ? `🏆 Döntetlen: ${names.join(', ')}`
    : `🏆 Győztes: ${names[0]}`;

  const winnerAuthor = authors.find(x => x.session_id === winners[0]);
  cardsEl.innerHTML = winnerAuthor
    ? winnerAuthor.cards.map(c => `<div class="sub-card">${esc(c.text)}</div>`).join('')
    : '';

  const overlay = document.getElementById('winnerOverlay');
  overlay.classList.remove('hidden');
  setTimeout(() => overlay.classList.add('hidden'), 3000);
}

function revealAuthors(authors) {
  document.querySelectorAll('.submission-pile').forEach(pile => {
    const sid    = pile.dataset.sid;
    const author = authors.find(a => a.session_id === sid);
    if (!author) return;
    const label = document.createElement('div');
    label.style.cssText = 'font-size:.72rem;font-weight:700;color:var(--text-muted);padding:4px 0 0 2px';
    label.textContent = author.username;
    pile.appendChild(label);
  });
}

function onGameEnd(msg) {
  // Voice channel is intentionally kept open on the end screen so players can
  // chat about the game. Cleanup runs on pagehide / explicit leave.
  showEndScreen(msg.final_scores);
}

function onRoomClosed() {
  showToast('A szobát bezárták.');
  setTimeout(() => location.replace('/join'), 1200);
}

function hideGameShell() {
  document.querySelector('.game-page')?.classList.add('hidden');
  document.getElementById('playersPanel')?.classList.remove('open');
  const panelOverlay = document.getElementById('panelOverlay');
  if (panelOverlay) panelOverlay.style.display = 'none';
  document.getElementById('feedbackBtn')?.classList.add('hidden');
  document.getElementById('scoresFab')?.classList.add('hidden');
  document.getElementById('confirmModal')?.classList.add('hidden');
  document.getElementById('feedbackModal')?.classList.add('hidden');
  document.getElementById('winnerOverlay')?.classList.add('hidden');
  voiceManager?.cleanup();
}

function showRoomNotFound() {
  hideGameShell();
  document.getElementById('roomNotFound')?.classList.remove('hidden');
}

function showGameAlreadyStarted() {
  hideGameShell();
  document.getElementById('gameAlreadyStarted')?.classList.remove('hidden');
}

function onPlayerDisconnected(msg) {
  showToast('Egy játékos kilépett.');
  const row = document.querySelector(`.player-row[data-sid="${msg.session_id}"]`);
  if (row) row.classList.add('disconnected');
  voiceManager?.removePeer(msg.session_id);
}

// ── Card selection ────────────────────────────────────────────────────────────

function onCardsSelected(cardIds) {
  socket.send('submit_card', { card_ids: cardIds });
}

// ── Czar picking ──────────────────────────────────────────────────────────────

function onCzarPick(winnerSessionId) {
  if (!canVoteForSubmission(winnerSessionId)) return;
  socket.send('czar_pick', { winner_session_id: winnerSessionId });
  showCzarHint(false);
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderBlackCard(card) {
  const el = document.getElementById('blackCard');
  const text = card.text.replace(/_+/g, '<span class="blank"></span>');
  el.innerHTML = `${text}${card.pick > 1 ? `<span class="pick-label">Húzz ${card.pick}-t</span>` : ''}`;
}

function renderSubmissionsViewer(submissions) {
  const el = document.getElementById('gameMiddle');
  el.innerHTML = `<div class="submissions-grid">${
    submissions.map(s => `
      <div class="submission-pile" data-sid="${s.session_id}">
        ${s.cards.map(c => `<div class="sub-card">${esc(c.text)}</div>`).join('')}
      </div>`).join('')
  }</div>`;
}

function renderSubmissionsCzar(submissions) {
  const el = document.getElementById('gameMiddle');
  el.innerHTML = `
    <div class="czar-pick-hint">Koppints a kedvenced kártyájára!</div>
    <div class="submissions-grid">${
      submissions.map(s => {
        const canPick = canVoteForSubmission(s.session_id);
        return `
        <div class="submission-pile${canPick ? '' : ' vote-disabled'}" data-sid="${s.session_id}" ${canPick ? `onclick="onCzarPick('${s.session_id}')"` : 'aria-disabled="true" title="Saját kártyára most nem szavazhatsz."'}>
          ${s.cards.map(c => `<div class="sub-card">${esc(c.text)}</div>`).join('')}
        </div>`;
      }).join('')
    }</div>`;
}

function renderSubmissionsVote(submissions) {
  const el = document.getElementById('gameMiddle');
  el.innerHTML = `
    <div id="voteCounter" style="text-align:center;font-size:.85rem;color:var(--text-muted);margin-bottom:8px"></div>
    <div class="submissions-grid">${
      submissions.map(s => {
        const canVote = canVoteForSubmission(s.session_id);
        return `
        <div class="submission-pile${canVote ? '' : ' vote-disabled'}" data-sid="${s.session_id}" ${canVote ? `onclick="castVote('${s.session_id}')"` : 'aria-disabled="true" title="Saját kártyára most nem szavazhatsz."'}>
          ${s.cards.map(c => `<div class="sub-card">${esc(c.text)}</div>`).join('')}
        </div>`;
      }).join('')
    }</div>`;
}

function canVoteForSubmission(sessionId) {
  return !(preventSelfVote && currentPlayerCount > 1 && sessionId === mySessionId);
}

function castVote(winnerSessionId) {
  if (!canVoteForSubmission(winnerSessionId)) return;
  socket.send('cast_vote', { winner_session_id: winnerSessionId });
  document.querySelectorAll('.submission-pile').forEach(el => {
    el.onclick = null;
    el.style.opacity = el.dataset.sid === winnerSessionId ? '1' : '0.5';
  });
}

function highlightWinner(winnerSid) {
  document.querySelectorAll('.submission-pile').forEach(el => {
    if (el.dataset.sid === winnerSid) el.classList.add('winner-pick');
  });
}

function renderPlayers(players, czarSid) {
  const panel = document.getElementById('playersPanel');
  const rows  = panel.querySelectorAll('.player-row');

  if (players) {
    panel.innerHTML = `<h2 style="margin-bottom:8px">Játékosok</h2>` +
      players.map(p => `
        <div class="player-row ${p.session_id === mySessionId ? 'you' : ''} ${p.session_id === czarSid ? 'czar' : ''} ${!p.connected ? 'disconnected' : ''}" data-sid="${p.session_id}">
          <span class="name">${esc(p.username)}</span>
          <span>
            <span class="voice-indicator" data-sid="${p.session_id}"></span>
            <span class="score-badge">${p.score}</span>
          </span>
        </div>`).join('');
  } else if (czarSid) {
    panel.querySelectorAll('.player-row').forEach(el => {
      el.classList.toggle('czar', el.dataset.sid === czarSid);
    });
  }
}

function updateScores(scores) {
  scores.forEach(s => {
    const row = document.querySelector(`.player-row[data-sid="${s.session_id}"] .score-badge`);
    if (row) row.textContent = s.score;
  });
}

function clearMiddle() {
  document.getElementById('gameMiddle').innerHTML = '';
}

function setStatus(msg) {
  document.getElementById('statusBar').textContent = msg;
}

function updateStatus() {
  const pick = currentBlack?.pick || 1;
  setStatus(pick > 1 ? `Válassz ${pick} kártyát!` : 'Válaszd ki a legjobb kártyádat!');
}

function resetTimer() {
  setTimer(0);
}

function setTimer(seconds) {
  const t = document.getElementById('timerEl');
  const value = Number(seconds || 0);
  t.textContent = value > 0 ? value : '';
  t.classList.toggle('urgent', value > 0 && value <= 10);
}

function showCzarHint(show) {
  document.getElementById('czarHint').classList.toggle('hidden', !show);
}

function getWinnerCardId(winnerSid) {
  const sub = revealedSubmissions.find(s => s.session_id === winnerSid);
  return sub ? sub.cards[0]?.id : null;
}

// ── Feedback ──────────────────────────────────────────────────────────────────

function showFeedbackBtn()  { /* always visible during game */ }
function hideFeedbackBtn()  { /* always visible during game */ }

function makeDraggableFab(btnId, storeKey, defaultX, defaultY, onTap) {
  const btn = document.getElementById(btnId);
  const SIZE = 44;

  function clamp(val, min, max) { return Math.max(min, Math.min(max, val)); }
  function applyPos(x, y) {
    btn.style.left = x + 'px'; btn.style.top = y + 'px';
    btn.style.right = ''; btn.style.bottom = '';
  }

  try {
    const saved = JSON.parse(localStorage.getItem(storeKey));
    // Validate saved position is actually on-screen (guards against stale/wrong-screen values)
    if (saved && typeof saved.x === 'number'
        && saved.x >= SIZE && saved.x <= window.innerWidth - SIZE
        && saved.y >= 0 && saved.y <= window.innerHeight - SIZE) {
      applyPos(saved.x, saved.y);
    } else {
      localStorage.removeItem(storeKey);
      applyPos(defaultX(), defaultY());
    }
  } catch { applyPos(defaultX(), defaultY()); }

  let dragging = false, startX, startY, origX, origY, moved;

  function onStart(cx, cy) {
    dragging = true; moved = false;
    startX = cx; startY = cy;
    origX = btn.getBoundingClientRect().left;
    origY = btn.getBoundingClientRect().top;
    btn.style.cursor = 'grabbing';
  }
  function onMove(cx, cy) {
    if (!dragging) return;
    const dx = cx - startX, dy = cy - startY;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
    applyPos(clamp(origX + dx, 0, window.innerWidth - SIZE), clamp(origY + dy, 0, window.innerHeight - SIZE));
  }
  function onEnd() {
    if (!dragging) return;
    dragging = false; btn.style.cursor = 'grab';
    try { localStorage.setItem(storeKey, JSON.stringify({ x: parseFloat(btn.style.left), y: parseFloat(btn.style.top) })); } catch {}
    if (!moved) onTap();
  }

  btn.addEventListener('mousedown', e => { e.preventDefault(); onStart(e.clientX, e.clientY); });
  window.addEventListener('mousemove', e => onMove(e.clientX, e.clientY));
  window.addEventListener('mouseup', onEnd);
  btn.addEventListener('touchstart', e => { e.preventDefault(); onStart(e.touches[0].clientX, e.touches[0].clientY); }, { passive: false });
  window.addEventListener('touchmove', e => { if (dragging) { e.preventDefault(); onMove(e.touches[0].clientX, e.touches[0].clientY); } }, { passive: false });
  window.addEventListener('touchend', onEnd);
}

makeDraggableFab('feedbackBtn', 'fabPos',
  () => window.innerWidth - 56, () => window.innerHeight - 280,
  openFeedback);

makeDraggableFab('scoresFab', 'scoresPos',
  () => window.innerWidth - 56, () => window.innerHeight - 220,
  togglePlayers);

function openFeedback() {
  document.getElementById('feedbackModal').classList.remove('hidden');
}

function sendFeedback(type) {
  if (!currentBlack) return;
  socket.send('feedback', { card_id: currentBlack.id, type });
  document.getElementById('feedbackModal').classList.add('hidden');
  showToast('Köszönjük a visszajelzést!');
}

function sendComment() {
  const text = document.getElementById('feedbackComment').value.trim();
  if (!text || !currentBlack) return;
  socket.send('feedback', { card_id: currentBlack.id, type: 'comment', comment: text });
  document.getElementById('feedbackComment').value = '';
  document.getElementById('feedbackModal').classList.add('hidden');
  showToast('Komment elküldve!');
}

// ── Players panel toggle ──────────────────────────────────────────────────────

function togglePlayers() {
  const panel = document.getElementById('playersPanel');
  const overlay = document.getElementById('panelOverlay');
  const open = panel.classList.toggle('open');
  overlay.style.display = open ? 'block' : 'none';
}

// ── End screen ────────────────────────────────────────────────────────────────

function showEndScreen(scores) {
  document.querySelector('.game-page').classList.add('hidden');
  const end = document.getElementById('endScreen');
  end.classList.remove('hidden');

  const sorted = [...scores].sort((a, b) => b.score - a.score);
  document.getElementById('podium').innerHTML = sorted.map((s, i) => `
    <div class="podium-row">
      <span class="rank">${i + 1}.</span>
      <span>${esc(s.username)}${s.session_id === mySessionId ? ' (te)' : ''}</span>
      <span class="score-badge">${s.score}</span>
    </div>`).join('');
}

// ── Swap ──────────────────────────────────────────────────────────────────────

function swapCard(cardId) {
  if (hasSubmitted) return;
  socket.send('swap_card', { card_id: cardId });
}

// ── New hand ──────────────────────────────────────────────────────────────────

function showNewHandBtn(show) {
  hand?.setAction(show, 'Kérek új kártyákat');
}

function requestNewHand() {
  socket.send('request_new_hand', {});
}

function withdrawSubmission() {
  socket.send('withdraw_submission', {});
}


// ── Toast ─────────────────────────────────────────────────────────────────────

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
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

// ── Hand drawer ───────────────────────────────────────────────────────────────

function _initHandDrawer() {
  const area = document.getElementById('handArea');
  const toggle = document.getElementById('handToggle');
  if (!area || !toggle) return;
  const collapsed = localStorage.getItem('eek_hand_collapsed') === 'true';
  if (collapsed) area.classList.add('collapsed');

  function _flip(e) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    const isNowCollapsed = area.classList.toggle('collapsed');
    localStorage.setItem('eek_hand_collapsed', isNowCollapsed);
  }

  // Touch-first: fire on touchend (tap) and also click for desktop.
  // Guard against double-fire by tracking last trigger time.
  let _lastFlip = 0;
  function _guarded(e) {
    const now = Date.now();
    if (now - _lastFlip < 350) return;
    _lastFlip = now;
    _flip(e);
  }
  toggle.addEventListener('click', _guarded);
  toggle.addEventListener('touchend', _guarded, { passive: false });
}

function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
