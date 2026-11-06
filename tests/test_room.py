# tests/test_room.py
"""Unit tests for Room / Player dataclass methods."""
from server.room import Player, create_room, delete_room, get_room

# ── create_room ───────────────────────────────────────────────────────────────

def test_create_room_sets_admin():
    room = create_room("R1", "admin_sid", "Admin")
    assert room.admin_session_id == "admin_sid"
    assert room.players["admin_sid"].is_admin is True
    assert room.prevent_self_vote is True
    assert "admin_sid" in room.join_order
    delete_room("R1")


def test_create_room_persists():
    create_room("R2", "s", "u")
    assert get_room("R2") is not None
    delete_room("R2")
    assert get_room("R2") is None


# ── all_submitted ─────────────────────────────────────────────────────────────

def test_all_submitted_false_when_no_connected():
    room = create_room("RS1", "s1", "u1")
    room.players["s1"].connected = False
    assert room.all_submitted() is False
    delete_room("RS1")


def test_all_submitted_true_when_all_connected_submitted():
    room = create_room("RS2", "s1", "u1")
    room.players["s1"].submitted_cards = [{"id": 1}]
    assert room.all_submitted() is True
    delete_room("RS2")


def test_all_submitted_false_when_one_pending():
    room = create_room("RS3", "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2", connected=True)
    room.players["s1"].submitted_cards = [{"id": 1}]
    # s2 connected but hasn't submitted
    assert room.all_submitted() is False
    delete_room("RS3")


def test_all_submitted_ignores_disconnected():
    room = create_room("RS4", "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2", connected=False)
    room.players["s1"].submitted_cards = [{"id": 1}]
    # s2 disconnected — shouldn't block
    assert room.all_submitted() is True
    delete_room("RS4")


# ── all_voted ─────────────────────────────────────────────────────────────────

def test_all_voted_false_when_no_connected():
    room = create_room("RV1", "s1", "u1")
    room.players["s1"].connected = False
    assert room.all_voted() is False
    delete_room("RV1")


def test_all_voted_true_when_all_voted():
    room = create_room("RV2", "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2", connected=True)
    room.join_order.append("s2")
    room.votes = {"s1": "s2", "s2": "s1"}
    assert room.all_voted() is True
    delete_room("RV2")


def test_all_voted_false_when_one_missing():
    room = create_room("RV3", "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2", connected=True)
    room.join_order.append("s2")
    room.votes = {"s1": "s2"}  # s2 hasn't voted
    assert room.all_voted() is False
    delete_room("RV3")


# ── czar rotation ─────────────────────────────────────────────────────────────

def test_czar_rotation_wraps():
    room = create_room("RC1", "s1", "u1")
    room.players["s2"] = Player(session_id="s2", username="u2")
    room.join_order = ["s1", "s2"]

    assert room.czar_session_id == "s1"
    room.next_czar()
    assert room.czar_session_id == "s2"
    room.next_czar()
    assert room.czar_session_id == "s1"
    delete_room("RC1")


# ── promote_next_admin ────────────────────────────────────────────────────────

def test_promote_next_admin():
    room = create_room("RP1", "s1", "Admin")
    room.players["s2"] = Player(session_id="s2", username="u2")
    room.join_order = ["s1", "s2"]

    new_admin = room.promote_next_admin()
    assert new_admin == "s2"
    assert room.admin_session_id == "s2"
    assert room.players["s2"].is_admin is True
    delete_room("RP1")


def test_promote_next_admin_no_one_available():
    room = create_room("RP2", "s1", "Admin")
    # Only the admin in the room
    new_admin = room.promote_next_admin()
    assert new_admin is None
    delete_room("RP2")
