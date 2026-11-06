"""Bug 6: A connected player who fails to submit during a round is kicked
when the submitting phase ends (i.e. inside `reveal_cards`).
Standalone (1-player) rooms are exempt."""
from __future__ import annotations

import pytest

from server import game as game_logic
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "KCK"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.mark.asyncio
async def test_idle_player_kicked_after_one_round():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    czar = room.czar_session_id
    # czar submits, one other submits, the third is idle
    submitters = [s for s in sids if s != czar][:1] + [czar]
    idle = [s for s in sids if s not in submitters][0]
    for sid in submitters:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)

    # The idle player should be gone from the room
    assert idle not in room.players
    assert idle not in room.join_order

    # Both a per-session `kicked` and a broadcast `player_kicked` must fire
    assert "kicked" in mgr.sent_events(CODE, idle)
    kick_broadcasts = [b for b in mgr.broadcasts(CODE) if b["event"] == "player_kicked"]
    assert len(kick_broadcasts) == 1
    assert kick_broadcasts[0]["session_id"] == idle
    assert kick_broadcasts[0]["reason"] == "inactive"


@pytest.mark.asyncio
async def test_submitter_missed_rounds_reset():
    """A player who submits has their missed_rounds counter reset."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    # Inflate counter artificially
    for sid in sids:
        room.players[sid].missed_rounds = 5

    for sid in sids:
        room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

    await game_logic.reveal_cards(room, mgr)

    for sid in sids:
        assert room.players[sid].missed_rounds == 0


@pytest.mark.asyncio
async def test_disconnected_players_not_kicked():
    """Disconnected players are not kicked — they might reconnect."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    czar = room.czar_session_id
    # One non-czar disconnects, one submits
    non_czar = [s for s in sids if s != czar]
    room.players[non_czar[0]].connected = False
    room.players[non_czar[1]].submitted_cards = [room.players[non_czar[1]].hand.pop()]
    room.players[czar].submitted_cards = [room.players[czar].hand.pop()]

    await game_logic.reveal_cards(room, mgr)

    # Disconnected player must remain (reconnect window)
    assert non_czar[0] in room.players


@pytest.mark.asyncio
async def test_standalone_player_not_kicked():
    """A solo player should never be auto-kicked — they're browsing."""
    room, sids = make_room(CODE, n_players=1, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    solo = sids[0]
    # Don't submit anything
    await game_logic.reveal_cards(room, mgr)

    assert solo in room.players
    assert not any(b["event"] == "player_kicked" for b in mgr.broadcasts(CODE))


@pytest.mark.asyncio
async def test_no_submissions_skips_round_without_kicking():
    """When nobody submits, the round is skipped — players are not kicked."""
    room, sids = make_room(CODE, n_players=2, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    # Neither player submits
    mgr.clear()
    await game_logic.reveal_cards(room, mgr)

    events = mgr.events(CODE)
    assert "round_skipped" in events
    assert "round_start" in events
    assert "player_kicked" not in events
    assert "game_aborted" not in events
    assert room.finished is False
    assert len(room.players) == 2


@pytest.mark.asyncio
async def test_kicked_admin_triggers_promotion():
    """If the admin is the one kicked, the next player is promoted."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    admin_sid = room.admin_session_id
    # Admin doesn't submit, others do
    for sid in sids:
        if sid != admin_sid:
            room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)

    assert admin_sid not in room.players
    assert room.admin_session_id != admin_sid
    assert any(b["event"] == "admin_promoted" for b in mgr.broadcasts(CODE))


@pytest.mark.asyncio
async def test_kicked_czar_skips_round_instead_of_revealing():
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3, timer_seconds=0)
    mgr = FakeManager()
    await game_logic.start_game(room, make_black_cards(10), make_white_cards(120), mgr)

    czar = room.czar_session_id
    for sid in sids:
        if sid != czar:
            room.players[sid].submitted_cards = [room.players[sid].hand.pop()]

    mgr.clear()
    await game_logic.reveal_cards(room, mgr)

    events = mgr.events(CODE)
    assert "player_kicked" in events
    assert "round_skipped" in events
    assert "cards_revealed" not in events
    assert czar not in room.players
    assert room.round_num == 2
    assert room.phase == "submitting"
    for sid in sids:
        if sid in room.players:
            assert len(room.players[sid].hand) == 10
