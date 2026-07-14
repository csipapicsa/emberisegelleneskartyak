"""Bug 1: When an admin opens a new room, any previous room they administrate
must be closed immediately; remaining participants get a `room_closed`
broadcast and the room is dropped from the store."""
from __future__ import annotations
import time
import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

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


# ── Admin-opened-a-new-room redirect (tab still on the old room gets a code) ──

@pytest.fixture(autouse=True)
def _cleanup_redirects():
    yield
    main_mod._admin_room_redirects.clear()


@pytest.mark.asyncio
async def test_closing_for_a_new_room_records_redirect(monkeypatch):
    """Passing new_code= records old_code -> new_code for later lookup."""
    create_room("OLD1", "admin_sid", "Admin")
    monkeypatch.setattr(main_mod.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(main_mod.manager, "_sockets", {})

    await main_mod._close_other_rooms_admined_by("admin_sid", except_code=None, new_code="NEW1")

    assert main_mod._get_admin_redirect("OLD1") == "NEW1"


@pytest.mark.asyncio
async def test_no_new_code_records_no_redirect(monkeypatch):
    """The existing callers that don't pass new_code (none currently do)
    still record nothing — redirect tracking is opt-in."""
    create_room("OLD2", "admin_sid", "Admin")
    monkeypatch.setattr(main_mod.manager, "broadcast", AsyncMock())
    monkeypatch.setattr(main_mod.manager, "_sockets", {})

    await main_mod._close_other_rooms_admined_by("admin_sid", except_code=None)

    assert main_mod._get_admin_redirect("OLD2") is None


def test_admin_redirect_expires(monkeypatch):
    main_mod._record_admin_redirect("OLD3", "NEW3")
    assert main_mod._get_admin_redirect("OLD3") == "NEW3"

    future = time.time() + main_mod._ADMIN_REDIRECT_TTL_SECONDS + 1
    monkeypatch.setattr(main_mod.time, "time", lambda: future)
    assert main_mod._get_admin_redirect("OLD3") is None


@pytest.mark.asyncio
async def test_api_get_room_404_includes_redirect_detail():
    main_mod._record_admin_redirect("OLD4", "NEW4")
    with pytest.raises(HTTPException) as exc:
        await main_mod.api_get_room("OLD4")
    assert exc.value.status_code == 404
    assert exc.value.detail == {"reason": "admin_opened_new_room", "new_code": "NEW4"}


@pytest.mark.asyncio
async def test_api_get_room_404_plain_when_no_redirect():
    with pytest.raises(HTTPException) as exc:
        await main_mod.api_get_room("NOPE")
    assert exc.value.status_code == 404
    assert isinstance(exc.value.detail, str)
