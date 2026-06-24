# Emberiség Ellenes Kártyák

Mobile-first, multiplayer, Hungarian-language card game in the spirit of *Cards Against Humanity*. Self-hosted FastAPI backend with a vanilla-JS frontend, served over WebSockets.

Live at: **emberisegelleneskartyak.hu**

### Stat: 

https://emberisegelleneskartyak.hu/admin/stats?key=***REMOVED***

---

## Stack

- **Backend:** Python 3.11, FastAPI, uvicorn, FastAPI-native WebSockets
- **Database:** SQLite (`cards.db`, `messages.db`)
- **Frontend:** Vanilla JS + CSS (no framework, no build step)
- **Process:** systemd unit (`emberisegellenes.service`), nginx reverse proxy, TLS

---

## Project structure

```
/opt/emberisegellenes_kartyak/
├── server/                       # FastAPI backend
│   ├── main.py                   # App entry: HTTP + WebSocket routes, static mounting
│   ├── game.py                   # Round logic, czar rotation, timer, reconnect handling
│   ├── room.py                   # In-memory Room / Player dataclasses + room store
│   ├── deck.py                   # Shuffle, deal, draw, reshuffle
│   ├── session.py                # UUID session token logic
│   ├── feedback.py               # like / dislike / comment DB writes
│   ├── messages_db.py            # Chat / message persistence
│   ├── voice.py                  # Voice/mute signalling over WS
│   └── db.py                     # SQLite connection, editions + cards queries
├── static/                       # Frontend (served by FastAPI)
│   ├── index.html                # Landing
│   ├── join.html  create.html    # Join / create room flows
│   ├── room.html                 # Lobby
│   ├── game.html                 # Game screen
│   ├── css/  js/                 # Stylesheets and client logic
│   ├── robots.txt  sitemap.xml   # SEO
│   └── og-image.png  news.json
├── tests/                        # Pytest suite (see "Running tests" below)
├── cards.db                      # Pre-populated card content (editions, cards)
├── messages.db                   # Chat / feedback persistence
├── config.json                   # Game tunables (hand size, timer options, etc.)
├── requirements.txt              # Python deps
├── pytest.ini                    # Pytest config (asyncio_mode = auto)
├── emberisegellenes.service      # systemd unit (production)
├── nginx_emberisegellenes.conf   # nginx reverse-proxy config
├── PROGRESS.md                   # Historical build log
├── PROJECT_STRUCTURE.md          # Detailed current structure/spec notes
├── WIP_2026-06-02_bugfixes.md    # Completed bugfix-session notes
└── venv/                         # Python virtualenv (Python 3.11)
```

### Database schema (`cards.db`)

```sql
CREATE TABLE editions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('Black','White')),
    language TEXT NOT NULL CHECK(language IN ('HUN','ENG')),
    pick INTEGER,                    -- NULL means 1
    edition_id INTEGER NOT NULL REFERENCES editions(id)
);
-- plus feedback_like, feedback_dislike, feedback_comment
```

Only `language = 'HUN'` and `pick = 1 OR pick IS NULL` cards are currently loaded into play.

---

## Running the server

Development (foreground):

```bash
cd /opt/emberisegellenes_kartyak
venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8010
```

Port **8010** is used because 8000 is taken on this host. nginx forwards public traffic to 8010.

Production (already configured on this server):

```bash
sudo systemctl restart emberisegellenes.service
```

After editing any `server/*.py`, the systemd service must be restarted — otherwise the live site keeps running stale code.

---

## Running the tests

Tests live in [tests/](tests/) and use **pytest** + **pytest-asyncio**. Configuration is in [pytest.ini](pytest.ini) (`asyncio_mode = auto`, `testpaths = tests`).

From the project root, using the project venv:

```bash
cd /opt/emberisegellenes_kartyak
venv/bin/pytest
```

Useful variants:

```bash
# Verbose, with short tracebacks
venv/bin/pytest -v --tb=short

# Just one file
venv/bin/pytest tests/test_game_czar.py

# Just one test
venv/bin/pytest tests/test_timer.py::test_timer_task_stored_on_room

# Match by name substring
venv/bin/pytest -k "czar or vote"

# Only list what would run (no execution)
venv/bin/pytest --collect-only -q

# Stop on first failure
venv/bin/pytest -x
```

If `pytest` is not on your PATH, always prefix with `venv/bin/` — the system Python does **not** have the test dependencies installed.

### Test files

| File | Covers |
|---|---|
| `test_connection_manager.py` | WebSocket connection registry |
| `test_deck.py` | Shuffle, deal, draw, reshuffle |
| `test_disconnect.py` | Player drop / reconnect flow, czar-disconnect skip |
| `test_game_czar.py` | Czar rotation and pick |
| `test_game_vote.py` | Voting / submission flow |
| `test_inactivity_kick.py` | Kick idle submit-phase players |
| `test_messages.py` | Chat / messages DB |
| `test_multi_pick.py` | Multi-pick cards (pick > 1) |
| `test_phase_guards.py` | Round phase state-machine guards |
| `test_room.py` | Room + admin promotion |
| `test_standalone.py` | One-player standalone game flow |
| `test_submission_change.py` | Submitted-card withdrawal + repeated full-hand refresh |
| `test_timer.py` | Per-round timer task lifecycle |
| `test_unique_username.py` | Case-insensitive unique usernames per room |
| `test_voting_timer.py` | Fresh revealed-phase timer for vote/czar phases |
| `test_voice.py` | Voice mute + ready signalling |

`tests/conftest.py` and `tests/helpers.py` hold shared fixtures and a fake WebSocket helper.

---

## First-time setup (fresh clone)

```bash
cd /opt/emberisegellenes_kartyak
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pytest          # sanity check
venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8010
```

`cards.db` is checked in pre-populated, so no DB seeding step is required.

---

## Notable gotchas

- All `server/*.py` files use `from __future__ import annotations` for Python 3.11 compatibility — keep that line when adding new modules.
- Rooms are **in-memory only**; restarting the server drops all active games.
- Card `type` values are capitalized: `'Black'` / `'White'`.
- One-player standalone games are allowed.
- All connected players submit, including the czar.
- Revealed phase gets a fresh timer in both voting mode and czar-pick mode. If the czar disconnects, times out, or is kicked while responsible for choosing, the round is skipped without points.
- Players may request a full new hand repeatedly during the submitting phase until they submit. Per-card swap UI is currently dormant/commented out.
- Public pages (`/`, `/robots.txt`, `/sitemap.xml`, HTML catch-all) must accept both `GET` and `HEAD` — social-media link scrapers issue `HEAD` first.
- `touchmove` handlers in the hand UI are intentionally non-passive (`e.preventDefault()`) to suppress page scrolling during card swipes.
- `fastapi.testclient.TestClient` hangs in this sandbox; endpoint tests call route functions directly where needed.
