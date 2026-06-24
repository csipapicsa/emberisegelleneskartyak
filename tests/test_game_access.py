"""Access checks for opening a game page directly."""
from __future__ import annotations

import pytest

from server import main as main_mod
from server.room import create_room, delete_room


CODE = "ACC"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_started_room_marks_existing_player_as_allowed():
    room = create_room(CODE, "admin_sid", "Admin")
    room.started = True

    result = await main_mod.api_get_room(CODE, session_id="admin_sid")

    assert result["started"] is True
    assert result["is_player"] is True
    assert result["can_join_game"] is True


@pytest.mark.asyncio
async def test_started_room_marks_outsider_as_not_allowed():
    room = create_room(CODE, "admin_sid", "Admin")
    room.started = True

    result = await main_mod.api_get_room(CODE, session_id="outsider_sid")

    assert result["started"] is True
    assert result["is_player"] is False
    assert result["can_join_game"] is False


@pytest.mark.asyncio
async def test_unstarted_room_allows_outsider_to_continue_to_lobby():
    create_room(CODE, "admin_sid", "Admin")

    result = await main_mod.api_get_room(CODE, session_id="outsider_sid")

    assert result["started"] is False
    assert result["is_player"] is False
    assert result["can_join_game"] is True
