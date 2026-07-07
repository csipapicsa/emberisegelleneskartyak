// Room-scoped chat: draggable bubble + modal, shared by the lobby (room.html)
// and the in-game screen (game.html). Backed by the room's existing
// GameSocket connection — no separate transport.

let _chatSocket = null;
let _chatMessages = [];
let _chatUnread = 0;
let _chatHistoryPushed = false;

function initChat(socket) {
  _chatSocket = socket;
  _chatMessages = [];
  _chatUnread = 0;
  _chatHistoryPushed = false;

  socket.on('chat_history', onChatHistory);
  socket.on('chat_message', onChatMessage);

  makeDraggableFab('chatBtn', 'chatFabPos',
    () => window.innerWidth - 56, () => window.innerHeight - 280,
    openChat);

  document.getElementById('chatSendBtn')?.addEventListener('click', sendChatMessage);
  document.getElementById('chatCloseBtn')?.addEventListener('click', () => closeChat());
  document.getElementById('chatInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); sendChatMessage(); }
  });

  // Phone back button: while chat is open, treat it as "close chat" rather
  // than letting it fall through to the browser's real back navigation
  // (which would tear down the room's websocket and boot the player out).
  window.addEventListener('popstate', () => {
    const modal = document.getElementById('chatModal');
    if (modal && !modal.classList.contains('hidden')) closeChat(true);
  });
}

function hideChatUi() {
  document.getElementById('chatBtn')?.classList.add('hidden');
  document.getElementById('chatModal')?.classList.add('hidden');
}

function showChatUi() {
  document.getElementById('chatBtn')?.classList.remove('hidden');
}

function openChat() {
  document.getElementById('chatModal')?.classList.remove('hidden');
  document.getElementById('chatBtn')?.classList.remove('has-unread');
  _chatUnread = 0;
  _scrollChatToBottom();

  // Push a no-op history entry so the first back-press is "caught" by
  // popstate above instead of navigating away from the room.
  if (!_chatHistoryPushed) {
    history.pushState({ eekChatOpen: true }, '');
    _chatHistoryPushed = true;
  }
}

function closeChat(fromPopstate = false) {
  document.getElementById('chatModal')?.classList.add('hidden');
  if (_chatHistoryPushed) {
    _chatHistoryPushed = false;
    if (!fromPopstate) history.back();
  }
}

function onChatHistory(msg) {
  _chatMessages = msg.messages || [];
  _renderChatMessages();
}

function onChatMessage(msg) {
  _chatMessages.push(msg);
  _renderChatMessages();
  const modal = document.getElementById('chatModal');
  if (!modal || modal.classList.contains('hidden')) {
    _chatUnread++;
    document.getElementById('chatBtn')?.classList.add('has-unread');
  }
}

function sendChatMessage() {
  const input = document.getElementById('chatInput');
  if (!input || !_chatSocket) return;
  const text = input.value.trim();
  if (!text) return;
  _chatSocket.send('chat_send', { message: text });
  input.value = '';
}

function _renderChatMessages() {
  const el = document.getElementById('chatMessages');
  if (!el) return;
  el.innerHTML = _chatMessages.length
    ? _chatMessages.map(m => `
        <div class="chat-msg">
          <span class="chat-msg-user">${m.username}</span><span>${m.message}</span>
        </div>`).join('')
    : '<div class="chat-empty">Még nincs üzenet</div>';
  _scrollChatToBottom();
}

function _scrollChatToBottom() {
  const el = document.getElementById('chatMessages');
  if (el) el.scrollTop = el.scrollHeight;
}

// ── Draggable FAB (shared by chat bubble + any other floating action button) ──

function makeDraggableFab(btnId, storeKey, defaultX, defaultY, onTap) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
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
