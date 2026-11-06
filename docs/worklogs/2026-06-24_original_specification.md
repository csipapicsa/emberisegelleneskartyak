> **Historical document:** the original specification as of June 2026, kept for the project history. The current design is in
> [docs/architecture.md](../architecture.md).

# Emberiség Ellenes Kártyák — Project Specification

Mobile-first, online multiplayer card game based on Cards Against Humanity rules, self-hosted at `emberisegelleneskartyak.hu` (server IP: `109.199.104.168`).

**Production:** nginx reverse proxy (port 80/443) → uvicorn on `127.0.0.1:8010`

**Systemd service:** `sudo systemctl start|stop|restart|status emberisegellenes`
- Service file: `/etc/systemd/system/emberisegellenes.service`
- Nginx config: `/etc/nginx/conf.d/emberisegellenes.conf`
- Autostart on reboot: enabled

**Dev run command:** `cd /opt/emberisegellenes_kartyak && venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8010`

**Activate venv:** `source /opt/emberisegellenes_kartyak/venv/bin/activate`

**Run tests:** `cd /opt/emberisegellenes_kartyak && venv/bin/python -m pytest -v`
(First time: `venv/bin/pip install pytest pytest-asyncio`)

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Async, lightweight, native WebSocket support |
| Realtime | FastAPI WebSockets (native) | No extra dependency, low resource use |
| Database | SQLite (`cards.db`) | Pre-populated card data + persistent feedback/messages |
| Frontend | Vanilla JS + CSS | No framework overhead, full animation control |
| Config | `config.json` | Non-sensitive tunables (timers, limits) |

---

## Pages / Routes

### `/` — Landing page
- Links to: Join game, Create room, Rules PDF, Support (Donably / Revolut), Pártvaltó
- "Egy nap alatt dobtam össze. Bug egyenlőre van." message under buttons
- "✉ Küldj üzenetet" link opens a contact modal (name, email, message → stored in DB)
- Credit: Készítette Győri Gergő

### `/join` — Join flow
1. Enter username (session token in `localStorage`, generated server-side)
2. Enter 4-digit room code (or pick from open room list, auto-refreshed every 5s)
3. Wait in lobby for admin approval

### `/create` — Create room flow
1. Enter username
2. Server generates 4-digit room code
3. Admin is redirected to lobby

### `/room/<code>` — Lobby (pre-game)
- Admin sees join requests with username, approve/deny buttons
- Admin configures:
  - Card editions (multi-select)
  - Number of rounds: 10 / 20 / 30 / 40 / 50
  - Think timer: ∞ / 15 / 30 / 45 / 60 seconds
  - Voting mode: **Cár választ** (czar picks) or **Mindenki szavaz** (everyone votes)
- Admin presses **Start Game** when ready (requires ≥ 1 player; standalone mode is supported)
- No mid-game joins

### `/game/<code>` — Game screen
- Main gameplay view

---

## Game Flow

### Setup
- Each player gets 10 white cards from the shared deck
- Czar order is round-robin by join order

### Each Round
1. Black card drawn and shown to all players
2. **All** players (including the czar) submit white card(s)
3. Timer counts down; on expiry, connected unsubmitted players are kicked for inactivity (standalone players are exempt)
4. If nobody submitted → round is skipped (`round_skipped` event), next czar
5. Submitted cards **shuffled**, revealed **anonymously** (no names)
6. Revealed phase starts a fresh timer window.
7. **Czar mode:** czar taps their favourite → that player earns 1 point. If the czar disconnects, is kicked, or times out while choosing, the round is skipped without points.
8. **Vote mode:** everyone taps their favourite; most votes wins 1 point; ties split points as floats (e.g. 0.5 each); timer expiry auto-tallies votes
9. Winner announced in a 3-second overlay (name + winning card); then player names appear on all cards
10. Players draw back up to 10 cards; next czar

### Card Refresh
- During the submitting phase, before submitting, a player can request a full new hand.
- Full-hand refresh can be used repeatedly within the same round.
- Once the player has submitted, refresh is blocked unless they withdraw their submission before reveal.
- The previous per-card swap UI is currently commented out/dormant for possible future use.

### Disconnection / Reconnection
- `session_id` UUID in `localStorage` = identity; reconnect restores full state
- Disconnected player's submission slot is skipped.
- Disconnected players are not kicked by submit-phase inactivity sweeps because they may reconnect.
- If the czar disconnects during czar-pick phase, the round is skipped without points and the next connected czar takes over.
- Czar rotation skips disconnected players when starting new rounds.

### End of Game
- After all rounds: final scoreboard shown, option to return to landing page

---

## UI Layout (Mobile-first)

```
+-------------------------------+
|  [Black Card]    [Timer]      |
|  (card text with blanks)      |
+-------------------------------+
|  [ Your hand — swipeable ]    |
|  Drag left/right to browse    |
|  Lift = card highlighted      |
|  Tap to confirm selection     |
+-------------------------------+
```

### Hand Interaction
- Cards overlap in a fan; drag left/right to browse
- Lift finger: last card stays highlighted for `card_hold_duration_ms` (config, default 2500ms)
- Tap while highlighted: confirmation popup — Yes / No
- Non-passive `touchmove` with `preventDefault()` — required for mobile scroll prevention

### Sidebar / Scores
- Fixed "Pontok" tab anchored to the right edge of the screen (rectangular, slides in panel)
- Panel lists all players with scores and czar marker

### Card Feedback (post-round)
- Button appears below "Pontok" tab after each round
- Options: Like / Dislike / Comment (free text) for the black + winning white combo
- Stored in `feedback_*` tables in `cards.db`

---

## Database — `cards.db`

**Location:** `/opt/emberisegellenes_kartyak/cards.db`

**To query messages received from users:**
```bash
sqlite3 /opt/emberisegellenes_kartyak/cards.db "SELECT created_at, name, email, message FROM messages ORDER BY created_at DESC;"
```

### Schema

```sql
-- Card content (pre-populated)
CREATE TABLE editions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE cards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL,
    type       TEXT    NOT NULL CHECK(type IN ('Black','White')),
    language   TEXT    NOT NULL CHECK(language IN ('HUN','ENG')),
    pick       INTEGER,
    edition_id INTEGER NOT NULL REFERENCES editions(id)
);

-- Card feedback
CREATE TABLE feedback_like (
    card_id    INTEGER NOT NULL REFERENCES cards(id),
    session_id TEXT NOT NULL,
    PRIMARY KEY (card_id, session_id)
);

CREATE TABLE feedback_dislike (
    card_id    INTEGER NOT NULL REFERENCES cards(id),
    session_id TEXT NOT NULL,
    PRIMARY KEY (card_id, session_id)
);

CREATE TABLE feedback_comment (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL REFERENCES cards(id),
    session_id TEXT NOT NULL,
    username   TEXT NOT NULL,
    comment    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Contact messages from landing page (created automatically on server startup)
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL    -- UTC ISO-8601
);
```

---

## WebSocket Events

### Client → Server
| Event | Payload | Description |
|---|---|---|
| `approve_player` | `{session_id}` | Admin approves a join request |
| `deny_player` | `{session_id}` | Admin denies a join request |
| `configure_room` | `{edition_ids?, round_total?, timer_seconds?, voting_mode?}` | Admin updates room config |
| `start_game` | `{}` | Admin starts the game |
| `submit_card` | `{card_ids: [int]}` | Player submits their card(s) |
| `withdraw_submission` | `{}` | Player takes back their submitted card(s), only before reveal |
| `czar_pick` | `{winner_session_id}` | Czar picks winner (czar mode only) |
| `cast_vote` | `{winner_session_id}` | Player votes for winner (vote mode only) |
| `request_new_hand` | `{}` | Player requests a full new hand before submitting |
| `swap_card` | `{card_id: int}` | Dormant legacy per-card swap endpoint; current UI does not call it |
| `feedback` | `{card_id, type, comment?}` | Like / dislike / comment |

### Server → Client
| Event | Payload | Description |
|---|---|---|
| `room_state` | full room snapshot | On join and after any state change |
| `pending_approval` | `{}` | Sent to new player waiting for approval |
| `game_started` | `{}` | Redirect all clients to game page |
| `round_start` | `{black_card, czar, czar_username, round_num, round_total, timer_seconds, voting_mode, scores}` | New round |
| `hand_update` | `{hand: [cards]}` | Player's current hand |
| `timer_tick` | `{seconds_remaining}` | Timer countdown |
| `player_submitted` | `{session_id, username}` | A player submitted (face-down indicator) |
| `submission_withdrawn` | `{session_id, username}` | Player withdrew their submission before reveal |
| `cards_revealed` | `{submissions: [{session_id, cards}], timer_seconds}` | Shuffled, anonymous reveal + fresh revealed-phase timer length |
| `submission_accepted` | `{}` | Confirms player's own submission |
| `vote_cast` | `{votes_in, total}` | Vote tally update (vote mode) |
| `round_end` | `{winner_session_id, winners, scores, authors}` | Round finished |
| `round_skipped` | `{}` | No submissions — round skipped |
| `game_end` | `{final_scores}` | Game over |
| `player_disconnected` | `{session_id}` | Player left |
| `player_kicked` | `{session_id, username, reason}` | Connected player kicked for inactivity |
| `kicked` | `{reason}` | Sent directly to kicked player |
| `game_aborted` | `{reason}` | Game aborted because no active players remain |
| `room_closed` | `{reason}` | Room closed by admin opening another room |
| `admin_promoted` | `{session_id}` | New admin assigned |
| `error` | `{message}` | Server-side error |

---

## HTTP API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/session` | Create or retrieve session token |
| `POST` | `/api/room` | Create a new room, returns 4-digit code |
| `GET` | `/api/room/<code>` | Get room metadata |
| `GET` | `/api/rooms` | List open (not started) rooms |
| `GET` | `/api/editions` | List available card editions |
| `GET` | `/api/config` | Frontend config |
| `POST` | `/api/contact` | Submit contact message (stored in `messages` table) |
| `WS` | `/ws/<room_code>` | WebSocket connection for a room |

---

## Config (`config.json`)

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

## File / Folder Structure

```
/opt/emberisegellenes_kartyak/
├── config.json
├── cards.db                     ← SQLite: cards, editions, feedback, messages
├── requirements.txt             ← fastapi, uvicorn, pytest, pytest-asyncio
├── pytest.ini                   ← asyncio_mode = auto, testpaths = tests
├── venv/                        ← Python 3.11 virtualenv
│   └── bin/activate             ← source this to activate
├── server/
│   ├── main.py                  # FastAPI app, room-scoped ConnectionManager, HTTP + WS handler
│   ├── game.py                  # Round logic, phase state machine, timer cancel/reference, scoring
│   ├── room.py                  # In-memory Room + Player dataclasses (phase, timer_task fields)
│   ├── voice.py                 # TURN credential generator (HMAC), signaling validator, ICE rate limiter
│   ├── deck.py                  # Deck build, shuffle, deal
│   ├── session.py               # Session UUID generation/validation
│   ├── feedback.py              # DB writes for like/dislike/comment
│   └── db.py                    # SQLite helpers, ensure_messages_table, save_message
├── tests/
│   ├── conftest.py              # autouse instant_sleep patch, real_sleep marker
│   ├── helpers.py               # FakeManager, make_room(), make_white/black_cards()
│   ├── test_deck.py             # Deck operations unit tests
│   ├── test_room.py             # Room/Player method unit tests
│   ├── test_game_czar.py        # Full czar-mode game flow tests
│   ├── test_game_vote.py        # Democratic voting mode tests
│   ├── test_phase_guards.py     # Phase state machine correctness tests
│   ├── test_disconnect.py       # Disconnect handling + auto-reveal + admin promotion + czar disconnect skip
│   ├── test_inactivity_kick.py  # Submit-phase inactivity kicking
│   ├── test_timer.py            # Timer lifecycle: cancel, stale timer, expiry trigger
│   ├── test_voting_timer.py     # Fresh revealed-phase timer for vote/czar phases
│   ├── test_standalone.py       # 1-player game flow
│   ├── test_submission_change.py # Submission withdrawal + repeated full-hand refresh
│   ├── test_unique_username.py  # Case-insensitive username uniqueness
│   ├── test_messages.py         # Chat/messages DB and endpoint behavior
│   ├── test_voice.py            # Voice mute/ready signaling
│   └── test_connection_manager.py  # Room-scoped socket isolation, evict, error resilience
├── static/
│   ├── index.html               # Landing page (contact modal, partvalto.hu, support links)
│   ├── join.html                # Join flow + open room list
│   ├── create.html              # Create room flow
│   ├── room.html                # Lobby (admin config, player approval, mic button)
│   ├── game.html                # Game screen (winner overlay, Pontok tab, feedback btn, mic button, Wake Lock)
│   ├── css/
│   │   ├── base.css             # Shared styles (dark theme, cards, seg-control, modal, mic-btn, voice-indicator)
│   │   ├── cards.css            # Hand fan, swipe animation styles
│   │   └── game.css             # Game layout, Pontok tab, winner overlay, end screen
│   └── js/
│       ├── ws.js                # WebSocket wrapper (auto-reconnect, fatal close 4001/4002/4004/4005, reconnected event)
│       ├── voice.js             # VoiceManager: WebRTC P2P mesh, mute/unmute, voice_ready handshake, _recover()
│       ├── game.js              # Game screen logic + event handlers + winner overlay + pagehide close
│       ├── hand.js              # Hand swipe (non-passive touchmove, RAF batching)
│       ├── lobby.js             # Lobby (seg-control delegation, admin config, approval, pagehide close)
│       └── session.js           # localStorage session token helpers
└── PROJECT_STRUCTURE.md
```

---

## Implemented
- ✅ Minden játékos (beleértve a cárt) beküldi a kártyáját
- ✅ Standalone mód: 1 játékos is tud szobát indítani és végigjátszani
- ✅ Kétféle szavazási mód: Cár választ / Mindenki szavaz (döntetlen esetén arányos pontszám)
- ✅ Felfedett fázis friss timerrel indul: szavazásnál automatikus tally, cár-választásnál timeout esetén pont nélküli körskip
- ✅ Lapok megkeverése reveal előtt; nevek csak a győztes meghatározása után láthatók
- ✅ Győztes overlay: 3 másodpercig megjelenik a nyertes neve + kártyája
- ✅ Üres kör kihagyása ha senki sem küldött lapot
- ✅ Kéz helyes visszaállítása beküldés után (ghost-kártya bug javítva)
- ✅ Beküldött lap visszavonható felfedés előtt; utána már nem módosítható
- ✅ Teljes kézfrissítés a beküldési fázisban, akár többször egy körben, beküldésig
- ✅ Nyitott szobák listája a csatlakozás oldalon (5mp-ként frissül)
- ✅ "Pontok" tab a játék képernyőn (jobb szél, téglalap)
- ✅ Per-card swap UI jelenleg kikommentelve/dormant, későbbi funkcióként visszakapcsolható
- ✅ Visszajelzés (like/dislike/comment) minden kör után
- ✅ Újracsatlakozás session_id alapján
- ✅ Admin jogok átadása kilépés esetén
- ✅ Cár disconnect/kick/timer timeout esetén a kör pont nélkül átugrik, a játék nem akad meg
- ✅ Következő cár választásánál disconnected játékosok átugrása
- ✅ "Szoba nem létezik" képernyő room/game oldalon; game oldal HTTP room-checket végez WS előtt
- ✅ Ha admin új szobát nyit, a korábbi szobája bezáródik és a bent lévők értesítést kapnak
- ✅ Egy session csak egy sockettel lehet jelen egy szobában; az újabb kapcsolat zárja a régit
- ✅ Egy szobában azonos név nem lehet; az újabb belépőnek nevet kell váltania
- ✅ Kapcsolatfelvételi űrlap a főoldalon (→ `messages` tábla)
- ✅ Pártvaltó link + támogatási linkek + "Egy nap alatt dobtam össze" üzenet + készítő
- ✅ WebRTC P2P hang (lobby + játék): mute/unmute gomb, mások mute-jelzője a játékoslistában
- ✅ TURN szerver integráció: coturn HMAC temporal credential (`/api/voice/credentials`), ICE rate limit (30/perc/session), signaling payload validáció
- ✅ Timer race condition javítva: `reveal_cards` / `tally_votes` / `_end_round` dupla hívás ellen guard; `_timer_task` `round_num` paramétert kap (nem futásidőben olvassa) → két párhuzamos task nem futhat ugyanarra a körre
- ✅ `all_submitted()` / `all_voted()` fix: üres (0 connected) szobán nem indul auto-skip loop
- ✅ Szoba cleanup szigorítva: lobby 10 perc, mindenki kilépett 5 perc, játék vége 5 perc, hard limit 20 perc; ellenőrzés 2 percenként
- ✅ Voice recovery: WS reconnect és telefon képernyőzár után automatikus hang-újraindítás (`_recover()`); `voice_ready` sorba kerül ha `init()` még nem futott le
- ✅ **Architectural refactor (anti-bleed):** `ConnectionManager` room-scoped (`room_code → session_id → ws`); régi szoba timer/event soha nem éri el az új szoba socketjét; `evict_from_all_rooms()` connect-kor
- ✅ **Phase state machine:** `room.phase` explicit állapotgép (`lobby → submitting → revealed → round_ending → finished`); minden event phase-checked — `submit_card` csak `submitting`-ben, `czar_pick`/`cast_vote` csak `revealed`-ben fogadott
- ✅ **Timer cancel + reference:** `room.timer_task` tárolja az asyncio taskot; `_cancel_timer()` minden `start_round` előtt lefut; stale timer `round_num` ÉS `phase` alapján is kilép
- ✅ **pagehide cleanup:** lobby.js és game.js `window.addEventListener('pagehide', ...)` → `socket.close()` navigáció előtt
- ✅ **Automated test suite:** 142 pytest teszt; lefedi: deck, room methods, czar/vote game flow, phase guards, disconnect/czar-skip, inactivity kick, timers, standalone, messages, voice, username uniqueness, connection manager isolation

## Backend Security Notes

Megvan:

- `/api/contact`: hossz limit, email regex, kötelező mezők.
- `configure_room`: `round_total`, `timer_seconds`, `voting_mode` whitelist alapján validált.
- `submit_card`: pick count ellenőrzés, dupla beküldés blokkolva.
- `czar_pick` / `cast_vote`: csak érvényes szobabeli `session_id` célpontot fogad.
- Admin ellenőrzés minden admin-only eventnél.
- Username collision case-insensitive ellenőrzéssel blokkolva.
- Chat és feedback comment HTML-escape-elve, hosszlimittel mentve.
- Voice signaling payload validáció és ICE rate limit van.

Nyitott/figyelendő:

- `username` hosszlimit a WS join és `/api/room` create útvonalon még érdemes lenne explicit egységesíteni.
- `edition_ids` létező edition ID-kre validálása még nincs teljesen szigorítva.
- Általános rate limiting nincs a contact endpointon és a gameplay WS eseményeken.



## Voice architecture notes
- **Signaling**: meglévő `/ws/<code>` WebSocket-en megy — nincs külön szerver
- **TURN**: coturn, config: `/etc/coturn/turnserver.conf`, service: `coturn`
- **TURN_SECRET**: `Environment=` a `/etc/systemd/system/emberisegellenes.service`-ben
- **Peer kapcsolat felépítés**: lexikografikusan kisebb `session_id` küld offert; `voice_ready` broadcast kezeli a page-load race conditiont
- **Tesztelés**: ugyanabban a böngészőben minden tab ugyanazt a `session_id`-t használja → hangot különböző böngészőkkel kell tesztelni (pl. Chrome + Firefox, vagy normál + incognito)
- **Jövőbeli fejlesztések**: push-to-talk, hangszint vizualizáció — a `VoiceManager` class-ba kell beleépíteni

## Out of Scope (for now)
- User accounts / login
- Spectator mode
- Custom card creation in-browser
- Make a sound: If a user waiting for a long time to be let in a room, and finally the admin let him in play a sound on the browser (beengedtek a játékba tesó.)
