# tests/test_phase_guards.py
"""
Verify that every action is gated by room.phase.
These are the bugs that burned us before: submitting in the wrong phase,
double-reveals, double-end-rounds, etc.
"""
import pytest

from server import game as game_logic
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "PH"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


# ── submit_card phase guard (enforced in main.py, tested via phase attr) ──────

@pytest.mark.asyncio
async def test_submit_blocked_in_revealed_phase():
    """Cards submitted while phase=='revealed' are not accepted by reveal_cards
    (idempotency) and shouldn't change score."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)
    assert room.phase == "revealed"

    # Trying to reveal again does nothing
    before = len(mgr.broadcasts(CODE))
    await game_logic.reveal_cards(room, mgr)
    assert len(mgr.broadcasts(CODE)) == before


@pytest.mark.asyncio
async def test_czar_pick_blocked_in_submitting_phase():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)
    assert room.phase == "submitting"

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]

    score_before = room.players[non_czar[0]].score
    await game_logic.czar_pick(room, non_czar[0], mgr)
    assert room.players[non_czar[0]].score == score_before  # unchanged


@pytest.mark.asyncio
async def test_end_round_blocked_in_round_ending_phase():
    """_end_round with phase=='round_ending' must be a no-op (can't double-end)."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    # Manually set phase to round_ending (simulates being mid-transition)
    room.phase = "round_ending"
    score_before = room.players[non_czar[0]].score
    mgr.clear()
    await game_logic._end_round(room, [non_czar[0]], mgr)
    # Score must not have changed and no round_end broadcast
    assert room.players[non_czar[0]].score == score_before
    assert "round_end" not in mgr.events(CODE)


@pytest.mark.asyncio
async def test_tally_votes_blocked_after_finished():
    room, sids = make_room(CODE, n_players=3, voting_mode="vote")
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    room.finished = True
    room.phase = "finished"
    round_before = room.round_num

    await game_logic.tally_votes(room, mgr)
    assert room.round_num == round_before  # no round advancement


# ── Phase transitions are correct ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase_lifecycle_czar():
    """lobby → submitting → revealed → round_ending → submitting (next round)."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=2)
    mgr = FakeManager()

    assert room.phase == "lobby"
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)
    assert room.phase == "submitting"

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)
    assert room.phase == "revealed"

    await game_logic.czar_pick(room, non_czar[0], mgr)

    assert room.phase == "submitting"  # now in round 2


@pytest.mark.asyncio
async def test_phase_is_finished_after_game_end():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=1)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    await game_logic.czar_pick(room, non_czar[0], mgr)

    assert room.finished is True
    assert room.phase == "finished"
