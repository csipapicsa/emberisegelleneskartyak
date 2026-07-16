import sqlite3

from server.db import get_editions


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
