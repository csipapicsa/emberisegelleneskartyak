"""Display order and Hungarian labels for card packages ("editions") shown in
the room lobby's package picker.

To add a new edition, reorder one, or change its label, just edit ORDER below
— nothing else needs to change. Keyed by the edition's display name in
card_master.db (``COALESCE(hun_name, name)``, or, for the virtual community
pack, messages_db.COMMUNITY_EDITION_NAME). An edition whose name isn't listed
here still shows up — sorted after every listed edition, in card_master.db's
``sort_order`` — so a new card_master.db upload never needs a code change.
Its label is the DB name, except that a numbered expansion ("Hetedik (7th)")
becomes "Hetedik kiadás" like the listed ones. Which editions show up at all is
decided by card_master.db: only packs with ``edition_status.complete = 1``.
"""
from __future__ import annotations

import re

ORDER: list[tuple[str, str]] = [
    # (name as stored in the DB, label shown to players)
    ("Magyar Alap",    "Magyar Alap"),
    ("Vegyes",         "Vegyes"),
    ("Ti küldtétek",   "Ti küldtétek"),
    ("Első (1st)",     "Első kiadás"),
    ("Második (2nd)",  "Második kiadás"),
    ("Harmadik (3rd)", "Harmadik kiadás"),
    ("Negyedik (4th)", "Negyedik kiadás"),
    ("Ötödik (5th)",   "Ötödik kiadás"),
    ("Hatodik (6th)",  "Hatodik kiadás"),
    ("Bōks",            "Bōks"),
]

_POSITION = {name: i for i, (name, _label) in enumerate(ORDER)}
_LABEL = {name: label for name, label in ORDER}
_NUMBERED_EXPANSION = re.compile(r"^(\S+) \(\d+(?:st|nd|rd|th)\)$")


def sort_key(name: str, sort_order: int | None = None) -> tuple[int, int, str]:
    """Sort position for an edition name; unlisted names sort after every
    listed one, by the database's sort_order among themselves (then name)."""
    return (_POSITION.get(name, len(ORDER)), sort_order if sort_order is not None else 0, name)


def label_for(name: str) -> str:
    """Hungarian display label for an edition name; falls back to the raw
    DB name ("Hetedik (7th)" → "Hetedik kiadás") when it isn't in ORDER."""
    if name in _LABEL:
        return _LABEL[name]
    numbered = _NUMBERED_EXPANSION.match(name)
    return f"{numbered.group(1)} kiadás" if numbered else name
