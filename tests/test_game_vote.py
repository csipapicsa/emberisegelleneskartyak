# tests/test_game_vote.py
"""Game logic tests — democratic voting mode."""
import pytest
from server.room import delete_room
from server import game as game_logic
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "VOTE"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def setup():
    room, sids = make_room(CODE, n_players=4, voting_mode="vote", round_total=3)
    mgr = FakeManager()
    return room, sids, mgr


async def _start_and_submit_all(room, sids, mgr, *, exclude_czar=False):
    """Helper: start game and have all (or non-czar) players submit."""
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    czar = room.czar_session_id
    submitters = [s for s in sids if s != czar] if exclude_czar else sids
    for sid in submitters:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)
    return czar


# ── cast_vote ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cast_vote_increments_vote_count(setup):
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    mgr.clear()
    await game_logic.cast_vote(room, non_czar[0], non_czar[1], mgr)
    assert len(room.votes) == 1


@pytest.mark.asyncio
async def test_cast_vote_duplicate_ignored(setup):
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    await game_logic.cast_vote(room, non_czar[0], non_czar[1], mgr)
    await game_logic.cast_vote(room, non_czar[0], non_czar[2], mgr)  # second vote same player
    assert len(room.votes) == 1
    assert room.votes[non_czar[0]] == non_czar[1]  # first vote stands


@pytest.mark.asyncio
async def test_all_votes_triggers_tally(setup):
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    # Everyone votes without voting for their own card.
    for voter in sids:
        winner = non_czar[0] if voter != non_czar[0] else non_czar[1]
        await game_logic.cast_vote(room, voter, winner, mgr)

    assert room.players[non_czar[0]].score > 0


@pytest.mark.asyncio
async def test_tally_awards_points_correctly(setup):
    """Winner gets 1.0, runner-up gets 0."""
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    # All eligible votes that are not self-votes go to non_czar[0].
    for voter in sids:
        winner = non_czar[0] if voter != non_czar[0] else non_czar[1]
        await game_logic.cast_vote(room, voter, winner, mgr)

    assert room.players[non_czar[0]].score == 1.0
    for sid in non_czar[1:]:
        assert room.players[sid].score == 0.0


@pytest.mark.asyncio
async def test_tally_tie_splits_point(setup):
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]  # 3 non-czar players

    # 4 voters total (czar + 3 non-czar):
    # non_czar[0] gets 3 votes, non_czar[1] gets 1 vote → [0] wins clearly
    # To make a 2-way tie: 2 vote for [0], 2 vote for [1]
    await game_logic.cast_vote(room, czar,        non_czar[0], mgr)
    await game_logic.cast_vote(room, non_czar[0], non_czar[1], mgr)
    await game_logic.cast_vote(room, non_czar[1], non_czar[0], mgr)
    await game_logic.cast_vote(room, non_czar[2], non_czar[1], mgr)

    # [0] has 2 votes, [1] has 2 votes → tie → 0.5 each
    assert room.players[non_czar[0]].score == 0.5
    assert room.players[non_czar[1]].score == 0.5
    assert room.players[non_czar[2]].score == 0.0


@pytest.mark.asyncio
async def test_vote_in_wrong_phase_ignored(setup):
    """Votes before reveal (phase==submitting) must be ignored."""
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    assert room.phase == "submitting"

    await game_logic.cast_vote(room, sids[0], sids[1], mgr)
    assert len(room.votes) == 0


@pytest.mark.asyncio
async def test_cast_vote_self_ignored_by_default(setup):
    room, sids, mgr = setup
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    await game_logic.cast_vote(room, non_czar[0], non_czar[0], mgr)

    assert room.votes == {}
    assert "vote_cast" not in mgr.events(CODE)


@pytest.mark.asyncio
async def test_cast_vote_self_allowed_when_config_disabled(setup):
    room, sids, mgr = setup
    room.prevent_self_vote = False
    czar = await _start_and_submit_all(room, sids, mgr, exclude_czar=True)
    non_czar = [s for s in sids if s != czar]

    await game_logic.cast_vote(room, non_czar[0], non_czar[0], mgr)

    assert room.votes[non_czar[0]] == non_czar[0]
    assert "vote_cast" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_cast_vote_self_allowed_for_solo_demo():
    room, sids = make_room(CODE, n_players=1, voting_mode="vote", round_total=3)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    sid = sids[0]
    room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    await game_logic.cast_vote(room, sid, sid, mgr)

    assert room.players[sid].score == 1.0
