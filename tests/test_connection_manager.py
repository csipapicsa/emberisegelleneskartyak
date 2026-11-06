# tests/test_connection_manager.py
"""
Unit tests for the room-scoped ConnectionManager.
No WebSocket objects needed — we use a tiny stub.
"""

import pytest

from server.room import Player, create_room, delete_room

# ── Minimal WebSocket stub ────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []
        self.accepted = False
        self.closed = False
        self.close_code = None

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=None):
        self.closed = True
        self.close_code = code


# ── Import the real ConnectionManager ────────────────────────────────────────

from server.main import ConnectionManager

CODE_A = "CONA"
CODE_B = "CONB"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE_A)
    delete_room(CODE_B)


@pytest.fixture
def mgr():
    return ConnectionManager()


# ── connect / disconnect ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connect_accepts_websocket(mgr):
    ws = FakeWS()
    await mgr.connect(CODE_A, "s1", ws)
    assert ws.accepted is True


@pytest.mark.asyncio
async def test_disconnect_removes_socket(mgr):
    ws = FakeWS()
    await mgr.connect(CODE_A, "s1", ws)
    mgr.disconnect(CODE_A, "s1")
    # Sending to disconnected session must silently do nothing
    await mgr.send(CODE_A, "s1", {"event": "test"})
    assert ws.sent == []


@pytest.mark.asyncio
async def test_reconnect_same_session_closes_prior_socket(mgr):
    """Only the newest socket for a (room, session) pair stays active."""
    first = FakeWS()
    second = FakeWS()

    await mgr.connect(CODE_A, "s1", first)
    await mgr.connect(CODE_A, "s1", second)

    assert first.closed is True
    assert first.close_code == 4003
    assert second.accepted is True

    await mgr.send(CODE_A, "s1", {"event": "new_socket_only"})

    assert first.sent == []
    assert second.sent == [{"event": "new_socket_only"}]


# ── send ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_reaches_correct_socket(mgr):
    ws1, ws2 = FakeWS(), FakeWS()
    await mgr.connect(CODE_A, "s1", ws1)
    await mgr.connect(CODE_A, "s2", ws2)
    await mgr.send(CODE_A, "s1", {"event": "hello"})
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 0


@pytest.mark.asyncio
async def test_send_does_not_cross_rooms(mgr):
    """Session s1 in room A must not receive messages sent to room B."""
    ws_a, ws_b = FakeWS(), FakeWS()
    await mgr.connect(CODE_A, "s1", ws_a)
    await mgr.connect(CODE_B, "s1", ws_b)

    await mgr.send(CODE_A, "s1", {"event": "room_a_event"})
    await mgr.send(CODE_B, "s1", {"event": "room_b_event"})

    assert ws_a.sent[0]["event"] == "room_a_event"
    assert ws_b.sent[0]["event"] == "room_b_event"
    assert len(ws_a.sent) == 1
    assert len(ws_b.sent) == 1


# ── broadcast ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_reaches_all_players_in_room(mgr):
    room = create_room(CODE_A, "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2")

    ws1, ws2 = FakeWS(), FakeWS()
    await mgr.connect(CODE_A, "s1", ws1)
    await mgr.connect(CODE_A, "s2", ws2)

    await mgr.broadcast(CODE_A, {"event": "ping"})
    assert any(m["event"] == "ping" for m in ws1.sent)
    assert any(m["event"] == "ping" for m in ws2.sent)


@pytest.mark.asyncio
async def test_broadcast_does_not_reach_other_room(mgr):
    room_a = create_room(CODE_A, "s1", "u1")
    room_b = create_room(CODE_B, "s2", "u2")

    ws_a, ws_b = FakeWS(), FakeWS()
    await mgr.connect(CODE_A, "s1", ws_a)
    await mgr.connect(CODE_B, "s2", ws_b)

    await mgr.broadcast(CODE_A, {"event": "only_a"})
    assert any(m["event"] == "only_a" for m in ws_a.sent)
    assert not any(m["event"] == "only_a" for m in ws_b.sent)


# ── evict_from_all_rooms ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evict_removes_session_from_other_rooms(mgr):
    ws_a, ws_b = FakeWS(), FakeWS()
    await mgr.connect(CODE_A, "s1", ws_a)
    await mgr.connect(CODE_B, "s1", ws_b)

    mgr.evict_from_all_rooms("s1", except_room=CODE_B)

    # s1 should no longer be addressable in room A
    await mgr.send(CODE_A, "s1", {"event": "test"})
    assert len(ws_a.sent) == 0  # evicted

    # s1 still exists in room B
    await mgr.send(CODE_B, "s1", {"event": "test"})
    assert len(ws_b.sent) == 1


@pytest.mark.asyncio
async def test_evict_leaves_except_room_intact(mgr):
    ws = FakeWS()
    await mgr.connect(CODE_A, "s1", ws)
    mgr.evict_from_all_rooms("s1", except_room=CODE_A)
    await mgr.send(CODE_A, "s1", {"event": "stay"})
    assert len(ws.sent) == 1


# ── error resilience ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_to_unknown_session_is_silent(mgr):
    """send() to a session that was never connected must not raise."""
    await mgr.send(CODE_A, "ghost", {"event": "x"})  # must not raise


@pytest.mark.asyncio
async def test_send_to_broken_socket_is_silent(mgr):
    """If the socket raises on send_json, the error is swallowed."""
    class BrokenWS(FakeWS):
        async def send_json(self, data):
            raise RuntimeError("connection lost")

    ws = BrokenWS()
    await mgr.connect(CODE_A, "s1", ws)
    await mgr.send(CODE_A, "s1", {"event": "x"})  # must not raise
