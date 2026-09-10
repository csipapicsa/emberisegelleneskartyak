# tests/test_card_feedback.py
"""Per-card feedback: the card_feedback storage layer (server/messages_db.py)
+ the "card_feedback" websocket event.

One flat row per submission — card text/type denormalized onto the row so
the table is readable by eye without joining against cards.db or the
community card_ideas table. No dedup/toggle logic: every submission is a
new row.

Popup lifetime (force-closed on round transitions/timeout) is a frontend-only
concern in static/js/game.js and isn't covered here — this tests only that a
submitted reaction/comment is stored correctly.
"""
from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch

from server.messages_db import ensure_tables, add_card_feedback
from server import main as main_mod
from server.room import delete_room
from tests.helpers import FakeManager, make_room


CODE = "FDBK"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def db():
    """In-memory SQLite DB with all tables initialised — isolated per test."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def msg_file(tmp_path):
    """Point messages_db at a temp file so websocket-level tests never touch messages.db."""
    db_path = str(tmp_path / "feedback_test.db")
    with patch("server.messages_db.DB_PATH", db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_tables(conn)
        conn.close()
        yield


# ── messages_db: add_card_feedback ─────────────────────────────────────────────

def test_add_card_feedback_like_row(db):
    add_card_feedback(db, 7, "Egy fehér kártya.", "white", "sid_1", True)
    row = db.execute("SELECT * FROM card_feedback WHERE card_id = 7").fetchone()
    assert row["card_text"] == "Egy fehér kártya."
    assert row["card_type"] == "white"
    assert row["like"] == 1
    assert row["dislike"] == 0
    assert row["comment"] == ""
    assert row["created_at"]


def test_add_card_feedback_dislike_row(db):
    add_card_feedback(db, 7, "Egy fekete kártya ____.", "black", "sid_1", False)
    row = db.execute("SELECT * FROM card_feedback WHERE card_id = 7").fetchone()
    assert row["like"] == 0
    assert row["dislike"] == 1
    assert row["card_type"] == "black"


def test_add_card_feedback_every_submission_is_a_new_row(db):
    """No dedup/toggle — the same session giving feedback on the same card
    twice produces two rows, not an upsert."""
    add_card_feedback(db, 7, "text", "white", "sid_1", True)
    add_card_feedback(db, 7, "text", "white", "sid_1", True)
    assert db.execute("SELECT COUNT(*) FROM card_feedback WHERE card_id = 7").fetchone()[0] == 2


def test_add_card_feedback_stores_escaped_and_truncated_comment(db):
    add_card_feedback(db, 7, "text", "white", "sid_1", True, "<b>" + "x" * 600)
    row = db.execute("SELECT * FROM card_feedback WHERE card_id = 7").fetchone()
    assert row["comment"].startswith("&lt;b&gt;")
    assert len(row["comment"]) == 500


def test_add_card_feedback_blank_comment_stored_as_empty_string(db):
    add_card_feedback(db, 7, "text", "white", "sid_1", True, "   ")
    row = db.execute("SELECT * FROM card_feedback WHERE card_id = 7").fetchone()
    assert row["comment"] == ""


def test_ensure_tables_drops_superseded_per_reaction_tables():
    """Regression: an existing messages.db from before this feature predates
    card_feedback and still has the old feedback_like/dislike/comment tables
    (created but never populated) — ensure_tables must drop them."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE feedback_like (card_id INTEGER NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY (card_id, session_id));
        CREATE TABLE feedback_dislike (card_id INTEGER NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY (card_id, session_id));
        CREATE TABLE feedback_comment (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, session_id TEXT NOT NULL, username TEXT NOT NULL, comment TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
    """)
    ensure_tables(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "feedback_like" not in tables
    assert "feedback_dislike" not in tables
    assert "feedback_comment" not in tables
    assert "card_feedback" in tables
    conn.close()


# ── websocket "card_feedback" handling ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_card_feedback_like_persists(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": 42, "card_text": "Miért késik a MÁV?", "card_type": "black", "liked": True},
    )

    conn = main_mod.get_msg_conn()
    try:
        rows = conn.execute("SELECT * FROM card_feedback WHERE card_id = 42 AND session_id = ?", (sids[0],)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["card_text"] == "Miért késik a MÁV?"
    assert rows[0]["card_type"] == "black"
    assert rows[0]["like"] == 1
    assert rows[0]["dislike"] == 0


@pytest.mark.asyncio
async def test_card_feedback_dislike_persists(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "white", "liked": False},
    )

    conn = main_mod.get_msg_conn()
    try:
        rows = conn.execute("SELECT * FROM card_feedback WHERE card_id = 42 AND session_id = ?", (sids[0],)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["like"] == 0
    assert rows[0]["dislike"] == 1


@pytest.mark.asyncio
async def test_card_feedback_with_comment_is_stored(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "white", "liked": True, "comment": "Nagyon jó!"},
    )

    conn = main_mod.get_msg_conn()
    try:
        row = conn.execute("SELECT * FROM card_feedback WHERE card_id = 42").fetchone()
    finally:
        conn.close()
    assert row["comment"] == "Nagyon jó!"


@pytest.mark.asyncio
async def test_card_feedback_ignores_unknown_session(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        "stranger_sid", CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "white", "liked": True},
    )

    conn = main_mod.get_msg_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM card_feedback").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_card_feedback_no_room_is_a_noop(msg_file, monkeypatch):
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    # No room created for CODE — should not raise.
    await main_mod._handle_message(
        "sid_0", CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "white", "liked": True},
    )


@pytest.mark.asyncio
async def test_card_feedback_rejects_non_bool_liked(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "white", "liked": "yes"},
    )

    conn = main_mod.get_msg_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM card_feedback").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_card_feedback_rejects_non_int_card_id(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": "abc", "card_type": "white", "liked": True},
    )

    conn = main_mod.get_msg_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM card_feedback").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_card_feedback_rejects_invalid_card_type(msg_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        sids[0], CODE,
        {"event": "card_feedback", "card_id": 42, "card_type": "green", "liked": True},
    )

    conn = main_mod.get_msg_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM card_feedback").fetchone()[0] == 0
    finally:
        conn.close()
