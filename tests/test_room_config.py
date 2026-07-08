"""Room configuration WebSocket handling."""
from __future__ import annotations

import pytest

from server import main as main_mod
from server.room import Player, create_room, delete_room
from tests.helpers import FakeManager


CODE = "CFG"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_configure_room_updates_prevent_self_vote(monkeypatch):
    room = create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        "admin_sid",
        CODE,
        {"event": "configure_room", "prevent_self_vote": False},
    )

    assert room.prevent_self_vote is False
    assert mgr.last_broadcast(CODE)["prevent_self_vote"] is False


@pytest.mark.asyncio
async def test_configure_room_ignores_non_boolean_prevent_self_vote(monkeypatch):
    room = create_room(CODE, "admin_sid", "Admin")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        "admin_sid",
        CODE,
        {"event": "configure_room", "prevent_self_vote": "false"},
    )

    assert room.prevent_self_vote is True
    assert mgr.last_broadcast(CODE)["prevent_self_vote"] is True


@pytest.mark.asyncio
async def test_start_game_blocked_while_pending_players_exist(monkeypatch):
    room = create_room(CODE, "admin_sid", "Admin")
    room.pending["guest_sid"] = Player(session_id="guest_sid", username="Guest")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("admin_sid", CODE, {"event": "start_game"})

    assert room.started is False
    assert mgr.sent_to(CODE, "admin_sid")[-1]["event"] == "error"


@pytest.mark.asyncio
async def test_deny_player_removes_pending_and_broadcasts_room_state(monkeypatch):
    room = create_room(CODE, "admin_sid", "Admin")
    room.pending["guest_sid"] = Player(session_id="guest_sid", username="Guest")
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message(
        "admin_sid",
        CODE,
        {"event": "deny_player", "session_id": "guest_sid"},
    )

    assert "guest_sid" not in room.pending
    assert mgr.sent_to(CODE, "guest_sid")[-1]["event"] == "denied"
    state = mgr.last_broadcast(CODE)
    assert state["event"] == "room_state"
    assert state["pending"] == []
