# tests/test_card_seen.py
"""The global "card shown to players" counter (database/card_seen.db)."""
import sqlite3

import pytest

from server import card_seen
from server.deck import build_deck, deal_hand, draw_black, draw_card
from server.game import start_game
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards


def _counts():
    conn = card_seen.get_conn()
    try:
        return card_seen.get_counts(conn)
    finally:
        conn.close()


def test_record_is_memory_only_until_flush(isolated_card_seen, tmp_path):
    card_seen.record([{"id": 1}, {"id": 2}, {"id": 1}])
    assert not (tmp_path / "card_seen.db").exists()
    card_seen.flush()
    assert _counts() == {1: 2, 2: 1}


def test_flush_adds_to_existing_counts():
    card_seen.record([{"id": 5}])
    card_seen.flush()
    card_seen.record([{"id": 5}, {"id": 6}])
    card_seen.flush()
    assert _counts() == {5: 2, 6: 1}


def test_flush_empties_the_batch():
    card_seen.record([{"id": 9}])
    card_seen.flush()
    card_seen.flush()
    assert _counts() == {9: 1}


def test_record_ignores_none_and_idless_cards():
    card_seen.record([None, {"text": "x"}, {"id": 3}])
    card_seen.flush()
    assert _counts() == {3: 1}


def test_failed_write_keeps_counts_for_next_flush(monkeypatch, tmp_path):
    card_seen.record([{"id": 7}, {"id": 7}])
    monkeypatch.setattr(card_seen, "DB_PATH", str(tmp_path / "missing_dir" / "x.db"))
    with pytest.raises(sqlite3.OperationalError):
        card_seen.flush()
    monkeypatch.setattr(card_seen, "DB_PATH", str(tmp_path / "card_seen.db"))
    card_seen.flush()
    assert _counts() == {7: 2}


def test_deck_draws_are_counted():
    white = make_white_cards(20)
    black = make_black_cards(3)
    hand = deal_hand(white, 10)
    refill = draw_card(white)
    black_card = draw_black(black)
    card_seen.flush()
    expected = {c["id"]: 1 for c in hand + [refill, black_card]}
    assert _counts() == expected


def test_build_deck_alone_counts_nothing():
    build_deck(make_black_cards(5), make_white_cards(30))
    assert card_seen.take_pending() == {}


@pytest.mark.asyncio
async def test_start_game_counts_hands_and_first_black_card():
    room, _ = make_room("SEEN1", n_players=2)
    try:
        await start_game(room, make_black_cards(5), make_white_cards(40), FakeManager())
    finally:
        delete_room("SEEN1")

    pending = card_seen.take_pending()
    shown = [c["id"] for p in room.players.values() for c in p.hand] + [room.current_black["id"]]
    assert pending == {cid: 1 for cid in shown}
    assert sum(pending.values()) == 2 * 10 + 1


def test_app_shutdown_flushes_pending_counts(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from server import analytics, messages_db
    from server.main import app

    # Startup also opens messages.db and analytics.db; keep it off the live ones.
    monkeypatch.setattr(messages_db, "DB_PATH", str(tmp_path / "messages.db"))
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "analytics.db")
    monkeypatch.setenv("EEK_DISABLE_ROOM_CLEANUP", "1")
    with TestClient(app):
        card_seen.record([{"id": 42}])
    assert _counts() == {42: 1}
