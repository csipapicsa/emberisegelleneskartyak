# Bugfix: Timer freeze when nobody submits a card

**Date:** 2026-06-17  
**File changed:** `server/game.py`  
**Tests updated:** `tests/test_timer.py`, `tests/test_inactivity_kick.py`

---

## Symptom

When the round timer reached 0 and no player had submitted a card, the game froze. The timer displayed a blinking red "0" and the game did not advance. Players had no way to move forward.

---

## Root cause

`reveal_cards()` always called `_kick_inactive_players()` first, before checking whether anyone had submitted. When nobody submitted, every connected player failed the inactivity check and was kicked. This caused `game_aborted` to be broadcast.

The client handled `game_aborted` with only a disappearing toast — no navigation, no state reset. Players were left on a broken game screen with a frozen timer.

---

## Fix

Added an early check at the top of `reveal_cards()`:

```python
any_submitted = any(p.submitted_cards for p in room.players.values())
if not any_submitted:
    await skip_revealed_round(room, manager)
    return
```

If nobody submitted, the round is skipped immediately without kicking anyone. `skip_revealed_round` broadcasts `round_skipped`, refills hands, and calls `start_round` — the game continues normally to the next round with no points awarded.

The inactivity kick still fires when *some* players submitted and others did not (genuine idle behaviour in a partial-submission round).

---

## Behaviour before vs. after

| Scenario | Before | After |
|---|---|---|
| Timer expires, nobody submitted | All players kicked → `game_aborted` → UI freezes | Round skipped → `round_start` → game continues |
| Timer expires, some submitted | Non-submitters kicked, cards revealed | Unchanged |
| Timer expires, all submitted | Already handled (timer fires after reveal) | Unchanged |

---

## Tests

- `test_timer_triggers_reveal_on_expiry_no_submissions` — updated: now asserts `round_skipped` + `round_start`, no `player_kicked`, no `game_aborted`
- `test_no_submissions_skips_round_without_kicking` — renamed and updated from `test_game_aborted_if_all_kicked`: asserts game continues with all players still in room
