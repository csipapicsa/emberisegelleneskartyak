"""Changing submitted cards is allowed only while the round is still submitting."""
from __future__ import annotations

import pytest

from server.room import delete_room
from server import game as game_logic
from server import main as main_mod
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "CHG"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_withdraw_submission_returns_cards_and_broadcasts(monkeypatch):
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    sid = sids[0]
    submitted = room.players[sid].hand.pop()
    room.players[sid].submitted_cards = [submitted]
    hand_len = len(room.players[sid].hand)

    await main_mod._handle_message(sid, CODE, {"event": "withdraw_submission"})

    assert room.players[sid].submitted_cards == []
    assert len(room.players[sid].hand) == hand_len + 1
    assert submitted in room.players[sid].hand
    assert "submission_withdrawn" in mgr.events(CODE)
    assert "hand_update" in mgr.sent_events(CODE, sid)


@pytest.mark.asyncio
async def test_withdraw_submission_ignored_after_reveal(monkeypatch):
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    sid = sids[0]
    submitted = list(room.players[sid].submitted_cards)
    mgr.clear()
    await main_mod._handle_message(sid, CODE, {"event": "withdraw_submission"})

    assert room.players[sid].submitted_cards == submitted
    assert "submission_withdrawn" not in mgr.events(CODE)


@pytest.mark.asyncio
async def test_request_new_hand_can_be_used_multiple_times_in_round(monkeypatch):
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    sid = sids[0]
    mgr.clear()
    await main_mod._handle_message(sid, CODE, {"event": "request_new_hand"})
    second_hand_ids = [c["id"] for c in room.players[sid].hand]

    await main_mod._handle_message(sid, CODE, {"event": "request_new_hand"})
    third_hand_ids = [c["id"] for c in room.players[sid].hand]

    assert len(second_hand_ids) == 10
    assert len(third_hand_ids) == 10
    assert mgr.sent_events(CODE, sid).count("new_hand_dealt") == 2
    assert mgr.sent_events(CODE, sid).count("hand_update") == 2


@pytest.mark.asyncio
async def test_request_new_hand_ignored_after_player_submitted(monkeypatch):
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    sid = sids[0]
    player = room.players[sid]
    player.submitted_cards = [player.hand.pop()]
    hand_before = list(player.hand)
    swaps_before = player.swaps_remaining

    mgr.clear()
    await main_mod._handle_message(sid, CODE, {"event": "request_new_hand"})

    assert player.hand == hand_before
    assert player.swaps_remaining == swaps_before
    assert "hand_update" not in mgr.sent_events(CODE, sid)
