from __future__ import annotations
import sqlite3
import os

# card_master.db is built offline (translation + review on the dev machine)
# and uploaded as a whole file, so the server only ever reads it. Use only
# the game_cards view, editions and card_editions — the other tables belong
# to the translation pipeline. game_cards already filters to validated,
# non-skipped, non-merged cards and holds the Hungarian text.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "card_master.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_editions(conn: sqlite3.Connection, min_cards: int = 0) -> list[dict]:
    # Editions with no playable card don't show up at all. Some only share a
    # card or two with bigger packs, so callers hide those with min_cards.
    rows = conn.execute("""
        SELECT
            e.id,
            COALESCE(e.hun_name, e.name) AS name,
            e.sort_order,
            SUM(g.type = 'Black') AS black_count,
            SUM(g.type = 'White') AS white_count
        FROM editions e
        JOIN card_editions ce ON ce.edition_id = e.id
        JOIN game_cards g ON g.card_id = ce.card_id
        GROUP BY e.id
        HAVING COUNT(*) >= ?
        ORDER BY e.sort_order, e.id
    """, (min_cards,)).fetchall()
    return [dict(r) for r in rows]


def get_edition_name(conn: sqlite3.Connection, edition_id: int) -> str | None:
    """Return an edition's display name, or ``None`` when it does not exist."""
    row = conn.execute(
        "SELECT COALESCE(hun_name, name) AS name FROM editions WHERE id = ?", (edition_id,)
    ).fetchone()
    return row["name"] if row else None


def get_cards_for_edition(conn: sqlite3.Connection, edition_id: int) -> tuple[list[dict], list[dict]]:
    """Return the playable cards for one edition in stable order."""
    rows = conn.execute(
        """
        SELECT g.card_id AS id, g.text, g.type, COALESCE(g.pick, 1) AS pick
        FROM game_cards g
        JOIN card_editions ce ON ce.card_id = g.card_id
        WHERE ce.edition_id = ?
        ORDER BY CASE g.type WHEN 'Black' THEN 0 WHEN 'White' THEN 1 ELSE 2 END, g.card_id
        """,
        (edition_id,),
    ).fetchall()
    black = [dict(r) for r in rows if r["type"] == "Black"]
    white = [dict(r) for r in rows if r["type"] == "White"]
    return black, white


def get_cards_for_editions(conn: sqlite3.Connection, edition_ids: list[int]) -> tuple[list[dict], list[dict]]:
    # DISTINCT: packs overlap (a card can be in e.g. Alappakli and Piros), and
    # a card in two selected packs must go into the deck only once.
    placeholders = ",".join("?" * len(edition_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT g.card_id AS id, g.text, g.type, COALESCE(g.pick, 1) AS pick
        FROM game_cards g
        JOIN card_editions ce ON ce.card_id = g.card_id
        WHERE ce.edition_id IN ({placeholders})
        """,
        edition_ids,
    ).fetchall()
    black = [dict(r) for r in rows if r["type"] == "Black"]
    white = [dict(r) for r in rows if r["type"] == "White"]
    return black, white
