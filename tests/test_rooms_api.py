"""Room listing API behavior."""
from __future__ import annotations

import pytest

from server import main as main_mod
from server.room import Player, create_room, delete_room

CODE = "LIST"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_api_list_rooms_includes_player_names():
    room = create_room(CODE, "admin_sid", "Admin")
    room.players["guest_sid"] = Player(session_id="guest_sid", username="Guest")
    room.join_order.append("guest_sid")

    rooms = await main_mod.api_list_rooms()
    listed = next(r for r in rooms if r["code"] == CODE)

    assert listed["player_count"] == 2
    assert listed["player_names"] == ["Admin", "Guest"]
