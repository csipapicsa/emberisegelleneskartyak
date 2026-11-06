"""Bug 7: entering the `revealed` phase starts a fresh timer for voting or
czar selection."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from server import game as game_logic
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "VTM"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_vote_mode_starts_new_timer_on_reveal():
    """A new timer_task is created when reveal_cards transitions into vote-mode revealed phase."""
    room, sids = make_room(CODE, n_players=3, voting_mode="vote", round_total=3, timer_seconds=30)
    mgr = FakeManager()

    parked = asyncio.Event()

    async def park_sleep(_n):
        await parked.wait()

    with patch("server.game.asyncio.sleep", side_effect=park_sleep):
        await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
        submit_timer = room.timer_task
        assert submit_timer is not None

        # Everyone submits
        for sid in sids:
            room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

        await game_logic.reveal_cards(room, mgr)

        vote_timer = room.timer_task
        # New timer object — submit-phase timer was replaced
        assert vote_timer is not None
        assert vote_timer is not submit_timer
        # Old timer was cancelled
        try:
            await submit_timer
        except asyncio.CancelledError:
            pass
        assert submit_timer.cancelled()

        # Clean up parked vote timer
        vote_timer.cancel()
        try:
            await vote_timer
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_czar_mode_starts_new_timer_on_reveal():
    """Czar selection also gets a fresh revealed-phase timer."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=30)
    mgr = FakeManager()

    parked = asyncio.Event()

    async def park_sleep(_n):
        await parked.wait()

    with patch("server.game.asyncio.sleep", side_effect=park_sleep):
        await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
        submit_timer = room.timer_task

        for sid in sids:
            room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

        await game_logic.reveal_cards(room, mgr)

        pick_timer = room.timer_task
        assert pick_timer is not None
        assert pick_timer is not submit_timer

        try:
            await submit_timer
        except asyncio.CancelledError:
            pass
        assert submit_timer.cancelled()

        pick_timer.cancel()
        try:
            await pick_timer
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_cards_revealed_includes_timer_seconds():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=45)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)

    revealed = next(b for b in mgr.broadcasts(CODE) if b["event"] == "cards_revealed")
    assert revealed["timer_seconds"] == 45


@pytest.mark.asyncio
async def test_czar_timer_expiry_skips_revealed_round_and_refills_hands():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=1)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)

    round_num = room.round_num
    mgr.clear()

    async def instant(_n):
        pass

    with patch("server.game.asyncio.sleep", side_effect=instant):
        await game_logic._timer_task(room, round_num, mgr)

    assert "round_skipped" in mgr.events(CODE)
    assert room.round_num == 2
    assert room.phase == "submitting"
    for sid in sids:
        assert len(room.players[sid].hand) == 10


@pytest.mark.asyncio
async def test_vote_mode_no_timer_when_timer_seconds_zero():
    """timer_seconds=0 ⇒ no timer in either phase."""
    room, sids = make_room(CODE, n_players=3, voting_mode="vote", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)
    assert room.timer_task is None

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]
    await game_logic.reveal_cards(room, mgr)
    assert room.timer_task is None
