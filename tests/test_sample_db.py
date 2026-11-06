"""scripts/make_sample_db.py: the made-up card_master.db that CI and fresh
clones run on instead of the real (untracked) card database."""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

from scripts import make_sample_db
from server import db
from server.db import get_cards_for_editions, get_editions, get_playable_cards


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "card_master.db"
    assert make_sample_db.main(["--out", str(path)]) == 0
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_lists_the_two_complete_packs_and_hides_the_draft(sample):
    assert [(e["id"], e["name"]) for e in get_editions(sample)] == [
        (1, "Minta alap"),
        (2, "Minta kiegészítő"),
    ]


def test_has_enough_cards_for_a_full_lobby_of_seven(sample):
    """start_game needs 10 white cards per player; the e2e tests play with up to 7."""
    black, white = get_cards_for_editions(sample, [1, 2])
    assert len(white) >= 70
    assert len(black) >= 10
    assert {c["pick"] for c in black} == {1, 2}


def test_shared_card_is_dealt_once(sample):
    _, white = get_cards_for_editions(sample, [1, 2])
    texts = [c["text"] for c in white]
    assert texts.count(make_sample_db.SHARED_WHITE) == 1
    assert make_sample_db.DRAFT_WHITE not in texts


def test_every_black_card_has_as_many_blanks_as_its_pick(sample):
    for card in get_playable_cards(sample):
        if card["type"] != "Black":
            continue
        blanks = card["text"].count(make_sample_db.BLANK)
        assert card["pick"] == max(blanks, 1), card["text"]
        assert "_" * 9 not in card["text"], "a blank is exactly 8 underscores"


def test_refuses_to_overwrite_without_force(tmp_path, capsys):
    path = tmp_path / "card_master.db"
    path.write_bytes(b"the real cards")

    assert make_sample_db.main(["--out", str(path)]) == 1
    assert path.read_bytes() == b"the real cards"
    assert "--force" in capsys.readouterr().err

    assert make_sample_db.main(["--out", str(path), "--force"]) == 0
    assert sqlite3.connect(path).execute("SELECT count(*) FROM editions").fetchone()[0] == 3
    assert [p.name for p in tmp_path.iterdir()] == ["card_master.db"]  # no temp file left behind


def _view_sql(conn: sqlite3.Connection, name: str) -> str:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?", (name,)).fetchone()[0]
    return re.sub(r"\s+", " ", sql).strip()


@pytest.mark.local_data
@pytest.mark.parametrize("view", ["game_cards", "edition_status"])
def test_sample_views_match_the_real_database(sample, view):
    """If an upload of the real card_master.db changes a view server/db.py
    reads, the sample DB (and so CI) would silently test the old one."""
    if not os.path.exists(db.DB_PATH):
        pytest.skip("real database/card_master.db is not present")
    real = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    try:
        assert _view_sql(sample, view) == _view_sql(real, view)
    finally:
        real.close()
