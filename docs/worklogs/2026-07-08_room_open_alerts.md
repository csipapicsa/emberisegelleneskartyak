# WIP — Free-room tab alert (sound + favicon/title flash), plus a dead-disconnect-handler bug it surfaced (2026-07-08)

Session that started as a small UX add-on (notice a new open room while `/join`
is backgrounded) and ended up finding a long-standing bug where the server has
never actually detected a client disconnect.

## Status

Branch `room-open-alerts`, not merged. Uncommitted on top of it: unrelated
pre-existing WIP in `server/main.py` (chat.db error-handling hardening) and
`static/js/hand.js` (drag hit-test fix) that was already sitting uncommitted
in the working tree before this session started — left untouched, not part
of this feature.

- Frontend (`static/join.html`): **live** — static files are served fresh
  from disk on every request (`FileResponse`, no in-memory caching), so no
  restart needed.
- Backend (`server/main.py`): **implemented, pytest-clean, NOT yet deployed.**
  uvicorn runs without `--reload`; a restart is required before any of the
  three backend fixes below take effect. Confirmed via `stat`: `main.py`'s
  last edit is newer than the service's last `ActiveEnterTimestamp`.

```bash
venv/bin/python -m pytest -q
# 230 passed
```

Restart when ready (not run by Claude — no passwordless sudo in this
environment, and there was live traffic on `/api/rooms` at the time):

```bash
sudo systemctl restart emberisegellenes.service
```

## What was built

### Frontend — `static/join.html`
The "Nyitott szobák" list on `/join` already polled `/api/rooms` every 5s
(`setInterval`); this adds an attention alert on top of that poll when the
tab is backgrounded:

- Two-note WebAudio chime (no audio asset — a couple of oscillator
  envelopes) — chosen over an `<audio>` file so there's nothing to fetch or
  cache-bust.
- Canvas-drawn red-dot badge over the existing SVG favicon, alternated with
  the original every 1s alongside a flashing document title
  (`🔴 Új szoba nyílt!`). `setFavicon()` clones-and-replaces the `<link
  rel="icon">` rather than mutating `.href` in place, since some browsers
  don't repaint the tab icon on a bare href mutation.
- **Alert condition is level-triggered, not edge-triggered**: active
  whenever a not-yet-started room currently exists AND the tab is
  backgrounded — re-evaluated on every 5s poll, so it turns itself off
  automatically once every room has started or closed, without requiring
  the user to ever refocus the tab. This was a deliberate revision after the
  first pass only fired once on a *newly appeared* room and had no way to
  turn itself off short of a refocus.
- The chime, however, stays edge-triggered on a separate flag
  (`_chimedForFreeRoom`): it fires once per continuous stretch of "a free
  room exists," not once per poll and not once per refocus/reblur cycle —
  see bug #1 below for why that distinction mattered.

### Backend — `server/main.py`
Three separate fixes, discovered by testing the frontend feature end-to-end
against the live room list rather than in isolation:

1. `/api/rooms` (line ~406) now excludes unstarted rooms where nobody is
   connected — an abandoned lobby has no admin left to approve a new
   joiner into, so it shouldn't be advertised as joinable.
2. `_room_cleanup_task`'s TTL for unstarted rooms: previously a flat 10min
   (`UNSTARTED_TTL`) regardless of connection state. Now an all-disconnected
   unstarted lobby reaps on the same 5min `EMPTY_TTL` already used for
   abandoned in-progress rooms, instead of the longer "still connected but
   quiet" grace period.
3. **The actual root cause** (see bug #3 below): the WebSocket message loop's
   `except WebSocketDisconnect:` was dead code. Fixed with `try/finally`;
   removed the now-unused `WebSocketDisconnect` import.

## Bugs found (all from live testing against the real /join page, not pytest)

1. **Chime replayed every time the tab was glanced at and left again.**
   Cause: the chime was gated on `_alertTimer`, which `stopRoomAlert()`
   clears on every refocus — so leaving the tab again after checking it
   restarted the timer from scratch, chime included. Fixed by decoupling:
   `startFlashing()`/`stopRoomAlert()` only manage the visual, a separate
   `_chimedForFreeRoom` flag (reset only when the free-room condition itself
   goes away) governs the sound.

2. **Flashing didn't stop when the tab was actually clicked.**
   Cause: `isPageActive()` checked `!document.hidden && document.hasFocus()`.
   `document.hasFocus()` reflects OS-level window focus, not tab selection —
   it can stay `false` right after clicking a tab, especially over
   remote/VM sessions (this environment is a remote dev box, which is
   likely how this got caught). Dropped `hasFocus()` entirely; `document.hidden`
   alone is reliable and fires the instant the tab is selected, at the cost
   of losing (never-verified-as-needed) detection of "user alt-tabbed to a
   different OS application while leaving this tab selected."

3. **Room stayed listed indefinitely after the browser was closed —
   root cause was much bigger than the room list itself.**
   `ws.iter_json()` (Starlette) catches `WebSocketDisconnect` internally and
   just ends the async generator — it never propagates to the `except
   WebSocketDisconnect:` wrapped around the loop in `websocket_endpoint`.
   That handler has therefore never fired, for any disconnect, ever (not
   specific to incognito windows or this feature) — confirmed live: `ss -tn`
   showed zero established connections on port 8010 while `/api/rooms`
   still reported the room's player as connected. Practical effect: `player.
   connected` never becomes `False` on a real disconnect, so admin
   promotion never ran, and both `all_gone` checks in the cleanup task
   (including the new one from fix #2 above) were inert — fixes #1/#2 were
   correct but had nothing to act on until this was fixed. Switched the loop
   to `try/finally` so cleanup runs on every exit path regardless of how
   `iter_json()` behaves.

## Decisions

- Sound+favicon/title-flash was chosen over real OS push notifications
  (Web Push + service worker) after discussing tradeoffs with Gergo:
  background tabs keep polling (throttled, not killed) on desktop, so this
  covers the "left the tab open, alt-tabbed away" case well; a
  fully-backgrounded phone (screen off / app switched) will not run the poll
  at all regardless of any client-side technique, and reaching that
  reliably would need Web Push (VAPID keys, permission prompt, server-side
  push, and still doesn't work on regular Safari tabs on iOS without an
  installed PWA) — explicitly out of scope for this pass.
- No pytest added for the alert itself — it's pure client-side JS (canvas,
  WebAudio, Page Visibility API), zero backend surface; `tests/` is
  pytest-only and has no browser/DOM harness to exercise it against. Verified
  by syntax-checking the inline script with Node (`new Function(script)`)
  and by live manual testing against the real site, not automated.
- Backend disconnect-handling fix (`try/finally`) intentionally broadened
  beyond "just make the room list right" once the dead-exception-handler
  root cause was found — narrowly patching only the room-list symptom would
  have left admin promotion and the started-room `EMPTY_TTL` path silently
  broken too.

## Also this session — block "Start" while join requests are pending

Separate, smaller ask on the same branch: in `room.html`, admins could hit
"Játék indítása" while a "Belépési kérelmek" (pending join request) was still
unresolved, silently leaving that person locked out mid-approval once the
game started.

- `server/main.py`, `start_game` handler: added `if room.pending: ... return`
  right after the existing admin/`room.started` guard, before the
  edition-selection checks — same guard-clause-with-error-message style
  already used there. This is the enforcement that actually matters; a
  disabled button alone doesn't stop a direct websocket event.
- `static/js/lobby.js` `onRoomState`: `startBtn.disabled` now also considers
  `state.pending.length > 0`; added `#pendingBlockHint`, a small muted hint
  line under the button in `static/room.html` explaining why it's disabled,
  toggled the same way. Both react live off the existing `room_state`
  broadcast, so approving/denying a request re-enables the button
  immediately with no extra wiring.
- Cache-bust bumped: `lobby.js?v=20260708-pendingblock`.
- New test: `tests/test_room_config.py::test_start_game_blocked_while_pending_players_exist`,
  modeled on the existing `test_deny_player_removes_pending_and_broadcasts_room_state`
  in the same file (`_handle_message` + `FakeManager`). Full suite: 231
  passed (230 → 231).
- This one **also needs the service restart** (backend change) to go live,
  same as the other `server/main.py` fixes above — bundle it into the same
  restart rather than a separate one.

## Also this session — denied joiner was stuck on the waiting screen

Reported from live testing: a pending joiner who got denied saw the toast
("A belépési kérelmet elutasították.") but the page just sat on "Várakozás…"
afterwards — no navigation away, needed a manual reload/back to reach
`/join` again. Root cause: `deny_player` sent the denial as a plain generic
`error` event, and `lobby.js`'s `.on('error', ...)` handler only ever shows a
toast, by design (it's shared by many unrelated error messages) — nothing
was listening for "this specific error means you're done here."

- `server/main.py` `deny_player`: renamed the event sent to the denied
  target from `error` to a dedicated `denied` event (same message text).
- `static/js/lobby.js`: new `onDenied()` handler, wired via
  `.on('denied', onDenied)` — mirrors the existing `onRoomClosed()` pattern
  exactly (toast, then `setTimeout(..., 1200)` before navigating away), but
  redirects through `redirectToJoin()` (prefills the room code) rather than
  bare `/join`, since the room itself is still alive — only this one
  joiner was rejected.
- Updated `tests/test_room_config.py::test_deny_player_removes_pending_and_broadcasts_room_state`
  for the renamed event; added nothing new since the existing test already
  covered the send, just needed the event-name assertion updated.
- Cache-bust: `lobby.js?v=20260708-denyredirect`.
- Backend change — needs the same pending restart as everything else above.

## Remaining / next steps

- **Restart required** to deploy all three backend fixes (see Status).
- After restarting, re-verify: close a lobby's only tab → room should drop
  out of `/join`'s list within one ~5s poll, not linger.
- Since the disconnect-handler bug was global (not feature-specific), worth
  a general eye on other `player.connected`-dependent behavior post-restart
  (admin promotion on disconnect, round-progression via `all_submitted`/
  `all_voted` when a player drops mid-round) in case fixing a long-dormant
  code path changes something that was quietly relying on the old (broken)
  behavior — none of the 230 existing tests caught this, since they don't
  exercise a real Starlette websocket disconnect end-to-end.
- Mobile "phone screen off" reach (Web Push) is a separate, larger follow-up
  if it turns out to matter — not started.
