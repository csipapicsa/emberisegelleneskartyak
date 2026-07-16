from __future__ import annotations
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cards.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_editions(conn: sqlite3.Connection) -> list[dict]:
    # Count only the Hungarian cards because those are the cards that
    # get_cards_for_editions() can add to a game.
    rows = conn.execute("""
        SELECT
            editions.id,
            editions.name,
            COUNT(CASE WHEN cards.language = 'HUN' AND cards.type = 'Black' THEN 1 END) AS black_count,
            COUNT(CASE WHEN cards.language = 'HUN' AND cards.type = 'White' THEN 1 END) AS white_count
        FROM editions
        LEFT JOIN cards ON cards.edition_id = editions.id
        GROUP BY editions.id, editions.name
        ORDER BY editions.name
    """).fetchall()
    return [dict(r) for r in rows]


def get_cards_for_editions(conn: sqlite3.Connection, edition_ids: list[int]) -> tuple[list[dict], list[dict]]:
    placeholders = ",".join("?" * len(edition_ids))
    rows = conn.execute(
        f"SELECT id, text, type, COALESCE(pick, 1) as pick, edition_id FROM cards WHERE edition_id IN ({placeholders}) AND language = 'HUN'",
        edition_ids,
    ).fetchall()
    black = [dict(r) for r in rows if r["type"] == "Black"]
    white = [dict(r) for r in rows if r["type"] == "White"]
    return black, white
