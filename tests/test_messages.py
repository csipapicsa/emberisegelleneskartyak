# tests/test_messages.py
"""Unit and HTTP tests for the global chat message wall."""
from __future__ import annotations
import json
import sqlite3
import pytest
from unittest.mock import patch
from fastapi import HTTPException, Request


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite DB with all tables initialised — isolated per test."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from server.messages_db import ensure_tables
    ensure_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def api_db(tmp_path):
    """Temp DB for endpoint-level tests so they never pollute messages.db."""
    db_path = str(tmp_path / "test.db")
    with patch("server.messages_db.DB_PATH", db_path):
        from server.messages_db import ensure_tables
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_tables(conn)
        conn.close()
        yield


def make_json_request(payload, headers: dict[str, str] | None = None) -> Request:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {
        "content-type": "application/json",
        "content-length": str(len(body)),
        **(headers or {}),
    }
    return make_raw_request(body, request_headers)


def make_raw_request(body: bytes, headers: dict[str, str] | None = None) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/card-ideas",
            "headers": raw_headers,
        },
        receive,
    )


# ── DB-layer unit tests ───────────────────────────────────────────────────────

def test_ensure_tables_creates_chat_messages_table(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "chat_messages" in tables


def test_ensure_tables_creates_card_ideas_table_with_vote_counters(db):
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    columns = {r[1] for r in db.execute("PRAGMA table_info(card_ideas)").fetchall()}
    assert "card_ideas" in tables
    assert {"like", "dislike", "approved", "seen_by_admin", "number_of_white_cards"}.issubset(columns)


def test_post_and_retrieve_message(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Feri", "Helló világ!")
    msgs = get_chat_messages(db)
    assert len(msgs) == 1
    assert msgs[0]["username"] == "Feri"
    assert msgs[0]["message"] == "Helló világ!"


def test_messages_returned_in_chronological_order(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Alice", "Első üzenet")
    post_chat_message(db, "Bob", "Második üzenet")
    msgs = get_chat_messages(db)
    assert msgs[0]["username"] == "Alice"
    assert msgs[1]["username"] == "Bob"


def test_xss_escaped_in_message(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Hacker<script>", "<img src=x onerror=alert(1)>")
    msgs = get_chat_messages(db)
    # html.escape converts < and > — the raw tag markers must not appear
    assert "<script>" not in msgs[0]["username"]
    assert "<img" not in msgs[0]["message"]   # tag opener is escaped to &lt;img


def test_empty_message_not_saved(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Feri", "")
    assert get_chat_messages(db) == []


def test_empty_username_not_saved(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "", "Valami")
    assert get_chat_messages(db) == []


def test_purge_expired_data_removes_old_contact_messages(db):
    from server.messages_db import purge_expired_data
    db.execute(
        """INSERT INTO messages (name, email, message, created_at)
           VALUES ('Régi', 'regi@example.com', 'régi üzenet', '2020-01-01T00:00:00+00:00')"""
    )
    db.execute(
        """INSERT INTO messages (name, email, message, created_at)
           VALUES ('Új', 'uj@example.com', 'új üzenet', datetime('now'))"""
    )
    purge_expired_data(db)
    assert [row[0] for row in db.execute("SELECT name FROM messages").fetchall()] == ["Új"]


def test_whitespace_only_not_saved(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "   ", "   ")
    assert get_chat_messages(db) == []


def test_username_truncated_to_24_chars(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "A" * 100, "Üzenet")
    msgs = get_chat_messages(db)
    assert len(msgs[0]["username"]) <= 24


def test_message_truncated_to_500_chars(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Feri", "X" * 1000)
    msgs = get_chat_messages(db)
    assert len(msgs[0]["message"]) <= 500


def test_old_messages_purged_on_read(db):
    from server.messages_db import get_chat_messages
    db.execute(
        "INSERT INTO chat_messages (username, message, created_at) "
        "VALUES (?, ?, datetime('now', '-31 days'))",
        ("OldUser", "Régi üzenet"),
    )
    db.commit()
    msgs = get_chat_messages(db)
    assert not any(m["username"] == "OldUser" for m in msgs)


def test_messages_within_30_days_kept(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Feri", "Friss üzenet")
    # Also insert one 29 days old — should survive
    db.execute(
        "INSERT INTO chat_messages (username, message, created_at) "
        "VALUES (?, ?, datetime('now', '-29 days'))",
        ("Marika", "Majdnem régi"),
    )
    db.commit()
    msgs = get_chat_messages(db)
    usernames = [m["username"] for m in msgs]
    assert "Feri" in usernames
    assert "Marika" in usernames


def test_limit_respected(db):
    from server.messages_db import post_chat_message, get_chat_messages
    for i in range(60):
        post_chat_message(db, "User", f"Üzenet {i}")
    msgs = get_chat_messages(db, limit=10)
    assert len(msgs) == 10


def test_limit_returns_most_recent(db):
    from server.messages_db import post_chat_message, get_chat_messages
    for i in range(10):
        post_chat_message(db, "User", f"Üzenet {i}")
    # limit=5 should return the last 5, oldest-first within that window
    msgs = get_chat_messages(db, limit=5)
    assert len(msgs) == 5
    assert msgs[-1]["message"] == "Üzenet 9"


def test_get_messages_returns_created_at_field(db):
    from server.messages_db import post_chat_message, get_chat_messages
    post_chat_message(db, "Feri", "Teszt")
    msgs = get_chat_messages(db)
    assert "created_at" in msgs[0]
    assert msgs[0]["created_at"]  # not empty


def test_save_card_idea_defaults_vote_counters(db):
    from server.messages_db import save_card_idea
    assert save_card_idea(db, "white", "Vicces fehér ötlet") is True
    row = db.execute(
        'SELECT card_type, text, "like", dislike, created_at FROM card_ideas'
    ).fetchone()
    assert row["card_type"] == "white"
    assert row["text"] == "Vicces fehér ötlet"
    assert row["like"] == 0
    assert row["dislike"] == 0
    assert row["created_at"]


def test_save_black_card_idea_preserves_inner_spaces(db):
    from server.messages_db import save_card_idea
    save_card_idea(db, "black", "A legjobb dolog:        .")
    row = db.execute("SELECT card_type, text FROM card_ideas").fetchone()
    assert row["card_type"] == "black"
    assert row["text"] == "A legjobb dolog:        ."


def test_save_card_idea_escapes_xss(db):
    from server.messages_db import save_card_idea
    save_card_idea(db, "white", '<script>alert("x")</script>')
    row = db.execute("SELECT text FROM card_ideas").fetchone()
    assert "<script>" not in row["text"]
    assert "&lt;script&gt;" in row["text"]
    assert "&quot;x&quot;" in row["text"]


def test_save_card_idea_sql_injection_stays_plain_text(db):
    from server.messages_db import save_card_idea
    attack = "'); DROP TABLE card_ideas; --"
    save_card_idea(db, "white", attack)
    row = db.execute("SELECT text FROM card_ideas").fetchone()
    count = db.execute("SELECT COUNT(*) FROM card_ideas").fetchone()[0]
    assert "DROP TABLE card_ideas" in row["text"]
    assert count == 1


def test_save_card_idea_strips_unsafe_control_characters(db):
    from server.messages_db import save_card_idea
    save_card_idea(db, "white", "jó\x00ötlet\x1f\tok")
    row = db.execute("SELECT text FROM card_ideas").fetchone()
    assert row["text"] == "jóötlet\tok"


def test_save_card_idea_truncates_by_card_type(db):
    from server.messages_db import save_card_idea
    save_card_idea(db, "white", "A" * 500)
    save_card_idea(db, "black", "B" * 500)
    rows = db.execute("SELECT card_type, text FROM card_ideas ORDER BY id").fetchall()
    assert len(rows[0]["text"]) == 280
    assert len(rows[1]["text"]) == 360


def test_save_card_idea_rejects_non_string_text(db):
    from server.messages_db import save_card_idea
    with pytest.raises(TypeError):
        save_card_idea(db, "white", 123)


def test_save_card_idea_rejects_invalid_type(db):
    from server.messages_db import save_card_idea
    with pytest.raises(ValueError):
        save_card_idea(db, "green", "Nem létező típus")


def test_save_card_idea_ignores_empty_text(db):
    from server.messages_db import save_card_idea
    assert save_card_idea(db, "white", "   ") is False
    count = db.execute("SELECT COUNT(*) FROM card_ideas").fetchone()[0]
    assert count == 0


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_chat_returns_list(api_db):
    from server.main import api_get_chat
    res = await api_get_chat()
    assert isinstance(res, list)


@pytest.mark.asyncio
async def test_post_chat_valid_returns_ok(api_db):
    from server.main import api_post_chat
    res = await api_post_chat({"username": "Teszter", "message": "API teszt"})
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_posted_message_appears_in_list(api_db):
    from server.main import api_get_chat, api_post_chat
    await api_post_chat({"username": "ChatUser", "message": "Ez látható"})
    msgs = await api_get_chat()
    assert any(m["username"] == "ChatUser" and m["message"] == "Ez látható" for m in msgs)


@pytest.mark.asyncio
async def test_post_chat_missing_username_returns_400(api_db):
    from server.main import api_post_chat
    with pytest.raises(HTTPException) as exc:
        await api_post_chat({"message": "Névtelen"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_chat_missing_message_returns_400(api_db):
    from server.main import api_post_chat
    with pytest.raises(HTTPException) as exc:
        await api_post_chat({"username": "Feri"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_chat_whitespace_only_username_returns_400(api_db):
    from server.main import api_post_chat
    with pytest.raises(HTTPException) as exc:
        await api_post_chat({"username": "   ", "message": "Valami"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_chat_whitespace_only_message_returns_400(api_db):
    from server.main import api_post_chat
    with pytest.raises(HTTPException) as exc:
        await api_post_chat({"username": "Feri", "message": "   "})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_chat_xss_not_stored_raw(api_db):
    from server.main import api_get_chat, api_post_chat
    await api_post_chat({"username": "XSSUser", "message": "<script>alert(1)</script>"})
    msgs = await api_get_chat()
    xss_msg = next((m for m in msgs if m["username"] == "XSSUser"), None)
    assert xss_msg is not None
    assert "<script>" not in xss_msg["message"]


@pytest.mark.asyncio
async def test_post_chat_empty_body_returns_400(api_db):
    from server.main import api_post_chat
    with pytest.raises(HTTPException) as exc:
        await api_post_chat({})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_valid_returns_ok(api_db):
    from server.main import api_card_ideas
    res = await api_card_ideas(make_json_request({"card_type": "black", "text": "Miért ilyen jó        ?"}))
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_post_card_idea_missing_text_returns_400(api_db):
    from server.main import api_card_ideas
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(make_json_request({"card_type": "white", "text": "   "}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_invalid_type_returns_400(api_db):
    from server.main import api_card_ideas
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(make_json_request({"card_type": "blue", "text": "Ötlet"}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_non_object_json_returns_400(api_db):
    from server.main import api_card_ideas
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(make_json_request(["white", "Ötlet"]))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_non_string_text_returns_400(api_db):
    from server.main import api_card_ideas
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(make_json_request({"card_type": "white", "text": 123}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_non_string_type_returns_400(api_db):
    from server.main import api_card_ideas
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(make_json_request({"card_type": ["white"], "text": "Ötlet"}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_malformed_json_returns_400(api_db):
    from server.main import api_card_ideas
    request = make_raw_request(
        b'{"card_type": "white", "text":',
        {"content-type": "application/json", "content-length": "31"},
    )
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_oversized_body_returns_413(api_db):
    from server.main import api_card_ideas, CARD_IDEA_MAX_BODY_BYTES
    request = make_raw_request(
        b"{}",
        {
            "content-type": "application/json",
            "content-length": str(CARD_IDEA_MAX_BODY_BYTES + 1),
        },
    )
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(request)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_post_card_idea_invalid_content_length_returns_400(api_db):
    from server.main import api_card_ideas
    request = make_raw_request(
        b"{}",
        {"content-type": "application/json", "content-length": "not-a-number"},
    )
    with pytest.raises(HTTPException) as exc:
        await api_card_ideas(request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_card_idea_xss_payload_not_stored_raw(api_db):
    from server.main import api_card_ideas
    await api_card_ideas(make_json_request({
        "card_type": "white",
        "text": '<img src=x onerror="alert(1)">',
    }))
    from server.messages_db import get_conn
    conn = get_conn()
    try:
        row = conn.execute("SELECT text FROM card_ideas").fetchone()
    finally:
        conn.close()
    assert "<img" not in row["text"]
    assert "onerror=&quot;alert(1)&quot;" in row["text"]


@pytest.mark.asyncio
async def test_post_card_idea_sql_injection_payload_does_not_execute(api_db):
    from server.main import api_card_ideas
    await api_card_ideas(make_json_request({
        "card_type": "white",
        "text": "'); DROP TABLE card_ideas; --",
    }))
    from server.messages_db import get_conn
    conn = get_conn()
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        count = conn.execute("SELECT COUNT(*) FROM card_ideas").fetchone()[0]
    finally:
        conn.close()
    assert "card_ideas" in tables
    assert count == 1


@pytest.mark.asyncio
async def test_card_idea_payload_validator_rejects_non_string_fields():
    from server.main import _validate_card_idea_payload
    with pytest.raises(HTTPException) as exc:
        _validate_card_idea_payload({"card_type": "white", "text": None})
    assert exc.value.status_code == 400
