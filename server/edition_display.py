"""Display order and Hungarian labels for card packages ("editions") shown in
the room lobby's package picker.

To add a new edition, reorder one, or change its label, just edit ORDER below
— nothing else needs to change. Keyed by the exact `name` stored in
cards.db.editions (or, for the virtual community pack,
messages_db.COMMUNITY_EDITION_NAME). An edition whose name isn't listed here
still shows up — with its raw DB name as the label — sorted after every
listed edition.
"""

ORDER: list[tuple[str, str]] = [
    # (name as stored in the DB, label shown to players)
    ("Magyar Alap",    "Magyar Alap"),
    ("Vegyes",         "Vegyes"),
    ("Ti küldtétek",   "Ti küldtétek"),
    ("2nd Expansion",  "Második kiadás"),
    ("3rd Expansion",  "Harmadik kiadás"),
    ("Negyedik (4th)", "Negyedik kiadás"),
    ("Ötödik (5th)",   "Ötödik kiadás"),
    ("Hatodik (6th)",  "Hatodik kiadás"),
    ("Bōks",            "Bōks"),
]

_POSITION = {name: i for i, (name, _label) in enumerate(ORDER)}
_LABEL = {name: label for name, label in ORDER}


def sort_key(name: str) -> tuple[int, str]:
    """Sort position for an edition name; unlisted names sort after every
    listed one, alphabetically among themselves."""
    return (_POSITION.get(name, len(ORDER)), name)


def label_for(name: str) -> str:
    """Hungarian display label for an edition name; falls back to the raw
    DB name when it isn't in ORDER."""
    return _LABEL.get(name, name)
