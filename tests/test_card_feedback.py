# tests/test_card_feedback.py
"""Per-card feedback: the card_feedback storage layer (server/messages_db.py),
the card search (server/card_search.py) and the /visszajelzes page's API
(/api/cards/search, /api/card-feedback).

One flat row per submission — card text/type denormalized onto the row so
the table is readable by eye without joining against cards.db or the
community card_ideas table. No dedup/toggle logic: every submission is a
new row.

The in-game lightbulb + websocket event this replaced is gone; feedback is
only given on the dedicated page now.
"""
from __future__ import annotations
import sqlite3
import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.messages_db import ensure_tables, add_card_feedback, COMMUNITY_ID_OFFSET
from server import card_search
from server import main as main_mod
from server.db import get_playable_cards
from tests.test_editions import make_master


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


# ── card_master.db: get_playable_cards ───────────────────────────────────────

def test_get_playable_cards_is_every_distinct_card_in_a_complete_pack():
    # make_master: cards 1-4 are in complete Piros, 8 in complete Kék; 3 is
    # also in Kék and incomplete Main Deck but must come back once; 5 is not
    # validated, 6 skipped, 7 merged.
    conn = make_master()
    # A validated card only in the incomplete Main Deck can't be dealt.
    conn.execute("INSERT INTO cards (id, type, hun_reviewed, validated) VALUES (9, 'White', 'Csak alap', 1)")
    conn.execute("INSERT INTO card_editions (card_id, edition_id) VALUES (9, 2)")
    cards = get_playable_cards(conn)
    assert sorted(c["id"] for c in cards) == [1, 2, 3, 4, 8]
    assert {c["id"]: c["type"] for c in cards}[2] == "Black"
    assert {c["id"]: c["pick"] for c in cards} == {1: 1, 2: 2, 3: 1, 4: 1, 8: 1}


# ── card_search ───────────────────────────────────────────────────────────────

CARDS = [
    {"id": 1, "text": "Öregecske Japán Fickók"},
    {"id": 2, "text": "Mi az, ami ________-t csinál a japán fickókkal?"},
    {"id": 3, "text": "Egy fickó, aki öreg"},
    {"id": 4, "text": "Nagyon hosszú szöveg, amiben valahol benne van: öregecske japán fickók"},
]


def _ids(query, cards=CARDS, **kw):
    return [c["id"] for c in card_search.search(cards, query, **kw)]


def test_search_ignores_case_and_hungarian_accents():
    assert _ids("OREGECSKE") == [1, 4]
    # Ranked by where the match starts: 1 early, 2 mid, 4 late.
    assert _ids("fickok") == [1, 2, 4]


def test_search_needs_every_word_in_any_order():
    assert set(_ids("fickó öreg")) == {1, 3, 4}
    assert _ids("japán kutya") == []


def test_search_ranks_phrase_at_start_then_shorter_cards_first():
    cards = [
        {"id": 1, "text": "Rövid: macska, kutya"},
        {"id": 2, "text": "Egy jóval hosszabb mondat, a végén: kutya macska"},
        {"id": 3, "text": "Kutya, macska és egy hosszú, hosszú, hosszú farok"},
        {"id": 4, "text": "Egy kicsit hosszabb: macska és kutya"},
    ]
    # 3 has the phrase at the start (the comma doesn't break it), 2 has it
    # late; 1 and 4 only have both words apart, shorter one first.
    assert _ids("kutya macska", cards) == [3, 2, 1, 4]


def test_search_ignores_punctuation():
    assert _ids("ficko aki oreg") == [3]
    assert _ids("japán fickókkal?") == [2]


def test_search_ignores_blanks_on_black_cards():
    assert _ids("____") == []
    assert _ids("csinál a japán") == [2]


def test_search_needs_at_least_two_letters():
    assert _ids("") == []
    assert _ids(" j ") == []
    assert _ids("ja") != []


def test_search_caps_results():
    many = [{"id": i, "text": f"kutya {i}"} for i in range(50)]
    assert len(_ids("kutya", many)) == card_search.MAX_RESULTS
    assert len(_ids("kutya", many, limit=3)) == 3


# ── /api/cards/search and /api/card-feedback ──────────────────────────────────

@pytest.fixture
def api(msg_file, monkeypatch):
    """App client over the make_master card_master.db fixture plus one
    approved and one unapproved community card in a temp messages.db."""
    monkeypatch.setattr(main_mod, "get_conn", make_master)
    conn = main_mod.get_msg_conn()
    try:
        conn.executemany(
            "INSERT INTO card_ideas (card_type, text, approved, created_at) VALUES (?, ?, ?, datetime('now'))",
            [("white", "Közösségi kutya", 1), ("white", "Jóvá nem hagyott kutya", 0)],
        )
        conn.commit()
        approved_id = conn.execute("SELECT id FROM card_ideas WHERE approved = 1").fetchone()[0]
        unapproved_id = conn.execute("SELECT id FROM card_ideas WHERE approved = 0").fetchone()[0]
    finally:
        conn.close()
    client = TestClient(main_mod.app)
    client.community_id = approved_id + COMMUNITY_ID_OFFSET
    client.unapproved_id = unapproved_id + COMMUNITY_ID_OFFSET
    return client


def _feedback_rows():
    conn = main_mod.get_msg_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM card_feedback ORDER BY id")]
    finally:
        conn.close()


def test_search_api_finds_only_playable_cards(api):
    res = api.get("/api/cards/search", params={"q": "kozos"})
    assert res.status_code == 200
    # Card 3 "Közös fehér" (complete pack) and the approved community card;
    # never the unvalidated/skipped/merged ones or the unapproved idea.
    assert res.json() == [
        {"id": 3, "text": "Közös fehér", "type": "white", "pick": 1},
        {"id": api.community_id, "text": "Közösségi kutya", "type": "white", "pick": 1},
    ]
    assert api.get("/api/cards/search", params={"q": "kutya"}).json() == [
        {"id": api.community_id, "text": "Közösségi kutya", "type": "white", "pick": 1},
    ]
    assert api.get("/api/cards/search", params={"q": "Nincs jóváhagyva"}).json() == []
    assert api.get("/api/cards/search", params={"q": "Kihagyva"}).json() == []


def test_search_api_returns_black_cards_with_pick(api):
    assert api.get("/api/cards/search", params={"q": "második"}).json() == [
        {"id": 2, "text": "Második ________, ________.", "type": "black", "pick": 2},
    ]


def test_search_api_rejects_overlong_query(api):
    assert api.get("/api/cards/search", params={"q": "x" * 101}).status_code == 400


def test_feedback_api_stores_text_and_type_from_the_database(api):
    res = api.post("/api/card-feedback", json={"card_id": 2, "liked": False, "comment": " rossz fordítás "})
    assert res.status_code == 200
    [row] = _feedback_rows()
    assert row["card_id"] == 2
    assert row["card_text"] == "Második ________, ________."
    assert row["card_type"] == "black"
    assert (row["like"], row["dislike"]) == (0, 1)
    assert row["comment"] == "rossz fordítás"
    assert row["session_id"] == ""  # no visitor identifier is kept


def test_feedback_api_accepts_approved_community_card(api):
    assert api.post("/api/card-feedback", json={"card_id": api.community_id, "liked": True}).status_code == 200
    [row] = _feedback_rows()
    assert (row["card_text"], row["card_type"], row["like"]) == ("Közösségi kutya", "white", 1)


@pytest.mark.parametrize("card_id", [5, 6, 7, 999])
def test_feedback_api_rejects_cards_not_in_the_game(api, card_id):
    assert api.post("/api/card-feedback", json={"card_id": card_id, "liked": True}).status_code == 404
    assert _feedback_rows() == []


def test_feedback_api_rejects_unapproved_community_card(api):
    assert api.post("/api/card-feedback", json={"card_id": api.unapproved_id, "liked": True}).status_code == 404


@pytest.mark.parametrize("body", [
    {"card_id": "3", "liked": True},
    {"card_id": True, "liked": True},
    {"card_id": 3, "liked": "yes"},
    {"card_id": 3},
    {"card_id": 3, "liked": True, "comment": 5},
    [3, True],
])
def test_feedback_api_rejects_malformed_body(api, body):
    assert api.post("/api/card-feedback", json=body).status_code == 400
    assert _feedback_rows() == []


def test_feedback_api_rejects_oversized_body(api):
    res = api.post("/api/card-feedback", json={"card_id": 3, "liked": True, "comment": "x" * 5000})
    assert res.status_code == 413
    assert _feedback_rows() == []


def test_feedback_page_is_served():
    res = TestClient(main_mod.app).get("/visszajelzes")
    assert res.status_code == 200
    assert "cardSearch" in res.text


def test_game_page_has_no_feedback_lightbulb():
    import pathlib
    static = pathlib.Path(main_mod.BASE_DIR) / "static"
    for name in ("game.html", "js/game.js", "js/hand.js", "css/cards.css"):
        text = (static / name).read_text(encoding="utf-8")
        assert "💡" not in text and "feedback-icon-btn" not in text, name
