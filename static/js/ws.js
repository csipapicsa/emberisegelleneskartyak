class GameSocket {
  constructor(roomCode, sessionId) {
    this.roomCode = roomCode;
    this.sessionId = sessionId;
    this.handlers = {};
    this._ws = null;
    this._dead = false;
  }

  connect(username = '') {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let url = `${proto}://${location.host}/ws/${this.roomCode}?session_id=${this.sessionId}`;
    if (username) url += `&username=${encodeURIComponent(username)}`;

    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
      if (this._everConnected) {
        const h = this.handlers['reconnected'];
        if (h) h({});
      }
      this._everConnected = true;
    };

    this._ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      const handler = this.handlers[msg.event];
      if (handler) handler(msg);
      const catchAll = this.handlers['*'];
      if (catchAll) catchAll(msg);
    };

    this._ws.onclose = (e) => {
      if (this._dead) return;
      if (e.code === 4001 || e.code === 4002 || e.code === 4004 || e.code === 4005 || e.code === 4006) {
        this._dead = true;
        const h = this.handlers['fatal_close'];
        if (h) h({ code: e.code });
        return;
      }
      setTimeout(() => this.connect(username), 2000);
    };

    this._ws.onerror = () => this._ws.close();
  }

  on(event, fn) {
    this.handlers[event] = fn;
    return this;
  }

  send(event, payload = {}) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ event, ...payload }));
    }
  }

  close() {
    this._dead = true;
    this._ws && this._ws.close();
  }
}
