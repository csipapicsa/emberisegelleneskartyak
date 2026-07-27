import sqlite3

import pytest
from fastapi import HTTPException

from server.db import get_cards_for_edition, get_edition_name, get_editions


def test_get_editions_includes_hungarian_card_counts_only():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE editions (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            language TEXT NOT NULL,
            edition_id INTEGER NOT NULL
        );
        INSERT INTO editions (id, name) VALUES (1, 'Első'), (2, 'Üres');
        INSERT INTO cards (type, language, edition_id) VALUES
            ('Black', 'HUN', 1),
            ('White', 'HUN', 1),
            ('White', 'HUN', 1),
            ('Black', 'ENG', 1),
            ('White', 'ENG', 1);
    """)

    editions = get_editions(conn)

    assert editions == [
        {'id': 1, 'name': 'Első', 'black_count': 1, 'white_count': 2},
        {'id': 2, 'name': 'Üres', 'black_count': 0, 'white_count': 0},
    ]


def test_get_cards_for_edition_returns_hungarian_cards_in_type_and_id_order():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE editions (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            type TEXT NOT NULL,
            language TEXT NOT NULL,
            pick INTEGER,
            edition_id INTEGER NOT NULL
        );
        INSERT INTO editions (id, name) VALUES (1, 'Első');
        INSERT INTO cards (id, text, type, language, pick, edition_id) VALUES
            (4, 'Második fehér', 'White', 'HUN', NULL, 1),
            (3, 'Második fekete', 'Black', 'HUN', 2, 1),
            (1, 'Első fekete', 'Black', 'HUN', NULL, 1),
            (2, 'Első fehér', 'White', 'HUN', 1, 1),
            (5, 'Angol', 'White', 'ENG', 1, 1);
    """)

    black, white = get_cards_for_edition(conn, 1)

    assert get_edition_name(conn, 1) == 'Első'
    assert get_edition_name(conn, 99) is None
    assert black == [
        {'id': 1, 'text': 'Első fekete', 'type': 'Black', 'pick': 1},
        {'id': 3, 'text': 'Második fekete', 'type': 'Black', 'pick': 2},
    ]
    assert white == [
        {'id': 2, 'text': 'Első fehér', 'type': 'White', 'pick': 1},
        {'id': 4, 'text': 'Második fehér', 'type': 'White', 'pick': 1},
    ]


@pytest.mark.asyncio
async def test_api_edition_cards_returns_only_display_fields(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE editions (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            type TEXT NOT NULL,
            language TEXT NOT NULL,
            pick INTEGER,
            edition_id INTEGER NOT NULL
        );
        INSERT INTO editions (id, name) VALUES (1, 'Első');
        INSERT INTO cards (id, text, type, language, pick, edition_id) VALUES
            (1, 'Kérdés _', 'Black', 'HUN', 2, 1),
            (2, 'Válasz', 'White', 'HUN', NULL, 1),
            (3, 'English answer', 'White', 'ENG', 1, 1);
    """)
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)

    cards = await main.api_edition_cards(1)

    assert cards == {
        'id': 1,
        'name': 'Első',
        'community': False,
        'black_cards': [{'text': 'Kérdés _', 'pick': 2}],
        'white_cards': [{'text': 'Válasz', 'pick': 1}],
    }


@pytest.mark.asyncio
async def test_api_edition_cards_returns_404_for_unknown_edition(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE editions (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    from server import main
    monkeypatch.setattr(main, "get_conn", lambda: conn)

    with pytest.raises(HTTPException) as exc_info:
        await main.api_edition_cards(999)

    assert exc_info.value.status_code == 404
