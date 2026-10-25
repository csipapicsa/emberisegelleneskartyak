"""Card source: server/db.py reads card_master.db — only the game_cards view,
editions and card_editions. The schema below is a trimmed copy of the real
file's tables; the game_cards view is copied verbatim so its filter
(validated, not skipped, not merged) is what gets tested."""
import os
import sqlite3

import pytest
from fastapi import HTTPException

from server import db as db_mod
from server.db import get_cards_for_edition, get_cards_for_editions, get_edition_name, get_editions

MASTER_SCHEMA = """
    CREATE TABLE editions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL UNIQUE,
        sheet           TEXT,
        hun_name        TEXT,
        origin_language TEXT NOT NULL DEFAULT 'ENG',
        sort_order      INTEGER
    );
    CREATE TABLE cards (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        type            TEXT NOT NULL CHECK(type IN ('Black','White')),
        origin_language TEXT NOT NULL DEFAULT 'ENG',
        norm_key        TEXT NOT NULL DEFAULT '',
        eng             TEXT,
        hun_reviewed    TEXT,
        validated       INTEGER NOT NULL DEFAULT 0,
        skip            INTEGER NOT NULL DEFAULT 0,
        pick            INTEGER,
        draw            INTEGER NOT NULL DEFAULT 0,
        duplicate_of    INTEGER REFERENCES cards(id)
    );
    CREATE TABLE card_editions (
        card_id    INTEGER NOT NULL REFERENCES cards(id),
        edition_id INTEGER NOT NULL REFERENCES editions(id),
        PRIMARY KEY(card_id, edition_id)
    );
    CREATE VIEW game_cards AS
        SELECT id AS card_id, type, pick, draw, hun_reviewed AS text, eng
        FROM cards
        WHERE validated = 1 AND skip = 0 AND duplicate_of IS NULL;
"""

# Two overlapping packs (card 3 is in both), a pack with only unplayable
# cards, and one card of each kind the view must drop.
SEED = """
    INSERT INTO editions (id, name, hun_name, origin_language, sort_order) VALUES
        (1, 'Red Box Expansion', 'Piros', 'ENG', 2),
        (2, 'Main Deck', NULL, 'ENG', 1),
        (3, 'Empty Pack', 'Üres', 'ENG', 3);
    INSERT INTO cards (id, type, hun_reviewed, validated, skip, pick, duplicate_of) VALUES
        (1, 'Black', 'Első fekete ________-t.', 1, 0, NULL, NULL),
        (2, 'Black', 'Második ________, ________.', 1, 0, 2, NULL),
        (3, 'White', 'Közös fehér', 1, 0, NULL, NULL),
        (4, 'White', 'Csak piros', 1, 0, NULL, NULL),
        (5, 'White', 'Nincs jóváhagyva', 0, 0, NULL, NULL),
        (6, 'White', 'Kihagyva', 1, 1, NULL, NULL),
        (7, 'White', 'Összevonva', 1, 0, NULL, 3);
    INSERT INTO card_editions (card_id, edition_id) VALUES
        (1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1),
        (3, 2),
        (5, 3), (6, 3), (7, 3);
"""


def make_master(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(MASTER_SCHEMA + SEED)
    conn.commit()
    return conn


def test_get_editions_counts_only_playable_cards_in_sort_order():
    conn = make_master()

    editions = get_editions(conn)

    # 'Üres' has no playable card so it is left out; hun_name falls back to name.
    assert editions == [
        {'id': 2, 'name': 'Main Deck', 'sort_order': 1, 'black_count': 0, 'white_count': 1},
        {'id': 1, 'name': 'Piros', 'sort_order': 2, 'black_count': 2, 'white_count': 2},
    ]


def test_get_editions_hides_packs_below_min_cards():
    conn = make_master()

    assert [e['id'] for e in get_editions(conn, min_cards=2)] == [1]


def test_get_cards_for_edition_returns_playable_cards_in_type_and_id_order():
    conn = make_master()

    black, white = get_cards_for_edition(conn, 1)

    assert get_edition_name(conn, 1) == 'Piros'
    assert get_edition_name(conn, 2) == 'Main Deck'
    assert get_edition_name(conn, 99) is None
    assert black == [
        {'id': 1, 'text': 'Első fekete ________-t.', 'type': 'Black', 'pick': 1},
        {'id': 2, 'text': 'Második ________, ________.', 'type': 'Black', 'pick': 2},
    ]
    assert white == [
        {'id': 3, 'text': 'Közös fehér', 'type': 'White', 'pick': 1},
        {'id': 4, 'text': 'Csak piros', 'type': 'White', 'pick': 1},
    ]


def test_get_cards_for_editions_puts_a_shared_card_in_the_deck_once():
    conn = make_master()

    black, white = get_cards_for_editions(conn, [1, 2])

    assert sorted(c['id'] for c in black) == [1, 2]
    assert sorted(c['id'] for c in white) == [3, 4]


def test_get_conn_is_read_only(tmp_path, monkeypatch):
    path = tmp_path / "card_master.db"
    make_master(str(path)).close()
    monkeypatch.setattr(db_mod, "DB_PATH", str(path))

    conn = db_mod.get_conn()
    try:
        assert len(get_cards_for_editions(conn, [1])[1]) == 2
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM cards")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_api_editions_applies_min_cards_label_and_order(monkeypatch):
    conn = make_master()
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)
    monkeypatch.setitem(main.CONFIG, "min_edition_cards", 2)
    monkeypatch.setattr(main, "get_community_cards", lambda c: ([], [{'id': 1}]))

    editions = await main.api_editions()

    # 'Ti küldtétek' is listed in edition_display.ORDER; 'Piros' isn't, so it
    # sorts after it. 'Main Deck' has 1 card, below min_edition_cards.
    assert [e['name'] for e in editions] == ['Ti küldtétek', 'Piros']
    assert all('sort_order' not in e for e in editions)


@pytest.mark.asyncio
async def test_api_edition_cards_returns_only_display_fields(monkeypatch):
    conn = make_master()
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)

    cards = await main.api_edition_cards(2)

    assert cards == {
        'id': 2,
        'name': 'Main Deck',
        'community': False,
        'black_cards': [],
        'white_cards': [{'text': 'Közös fehér', 'pick': 1}],
    }


@pytest.mark.asyncio
async def test_api_edition_cards_returns_404_for_unknown_edition(monkeypatch):
    conn = make_master()
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc_info:
        await main.api_edition_cards(999)

    assert exc_info.value.status_code == 404


REAL_DB = os.path.join(os.path.dirname(db_mod.__file__), "..", "database", "card_master.db")


@pytest.mark.skipif(not os.path.exists(REAL_DB), reason="card_master.db not uploaded")
def test_uploaded_card_master_has_what_the_server_needs():
    """Smoke-check an uploaded file: the view and joins the server uses exist
    and every playable black card has a pick the client can handle."""
    conn = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        editions = get_editions(conn)
        assert editions
        black, white = get_cards_for_editions(conn, [e['id'] for e in editions])
        assert black and white
        assert {c['pick'] for c in black} <= {1, 2, 3}
        assert all(c['text'] for c in black + white)
    finally:
        conn.close()
