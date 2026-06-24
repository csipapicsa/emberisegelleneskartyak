# tests/test_game_czar.py
"""Game logic tests — czar voting mode."""
import pytest
from server.room import delete_room
from server import game as game_logic
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "CZAR"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def setup():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3)
    mgr = FakeManager()
    return room, sids, mgr


def submit_one_from_each(room, sids):
    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]


# ── start_game ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_game_broadcasts_game_started(setup):
    room, sids, mgr = setup
    black = make_black_cards(10)
    white = make_white_cards(60)
    await game_logic.start_game(room, black, white, mgr)

    assert "game_started" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_start_game_deals_10_cards_each(setup):
    room, sids, mgr = setup
    black = make_black_cards(10)
    white = make_white_cards(60)
    await game_logic.start_game(room, black, white, mgr)

    for sid in sids:
        assert len(room.players[sid].hand) == 10


@pytest.mark.asyncio
async def test_start_game_enters_submitting_phase(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)
    assert room.phase == "submitting"


@pytest.mark.asyncio
async def test_start_game_sets_round_num_to_1(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)
    assert room.round_num == 1


# ── submit + reveal ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reveal_cards_changes_phase_to_revealed(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    submit_one_from_each(room, sids)

    await game_logic.reveal_cards(room, mgr)
    assert room.phase == "revealed"


@pytest.mark.asyncio
async def test_reveal_cards_broadcasts_cards_revealed(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    submit_one_from_each(room, sids)

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)
    assert "cards_revealed" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_reveal_cards_is_idempotent(setup):
    """Calling reveal_cards twice must not double-broadcast."""
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    submit_one_from_each(room, sids)

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)
    await game_logic.reveal_cards(room, mgr)  # second call must be no-op

    revealed = [e for e in mgr.events(CODE) if e == "cards_revealed"]
    assert len(revealed) == 1


@pytest.mark.asyncio
async def test_reveal_skips_round_when_no_submissions(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)
    # No connected player submitted. Disconnected players are left alone and
    # may reconnect later, so this still exercises the round-skip branch.
    for sid in sids:
        room.players[sid].connected = False
    mgr.clear()
    await game_logic.reveal_cards(room, mgr)
    assert "round_skipped" in mgr.events(CODE)
    assert room.round_num == 2  # advanced to next round


# ── czar_pick ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_czar_pick_awards_point(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]

    submit_one_from_each(room, sids)
    await game_logic.reveal_cards(room, mgr)

    winner = non_czar[0]
    mgr.clear()
    await game_logic.czar_pick(room, winner, mgr)

    assert room.players[winner].score == 1.0


@pytest.mark.asyncio
async def test_czar_pick_advances_round(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    submit_one_from_each(room, sids)
    await game_logic.reveal_cards(room, mgr)

    await game_logic.czar_pick(room, non_czar[0], mgr)
    assert room.round_num == 2


@pytest.mark.asyncio
async def test_czar_pick_self_ignored_when_prevent_self_vote_enabled():
    """Czar cannot pick their own submission when prevent_self_vote is True."""
    room, sids = make_room(CODE, n_players=2, voting_mode="czar", prevent_self_vote=True)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    submit_one_from_each(room, sids)
    await game_logic.reveal_cards(room, mgr)

    mgr.clear()
    await game_logic.czar_pick(room, czar, mgr)

    assert room.players[czar].score == 0.0
    assert room.round_num == 1  # round not advanced


@pytest.mark.asyncio
async def test_czar_pick_self_allowed_when_prevent_self_vote_disabled():
    """Czar can pick their own submission when prevent_self_vote is False."""
    room, sids = make_room(CODE, n_players=2, voting_mode="czar", prevent_self_vote=False)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    submit_one_from_each(room, sids)
    await game_logic.reveal_cards(room, mgr)

    mgr.clear()
    await game_logic.czar_pick(room, czar, mgr)

    assert room.players[czar].score == 1.0
    assert room.round_num == 2  # round advanced normally


@pytest.mark.asyncio
async def test_czar_pick_ignored_before_reveal(setup):
    """czar_pick while phase == 'submitting' must do nothing."""
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    # Don't reveal — phase is still "submitting"
    mgr.clear()
    await game_logic.czar_pick(room, non_czar[0], mgr)

    # Score unchanged, round not advanced
    assert room.players[non_czar[0]].score == 0.0
    assert room.round_num == 1


@pytest.mark.asyncio
async def test_round_end_replenishes_hand(setup):
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(60), mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    submit_one_from_each(room, sids)
    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, non_czar[0], mgr)

    for sid in sids:
        assert len(room.players[sid].hand) == 10


# ── game_end ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_game_ends_after_round_total(setup):
    """Play through all rounds and verify game_end is broadcast."""
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    for _ in range(room.round_total):
        if room.finished:
            break
        czar = room.czar_session_id
        non_czar = [s for s in sids if s != czar]
        for sid in sids:
            if not room.players[sid].submitted_cards:
                room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
        await game_logic.reveal_cards(room, mgr)
        if room.phase == "revealed":
            await game_logic.czar_pick(room, non_czar[0], mgr)

    assert room.finished is True
    assert "game_end" in mgr.events(CODE)


@pytest.mark.asyncio
async def test_no_action_after_game_end(setup):
    """Submitting or picking after game_end must be silently ignored."""
    room, sids, mgr = setup
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    # Exhaust all rounds
    for _ in range(room.round_total):
        if room.finished:
            break
        czar = room.czar_session_id
        non_czar = [s for s in sids if s != czar]
        for sid in sids:
            if not room.players[sid].submitted_cards:
                room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
        await game_logic.reveal_cards(room, mgr)
        if room.phase == "revealed":
            await game_logic.czar_pick(room, non_czar[0], mgr)

    round_after = room.round_num
    mgr.clear()
    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, sids[0], mgr)
    assert room.round_num == round_after  # no advancement
