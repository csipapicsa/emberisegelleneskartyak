# tests/test_community_edition.py
"""Unit and HTTP tests for the "Ti küldtétek" community edition + per-IP voting."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from server.messages_db import (
    COMMUNITY_EDITION_ID,
    COMMUNITY_ID_OFFSET,
    ensure_tables,
    get_community_cards,
    list_card_ideas,
    save_card_idea,
    vote_card_idea,
)
from server.privacy import pseudonymize_ip

# ── Fixtures & helpers ────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite DB with all tables initialised — isolated per test."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def privacy_hmac_secret(monkeypatch):
    monkeypatch.setenv("EEK_PRIVACY_HMAC_SECRET", "test-only-privacy-secret")


def voter_hash(ip: str) -> str:
    return pseudonymize_ip(ip, "card-idea-vote")


@pytest.fixture
def api_db(tmp_path):
    """Temp messages.db for endpoint-level tests so they never pollute the real one."""
    db_path = str(tmp_path / "test.db")
    with patch("server.messages_db.DB_PATH", db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_tables(conn)
        conn.close()
        yield


def seed_idea(card_type: str, text: str) -> int:
    """Insert one card idea through the patched DB_PATH and return its id."""
    from server.messages_db import get_conn
    conn = get_conn()
    try:
        save_card_idea(conn, card_type, text)
        return conn.execute("SELECT MAX(id) FROM card_ideas").fetchone()[0]
    finally:
        conn.close()


def make_request(method="GET", body: bytes = b"", headers=None, client=None) -> Request:
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": "/", "headers": raw_headers}
    if client is not None:
        scope["client"] = client

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def json_request(payload, headers=None) -> Request:
    body = json.dumps(payload).encode("utf-8")
    base = {"content-type": "application/json", "content-length": str(len(body))}
    return make_request("POST", body, {**base, **(headers or {})})


# ── get_community_cards ───────────────────────────────────────────────────────

def test_community_cards_convert_type_and_metadata(db):
    save_card_idea(db, "black", "Mi a baj?")
    save_card_idea(db, "white", "Egy fehér válasz.")
    db.execute("UPDATE card_ideas SET approved = 1")
    db.commit()
    black, white = get_community_cards(db)
    assert len(black) == 1 and len(white) == 1
    b = black[0]
    assert b["type"] == "Black"
    assert b["pick"] == 1
    assert b["edition_id"] == COMMUNITY_EDITION_ID
    assert b["community"] is True
    assert white[0]["type"] == "White"


def test_community_card_ids_are_offset(db):
    save_card_idea(db, "white", "Eltolt azonosító.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    db.execute("UPDATE card_ideas SET approved = 1 WHERE id = ?", (idea_id,))
    db.commit()
    _, white = get_community_cards(db)
    assert white[0]["id"] == idea_id + COMMUNITY_ID_OFFSET
    assert white[0]["id"] >= COMMUNITY_ID_OFFSET


def test_community_card_text_is_sent_raw_for_the_client_to_escape(db):
    # Stored escaped (sanitize_card_idea_text), but the game client escapes
    # every card text itself — sending it escaped would show "&quot;".
    save_card_idea(db, "white", '"Idézet" & <b>')
    db.execute("UPDATE card_ideas SET approved = 1")
    db.commit()
    assert "&quot;" in db.execute("SELECT text FROM card_ideas").fetchone()[0]
    _, white = get_community_cards(db)
    assert white[0]["text"] == '"Idézet" & <b>'


def test_community_cards_empty_when_no_ideas(db):
    black, white = get_community_cards(db)
    assert black == [] and white == []


def test_community_cards_exclude_unapproved(db):
    save_card_idea(db, "black", "Jóváhagyatlan ötlet.")
    black, white = get_community_cards(db)
    assert black == [] and white == []


def test_community_cards_use_number_of_white_cards_as_pick(db):
    save_card_idea(db, "black", "Két üres hely ____ és ____.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    db.execute(
        "UPDATE card_ideas SET approved = 1, number_of_white_cards = 2 WHERE id = ?",
        (idea_id,),
    )
    db.commit()
    black, _ = get_community_cards(db)
    assert black[0]["pick"] == 2


# ── list_card_ideas ───────────────────────────────────────────────────────────

def test_list_card_ideas_zero_counts_initially(db):
    save_card_idea(db, "white", "Friss ötlet.")
    rows = list_card_ideas(db, voter_hash("1.1.1.1"))
    assert len(rows) == 1
    assert rows[0]["likes"] == 0
    assert rows[0]["dislikes"] == 0
    assert rows[0]["my_vote"] is None


def test_list_card_ideas_reports_my_vote_per_ip(db):
    save_card_idea(db, "black", "Szavazható kártya.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")
    assert list_card_ideas(db, voter_hash("1.1.1.1"))[0]["my_vote"] == "like"
    # A different IP sees the count but not its own vote
    other = list_card_ideas(db, voter_hash("2.2.2.2"))[0]
    assert other["likes"] == 1
    assert other["my_vote"] is None


# ── idea status (approved / rejected / pending) ────────────────────────────────

def test_list_card_ideas_status_pending_by_default(db):
    save_card_idea(db, "white", "Friss ötlet.")
    assert list_card_ideas(db, voter_hash("1.1.1.1"))[0]["status"] == "pending"


def test_list_card_ideas_status_rejected_when_seen_but_not_approved(db):
    save_card_idea(db, "white", "Elutasított.")
    db.execute("UPDATE card_ideas SET seen_by_admin = 1")
    db.commit()
    assert list_card_ideas(db, voter_hash("1.1.1.1"))[0]["status"] == "rejected"


def test_list_card_ideas_status_approved_wins_even_if_unseen(db):
    """Regression: admins approve ideas with a direct DB update that doesn't
    also set seen_by_admin — approved must still take priority over that,
    or an approved idea wrongly shows up as pending on the vote page."""
    save_card_idea(db, "white", "Jóváhagyva, de nem jelölve látottnak.")
    db.execute("UPDATE card_ideas SET approved = 1")
    db.commit()
    assert list_card_ideas(db, voter_hash("1.1.1.1"))[0]["status"] == "approved"


def test_list_card_ideas_status_approved_wins_when_also_seen(db):
    save_card_idea(db, "white", "Jóváhagyva és látott.")
    db.execute("UPDATE card_ideas SET approved = 1, seen_by_admin = 1")
    db.commit()
    assert list_card_ideas(db, voter_hash("1.1.1.1"))[0]["status"] == "approved"


# ── vote_card_idea ────────────────────────────────────────────────────────────

def test_vote_like_increments_count(db):
    save_card_idea(db, "white", "Tetszik?")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    res = vote_card_idea(db, idea_id, voter_hash("5.5.5.5"), "like")
    assert res == {"id": idea_id, "my_vote": "like", "likes": 1, "dislikes": 0}


def test_same_vote_again_unvotes(db):
    save_card_idea(db, "white", "Toggle.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("5.5.5.5"), "like")
    res = vote_card_idea(db, idea_id, voter_hash("5.5.5.5"), "like")
    assert res["my_vote"] is None
    assert res["likes"] == 0


def test_opposite_vote_switches(db):
    save_card_idea(db, "white", "Switch.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("5.5.5.5"), "like")
    res = vote_card_idea(db, idea_id, voter_hash("5.5.5.5"), "dislike")
    assert res["my_vote"] == "dislike"
    assert res["likes"] == 0
    assert res["dislikes"] == 1


def test_one_vote_per_ip(db):
    save_card_idea(db, "white", "Egy IP egy szavazat.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")  # toggles off, no double-count
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")  # on again
    rows_for_ip = db.execute(
        "SELECT COUNT(*) FROM card_idea_votes WHERE idea_id = ? AND voter_hash = ?",
        (idea_id, voter_hash("1.1.1.1")),
    ).fetchone()[0]
    assert rows_for_ip == 1


def test_distinct_ips_counted_separately(db):
    save_card_idea(db, "white", "Több IP.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")
    vote_card_idea(db, idea_id, voter_hash("2.2.2.2"), "like")
    res = vote_card_idea(db, idea_id, voter_hash("3.3.3.3"), "dislike")
    assert res["likes"] == 2
    assert res["dislikes"] == 1


def test_vote_refreshes_denormalized_counters(db):
    save_card_idea(db, "white", "Denormalizált.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "like")
    vote_card_idea(db, idea_id, voter_hash("2.2.2.2"), "dislike")
    row = db.execute('SELECT "like", dislike FROM card_ideas WHERE id = ?', (idea_id,)).fetchone()
    assert row["like"] == 1
    assert row["dislike"] == 1


def test_vote_nonexistent_idea_returns_none(db):
    assert vote_card_idea(db, 999999, voter_hash("1.1.1.1"), "like") is None


def test_vote_invalid_value_raises(db):
    save_card_idea(db, "white", "Rossz szavazat.")
    idea_id = db.execute("SELECT id FROM card_ideas").fetchone()[0]
    with pytest.raises(ValueError):
        vote_card_idea(db, idea_id, voter_hash("1.1.1.1"), "meh")


def test_legacy_raw_ip_votes_are_migrated_to_hmac(db):
    db.execute("DROP TABLE card_idea_votes")
    db.execute("""
        CREATE TABLE card_idea_votes (
            idea_id INTEGER NOT NULL,
            voter_ip TEXT NOT NULL,
            vote TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (idea_id, voter_ip)
        )
    """)
    db.execute(
        "INSERT INTO card_idea_votes VALUES (1, '1.2.3.4', 'like', '2026-01-01T00:00:00Z')"
    )
    ensure_tables(db, migrate_legacy_votes=True)

    columns = {row[1] for row in db.execute("PRAGMA table_info(card_idea_votes)")}
    assert "voter_ip" not in columns
    assert db.execute("SELECT voter_hash FROM card_idea_votes").fetchone()[0] == voter_hash("1.2.3.4")


# ── _client_ip helper ─────────────────────────────────────────────────────────

def test_client_ip_uses_nginx_real_ip_header():
    from server.main import _client_ip
    req = make_request(headers={"x-real-ip": "1.2.3.4"})
    assert _client_ip(req) == "1.2.3.4"


def test_client_ip_does_not_trust_forwarded_header():
    from server.main import _client_ip
    req = make_request(headers={"x-forwarded-for": "1.2.3.4"})
    assert _client_ip(req) is None


def test_client_ip_falls_back_to_socket():
    from server.main import _client_ip
    req = make_request(client=("9.9.9.9", 4321))
    assert _client_ip(req) == "9.9.9.9"


def test_client_ip_unknown_when_no_client():
    from server.main import _client_ip
    assert _client_ip(make_request()) is None


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_editions_appends_community_edition(api_db, monkeypatch):
    from server import main as main_mod
    from server.main import api_editions
    from tests.test_editions import make_master
    monkeypatch.setattr(main_mod, "get_conn", make_master)
    editions = await api_editions()
    community = [e for e in editions if e["id"] == COMMUNITY_EDITION_ID]
    assert len(community) == 1
    assert community[0]["name"] == "Ti küldtétek"
    assert community[0]["community"] is True


@pytest.mark.asyncio
async def test_api_list_card_ideas_returns_list(api_db):
    from server.main import api_list_card_ideas
    seed_idea("white", "Listázható.")
    res = await api_list_card_ideas(make_request())
    assert isinstance(res, list)
    assert res[0]["text"] == "Listázható."


@pytest.mark.asyncio
async def test_api_vote_valid_returns_counts(api_db):
    from server.main import api_vote_card_idea
    idea_id = seed_idea("white", "API szavazás.")
    res = await api_vote_card_idea(idea_id, json_request({"vote": "like"}, {"x-real-ip": "8.8.8.8"}))
    assert res["likes"] == 1
    assert res["my_vote"] == "like"


@pytest.mark.asyncio
async def test_api_vote_persists_per_ip_across_calls(api_db):
    from server.main import api_list_card_ideas, api_vote_card_idea
    idea_id = seed_idea("white", "IP-alapú.")
    await api_vote_card_idea(idea_id, json_request({"vote": "like"}, {"x-real-ip": "8.8.8.8"}))
    # Same IP listing sees its own vote
    same_ip = await api_list_card_ideas(make_request(headers={"x-real-ip": "8.8.8.8"}))
    assert same_ip[0]["my_vote"] == "like"
    # Different IP sees the count but no personal vote
    other_ip = await api_list_card_ideas(make_request(headers={"x-real-ip": "9.9.9.9"}))
    assert other_ip[0]["likes"] == 1
    assert other_ip[0]["my_vote"] is None


@pytest.mark.asyncio
async def test_api_vote_invalid_value_returns_400(api_db):
    from server.main import api_vote_card_idea
    idea_id = seed_idea("white", "Hibás.")
    with pytest.raises(HTTPException) as exc:
        await api_vote_card_idea(idea_id, json_request({"vote": "love"}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_api_vote_nonexistent_returns_404(api_db):
    from server.main import api_vote_card_idea
    with pytest.raises(HTTPException) as exc:
        await api_vote_card_idea(424242, json_request({"vote": "like"}, {"x-real-ip": "8.8.8.8"}))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_api_vote_malformed_json_returns_400(api_db):
    from server.main import api_vote_card_idea
    req = make_request("POST", b'{"vote":', {"content-type": "application/json", "content-length": "8"})
    with pytest.raises(HTTPException) as exc:
        await api_vote_card_idea(1, req)
    assert exc.value.status_code == 400
