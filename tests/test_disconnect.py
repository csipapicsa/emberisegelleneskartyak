# tests/test_disconnect.py
"""
Disconnect handling: auto-reveal, admin promotion, all-disconnected guard.
"""
import pytest
from server.room import delete_room, Player
from server import game as game_logic
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "DC"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_disconnect_marks_player_not_connected():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    await game_logic.handle_disconnect(room, sids[1], mgr)
    assert room.players[sids[1]].connected is False


@pytest.mark.asyncio
async def test_disconnect_broadcasts_player_disconnected():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    mgr.clear()
    await game_logic.handle_disconnect(room, sids[1], mgr)
    assert "player_disconnected" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_disconnect_triggers_reveal_if_all_remaining_submitted():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]

    # czar and non_czar[0] submit; non_czar[1] hasn't yet
    room.players[czar].submitted_cards = [room.players[czar].hand.pop()]
    room.players[non_czar[0]].submitted_cards = [room.players[non_czar[0]].hand.pop()]

    mgr.clear()
    # non_czar[1] disconnects → czar + non_czar[0] remain (both submitted) → reveal
    await game_logic.handle_disconnect(room, non_czar[1], mgr)

    assert "cards_revealed" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_disconnect_does_not_reveal_when_connected_not_all_submitted():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]

    # Nobody submitted; disconnect one non-czar, one non-czar still connected without submission
    # Add a 4th player so after disconnect there are still 2 connected non-czars
    room.players["s3"] = Player(session_id="s3", username="p3", connected=True)
    room.join_order.append("s3")

    mgr.clear()
    await game_logic.handle_disconnect(room, non_czar[1], mgr)
    events = mgr.events(CODE)
    assert "cards_revealed" not in events


@pytest.mark.asyncio
async def test_all_disconnected_does_not_auto_reveal():
    """all_submitted() returns False when nobody is connected → no reveal loop."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    for sid in sids:
        room.players[sid].connected = False

    mgr.clear()
    # Disconnect last player — no connected players remain
    await game_logic.handle_disconnect(room, sids[0], mgr)
    events = mgr.events(CODE)
    assert "cards_revealed" not in events
    assert "round_skipped" not in events


@pytest.mark.asyncio
async def test_admin_promoted_on_admin_disconnect_before_start():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    # Do NOT call start_game — still in lobby

    mgr.clear()
    await game_logic.handle_disconnect(room, sids[0], mgr)  # sids[0] is admin

    assert "admin_promoted" in mgr.events(CODE)
    assert room.admin_session_id != sids[0]


@pytest.mark.asyncio
async def test_admin_not_promoted_after_game_start():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    mgr.clear()
    await game_logic.handle_disconnect(room, sids[0], mgr)  # admin disconnects mid-game

    events = mgr.events(CODE)
    assert "admin_promoted" not in events


@pytest.mark.asyncio
async def test_czar_disconnect_during_revealed_skips_round():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    czar = room.czar_session_id
    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    mgr.clear()
    await game_logic.handle_disconnect(room, czar, mgr)

    events = mgr.events(CODE)
    assert "player_disconnected" in events
    assert "round_skipped" in events
    assert "round_end" not in events
    assert room.round_num == 2
    assert room.phase == "submitting"
    assert room.players[czar].connected is False
    assert room.czar_session_id != czar
    for sid in sids:
        assert len(room.players[sid].hand) == 10


@pytest.mark.asyncio
async def test_disconnected_player_is_not_selected_as_next_czar():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    first_czar = room.czar_session_id
    non_czar = next(sid for sid in sids if sid != first_czar)
    room.players[sids[1]].connected = False

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, non_czar, mgr)

    assert room.round_num == 2
    assert room.czar_session_id == sids[2]
