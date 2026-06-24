"""Bug 8: a single player can start and play a standalone game."""
from __future__ import annotations

import pytest

from server.room import delete_room
from server import game as game_logic
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "SOL"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_standalone_player_can_start_game():
    room, sids = make_room(CODE, n_players=1, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()

    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    assert room.started is True
    assert room.finished is False
    assert room.phase == "submitting"
    assert room.czar_session_id == sids[0]
    assert len(room.players[sids[0]].hand) == 10
    assert "game_started" in mgr.events(CODE)
    assert "round_start" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_standalone_player_can_finish_round_as_czar_and_submitter():
    room, sids = make_room(CODE, n_players=1, voting_mode="czar", round_total=3, timer_seconds=0)
    solo = sids[0]
    mgr = FakeManager()

    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    submitted = room.players[solo].hand.pop()
    room.players[solo].submitted_cards = [submitted]

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, solo, mgr)

    assert room.players[solo].score == 1.0
    assert "cards_revealed" in mgr.events(CODE)
    assert "round_end" in mgr.events(CODE)
    assert len(room.players[solo].hand) == 10


@pytest.mark.asyncio
async def test_standalone_game_reaches_game_end():
    room, sids = make_room(CODE, n_players=1, voting_mode="czar", round_total=1, timer_seconds=0)
    solo = sids[0]
    mgr = FakeManager()

    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    room.players[solo].submitted_cards = [room.players[solo].hand.pop()]

    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, solo, mgr)

    assert room.finished is True
    assert room.phase == "finished"
    game_end_events = [b for b in mgr.broadcasts(CODE) if b["event"] == "game_end"]
    assert game_end_events
    assert game_end_events[-1]["final_scores"] == [
        {"session_id": solo, "username": "Player0", "score": 1.0}
    ]
