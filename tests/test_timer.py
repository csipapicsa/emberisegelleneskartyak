# tests/test_timer.py
"""
Timer lifecycle tests.
Key invariants:
  - only one timer task is stored per round (old one cancelled)
  - stale timer from old round ignores current room state
  - timer expiry triggers reveal_cards (submitting) or tally_votes (revealed+vote)
"""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from server.room import delete_room
from server import game as game_logic
from tests.helpers import FakeManager, make_room, make_white_cards, make_black_cards


CODE = "TMR"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


# ── Timer task is stored and cancelled on new round ───────────────────────────

@pytest.mark.asyncio
async def test_timer_task_stored_on_room():
    """start_round stores a task reference in room.timer_task."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=60)
    mgr = FakeManager()

    async def never_sleep(n):
        await asyncio.sleep(9999)  # keep task alive

    with patch("server.game.asyncio.sleep", side_effect=never_sleep):
        await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
        assert room.timer_task is not None
        assert not room.timer_task.done()
        room.timer_task.cancel()
        try:
            await room.timer_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_new_round_cancels_old_timer():
    """Starting a new round must cancel the previous timer task."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=60)
    mgr = FakeManager()

    # Park the timer task on an asyncio.Event so cancellation is delivered instantly
    parked = asyncio.Event()

    async def park_sleep(n):
        await parked.wait()  # yields; responds to CancelledError immediately

    with patch("server.game.asyncio.sleep", side_effect=park_sleep):
        await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
        first_task = room.timer_task

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    for sid in non_czar:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    # czar_pick → _end_round → _cancel_timer(room) → start_round creates new task
    await game_logic.czar_pick(room, non_czar[0], mgr)

    # Directly await the cancelled task — this delivers the CancelledError and marks it done
    try:
        await first_task
    except asyncio.CancelledError:
        pass

    assert first_task.done()
    assert first_task.cancelled()


# ── Stale timer is ignored ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_timer_ignored_after_round_advance():
    """
    A _timer_task created for round N must exit immediately if
    room.round_num has already moved to N+1.
    """
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=5, timer_seconds=2)
    mgr = FakeManager()

    # Start game; timer_seconds=2 so after 2 ticks the timer fires
    # We capture round_num before advancing
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    stale_round = room.round_num

    # Advance the room to a new round manually
    room.round_num += 1
    room.phase = "submitting"

    mgr.clear()
    # Run stale timer with the OLD round num — it should exit without broadcasting
    ticks = []

    async def counting_sleep(n):
        ticks.append(n)

    with patch("server.game.asyncio.sleep", side_effect=counting_sleep):
        await game_logic._timer_task(room, stale_round, mgr)

    # Timer should exit after first sleep because round_num mismatch
    assert ticks == [1]  # slept once, then detected mismatch and returned
    reveals = [e for e in mgr.events(CODE) if e == "cards_revealed"]
    assert len(reveals) == 0


# ── Timer triggers reveal on expiry ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_timer_triggers_reveal_on_expiry_no_submissions():
    """Timer at 0 with no submissions skips the round — nobody gets kicked."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=2)
    mgr = FakeManager()

    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    round_num = room.round_num

    async def instant(n):
        pass

    mgr.clear()
    with patch("server.game.asyncio.sleep", side_effect=instant):
        await game_logic._timer_task(room, round_num, mgr)

    events = mgr.events(CODE)
    assert "round_skipped" in events
    assert "round_start" in events
    assert "player_kicked" not in events
    assert "game_aborted" not in events
    assert room.finished is False


@pytest.mark.asyncio
async def test_timer_triggers_reveal_when_submissions_exist():
    """Timer at 0 with at least one submission → cards_revealed broadcast."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=2)
    mgr = FakeManager()

    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    round_num = room.round_num

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    room.players[czar].submitted_cards = [room.players[czar].hand.pop()]
    room.players[non_czar[0]].submitted_cards = [room.players[non_czar[0]].hand.pop()]

    mgr.clear()

    async def instant(n):
        pass

    with patch("server.game.asyncio.sleep", side_effect=instant):
        await game_logic._timer_task(room, round_num, mgr)

    assert "cards_revealed" in mgr.events(CODE)
