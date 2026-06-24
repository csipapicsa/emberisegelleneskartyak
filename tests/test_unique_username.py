"""Bug 3: Usernames must be unique within a room (case-insensitive).
The `_username_taken` helper is the gatekeeper for both the WS join path
and admin approval."""
from __future__ import annotations
import pytest

from server.room import create_room, delete_room, Player
from server.main import _username_taken


CODE = "UNQ"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


def test_collision_detected_exact_case():
    room = create_room(CODE, "s1", "Alice")
    assert _username_taken(room, "Alice") is True


def test_collision_is_case_insensitive():
    room = create_room(CODE, "s1", "Alice")
    assert _username_taken(room, "alice") is True
    assert _username_taken(room, "ALICE") is True
    assert _username_taken(room, "aLiCe") is True


def test_collision_ignores_surrounding_whitespace():
    room = create_room(CODE, "s1", "Bob")
    assert _username_taken(room, "  Bob  ") is True


def test_no_collision_for_different_name():
    room = create_room(CODE, "s1", "Alice")
    assert _username_taken(room, "Bob") is False


def test_exclude_sid_lets_player_keep_their_own_name():
    """Used during reconnect: the player's own row shouldn't shadow them."""
    room = create_room(CODE, "s1", "Alice")
    assert _username_taken(room, "Alice", exclude_sid="s1") is False


def test_blank_name_never_collides():
    room = create_room(CODE, "s1", "Alice")
    assert _username_taken(room, "") is False
    assert _username_taken(room, "   ") is False


def test_collision_against_second_player():
    room = create_room(CODE, "s1", "Alice")
    room.players["s2"] = Player(session_id="s2", username="Bob")
    room.join_order.append("s2")
    assert _username_taken(room, "bob") is True
    assert _username_taken(room, "Charlie") is False
