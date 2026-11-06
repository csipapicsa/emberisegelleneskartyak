# server/card_seen.py
"""Global "how often has this card been shown" counter, in its own database.

Every card that leaves a deck (dealt into a hand, drawn as a refill or swap,
or turned up as the round's black card) gets +1. It doesn't matter who saw it
or whether it was played. deck.build_deck() reads the counts back through
current_counts() and puts the least-seen cards near the top, so a newly added
pack doesn't drown in cards players have already seen many times.

Recording must never slow the game down, so record() only bumps an in-memory
Counter. flush() writes the batch in one transaction; the app calls it from a
background task every FLUSH_INTERVAL seconds (off the event loop) and once on
shutdown. A crash loses at most one interval of counts, which is fine for a
statistic.

Storage is one row per card: (card_id INTEGER PRIMARY KEY, seen INTEGER).
card_id is the rowid itself, so a row is a handful of bytes and a lookup is a
single b-tree probe. Community cards are counted under their offset ids
(COMMUNITY_ID_OFFSET), so they never collide with card_master.db ids.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import Counter
from collections.abc import Iterable

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "card_seen.db")
FLUSH_INTERVAL = 60

_pending: Counter[int] = Counter()


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS card_seen (
            card_id INTEGER PRIMARY KEY,
            seen    INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()


def record(cards: Iterable[dict | None]) -> None:
    """Count each card as seen once. In memory only — safe on the hot path."""
    for card in cards:
        if card is not None and "id" in card:
            _pending[card["id"]] += 1


def take_pending() -> dict[int, int]:
    """Hand over the counts gathered so far and start a fresh batch.

    Call this on the event loop thread (where record() runs), then pass the
    result to write_counts() in a worker thread, so the two never race."""
    global _pending
    batch, _pending = _pending, Counter()
    return dict(batch)


def write_counts(counts: dict[int, int]) -> None:
    """Add a batch of counts to the database in one transaction."""
    if not counts:
        return
    conn = get_conn()
    try:
        ensure_tables(conn)
        with conn:
            conn.executemany(
                """
                INSERT INTO card_seen (card_id, seen) VALUES (?, ?)
                ON CONFLICT(card_id) DO UPDATE SET seen = seen + excluded.seen
                """,
                counts.items(),
            )
    finally:
        conn.close()


def restore_pending(counts: dict[int, int]) -> None:
    """Put a batch back after a failed write, so it goes out with the next one."""
    _pending.update(counts)


def flush() -> None:
    """Synchronous take + write, for shutdown and tests."""
    batch = take_pending()
    try:
        write_counts(batch)
    except Exception:
        restore_pending(batch)
        raise


def get_counts(conn: sqlite3.Connection) -> dict[int, int]:
    ensure_tables(conn)
    return dict(conn.execute("SELECT card_id, seen FROM card_seen").fetchall())


def current_counts() -> dict[int, int]:
    """Counts as of now: what's on disk plus the batch not yet flushed, so
    games started within the same minute see each other's deals.

    Never raises — a deck can always be built from uniform odds, so a broken
    statistics file must not stop a game from starting."""
    counts: Counter[int] = Counter()
    try:
        conn = get_conn()
        try:
            counts.update(get_counts(conn))
        finally:
            conn.close()
    except Exception:
        logger.exception("card_seen read failed; dealing without seen counts")
    counts.update(_pending)
    return dict(counts)
