# tests/test_chat.py
"""Per-room ephemeral chat: chat_db storage layer + websocket wiring.

Chat is scoped to a room_code and only meant to live as long as the room:
these tests cover the chat.db helpers themselves, the "chat_send" websocket
event (available to approved *and* pending players), history delivered on
join/reconnect, and that deleting a room purges its chat rows.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from server import chat_db
from server import main as main_mod
from server.room import Player, create_room, delete_room
from tests.helpers import FakeManager, make_room

CODE = "CHAT"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def db():
    """In-memory SQLite DB with chat tables initialised — isolated per test."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    chat_db.ensure_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def chat_file(tmp_path):
    """Point chat_db at a temp file so websocket-level tests never touch chat.db."""
    db_path = str(tmp_path / "chat_test.db")
    with patch("server.chat_db.DB_PATH", db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        chat_db.ensure_tables(conn)
        conn.close()
        yield


# ── chat_db: ensure_tables / post_message ─────────────────────────────────────

def test_ensure_tables_creates_room_chat_messages(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "room_chat_messages" in tables


def test_post_message_escapes_html(db):
    msg = chat_db.post_message(db, "1234", "Béla", "<script>alert(1)</script>")
    assert msg["message"] == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert msg["username"] == "Béla"


def test_post_message_truncates_long_message(db):
    msg = chat_db.post_message(db, "1234", "Ann", "x" * 600)
    assert len(msg["message"]) == chat_db.MAX_MESSAGE_LENGTH


def test_post_message_rejects_blank_message(db):
    assert chat_db.post_message(db, "1234", "Ann", "   ") is None


def test_post_message_rejects_blank_username(db):
    assert chat_db.post_message(db, "1234", "   ", "hello") is None


def test_post_message_returns_stored_row_shape(db):
    msg = chat_db.post_message(db, "1234", "Ann", "hi")
    assert set(msg.keys()) == {"id", "username", "message", "created_at"}


# ── chat_db: get_messages / delete_room_messages ──────────────────────────────

def test_get_messages_scoped_to_room_code(db):
    chat_db.post_message(db, "AAAA", "Ann", "hi room A")
    chat_db.post_message(db, "BBBB", "Ben", "hi room B")
    a_msgs = chat_db.get_messages(db, "AAAA")
    b_msgs = chat_db.get_messages(db, "BBBB")
    assert len(a_msgs) == 1 and a_msgs[0]["message"] == "hi room A"
    assert len(b_msgs) == 1 and b_msgs[0]["message"] == "hi room B"


def test_get_messages_ordered_oldest_first(db):
    chat_db.post_message(db, "1234", "Ann", "first")
    chat_db.post_message(db, "1234", "Ann", "second")
    msgs = chat_db.get_messages(db, "1234")
    assert [m["message"] for m in msgs] == ["first", "second"]


def test_get_messages_respects_limit_keeping_most_recent(db):
    for i in range(5):
        chat_db.post_message(db, "1234", "Ann", f"msg{i}")
    msgs = chat_db.get_messages(db, "1234", limit=2)
    assert [m["message"] for m in msgs] == ["msg3", "msg4"]


def test_get_messages_empty_room_returns_empty_list(db):
    assert chat_db.get_messages(db, "NOPE") == []


def test_delete_room_messages_only_clears_that_room(db):
    chat_db.post_message(db, "AAAA", "Ann", "hi A")
    chat_db.post_message(db, "BBBB", "Ben", "hi B")
    chat_db.delete_room_messages(db, "AAAA")
    assert chat_db.get_messages(db, "AAAA") == []
    assert len(chat_db.get_messages(db, "BBBB")) == 1


# ── websocket "chat_send" handling ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_send_broadcasts_and_persists(chat_file, monkeypatch):
    room, sids = make_room(CODE, n_players=2)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(sids[0], CODE, {"event": "chat_send", "message": "Sziasztok!"})

    broadcasts = mgr.broadcasts(CODE)
    assert len(broadcasts) == 1
    assert broadcasts[0]["event"] == "chat_message"
    assert broadcasts[0]["message"] == "Sziasztok!"
    assert broadcasts[0]["username"] == room.players[sids[0]].username

    conn = chat_db.get_conn()
    try:
        stored = chat_db.get_messages(conn, CODE)
    finally:
        conn.close()
    assert len(stored) == 1
    assert stored[0]["message"] == "Sziasztok!"


@pytest.mark.asyncio
async def test_chat_send_allows_pending_player(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    room = main_mod.get_room(CODE)
    room.pending["pending_sid"] = Player(session_id="pending_sid", username="Ujonc")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("pending_sid", CODE, {"event": "chat_send", "message": "Meg varok"})

    broadcasts = mgr.broadcasts(CODE)
    assert len(broadcasts) == 1
    assert broadcasts[0]["username"] == "Ujonc"


@pytest.mark.asyncio
async def test_chat_send_ignores_unknown_session(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("stranger_sid", CODE, {"event": "chat_send", "message": "hi"})

    assert mgr.broadcasts(CODE) == []


@pytest.mark.asyncio
async def test_chat_send_ignores_blank_message(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("admin_sid", CODE, {"event": "chat_send", "message": "   "})

    assert mgr.broadcasts(CODE) == []


@pytest.mark.asyncio
async def test_chat_send_ignores_missing_message_key(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("admin_sid", CODE, {"event": "chat_send"})

    assert mgr.broadcasts(CODE) == []


@pytest.mark.asyncio
async def test_chat_send_no_room_is_a_noop(chat_file, monkeypatch):
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    # No room created for CODE — should not raise.
    await main_mod._handle_message("admin_sid", CODE, {"event": "chat_send", "message": "hi"})

    assert mgr.broadcasts(CODE) == []


# ── chat history delivered on (re)connect ─────────────────────────────────────

@pytest.mark.asyncio
async def test_send_chat_history_sends_existing_messages(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    conn = chat_db.get_conn()
    try:
        chat_db.post_message(conn, CODE, "Admin", "Earlier message")
    finally:
        conn.close()

    await main_mod._send_chat_history(CODE, "admin_sid")

    sent = mgr.sent_to(CODE, "admin_sid")
    assert len(sent) == 1
    assert sent[0]["event"] == "chat_history"
    assert sent[0]["messages"][0]["message"] == "Earlier message"


@pytest.mark.asyncio
async def test_send_chat_history_empty_when_no_messages(chat_file, monkeypatch):
    create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._send_chat_history(CODE, "admin_sid")

    sent = mgr.sent_to(CODE, "admin_sid")
    assert sent[0]["messages"] == []


# ── room deletion purges chat ─────────────────────────────────────────────────

def test_delete_room_and_chat_purges_only_that_room(chat_file):
    create_room(CODE, "admin_sid", "Admin")
    create_room("OTHR", "admin2", "Admin2")
    try:
        conn = chat_db.get_conn()
        try:
            chat_db.post_message(conn, CODE, "Admin", "bye room")
            chat_db.post_message(conn, "OTHR", "Admin2", "still here")
        finally:
            conn.close()

        main_mod._delete_room_and_chat(CODE)

        conn = chat_db.get_conn()
        try:
            assert chat_db.get_messages(conn, CODE) == []
            assert len(chat_db.get_messages(conn, "OTHR")) == 1
        finally:
            conn.close()
        assert main_mod.get_room(CODE) is None
    finally:
        delete_room("OTHR")
