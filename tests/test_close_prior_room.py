"""Bug 1: When an admin opens a new room, any previous room they administrate
must be closed immediately; remaining participants get a `room_closed`
broadcast and the room is dropped from the store."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock

from server.room import create_room, get_room, delete_room, _rooms
from server import main as main_mod


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for code in list(_rooms.keys()):
        delete_room(code)


@pytest.mark.asyncio
async def test_close_other_rooms_admined_by_broadcasts_and_drops(monkeypatch):
    """An admin's prior room is broadcast-closed and deleted."""
    create_room("AAA", "admin_sid", "Admin")

    broadcasts = []
    async def fake_broadcast(code, data):
        broadcasts.append((code, data))
    monkeypatch.setattr(main_mod.manager, "broadcast", fake_broadcast)
    # Replace per-room socket map with empty dict — no live WS to close
    monkeypatch.setattr(main_mod.manager, "_sockets", {})

    await main_mod._close_other_rooms_admined_by("admin_sid", except_code=None)

    assert get_room("AAA") is None
    assert any(c == "AAA" and d.get("event") == "room_closed" for c, d in broadcasts)
    assert any(d.get("reason") == "admin_opened_new_room" for _, d in broadcasts)


@pytest.mark.asyncio
async def test_close_other_rooms_skips_finished(monkeypatch):
    """Finished rooms are left alone (they'll be GC'd by the cleanup task)."""
    r = create_room("FIN", "admin_sid", "Admin")
    r.finished = True

    monkeypatch.setattr(main_mod.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(main_mod.manager, "_sockets", {})

    await main_mod._close_other_rooms_admined_by("admin_sid", except_code=None)
    # Finished room kept (cleanup task handles it)
    assert get_room("FIN") is not None


@pytest.mark.asyncio
async def test_close_other_rooms_only_targets_same_admin(monkeypatch):
    """Rooms administered by *other* sessions are not touched."""
    create_room("MINE", "admin_sid", "Admin")
    create_room("THEIRS", "someone_else", "Other")

    monkeypatch.setattr(main_mod.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(main_mod.manager, "_sockets", {})

    await main_mod._close_other_rooms_admined_by("admin_sid", except_code=None)

    assert get_room("MINE") is None
    assert get_room("THEIRS") is not None


@pytest.mark.asyncio
async def test_close_room_with_notice_drops_socket_map(monkeypatch):
    """`_close_room_with_notice` clears the manager's socket map for the room."""
    create_room("SOCK", "admin_sid", "Admin")

    fake_ws = AsyncMock()
    monkeypatch.setattr(main_mod.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(main_mod.manager, "_sockets", {"SOCK": {"admin_sid": fake_ws}})

    await main_mod._close_room_with_notice("SOCK", reason="test")

    fake_ws.close.assert_awaited()
    assert "SOCK" not in main_mod.manager._sockets
    assert get_room("SOCK") is None
