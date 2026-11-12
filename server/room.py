# server/room.py

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Player:
    session_id: str
    username: str
    score: float = 0.0
    hand: list[dict] = field(default_factory=list)
    submitted_cards: list[dict] = field(default_factory=list)
    swaps_remaining: int = 0
    hand_refresh_used: bool = False
    connected: bool = True
    is_admin: bool = False
    missed_rounds: int = 0
    is_bot: bool = False  # a computer player (server/bot.py): always "connected", has no socket


@dataclass
class Room:
    code: str
    admin_session_id: str
    players: dict[str, Player] = field(default_factory=dict)
    join_order: list[str] = field(default_factory=list)
    pending: dict[str, Player] = field(default_factory=dict)

    # config
    edition_ids: list[int] = field(default_factory=list)
    round_total: int = 20
    timer_seconds: int = 60
    voting_mode: str = "czar"   # "czar" | "vote"
    prevent_self_vote: bool = True

    # game state
    phase: str = "lobby"  # lobby | submitting | revealed | round_ending | finished
    started: bool = False
    finished: bool = False
    closed: bool = False  # deleted from memory (server/main.py); stops the round loop, see game.close_room
    round_num: int = 0
    cards_revealed: bool = False  # kept for compat; prefer phase checks
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    czar_index: int = 0
    black_deck: list[dict] = field(default_factory=list)
    white_deck: list[dict] = field(default_factory=list)
    discard_pile: list[dict] = field(default_factory=list)
    current_black: dict | None = None
    round_start_time: float | None = None
    votes: dict[str, str] = field(default_factory=dict)  # voter_sid → winner_sid
    timer_task: object = field(default=None, repr=False)  # asyncio.Task | None
    gameplay: object = field(default=None, repr=False)  # gameplay_log.GameLog | None

    @property
    def czar_session_id(self) -> str | None:
        if not self.join_order:
            return None
        return self.join_order[self.czar_index % len(self.join_order)]

    def next_czar(self) -> None:
        self.czar_index = (self.czar_index + 1) % len(self.join_order)

    def all_submitted(self) -> bool:
        connected = [p for p in self.players.values() if p.connected]
        # With no human connected (e.g. everyone between the lobby and the game page) a computer player's
        # submission alone mustn't reveal the round.
        if not connected or not self.humans_connected():
            return False
        return all(bool(p.submitted_cards) for p in connected)

    def humans_connected(self) -> bool:
        """Is any human still here? Computer players always count as connected, so a room with only
        computer players left is empty."""
        return any(p.connected and not p.is_bot for p in self.players.values())

    def bot_count(self) -> int:
        return sum(1 for p in self.players.values() if p.is_bot)

    def all_voted(self) -> bool:
        connected = [sid for sid, p in self.players.items() if p.connected]
        if not connected or not self.humans_connected():
            return False
        return all(sid in self.votes for sid in connected)

    def promote_next_admin(self) -> str | None:
        for sid in self.join_order:
            if sid != self.admin_session_id and sid in self.players and not self.players[sid].is_bot:
                self.admin_session_id = sid
                self.players[sid].is_admin = True
                return sid
        return None


_rooms: dict[str, Room] = {}


def create_room(code: str, admin_session_id: str, admin_username: str) -> Room:
    player = Player(session_id=admin_session_id, username=admin_username, is_admin=True)
    room = Room(code=code, admin_session_id=admin_session_id)
    room.players[admin_session_id] = player
    room.join_order.append(admin_session_id)
    _rooms[code] = room
    return room


def get_room(code: str) -> Room | None:
    return _rooms.get(code)


def room_of_player(session_id: str) -> Room | None:
    """The open room this session is an approved player in (pending players don't count), or None."""
    for room in _rooms.values():
        if not room.closed and session_id in room.players:
            return room
    return None


def delete_room(code: str) -> None:
    _rooms.pop(code, None)
