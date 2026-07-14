# WIP — Edition display order, card-idea moderation status, room-replaced info screen (2026-07-11)

## Status

Branch `card-moderation-and-edition-order`, off `main` after fast-forwarding
`room-open-alerts` → `main` (ff-only, 1 commit: "Add free-room tab alert, fix
dead websocket disconnect handler, block start while pending, fix
denied-joiner redirect"). **Not merged, not deployed, not pushed to origin.**

```bash
venv/bin/python -m pytest -q
# 237 passed
```

Backend changes below need `sudo systemctl restart emberisegellenes.service`
to go live (not run this session — user must run it).

## What was built

### 1. Edition display order + Hungarian labels
New file `server/edition_display.py`: a single `ORDER` list of
`(db_name, hungarian_label)` tuples controlling both sort order and label for
the "Kártyacsomagok" checkbox list in the lobby. Wired into `/api/editions`
(`server/main.py`, `api_editions()`): after appending the virtual community
edition, the combined list is sorted by `edition_display.sort_key(name)` and
every `name` is relabeled via `edition_display.label_for(name)`. Anything not
listed in `ORDER` falls back to its raw DB name and sorts after every listed
edition.

Current order (per Gergo): Magyar Alap → Vegyes → Ti küldtétek → Második
kiadás (2nd Expansion) → Harmadik kiadás (3rd Expansion) → Negyedik kiadás →
Ötödik kiadás → Hatodik kiadás.

To add/reorder/rename an edition later: edit `ORDER` in
`server/edition_display.py`, nothing else needs to change.

### 2. Card-idea moderation status marker (read-only)
`server/messages_db.py` `list_card_ideas()`: now also selects `approved` and
`seen_by_admin`, and orders by `created_at DESC, id DESC` (newest submission
first) instead of by like count.

`static/kartyaszavazas.html`: each card now shows a small status line under
the vote buttons:
- `✅ Jóváhagyva – játszható a „Ti küldtétek” csomagban` (approved=1)
- `👀 Megnézve, egyelőre nem került be a játékba` (seen_by_admin=1, approved=0)
- `⏳ Elbírálás alatt` (neither)

Explicitly **read-only, public, no admin controls** — confirmed with Gergo.
Approval is still done by hand in `messages.db` via sqlite; this only makes
the current status visible to everyone on the voting page.

### 3. "Room replaced" info screen (two-tabs-same-admin race)
Traced from a real bug report: Gergo opened `/create` in two tabs of the same
browser. Both tabs share one `session_id` (localStorage), and the server
enforces "one admin, one active room" — `_close_other_rooms_admined_by()`
(`server/main.py`) force-closes any room a session already administers when
that session creates a new one. If the first tab's websocket hadn't connected
yet ("room init" screen) when the second tab created its room, the first
tab's room was deleted out from under it with zero explanation — it just
silently failed to open, landing nowhere useful.

This is **intentional** behavior (one admin can't run two rooms at once), so
the fix is purely informational, not a behavior change:

- `server/main.py`: new short-lived redirect table
  `_admin_room_redirects: dict[str, tuple[new_code, expires_at]]`
  (`_ADMIN_REDIRECT_TTL_SECONDS = 120`), written by
  `_record_admin_redirect()`/read by `_get_admin_redirect()`. `api_create_room`
  now generates the new room's code *before* closing the admin's prior
  room(s), so `_close_other_rooms_admined_by(session_id, except_code=None,
  new_code=code)` can record `old_code -> new_code` for each room it closes.
- Both existing "room not found" surfaces now consult this table before
  giving up:
  - `GET /api/room/{code}` 404 → `HTTPException(404, detail={"reason":
    "admin_opened_new_room", "new_code": ...})` instead of a bare string
    detail, when a redirect is on record.
  - `websocket_endpoint`'s final `else: await ws.close(code=4004)` branch (room
    truly doesn't exist) → if a redirect is on record, accepts the socket
    first, sends `{"event": "room_replaced", "new_code": ...}`, *then* closes
    with 4004 — same accept-send-close pattern already used for
    `username_taken`/4005.
- Frontend (`static/room.html`, `static/js/lobby.js`):
  - New full-page state `#roomReplaced` in `room.html` (same pattern as
    `#roomNotFound`/`#gameAlreadyStarted`): explains what happened, shows the
    new room's code, and links straight to `/room/{new_code}`.
  - `lobby.js`: `ensureLobbyAccess()`'s 404 branch now reads the response body
    for `detail.reason === 'admin_opened_new_room'`; if present, calls the new
    `onRoomReplaced(msg)` instead of `redirectToJoin()`. New
    `.on('room_replaced', onRoomReplaced)` websocket handler covers the case
    where the socket did connect just before the close. A new
    `roomReplacedHandled` flag (mirrors the existing
    `usernameChangeRequested` pattern for 4005) stops the generic `fatal_close`
    4004 handler from firing `redirectToJoin()` after `onRoomReplaced` already
    took over.
  - Cache-bust bumped: `lobby.js?v=20260711-roomreplaced`.

Verified live end-to-end on port 8099 (prod untouched, port 8010): created
room A, then room B under the same `session_id` before "connecting" room A's
websocket; confirmed `GET /api/room/A` returns the 404-with-redirect-detail
shape, and a raw websocket connect to `/ws/A` receives the `room_replaced`
message followed by close code 4004.

## Tests added
- `tests/test_lobby_started_redirect.py`: refactored the existing 404→/join
  test's fetch mock to include a `json()` method (needed now that
  `ensureLobbyAccess` reads the body), and added
  `test_lobby_shows_room_replaced_screen_when_admin_opened_new_room` — asserts
  no socket is created, no `/join` redirect happens, and the replacement
  screen's code/link get filled in correctly. Also now loads `chat.js` into
  the vm context (needed because `showRoomReplaced()` calls `hideChatUi()`).
- `tests/test_close_prior_room.py`: new tests for
  `_record_admin_redirect`/`_get_admin_redirect` (record, no-op when
  `new_code` isn't passed, TTL expiry) and for `api_get_room`'s 404 shape with
  and without a redirect on record.

Full suite: 231 → 237 passed.

## Decisions (confirmed with Gergo)
- Card-idea status marker: public and read-only, no approve/reject buttons —
  moderation stays manual in sqlite for now.
- Two-tabs-same-admin behavior itself is correct and left unchanged; only the
  messaging around it improved.

## Remaining / next steps
- Merge `card-moderation-and-edition-order` → `main`, push to origin, and
  restart the service when ready to deploy (none of this is live yet).
- Not yet manually re-tested in an actual two-tab browser session (only
  verified via curl/raw-websocket against a throwaway dev server) — worth one
  real-browser pass before/after deploy.
