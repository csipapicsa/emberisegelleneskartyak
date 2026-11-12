"""Browser-side game behavior smoke tests."""
from __future__ import annotations

import subprocess
import textwrap


def run_node(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "-e", textwrap.dedent(script)],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )


def test_request_new_hand_keeps_action_visible():
    result = run_node(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, {
              id,
              style: {},
              classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {},
              querySelectorAll() { return []; },
              querySelector() { return null; },
              getBoundingClientRect() { return { left: 0, top: 0 }; },
              textContent: '',
              innerHTML: '',
            });
          }
          return elements.get(id);
        }

        const sent = [];
        const actionCalls = [];
        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          window: {
            innerWidth: 480,
            innerHeight: 800,
            addEventListener() {},
          },
          document: {
            getElementById: element,
            querySelector() { return element('query'); },
            querySelectorAll() { return []; },
          },
          location: { replace() {} },
          actionCalls,
          sent,
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/chat.js', 'utf8'), context);
        vm.runInContext(fs.readFileSync('static/js/game.js', 'utf8'), context);
        vm.runInContext(`
          hand = { setAction(visible, label) { actionCalls.push({ visible, label }); } };
          socket = { send(event, payload) { sent.push({ event, payload }); } };
          showNewHandBtn(true);
          requestNewHand();
        `, context);

        assert.strictEqual(
          JSON.stringify(sent),
          JSON.stringify([{ event: 'request_new_hand', payload: {} }])
        );
        assert.strictEqual(actionCalls.length, 1);
        assert.strictEqual(actionCalls[0].visible, true);
        """
    )

    assert result.returncode == 0, result.stderr


def test_vote_renderer_disables_own_submission_when_self_vote_prevented():
    result = run_node(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, {
              id,
              style: {},
              classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {},
              querySelectorAll() { return []; },
              querySelector() { return null; },
              getBoundingClientRect() { return { left: 0, top: 0 }; },
              textContent: '',
              innerHTML: '',
            });
          }
          return elements.get(id);
        }

        const sent = [];
        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          window: {
            innerWidth: 480,
            innerHeight: 800,
            addEventListener() {},
          },
          document: {
            getElementById: element,
            querySelector() { return element('query'); },
            querySelectorAll() { return []; },
          },
          location: { replace() {} },
          sent,
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/chat.js', 'utf8'), context);
        vm.runInContext(fs.readFileSync('static/js/game.js', 'utf8'), context);
        vm.runInContext(`
          mySessionId = 'sid_1';
          preventSelfVote = true;
          currentPlayerCount = 2;
          socket = { send(event, payload) { sent.push({ event, payload }); } };
          renderSubmissionsVote([
            { session_id: 'sid_1', cards: [{ id: 1, text: 'own' }] },
            { session_id: 'sid_2', cards: [{ id: 2, text: 'other' }] },
          ]);
          castVote('sid_1');
          castVote('sid_2');
        `, context);

        const html = elements.get('gameMiddle').innerHTML;
        assert(html.includes('data-sid="sid_1" aria-disabled="true"'));
        assert(html.includes('vote-disabled'));
        assert(!html.includes(`onclick="castVote('sid_1')"`));
        assert(html.includes(`onclick="castVote('sid_2')"`));
        assert.strictEqual(
          JSON.stringify(sent),
          JSON.stringify([{ event: 'cast_vote', payload: { winner_session_id: 'sid_2' } }])
        );
        """
    )

    assert result.returncode == 0, result.stderr


def test_room_state_restores_revealed_vote_table():
    result = run_node(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, {
              id,
              style: {},
              classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {},
              querySelectorAll() { return []; },
              querySelector() { return null; },
              getBoundingClientRect() { return { left: 0, top: 0 }; },
              textContent: '',
              innerHTML: '',
            });
          }
          return elements.get(id);
        }

        const actionCalls = [];
        const disabledCalls = [];
        const pickCountCalls = [];
        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          window: {
            innerWidth: 480,
            innerHeight: 800,
            addEventListener() {},
          },
          document: {
            getElementById: element,
            querySelector() { return element('query'); },
            querySelectorAll() { return []; },
          },
          location: { replace() {} },
          actionCalls,
          disabledCalls,
          pickCountCalls,
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/chat.js', 'utf8'), context);
        vm.runInContext(fs.readFileSync('static/js/game.js', 'utf8'), context);
        vm.runInContext(`
          mySessionId = 'sid_1';
          hand = {
            setAction(visible, label) { actionCalls.push({ visible, label }); },
            setDisabled(value) { disabledCalls.push(value); },
            setPickCount(n) { pickCountCalls.push(n); },
          };
          onRoomState({
            event: 'room_state',
            code: '1234',
            started: true,
            finished: false,
            round_num: 2,
            round_total: 10,
            timer_seconds: 30,
            voting_mode: 'vote',
            prevent_self_vote: true,
            czar: 'sid_0',
            admin: 'sid_0',
            phase: 'revealed',
            black_card: { id: 100, text: 'black _', pick: 1 },
            players: [
              { session_id: 'sid_1', username: 'One', score: 0, connected: true, submitted: true },
              { session_id: 'sid_2', username: 'Two', score: 0, connected: true, submitted: true },
            ],
            pending: [],
            submissions: [
              { session_id: 'sid_1', cards: [{ id: 1, text: 'own card' }] },
              { session_id: 'sid_2', cards: [{ id: 2, text: 'other card' }] },
            ],
          });
        `, context);

        const html = elements.get('gameMiddle').innerHTML;
        assert(html.includes('own card'));
        assert(html.includes('other card'));
        assert(html.includes('voteCounter'));
        assert(html.includes('vote-disabled'));
        assert.strictEqual(elements.get('statusBar').textContent, 'Szavazz a kedvenced kártyájára!');
        assert.strictEqual(actionCalls.at(-1).visible, false);
        assert.strictEqual(disabledCalls.at(-1), true);
        assert.strictEqual(pickCountCalls.at(-1), 1);
        """
    )

    assert result.returncode == 0, result.stderr


def test_room_state_restores_multi_pick_count():
    # First round of a game arrives via the room_state snapshot (the fresh
    # socket greeting), not a round_start event. onRoomState must still tell the
    # hand how many cards a multi-blank black card needs, otherwise the hand
    # keeps its default pickCount of 1 and submits a single card for a pick-2
    # black card — which the server rejects.
    result = run_node(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, {
              id,
              style: {},
              classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {},
              querySelectorAll() { return []; },
              querySelector() { return null; },
              getBoundingClientRect() { return { left: 0, top: 0 }; },
              textContent: '',
              innerHTML: '',
            });
          }
          return elements.get(id);
        }

        const pickCountCalls = [];
        const context = {
          console,
          encodeURIComponent,
          setTimeout,
          clearTimeout,
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          window: { innerWidth: 480, innerHeight: 800, addEventListener() {} },
          document: {
            getElementById: element,
            querySelector() { return element('query'); },
            querySelectorAll() { return []; },
          },
          location: { replace() {} },
          pickCountCalls,
        };

        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/chat.js', 'utf8'), context);
        vm.runInContext(fs.readFileSync('static/js/game.js', 'utf8'), context);
        vm.runInContext(`
          mySessionId = 'sid_1';
          hand = {
            setAction() {},
            setDisabled() {},
            setPickCount(n) { pickCountCalls.push(n); },
          };
          onRoomState({
            event: 'room_state',
            code: '1234',
            started: true,
            finished: false,
            round_num: 1,
            round_total: 10,
            timer_seconds: 30,
            voting_mode: 'czar',
            prevent_self_vote: true,
            czar: 'sid_0',
            admin: 'sid_0',
            phase: 'submitting',
            black_card: { id: 100, text: 'black _ _', pick: 2 },
            players: [
              { session_id: 'sid_1', username: 'One', score: 0, connected: true, submitted: false },
              { session_id: 'sid_2', username: 'Two', score: 0, connected: true, submitted: false },
            ],
            pending: [],
          });
        `, context);

        assert.strictEqual(pickCountCalls.at(-1), 2);
        """
    )

    assert result.returncode == 0, result.stderr


def test_a_tie_shows_every_winning_submission():
    """Round end with a tie: the overlay shows each winner's cards under their name, and every winning pile
    is highlighted, not only the first one."""
    result = run_node(
        """
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const elements = new Map();
        function element(id) {
          if (!elements.has(id)) {
            elements.set(id, { id, style: {}, classList: { add() {}, remove() {}, toggle() {} },
              addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
              getBoundingClientRect() { return { left: 0, top: 0 }; }, textContent: '', innerHTML: '' });
          }
          return elements.get(id);
        }
        function pile(sid) {
          const classes = new Set();
          return { dataset: { sid }, classes, classList: { add(c) { classes.add(c); } }, appendChild() {} };
        }
        const piles = [pile('a'), pile('b'), pile('c')];
        const context = {
          console, encodeURIComponent, clearTimeout,
          setTimeout() {},
          localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
          window: { innerWidth: 480, innerHeight: 800, addEventListener() {} },
          document: {
            getElementById: element,
            querySelector() { return element('query'); },
            querySelectorAll(sel) { return sel === '.submission-pile' ? piles : []; },
            createElement() { return { style: {} }; },
          },
          location: { replace() {} },
        };
        vm.createContext(context);
        vm.runInContext(fs.readFileSync('static/js/chat.js', 'utf8'), context);
        vm.runInContext(fs.readFileSync('static/js/game.js', 'utf8'), context);
        vm.runInContext(`
          updateScores = () => {};
          onRoundEnd({
            winner_session_id: 'a', winners: ['a', 'b'], scores: [],
            authors: [
              { session_id: 'a', username: 'Anna',  cards: [{ id: 1, text: 'Első lap' }] },
              { session_id: 'b', username: 'Bence', cards: [{ id: 2, text: 'Második lap' }] },
              { session_id: 'c', username: 'Csilla', cards: [{ id: 3, text: 'Vesztes lap' }] },
            ],
          });
        `, context);

        const label = element('winnerLabel').innerHTML;
        const cards = element('winnerCards').innerHTML;
        assert.ok(label.includes('Döntetlen') && label.includes('Anna') && label.includes('Bence'), label);
        assert.ok(cards.includes('Első lap') && cards.includes('Második lap'), cards);
        assert.ok(!cards.includes('Vesztes lap'), cards);
        assert.ok(cards.includes('Anna') && cards.includes('Bence'), 'each group is labelled with its author');
        assert.deepStrictEqual(piles.map(p => p.classes.has('winner-pick')), [true, true, false]);

        // A single winner: just the cards, no group labels.
        vm.runInContext(`
          showWinnerOverlay({ winner_session_id: 'c', winners: ['c'],
            authors: [{ session_id: 'c', username: 'Csilla', cards: [{ id: 3, text: 'Vesztes lap' }] }] });
        `, context);
        assert.ok(element('winnerLabel').innerHTML.includes('Győztes: Csilla'));
        assert.strictEqual(element('winnerCards').innerHTML, '<div class="sub-card">Vesztes lap</div>');
        console.log('ok');
        """
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
