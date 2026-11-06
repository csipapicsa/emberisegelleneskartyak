"""Reconnect/reload state while cards are already revealed."""
from __future__ import annotations

import pytest

from server import game as game_logic
from server import main as main_mod
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "REVL"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_room_snapshot_includes_revealed_submissions_for_reconnect():
    room, sids = make_room(CODE, n_players=3, voting_mode="vote", timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    snapshot = main_mod._room_snapshot(room)

    assert snapshot["phase"] == "revealed"
    assert len(snapshot["submissions"]) == 3
    assert all("cards" in sub for sub in snapshot["submissions"])
    assert all("username" not in sub for sub in snapshot["submissions"])
