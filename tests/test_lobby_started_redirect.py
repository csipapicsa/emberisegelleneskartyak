"""Lobby page behavior when a room is already in game state."""
from __future__ import annotations

import subprocess
import textwrap


def test_lobby_redirects_started_room_state_to_game_page():
    script = textwrap.dedent(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        let createdSocket = null;
        class FakeSocket {
          constructor(code, sid) {
            this.code = code;
            this.sid = sid;
            this.handlers = {};
          }
          on(event, handler) {
            this.handlers[event] = handler;
            return this;
          }
          connect() {}
          close() {}
          send() {}
        }

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, {
              classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {},
              querySelectorAll() { return []; },
              querySelector() { return { checked: false }; },
              innerHTML: '',
              textContent: '',
              disabled: false,
            });
          }
          return elements.get(id);
        }

        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          GameSocket: function(code, sid) {
            createdSocket = new FakeSocket(code, sid);
            return createdSocket;
          },
          VoiceManager: function() {
            return {
              init() {},
              connectToPeers() {},
              cleanup() {},
              toggleMute() { return false; },
              toggleSpeaker() { return false; },
            };
          },
          window: { addEventListener() {} },
          document: { getElementById: element },
          location: {
            replaced: null,
            href: '',
            replace(url) { this.replaced = url; },
          },
          fetch: async () => ({
            ok: true,
            status: 200,
            json: async () => ({ started: false, is_player: true }),
          }),
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/lobby.js', 'utf8'), context);

        (async () => {
          const started = await context.initLobby('7137', 'admin_sid', 'Admin');
          assert.strictEqual(started, true);
          createdSocket.handlers.room_state({
            event: 'room_state',
            code: '7137',
            started: true,
            admin: 'admin_sid',
            players: [{ session_id: 'admin_sid', username: 'Admin' }],
            pending: [],
          });

          assert.strictEqual(context.location.replaced, '/game/7137');
        })().catch(err => {
          console.error(err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_lobby_redirects_missing_room_to_join_with_code():
    script = textwrap.dedent(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        let socketCreated = false;

        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          GameSocket: function() {
            socketCreated = true;
            return { on() { return this; }, connect() {}, close() {}, send() {} };
          },
          window: { addEventListener() {} },
          document: { getElementById: () => ({
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener() {},
            querySelectorAll() { return []; },
            querySelector() { return { checked: false }; },
          }) },
          location: {
            replaced: null,
            replace(url) { this.replaced = url; },
          },
          fetch: async () => ({ ok: false, status: 404 }),
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/lobby.js', 'utf8'), context);

        (async () => {
          const started = await context.initLobby('1000', 'sid', 'Admin');
          assert.strictEqual(started, false);
          assert.strictEqual(socketCreated, false);
          assert.strictEqual(context.location.replaced, '/join?code=1000');
        })().catch(err => {
          console.error(err);
          process.exit(1);
        });
        """
    )

    result = subprocess.run(
        ["node", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
