# tests/test_voice.py
"""Server-side tests for per-user voice mute/unmute event handling."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from server.main import _handle_message
from server.room import delete_room
from tests.helpers import FakeManager, make_room

CODE = "VOICE"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def setup():
    room, sids = make_room(CODE, n_players=3)
    mgr = FakeManager()
    return room, sids, mgr


async def _send(sid: str, msg: dict, mgr: FakeManager) -> None:
    with patch("server.main.manager", mgr):
        await _handle_message(sid, CODE, msg)


# ── voice_mute_state ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mute_true_is_broadcast(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_mute_state", "muted": True}, mgr)
    b = mgr.broadcasts(CODE)
    assert len(b) == 1
    assert b[0] == {"event": "voice_mute_state", "session_id": sids[0], "muted": True}


@pytest.mark.asyncio
async def test_mute_false_is_broadcast(setup):
    room, sids, mgr = setup
    await _send(sids[1], {"event": "voice_mute_state", "muted": False}, mgr)
    b = mgr.broadcasts(CODE)
    assert b[-1]["muted"] is False
    assert b[-1]["session_id"] == sids[1]


@pytest.mark.asyncio
async def test_each_player_mutes_independently(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_mute_state", "muted": True}, mgr)
    await _send(sids[1], {"event": "voice_mute_state", "muted": False}, mgr)
    await _send(sids[2], {"event": "voice_mute_state", "muted": True}, mgr)
    events = [b for b in mgr.broadcasts(CODE) if b["event"] == "voice_mute_state"]
    assert events[0] == {"event": "voice_mute_state", "session_id": sids[0], "muted": True}
    assert events[1] == {"event": "voice_mute_state", "session_id": sids[1], "muted": False}
    assert events[2] == {"event": "voice_mute_state", "session_id": sids[2], "muted": True}


@pytest.mark.asyncio
async def test_mute_state_ignored_for_unknown_session(setup):
    _, _, mgr = setup
    await _send("not-in-room", {"event": "voice_mute_state", "muted": True}, mgr)
    assert not mgr.broadcasts(CODE)


@pytest.mark.asyncio
async def test_mute_state_ignored_when_muted_is_string(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_mute_state", "muted": "true"}, mgr)
    assert not mgr.broadcasts(CODE)


@pytest.mark.asyncio
async def test_mute_state_ignored_when_muted_is_int(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_mute_state", "muted": 1}, mgr)
    assert not mgr.broadcasts(CODE)


@pytest.mark.asyncio
async def test_mute_state_ignored_when_muted_missing(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_mute_state"}, mgr)
    assert not mgr.broadcasts(CODE)


# ── voice_ready ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_ready_notifies_each_other_player(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_ready"}, mgr)
    for target in sids[1:]:
        sent = mgr.sent_to(CODE, target)
        assert any(m == {"event": "voice_ready", "from": sids[0]} for m in sent)


@pytest.mark.asyncio
async def test_voice_ready_not_sent_to_self(setup):
    room, sids, mgr = setup
    await _send(sids[0], {"event": "voice_ready"}, mgr)
    assert not any(m["event"] == "voice_ready" for m in mgr.sent_to(CODE, sids[0]))


@pytest.mark.asyncio
async def test_voice_ready_ignored_for_unknown_session(setup):
    _, sids, mgr = setup
    await _send("not-in-room", {"event": "voice_ready"}, mgr)
    for sid in sids:
        assert not mgr.sent_to(CODE, sid)
