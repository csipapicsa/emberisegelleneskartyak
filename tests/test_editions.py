"""Card source: server/db.py reads card_master.db — only the game_cards and
edition_status views, editions and card_editions. The schema below is a
trimmed copy of the real file's tables; both views are copied verbatim so
their filters (validated, not skipped, not merged; complete packs) are what
gets tested."""
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
    CREATE VIEW edition_status AS
        SELECT e.id AS edition_id, coalesce(e.hun_name, e.name) AS name, e.origin_language, e.sort_order,
               count(*) AS cards,
               sum(c.validated) AS playable,
               sum(c.validated AND c.type = 'Black') AS playable_black,
               sum(c.validated AND c.type = 'White') AS playable_white,
               count(*) - sum(c.validated) AS missing,
               count(*) = sum(c.validated) AS complete
        FROM editions e
        JOIN card_editions ce ON ce.edition_id = e.id
        JOIN cards c ON c.id = ce.card_id AND c.skip = 0 AND c.duplicate_of IS NULL
        GROUP BY e.id;
"""

# Card 3 is in three packs. Piros and Kék are complete (Piros's skipped and
# merged cards don't count against it). Main Deck is incomplete but still has
# a playable card through the overlap; Üres has none.
SEED = """
    INSERT INTO editions (id, name, hun_name, origin_language, sort_order) VALUES
        (1, 'Red Box Expansion', 'Piros', 'ENG', 2),
        (2, 'Main Deck', NULL, 'ENG', 1),
        (3, 'Empty Pack', 'Üres', 'ENG', 3),
        (4, 'Blue Box Expansion', 'Kék', 'ENG', 4);
    INSERT INTO cards (id, type, hun_reviewed, validated, skip, pick, duplicate_of) VALUES
        (1, 'Black', 'Első fekete ________-t.', 1, 0, NULL, NULL),
        (2, 'Black', 'Második ________, ________.', 1, 0, 2, NULL),
        (3, 'White', 'Közös fehér', 1, 0, NULL, NULL),
        (4, 'White', 'Csak piros', 1, 0, NULL, NULL),
        (5, 'White', 'Nincs jóváhagyva', 0, 0, NULL, NULL),
        (6, 'White', 'Kihagyva', 1, 1, NULL, NULL),
        (7, 'White', 'Összevonva', 1, 0, NULL, 3),
        (8, 'White', 'Csak kék', 1, 0, NULL, NULL);
    INSERT INTO card_editions (card_id, edition_id) VALUES
        (1, 1), (2, 1), (3, 1), (4, 1), (6, 1), (7, 1),
        (3, 2), (5, 2),
        (5, 3), (6, 3), (7, 3),
        (3, 4), (8, 4);
"""


def make_master(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(MASTER_SCHEMA + SEED)
    conn.commit()
    return conn


def test_get_editions_lists_only_complete_packs_in_sort_order():
    conn = make_master()

    editions = get_editions(conn)

    # Main Deck has a playable card (shared with Piros) but one of its own is
    # not validated, so it is hidden, like Üres.
    assert editions == [
        {'id': 1, 'name': 'Piros', 'sort_order': 2, 'black_count': 2, 'white_count': 2},
        {'id': 4, 'name': 'Kék', 'sort_order': 4, 'black_count': 0, 'white_count': 2},
    ]


def test_get_editions_shows_a_pack_once_its_last_card_is_validated():
    conn = make_master()
    assert 2 not in [e['id'] for e in get_editions(conn)]

    conn.execute("UPDATE cards SET validated = 1 WHERE id = 5")

    # Card 5 was the last missing card of Main Deck and of Üres.
    assert [e['id'] for e in get_editions(conn)] == [2, 1, 3, 4]


def test_get_cards_for_edition_returns_playable_cards_in_type_and_id_order():
    conn = make_master()

    black, white = get_cards_for_edition(conn, 1)

    assert get_edition_name(conn, 1) == 'Piros'
    assert get_edition_name(conn, 2) == 'Main Deck'  # hun_name falls back to name
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

    black, white = get_cards_for_editions(conn, [1, 4])

    assert sorted(c['id'] for c in black) == [1, 2]
    assert sorted(c['id'] for c in white) == [3, 4, 8]


def test_get_cards_for_editions_ignores_incomplete_packs():
    conn = make_master()

    assert get_cards_for_editions(conn, [2, 3]) == ([], [])
    black, white = get_cards_for_editions(conn, [2, 4])
    assert black == []
    assert sorted(c['id'] for c in white) == [3, 8]


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
async def test_api_editions_lists_complete_packs_with_label_and_order(monkeypatch):
    conn = make_master()
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)
    monkeypatch.setattr(main, "get_community_cards", lambda c: ([], [{'id': 1}]))

    editions = await main.api_editions()

    # 'Ti küldtétek' is listed in edition_display.ORDER; 'Piros' and 'Kék'
    # aren't, so they follow in sort_order. 'Main Deck' is incomplete.
    assert [e['name'] for e in editions] == ['Ti küldtétek', 'Piros', 'Kék']
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
    """Smoke-check an uploaded file: the views and joins the server uses exist,
    at least one complete pack has black cards, and every playable black card
    has a pick the client can handle."""
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


def test_edition_display_lists_first_expansion_before_second():
    from server import edition_display as ed
    assert ed.label_for("Első (1st)") == "Első kiadás"
    assert ed.sort_key("Első (1st)") < ed.sort_key("Második (2nd)")
    assert ed.sort_key("Ti küldtétek") < ed.sort_key("Első (1st)")


def test_edition_display_labels_unlisted_numbered_expansion():
    # A pack that shows up in a new card_master.db upload must look right
    # without a code change.
    from server import edition_display as ed
    assert "Hetedik (7th)" not in dict(ed.ORDER)
    assert ed.label_for("Hetedik (7th)") == "Hetedik kiadás"
    assert ed.label_for("Piros") == "Piros"
    assert ed.label_for("Valami (extra)") == "Valami (extra)"
