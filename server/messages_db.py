from __future__ import annotations
import sqlite3
import html
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "messages.db")
CARD_IDEA_MAX_LENGTHS = {"white": 280, "black": 360}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            message    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS feedback_like (
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            PRIMARY KEY (card_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_dislike (
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            PRIMARY KEY (card_id, session_id)
        );

        CREATE TABLE IF NOT EXISTS feedback_comment (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id    INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            username   TEXT NOT NULL,
            comment    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS card_ideas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            card_type  TEXT NOT NULL CHECK (card_type IN ('white', 'black')),
            text       TEXT NOT NULL,
            "like"     INTEGER NOT NULL DEFAULT 0 CHECK ("like" >= 0),
            dislike    INTEGER NOT NULL DEFAULT 0 CHECK (dislike >= 0),
            created_at TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(card_ideas)").fetchall()}
    if "like" not in columns:
        conn.execute('ALTER TABLE card_ideas ADD COLUMN "like" INTEGER NOT NULL DEFAULT 0 CHECK ("like" >= 0)')
    if "dislike" not in columns:
        conn.execute("ALTER TABLE card_ideas ADD COLUMN dislike INTEGER NOT NULL DEFAULT 0 CHECK (dislike >= 0)")
    conn.commit()


def save_message(conn: sqlite3.Connection, name: str, email: str, message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO messages (name, email, message, created_at) VALUES (?, ?, ?, ?)",
        (name, email, message, now),
    )
    conn.commit()


def _strip_unsafe_controls(value: str) -> str:
    return "".join(
        char for char in value
        if char in ("\n", "\r", "\t") or (ord(char) >= 32 and ord(char) != 127)
    )


def sanitize_card_idea_text(card_type: str, text: str) -> str:
    if card_type not in CARD_IDEA_MAX_LENGTHS:
        raise ValueError("invalid card type")
    if not isinstance(text, str):
        raise TypeError("card idea text must be a string")

    clean_text = _strip_unsafe_controls(text).strip()
    clean_text = clean_text[:CARD_IDEA_MAX_LENGTHS[card_type]]
    return html.escape(clean_text, quote=True)


def save_card_idea(conn: sqlite3.Connection, card_type: str, text: str) -> bool:
    if not isinstance(card_type, str):
        raise TypeError("card type must be a string")
    clean_type = card_type.strip().lower()
    if clean_type not in CARD_IDEA_MAX_LENGTHS:
        raise ValueError("invalid card type")

    clean_text = sanitize_card_idea_text(clean_type, text)
    if not clean_text:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        'INSERT INTO card_ideas (card_type, text, "like", dislike, created_at) VALUES (?, ?, 0, 0, ?)',
        (clean_type, clean_text, now),
    )
    conn.commit()
    return True


def add_like(conn: sqlite3.Connection, card_id: int, session_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO feedback_like (card_id, session_id) VALUES (?, ?)",
        (card_id, session_id),
    )
    conn.execute(
        "DELETE FROM feedback_dislike WHERE card_id = ? AND session_id = ?",
        (card_id, session_id),
    )
    conn.commit()


def add_dislike(conn: sqlite3.Connection, card_id: int, session_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO feedback_dislike (card_id, session_id) VALUES (?, ?)",
        (card_id, session_id),
    )
    conn.execute(
        "DELETE FROM feedback_like WHERE card_id = ? AND session_id = ?",
        (card_id, session_id),
    )
    conn.commit()


def get_chat_messages(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    conn.execute("DELETE FROM chat_messages WHERE created_at < datetime('now', '-30 days')")
    conn.commit()
    rows = conn.execute(
        "SELECT id, username, message, created_at FROM chat_messages ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def post_chat_message(conn: sqlite3.Connection, username: str, message: str) -> None:
    clean_user = html.escape(username.strip()[:24])
    clean_msg  = html.escape(message.strip()[:500])
    if not clean_user or not clean_msg:
        return
    conn.execute(
        "INSERT INTO chat_messages (username, message) VALUES (?, ?)",
        (clean_user, clean_msg),
    )
    conn.commit()


def add_comment(conn: sqlite3.Connection, card_id: int, session_id: str, username: str, comment: str) -> None:
    clean = html.escape(comment.strip())[:500]
    if not clean:
        return
    conn.execute(
        "INSERT INTO feedback_comment (card_id, session_id, username, comment) VALUES (?, ?, ?, ?)",
        (card_id, session_id, username, clean),
    )
    conn.commit()
