# Emberiség Ellenes Kártyák — Historical Developer Progress Notes

> Historical snapshot from the initial Claude build. The frontend and many bugfixes
> have since been implemented. For current structure and behavior, use
> [README.md](README.md) and [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md).

## Current Status Summary

- Backend and vanilla-JS frontend are implemented.
- WebSocket gameplay, lobby, voice, chat/messages, feedback, standalone mode, inactivity handling, full-hand refresh, and invalid-room screens are implemented.
- Current regression suite: `timeout 180 venv/bin/pytest -q` → **142 passed**.

## Original Snapshot Status: Backend complete, frontend not yet started

---

## Server

- **Host:** 109.199.104.168
- **Port:** 8010
- **Run command:** `venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8010`
- **Python:** 3.11 (venv at `/opt/emberisegellenes_kartyak/venv`)
- **Working directory:** `/opt/emberisegellenes_kartyak`

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Realtime | FastAPI native WebSockets |
| Database | SQLite (`cards.db`) |
| Frontend | Vanilla JS + CSS (implemented after this snapshot) |

---

## Project Structure

```
/opt/emberisegellenes_kartyak/
├── cards.db                  # Pre-populated card/edition database
├── config.json               # Runtime tunables
├── requirements.txt
├── venv/
├── server/
│   ├── __init__.py
│   ├── main.py               # FastAPI app, all routes, WS handler
│   ├── game.py               # Round logic, timers, disconnect handling
│   ├── room.py               # In-memory Room/Player state
│   ├── deck.py               # Shuffle, deal, draw, reshuffle
│   ├── session.py            # UUID session token
│   ├── feedback.py           # Like/dislike/comment writes to DB
│   └── db.py                 # SQLite connection + card/edition queries
└── static/                   # Frontend (now implemented; placeholder note was historical)
    ├── index.html
    ├── join.html
    ├── create.html
    ├── room.html
    ├── game.html
    ├── css/
    │   └── base.css
    └── js/
        └── session.js
```

---

## Database Schema (`cards.db`)

```sql
CREATE TABLE edition (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE card (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL UNIQUE,
    type       TEXT NOT NULL,       -- 'black' or 'white'
    pick       INTEGER NOT NULL DEFAULT 1,  -- blanks on black cards
    edition_id INTEGER NOT NULL REFERENCES edition(id)
);

CREATE TABLE feedback_like (
    card_id    INTEGER NOT NULL REFERENCES card(id),
    session_id TEXT NOT NULL,
    PRIMARY KEY (card_id, session_id)
);

CREATE TABLE feedback_dislike (
    card_id    INTEGER NOT NULL REFERENCES card(id),
    session_id TEXT NOT NULL,
    PRIMARY KEY (card_id, session_id)
);

CREATE TABLE feedback_comment (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL REFERENCES card(id),
    session_id TEXT NOT NULL,
    username   TEXT NOT NULL,
    comment    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## HTTP API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/session` | Create or retrieve session token |
| `POST` | `/api/room` | Create room, returns 4-digit code |
| `GET` | `/api/room/<code>` | Room metadata |
| `GET` | `/api/editions` | List card editions |
| `GET` | `/api/config` | Frontend config (timers, hand size, etc.) |
| `WS` | `/ws/<room_code>?session_id=&username=` | WebSocket for a room |

---

## WebSocket Events

### Client → Server
| Event | Key fields | Notes |
|---|---|---|
| `approve_player` | `session_id` | Admin only |
| `deny_player` | `session_id` | Admin only |
| `configure_room` | `edition_ids`, `round_total`, `timer_seconds` | Admin only, pre-game |
| `start_game` | — | Admin only |
| `submit_card` | `card_ids: [int]` | Must match `pick` count of black card |
| `czar_pick` | `winner_session_id` | Czar only |
| `request_new_hand` | — | Current UI: full-hand refresh before submission, repeatable within a round |
| `swap_card` | `card_id` | Historical/dormant per-card swap endpoint; current UI does not call it |
| `feedback` | `card_id`, `type` (`like`/`dislike`/`comment`), `comment?` | Any time |

### Server → Client
| Event | Key fields |
|---|---|
| `room_state` | Full snapshot — sent on join, reconnect, any state change |
| `pending_approval` | Sent to joining player while waiting |
| `player_joined` | Sent to admin when someone is pending |
| `hand_update` | `hand: [card]` — sent after round start, round end, swap |
| `round_start` | `black_card`, `czar`, `round_num`, `round_total`, `scores` |
| `timer_tick` | `seconds_remaining` |
| `cards_revealed` | `submissions: [{session_id, username, cards}]` |
| `round_end` | `winner_session_id`, `scores` |
| `game_end` | `final_scores` |
| `submission_accepted` | Confirmation to submitting player |
| `player_disconnected` | `session_id` |
| `admin_promoted` | `session_id` |
| `error` | `message` |

---

## config.json

```json
{
  "card_hold_duration_ms": 2500,
  "max_room_players": 20,
  "room_code_digits": 4,
  "timer_options_seconds": [0, 15, 30, 45, 60],
  "round_options": [10, 20, 30, 40, 50],
  "hand_size": 10
}
```

---

## Key Decisions & Gotchas

- **Python 3.11** — system had 3.9; upgraded for performance. All server files use `from __future__ import annotations` to support modern type hint syntax.
- **Rooms are in-memory** — no persistence across server restarts. Rooms are short-lived by design.
- **Session identity** — `session_id` is a UUID generated server-side on first visit, stored in `localStorage`. This is how players reconnect mid-game.
- **Reconnection** — if a player with a known `session_id` reconnects to an active room, they get a full `room_state` + `hand_update` snapshot and resume normally.
- **Admin disconnect** — next player in join order is silently promoted. Original admin reclaims role on reconnect only if promotion hasn't happened yet.
- **Czar disconnect** — current behavior: if the czar disconnects/is kicked/times out while choosing, the round is skipped without points and the next connected czar takes over.
- **Deck exhaustion** — all played white cards are reshuffled back in when the deck runs out.
- **Feedback** — comments are HTML-escaped and capped at 500 chars server-side.
- **Bash tool** — Claude Code's bash tool does not work on this server (exit 127). File read/write/search tools work fine.

---

## Original What's Next (historical)

1. **Frontend** — historical TODO; pages are now built:
   - `index.html` — landing page
   - `join.html` — username + room code entry
   - `create.html` — username entry, get room code
   - `room.html` — lobby: player approval, game config, start button
   - `game.html` — main game screen: black card, hand fan, czar view, scoring
   - CSS: `base.css`, `cards.css` (fan/swipe/flip animations), `game.css`
   - JS: `ws.js`, `session.js`, `hand.js`, `game.js`, `lobby.js`

2. **Nginx** — historical TODO; reverse proxy config exists in `nginx_emberisegellenes.conf`

3. **Systemd service** — historical TODO; service file exists in `emberisegellenes.service`
