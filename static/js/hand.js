// Maximum resting angle, in degrees, for a card in the fan. Each card keeps
// its own angle until it is actually picked up or put down, so the hand looks
// dealt by a person without the cards twitching during a drag.
const HAND_TILT_DEG = 5;

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
    this._tilts           = {};   // card id -> resting angle, stable between pickups

    this._CARD_STEP = 90; // approximate step, only used to bound horizontal drag/scroll range

    this._bindEvents();
  }

  setCards(cards) {
    this.cards     = cards;
    this._tilts    = {};   // a new deal gets laid out afresh
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
      <div class="hand-card" data-idx="${i}" data-id="${c.id}" style="${this._tiltStyle(c.id)}">
        <!-- Egyenkénti kártyacsere későbbi használatra:
        <button class="swap-card-btn" type="button" data-id="${c.id}" title="Kártya cseréje">Csere</button>
        -->
        <span>${esc(c.text)}</span>
        ${c.community ? '<span class="community-badge">Ti küldtétek</span>' : ''}
      </div>
    `).join('');
    const action = this.action.visible ? `
      <div class="hand-card hand-action-card" data-action="new-hand" style="${this._tiltStyle('__action__')}">
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
      const next       = this.cards[i + 1];
      const precedesSelected = !isSelected && next && this.selected.includes(next.id);
      el.className = 'hand-card'
        + (isSelected ? ' selected' : isActive ? ' active' : '')
        + (precedesSelected ? ' before-selected' : '')
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

  // A card keeps its angle until it is handled. Deliberately NOT re-rolled from
  // _updateClasses(): that runs inside the requestAnimationFrame drag loop, so
  // re-rolling there would make the whole fan vibrate while you swipe.
  _tiltFor(id) {
    if (!(id in this._tilts)) this._tilts[id] = this._rollTilt();
    return this._tilts[id];
  }

  _rollTilt() {
    return (Math.random() * 2 - 1) * HAND_TILT_DEG;
  }

  _tiltStyle(id) {
    return `--tilt:${this._tiltFor(id).toFixed(2)}deg`;
  }

  // Picked up or put back down — it never lands at quite the same angle.
  _reTilt(id) {
    this._tilts[id] = this._rollTilt();
    const el = this.track.querySelector(`.hand-card[data-id="${id}"]`);
    if (el) el.style.setProperty('--tilt', `${this._tilts[id].toFixed(2)}deg`);
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
      this._onMove(e.touches[0].clientX, e.touches[0].clientY);
    }, { passive: false });
    el.addEventListener('touchend', e => this._onEnd(e.changedTouches[0].clientX, e.changedTouches[0].clientY));
    el.addEventListener('touchcancel', () => { this._dragging = false; });

    el.addEventListener('mousedown', e => {
      if (e.target.closest('.swap-card-btn')) return;
      this._onStart(e.clientX);
    });
    window.addEventListener('mousemove', e => { if (this._dragging) this._onMove(e.clientX, e.clientY); });
    window.addEventListener('mouseup',   e => { if (this._dragging) this._onEnd(e.clientX, e.clientY); });

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

  _onMove(x, y) {
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

        // Only update active card highlight, no full rebuild. Real hit-test
        // (not fixed-step math) — the fan's actual per-card width/step varies
        // with padding/border and with which neighbours are selected/active,
        // so any fixed-step estimate drifts a card off once selections exist.
        const idx = this._idxAtPoint(x, y);
        if (idx !== this._activeIdx) {
          this._activeIdx = idx;
          this._updateClasses();
        }
      });
    }
  }

  _onEnd(x, y) {
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

    // Tap — resolve against whatever card is actually rendered under the
    // finger, not fixed-step math (selected/before-selected cards shift
    // their own margin for visual breathing room, which a fixed-step
    // formula has no way to know about).
    const idx = this._idxAtPoint(x, y);
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

  // Resolve a point against the real rendered card there — used both for the
  // live in-drag preview (per animation frame) and the final tap decision.
  // A hit-test is cheap enough for both; a fixed-step estimate isn't safe
  // once padding/border/selection state make per-card width/spacing vary.
  _idxAtPoint(clientX, clientY) {
    const el = document.elementFromPoint(clientX, clientY)?.closest('.hand-card');
    if (!el) return -1;
    if (el.dataset.action !== undefined) return this.cards.length;
    const idx = parseInt(el.dataset.idx, 10);
    return Number.isNaN(idx) ? -1 : idx;
  }

  _confirmCard(idx) {
    clearTimeout(this._holdTimer);
    this._activeIdx = -1;
    const card = this.cards[idx];
    if (!card) return;

    if (this.selected.includes(card.id)) {
      this.selected = this.selected.filter(id => id !== card.id);
      this._reTilt(card.id);                       // put back down
      this._updateClasses();
      return;
    }

    if (this.selected.length >= this.pickCount) {
      const replaced = this.selected[this.pickCount - 1];
      this.selected[this.pickCount - 1] = card.id;
      if (replaced !== undefined) this._reTilt(replaced);   // bumped one goes back too
    } else {
      this.selected.push(card.id);
    }
    this._reTilt(card.id);                         // picked up
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
