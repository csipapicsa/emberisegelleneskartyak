# tests/helpers.py
from __future__ import annotations

from collections import defaultdict

from server.room import Player, Room, create_room


def make_white_cards(n: int, pick: int = 1) -> list[dict]:
    return [{"id": i, "text": f"white {i}", "pick": pick} for i in range(n)]


def make_black_cards(n: int, pick: int = 1) -> list[dict]:
    return [{"id": 100 + i, "text": f"black {i} _", "pick": pick} for i in range(n)]


def make_room(code: str = "TEST", n_players: int = 3,
              voting_mode: str = "czar", round_total: int = 3,
              timer_seconds: int = 0,
              prevent_self_vote: bool = True) -> tuple[Room, list[str]]:
    """Create an in-memory room with n_players already approved."""
    room = create_room(code, "sid_0", "Player0")
    room.voting_mode = voting_mode
    room.round_total = round_total
    room.timer_seconds = timer_seconds
    room.prevent_self_vote = prevent_self_vote

    sids = ["sid_0"]
    for i in range(1, n_players):
        sid = f"sid_{i}"
        player = Player(session_id=sid, username=f"Player{i}")
        room.players[sid] = player
        room.join_order.append(sid)
        sids.append(sid)

    return room, sids


class FakeManager:
    """
    Drop-in for ConnectionManager.
    Records every broadcast/send so tests can assert on them.
    Does NOT involve WebSockets.
    """

    def __init__(self):
        # room_code -> session_id -> list[dict]
        self._sent: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        # room_code -> list[dict]
        self._broadcasts: dict[str, list[dict]] = defaultdict(list)

    async def send(self, room_code: str, session_id: str, data: dict) -> None:
        self._sent[room_code][session_id].append(data)

    async def broadcast(self, room_code: str, data: dict) -> None:
        self._broadcasts[room_code].append(data)

    async def broadcast_all(self, room_code: str, data: dict) -> None:
        self._broadcasts[room_code].append(data)

    # ── Query helpers ─────────────────────────────────────────────────────────

    def broadcasts(self, room_code: str) -> list[dict]:
        return self._broadcasts[room_code]

    def events(self, room_code: str) -> list[str]:
        return [m["event"] for m in self._broadcasts[room_code]]

    def sent_to(self, room_code: str, session_id: str) -> list[dict]:
        return self._sent[room_code][session_id]

    def sent_events(self, room_code: str, session_id: str) -> list[str]:
        return [m["event"] for m in self._sent[room_code][session_id]]

    def last_broadcast(self, room_code: str) -> dict | None:
        b = self._broadcasts[room_code]
        return b[-1] if b else None

    def clear(self) -> None:
        self._sent.clear()
        self._broadcasts.clear()
