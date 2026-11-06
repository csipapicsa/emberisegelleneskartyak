# server/chat_db.py
"""Ephemeral per-room chat, kept in its own database.

Messages are scoped to a room_code and are only meant to live as long as the
room itself: callers must purge a room's rows (delete_room_messages) whenever
the in-memory Room is torn down, so nothing outlives the room it belongs to.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import UTC, datetime

from server.paths import DATA_DIR

DB_PATH = str(DATA_DIR / "chat.db")
MAX_MESSAGE_LENGTH = 500
HISTORY_LIMIT = 200


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS room_chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code  TEXT NOT NULL,
            username   TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_room_chat_room_code ON room_chat_messages (room_code);
    """)
    conn.commit()


def post_message(conn: sqlite3.Connection, room_code: str, username: str, message: str) -> dict | None:
    """Persist one chat line. Returns the stored row (already escaped for
    direct HTML insertion), or None if there's nothing worth sending."""
    clean_user = html.escape(username.strip())
    clean_msg = html.escape(message.strip()[:MAX_MESSAGE_LENGTH])
    if not clean_user or not clean_msg:
        return None

    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO room_chat_messages (room_code, username, message, created_at) VALUES (?, ?, ?, ?)",
        (room_code, clean_user, clean_msg, now),
    )
    conn.commit()
    return {"id": cur.lastrowid, "username": clean_user, "message": clean_msg, "created_at": now}


def get_messages(conn: sqlite3.Connection, room_code: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, username, message, created_at FROM room_chat_messages
        WHERE room_code = ? ORDER BY id DESC LIMIT ?
        """,
        (room_code, limit),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def delete_room_messages(conn: sqlite3.Connection, room_code: str) -> None:
    conn.execute("DELETE FROM room_chat_messages WHERE room_code = ?", (room_code,))
    conn.commit()
