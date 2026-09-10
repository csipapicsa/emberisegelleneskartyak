# WIP — Card-vote page status filter + pending-status bug fix (2026-09-10)

## Status

Implemented, tested, deployed (service restarted by user on branch
`card-position-fix`).

## Requirement

On `/kartyaszavazas` (public card-idea voting page), add a filter for
moderation status alongside the existing type filter (all/white/black):
all / approved only / rejected only / pending only.

## Implementation

- `static/kartyaszavazas.html`: added a second `.filter-row` with
  `data-status="all|approved|rejected|pending"` buttons, wired the same way
  as the existing type filter. `render()` now combines both filters.

## Bug found during review

The user spot-checked the "Elbírálás alatt" (pending) filter and pasted a
sample: several of the listed cards were actually shown as "✅ Jóváhagyva".

Root cause: the pending filter was implemented client-side as
`!i.seen_by_admin`. Cards get approved via a direct DB update
(`UPDATE card_ideas SET approved = 1 ...`) run by hand — there is no admin
endpoint in `server/main.py` for this — and that update does not also set
`seen_by_admin`. So an approved-but-never-"seen" card had `approved = 1,
seen_by_admin = 0`, and the pending check let it through.

## Fix — single source of truth in the backend

Rather than re-fixing the same precedence bug in two places (the page's
`statusText()` already had it right: approved wins over seen_by_admin), moved
status derivation into `server/messages_db.py`:

```python
def idea_status(idea: dict) -> str:
    if idea["approved"]:
        return "approved"
    if idea["seen_by_admin"]:
        return "rejected"
    return "pending"
```

`list_card_ideas()` now attaches `status` to every row. The frontend
(`statusText()` and the new filter's `matchesStatus()`) just reads
`i.status` instead of re-deriving it from the two raw flags.

## Tests

Added to `tests/test_community_edition.py`:
- `test_list_card_ideas_status_pending_by_default`
- `test_list_card_ideas_status_rejected_when_seen_but_not_approved`
- `test_list_card_ideas_status_approved_wins_even_if_unseen` — regression
  test reproducing the exact bug (approved, `seen_by_admin` never set)
- `test_list_card_ideas_status_approved_wins_when_also_seen`

Full suite: `venv/bin/python -m pytest -q` → 257 passed.

## Follow-up

Per user instruction, going forward every backend/logic change should ship
with a pytest addition to `tests/` (see auto-memory
`feedback_python_tests.md`).
