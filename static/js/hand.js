class Hand {
  constructor(containerEl, onSelect, holdDurationMs = 2500, onAction = null) {
    this.container    = containerEl;
    this.track        = containerEl.querySelector('.hand-track');
    this.onSelect     = onSelect;
    this.onAction     = onAction;
    this.holdDuration = holdDurationMs;
    this.cards        = [];
    this.action       = { visible: false, label: 'Kérek új kártyákat' };
    this.selected     = [];
    this.pickCount    = 1;
    this.submitted    = false;
    this.disabled     = false;

    this._offset          = 0;
    this._activeIdx       = -1;
    this._holdTimer       = null;
    this._dragging        = false;
    this._moved           = false;
    this._dragStartX      = 0;
    this._dragStartOffset = 0;
    this._pendingOffset   = 0;
    this._rafId           = null;

    this._CARD_STEP = 90; // 130px card width - 40px overlap

    this._bindEvents();
  }

  setCards(cards) {
    this.cards     = cards;
    this.selected  = [];
    this.submitted = false;
    this.disabled  = false;
    this._offset   = 0;
    this._activeIdx = -1;
    clearTimeout(this._holdTimer);
    this._rebuild();
  }

  setPickCount(n) { this.pickCount = n; }

  setAction(visible, label = 'Kérek új kártyákat') {
    this.action = { visible, label };
    this._rebuild();
  }

  setDisabled(val) {
    this.disabled = val;
    this._updateClasses();
  }

  // Full DOM rebuild — only on setCards
  _rebuild() {
    const cards = this.cards.map((c, i) => `
      <div class="hand-card" data-idx="${i}" data-id="${c.id}">
        <!-- Egyenkénti kártyacsere későbbi használatra:
        <button class="swap-card-btn" type="button" data-id="${c.id}" title="Kártya cseréje">Csere</button>
        -->
        <span>${esc(c.text)}</span>
      </div>
    `).join('');
    const action = this.action.visible ? `
      <div class="hand-card hand-action-card" data-action="new-hand">
        <span>${esc(this.action.label)}</span>
      </div>
    ` : '';
    this.track.innerHTML = cards + action;
    this._applyOffset();
    this._updateClasses();
  }

  // Update classes on existing elements — no DOM rebuild
  _updateClasses() {
    this.track.querySelectorAll('.hand-card[data-idx]').forEach((el, i) => {
      const c          = this.cards[i];
      const isSelected = this.selected.includes(c.id);
      const isActive   = i === this._activeIdx;
      el.className = 'hand-card'
        + (isSelected ? ' selected' : isActive ? ' active' : '')
        + (this.disabled || this.submitted ? ' disabled' : '');

      const badge = el.querySelector('.pick-badge');
      if (isSelected && this.pickCount > 1) {
        const order = this.selected.indexOf(c.id) + 1;
        if (badge) { badge.textContent = order; }
        else {
          const b = document.createElement('span');
          b.className = 'pick-badge';
          b.textContent = order;
          el.appendChild(b);
        }
      } else if (badge) {
        badge.remove();
      }
    });

    this.track.querySelectorAll('.hand-action-card').forEach(el => {
      el.className = 'hand-card hand-action-card' + (this.disabled || this.submitted ? ' disabled' : '');
    });
  }

  _applyOffset() {
    this.track.style.transform = `translateX(${this._offset}px)`;
  }

  _bindEvents() {
    const el = this.container;

    // Non-passive touchmove so we can preventDefault and block page scroll
    el.addEventListener('touchstart', e => {
      if (e.target.closest('.swap-card-btn')) return;
      this._onStart(e.touches[0].clientX);
    }, { passive: true });
    el.addEventListener('touchmove',  e => {
      e.preventDefault();
      this._onMove(e.touches[0].clientX);
    }, { passive: false });
    el.addEventListener('touchend', e => this._onEnd(e.changedTouches[0].clientX));
    el.addEventListener('touchcancel', () => { this._dragging = false; });

    el.addEventListener('mousedown', e => {
      if (e.target.closest('.swap-card-btn')) return;
      this._onStart(e.clientX);
    });
    window.addEventListener('mousemove', e => { if (this._dragging) this._onMove(e.clientX); });
    window.addEventListener('mouseup',   e => { if (this._dragging) this._onEnd(e.clientX); });

    // Egyenkénti kártyacsere későbbi használatra:
    // this.track.addEventListener('click', e => {
    //   const btn = e.target.closest('.swap-card-btn');
    //   if (!btn || this.disabled || this.submitted) return;
    //   e.preventDefault();
    //   e.stopPropagation();
    //   this.onSwap?.(parseInt(btn.dataset.id, 10));
    // });
  }

  _onStart(x) {
    if (this.disabled || this.submitted) return;
    this._dragStartX      = x;
    this._dragStartOffset = this._offset;
    this._dragging        = true;
    this._moved           = false;
    this._pendingOffset   = this._offset;
    if (this._rafId) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  }

  _onMove(x) {
    if (!this._dragging) return;
    const dx = x - this._dragStartX;
    if (Math.abs(dx) > 4) this._moved = true;

    const max = 0;
    const min = -(Math.max(0, this._itemCount() - 2) * this._CARD_STEP);
    this._pendingOffset = Math.min(max, Math.max(min, this._dragStartOffset + dx));

    if (!this._rafId) {
      this._rafId = requestAnimationFrame(() => {
        this._rafId   = null;
        this._offset  = this._pendingOffset;
        this._applyOffset();

        // Only update active card highlight, no full rebuild
        const idx = this._idxAtX(x);
        if (idx !== this._activeIdx) {
          this._activeIdx = idx;
          this._updateClasses();
        }
      });
    }
  }

  _onEnd(x) {
    if (!this._dragging) return;
    this._dragging = false;

    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId  = null;
      this._offset = this._pendingOffset;
      this._applyOffset();
    }

    if (this._moved) {
      this._activeIdx = -1;
      this._updateClasses();
      return;
    }

    // Tap
    const idx = this._idxAtX(x);
    if (idx < 0 || idx >= this._itemCount()) return;

    if (this._isActionIdx(idx)) {
      this.onAction?.();
      return;
    }

    if (this._activeIdx === idx) {
      // Second tap on same card → confirm
      this._confirmCard(idx);
      return;
    }

    clearTimeout(this._holdTimer);
    this._activeIdx = idx;
    this._updateClasses();

    this._holdTimer = setTimeout(() => {
      this._activeIdx = -1;
      this._updateClasses();
    }, this.holdDuration);
  }

  // Calculate card index from client X using math (no getBoundingClientRect in hot path)
  _idxAtX(clientX) {
    const trackRect = this.track.getBoundingClientRect();
    const localX    = clientX - trackRect.left;
    if (localX < 0 || !this._itemCount()) return -1;
    const idx = Math.floor(localX / this._CARD_STEP);
    return Math.min(Math.max(0, idx), this._itemCount() - 1);
  }

  _confirmCard(idx) {
    clearTimeout(this._holdTimer);
    this._activeIdx = -1;
    const card = this.cards[idx];
    if (!card) return;

    if (this.selected.includes(card.id)) {
      this.selected = this.selected.filter(id => id !== card.id);
      this._updateClasses();
      return;
    }

    if (this.selected.length >= this.pickCount) {
      this.selected[this.pickCount - 1] = card.id;
    } else {
      this.selected.push(card.id);
    }
    this._updateClasses();

    if (this.selected.length === this.pickCount) {
      this._showConfirm();
    }
  }

  _showConfirm() {
    const modal   = document.getElementById('confirmModal');
    const preview = document.getElementById('confirmPreview');
    const texts   = this.selected.map(id => this.cards.find(c => c.id === id)?.text || '');
    const multi = document.getElementById('confirmMultiPreview');
    if (texts.length === 1) {
      preview.textContent = texts[0];
      preview.style.display = '';
      multi.style.display = 'none';
    } else {
      preview.style.display = 'none';
      multi.style.display = 'flex';
      multi.innerHTML = texts.map(t =>
        `<div class="card-white" style="font-size:.9rem">${esc(t)}</div>`
      ).join('');
    }
    modal.classList.remove('hidden');

    // Clone buttons to remove any stale listeners
    const yesBtn = document.getElementById('confirmYes');
    const noBtn  = document.getElementById('confirmNo');
    const newYes = yesBtn.cloneNode(true);
    const newNo  = noBtn.cloneNode(true);
    yesBtn.replaceWith(newYes);
    noBtn.replaceWith(newNo);

    newYes.addEventListener('click', () => {
      modal.classList.add('hidden');
      this.submitted = true;
      this._updateClasses();
      this.onSelect(this.selected.slice());
    });
    newNo.addEventListener('click', () => {
      modal.classList.add('hidden');
      this.selected = [];
      this._updateClasses();
    });
  }

  _itemCount() {
    return this.cards.length + (this.action.visible ? 1 : 0);
  }

  _isActionIdx(idx) {
    return this.action.visible && idx === this.cards.length;
  }
}

function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
