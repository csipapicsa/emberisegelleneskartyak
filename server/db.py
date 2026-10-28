from __future__ import annotations
import sqlite3
import os

# card_master.db is built offline (translation + review on the dev machine)
# and uploaded as a whole file, so the server only ever reads it. Use only
# the game_cards and edition_status views, editions and card_editions — the
# other tables belong to the translation pipeline. game_cards already filters to validated,
# non-skipped, non-merged cards and holds the Hungarian text.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "card_master.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_editions(conn: sqlite3.Connection) -> list[dict]:
    # Only fully translated packs can be picked. Packs overlap a lot, so many
    # incomplete ones still have a few playable cards (shared with a complete
    # pack) and would look playable when almost none of their own cards are.
    # edition_status is computed on read, so a new upload is picked up as is.
    rows = conn.execute("""
        SELECT edition_id AS id, name, sort_order,
               playable_black AS black_count, playable_white AS white_count
        FROM edition_status
        WHERE complete = 1
        ORDER BY sort_order, edition_id
    """).fetchall()
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
    # a card in two selected packs must go into the deck only once. Ids of
    # incomplete packs are dropped, the same packs get_editions() hides, so a
    # stale lobby or a hand-made message can't deal a half-translated pack.
    placeholders = ",".join("?" * len(edition_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT g.card_id AS id, g.text, g.type, COALESCE(g.pick, 1) AS pick
        FROM game_cards g
        JOIN card_editions ce ON ce.card_id = g.card_id
        WHERE ce.edition_id IN ({placeholders})
          AND ce.edition_id IN (SELECT edition_id FROM edition_status WHERE complete = 1)
        """,
        edition_ids,
    ).fetchall()
    black = [dict(r) for r in rows if r["type"] == "Black"]
    white = [dict(r) for r in rows if r["type"] == "White"]
    return black, white


def get_playable_cards(conn: sqlite3.Connection) -> list[dict]:
    """Every distinct card in a complete pack — the cards a game can deal."""
    rows = conn.execute(
        """
        SELECT DISTINCT g.card_id AS id, g.text, g.type, COALESCE(g.pick, 1) AS pick
        FROM game_cards g
        JOIN card_editions ce ON ce.card_id = g.card_id
        WHERE ce.edition_id IN (SELECT edition_id FROM edition_status WHERE complete = 1)
        """
    ).fetchall()
    return [dict(r) for r in rows]
