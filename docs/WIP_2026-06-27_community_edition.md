# WIP — "Ti küldtétek" community edition + per-IP card voting (2026-06-27)

Session that turned the previously-unused user-submitted card ideas
(`messages.db.card_ideas`) into a playable edition and added a public voting page.

## Status

On branch `community-edition` (created off `main` after fast-forwarding
`multi-pick-unlock` → `main` and pushing). **Not merged to `main`, not deployed.**
Changes are committed/working on the branch; the systemd service runs whatever is
on disk, so a `sudo systemctl restart emberisegellenes.service` is enough to test
the branch live (no merge needed).

Full suite: ✅

```bash
venv/bin/python -m pytest -q
# 208 passed   (was 185; +23 in tests/test_community_edition.py)
```

Verified manually on port 8011 (prod is 8010, untouched): `/api/editions` includes
the `-1` community edition; voting toggle / switch / multi-IP / 404 / 400 all
correct; community cards merge into a real deck carrying the `community` flag; real
`messages.db` left clean.

## What was built

### Architecture — virtual edition (nothing copied into cards.db)
Community cards stay in `messages.db.card_ideas`. The edition is synthetic:
- `COMMUNITY_EDITION_ID = -1`, `COMMUNITY_ID_OFFSET = 10_000_000`
  (defined in `server/messages_db.py`).
- `get_community_cards(conn)` converts `card_ideas` rows → game card dicts:
  lowercase `white`/`black` → `White`/`Black`, `pick=1`, `edition_id=-1`,
  `community=True`, `id = idea.id + OFFSET` (offset prevents id collision with
  cards.db ids). The `community` flag flows untouched to the client.

### Backend (`server/main.py`, `server/messages_db.py`)
- `/api/editions` appends `{"id":-1,"name":"Ti küldtétek","community":true}`.
- `start_game` handler splits real edition ids vs `-1`, fetches
  `get_cards_for_editions(real_ids)` + `get_community_cards()`, concatenates.
  **Standalone guard:** selecting only the community edition is rejected with an
  `error` event (Hungarian message). Lobby blocks it client-side too.
- Voting (one vote per IP per card):
  - New table `card_idea_votes (idea_id, voter_ip, vote, created_at,
    PK(idea_id, voter_ip))` added in `ensure_tables`.
  - `vote_card_idea()` toggles (same vote → un-vote; opposite → switch) and
    refreshes the denormalized `card_ideas.like/dislike` columns from the votes
    table after every change.
  - `_client_ip(request)` → first `X-Forwarded-For` hop (nginx forwards it,
    conf line 13), else `request.client.host`, else `"unknown"`.
  - Endpoints: `GET /api/card-ideas` (list + counts + caller's `my_vote`),
    `POST /api/card-ideas/{id}/vote` `{vote: like|dislike}`. The pre-existing
    `POST /api/card-ideas` (submit) is unchanged.

### Frontend
- Lobby (`static/js/lobby.js`, `static/room.html`): community edition tagged with
  a "(csak másik csomaggal)" hint; `onlyCommunitySelected()` blocks the start
  button client-side (server is still the source of truth).
- Badge (`static/css/cards.css` `.community-badge`): community cards show a small
  bottom-right "Ti küldtétek" label on the black card (`game.js renderBlackCard`)
  and hand white cards (`hand.js _rebuild`). Submission piles intentionally not
  badged (stacked, would clutter).
- Voting page `static/kartyaszavazas.html` (route added to `serve_html` map):
  lists ideas with white/black filter, 👍/👎 buttons with live counts and
  your-vote highlighting. Linked from `kartyaotlet.html`.
- Cache-bust bumped to `?v=20260627-community` on base.css / cards.css / game.js /
  hand.js / lobby.js.

## Decisions (confirmed with Gergo)
- **All submissions are immediately playable** — no moderation/approval gate.
- **Only community cards get a badge**; normal edition cards unchanged.
- Merged `multi-pick-unlock` → `main` first, then branched.

## Remaining / next steps
- Manual in-browser playthrough of a game with the community edition + one normal
  edition to eyeball the badge.
- Decide whether to merge to `main` and deploy. After deploy:
  `sudo systemctl restart emberisegellenes.service`.
- Possible future: moderation/hide toggle, sort voting page by score, surface the
  voting link on the landing page.
