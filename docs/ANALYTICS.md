# Analytics — Implementation Summary

Private, lightweight event tracking to understand real game usage. No external services and no raw IP storage.

---

## Dashboard

**URL:** `https://emberisegelleneskartyak.hu/admin/stats?key=***REMOVED***`

Shows:
- Total counts per event type + game completion rate
- Unique visitors today / last 7 days / last 30 days (by hashed IP)
- Peak hours bar chart (last 30 days, local time)
- Per-day breakdown for the last 14 days
- Live log of the last 60 events

---

## Storage

New database: `analytics.db` (SQLite, same directory as `cards.db`)

```sql
CREATE TABLE events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT    NOT NULL,
    ts           TEXT    NOT NULL DEFAULT (datetime('now')),
    room_code    TEXT,
    username     TEXT,
    ip_hash      TEXT,       -- HMAC(IP, internal-statistics purpose)
    player_count INTEGER,
    user_agent   TEXT,       -- maximum 200 karakter
    extra        TEXT        -- JSON blob for event-specific data
);
```

Indexes on `ts` and `event_type`.

> **Timezone note:** The DB schema shows `DEFAULT (datetime('now'))` (UTC), but `log_event()` always passes the timestamp explicitly via Python's `datetime.now()`, which uses the server's local timezone (CEST, UTC+2). The SQL default is never used in practice. All `get_stats()` queries use `date('now','localtime')` for correct day/week/month windowing. Events written before 2026-06-18 are stored in UTC and will appear 2 hours early in the peak-hours chart.

---

## Tracked Events

| event_type | Trigger | Captured fields |
|---|---|---|
| `room_created` | `POST /api/room` | room_code, username, purpose-bound IP HMAC, user_agent |
| `player_joined` | Admin approves a player via WebSocket | room_code, username, player_count |
| `game_started` | Admin sends `start_game` event | room_code, player_count |
| `game_ended` | Game finishes (all rounds done) | room_code, player_count, extra: `{"rounds": N}` |
| `room_abandoned` | Cleanup task removes an unstarted room | room_code, username, player_count |

**What is NOT tracked:** page views, bot traffic, reconnections, chat messages, card feedback.

---

## Bot Filtering

`log_event()` checks the `user_agent` string against a list of known patterns before writing. Filtered patterns include: `bot`, `crawler`, `spider`, `curl/`, `wget/`, `scrapy`, `selenium`, `headlesschrome`, `semrushbot`, `ahrefsbot`, and others.

Events logged without a user_agent (e.g. `game_ended` from game logic) are never filtered.

---

## Privacy

- IP addresses are **never stored raw**. A server-secret HMAC is stored only
  for the internal-statistics purpose; it cannot be reused for voting.
- A játékos által megadott név, szobakód és legfeljebb 200 karakteres
  user-agent az admin statisztikai nézetéhez tárolódik.
- Events are retained for 90 days; older events are deleted automatically.
- The site has no tracking pixels or third-party analytics. Browser
  `localStorage` is used for game functionality and preferences; see the
  public privacy notice when it is published.

---

## Files Changed

| File | Change |
|---|---|
| `server/analytics.py` | New file — DB init, `log_event()`, `get_stats()` |
| `server/main.py` | Added 4 `log_event()` calls + `/admin/stats` endpoint |
| `server/game.py` | Added `log_event("game_ended")` call in `start_round()` |

---

## Adding New Events

Call `log_event()` anywhere in the backend:

```python
from server.analytics import log_event

log_event(
    "my_event",
    room_code="1234",
    username="Feri",
    ip=request.client.host,
    user_agent=request.headers.get("user-agent"),
    player_count=4,
    extra={"some": "data"},
)
```

All parameters except `event_type` are optional.
