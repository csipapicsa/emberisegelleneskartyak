# WIP — Multi-pick (two-card) rollout (2026-06-24)

Session where the project was put under git for the first time and the
multi-pick (pick > 1 black cards) path was unlocked and corrected.

## Status

On branch `multi-pick-unlock` (pushed to `origin`). Being tested live as-is —
the site has very low traffic, so the user opted to test in production rather
than on a separate port.

Full suite: ✅

```bash
venv/bin/python -m pytest -q
# 184 passed
```

## Context — most of this was already built

The two-card feature was ~95% implemented before this session; it was just
gated and had one ordering bug:

- **Backend** — `Player.submitted_cards` is a `list[dict]`; `submit_card`
  validates `len(selected) == pick`; reveal / czar_pick / vote / `_end_round`
  / hand-refill all iterate the list. Already done.
- **Frontend** — `hand.js` has `pickCount`, an ordered `selected[]`, pick
  badges showing tap order (1, 2), and replaces the last pick when over the
  limit. `game.js` sets `pickCount` from `black_card.pick`, shows "Válassz N
  kártyát!", and renders N cards per submission in the czar / vote / viewer
  grids. Already done.
- **Data** — `cards.db` already holds 35 HUN pick-2 and 2 HUN pick-3 black
  cards (plus ENG equivalents, which are filtered out by the HUN language
  clause).
- **Tests** — `tests/test_multi_pick.py` existed (pick-2 and pick-3).

## What changed this session

### Git / infra (new)
- Repo initialized; `.gitignore` added. `cards.db` **is tracked** (game
  content); `analytics.db` and `messages.db` are ignored (runtime/user data).
- Baseline commit on `main` = full deployed state (71 files).
- Private GitHub repo `csipapicsa/emberisegelleneskartyak.hu`, pushed over SSH
  (key already registered with the `csipapicsa` account).
- Feature branch `multi-pick-unlock`.

### Code
- **`server/db.py`** — removed the `AND (pick = 1 OR pick IS NULL)` clause in
  `get_cards_for_editions` so multi-pick black cards are dealt.
- **`server/main.py`** (`submit_card` handler) — was building the submission
  from a `set()` and iterating `player.hand`, which discarded the order the
  player chose. For multi-blank black cards the order the white cards fill the
  blanks is meaningful, so this was wrong. Now:
  - reads `card_ids` as an ordered list,
  - builds `selected` in the player's chosen order via an id→card map,
  - rejects duplicate ids (`len(set(card_ids)) != len(card_ids)`).

### Tests
- **`tests/test_multi_pick.py`** — added two tests that exercise the real
  `submit_card` handler via `main_mod._handle_message`:
  - `test_pick2_preserves_submission_order` — selecting in non-hand order is
    preserved server-side.
  - `test_pick2_rejects_duplicate_card` — same id twice is rejected.

## No frontend change
`game.js` / `cards.css` untouched, so no `?v=` cache-bust needed.

## How to go live / roll back
- Live: stay on `multi-pick-unlock`, then
  `sudo systemctl restart emberisegellenes.service` (service `WorkingDirectory`
  is the repo root, so the working tree is what runs).
- Roll back: `git checkout main` + restart. Runtime DBs are gitignored, so
  branch switches / resets never touch them. (Only `git clean -fdx` would —
  don't run that.)

## Manual QA checklist (next)
- [ ] A pick-2 black card actually appears in a real game.
- [ ] Two-blank card renders well on mobile; selection order badges (1/2) feel right.
- [ ] Reveal / czar-pick / vote grids show both cards per submission.
- [ ] Winner overlay shows both submitted cards.
- [ ] After the round, hands refill back to `hand_size`.

## Next steps
- If QA is good, merge `multi-pick-unlock` → `main`.
- pick-3 cards exist (2 HUN) but are barely worth special UI attention; verify
  they at least work end-to-end.
