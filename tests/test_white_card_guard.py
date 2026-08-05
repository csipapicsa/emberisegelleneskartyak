"""Universal white-card guard: a room needs at least
CONFIG["min_white_cards_per_player"] (10) white cards per seated player
across its selected decks before it can start. Replaces the old rule that
only forbade starting with the "Ti küldtétek" community edition alone."""
from __future__ import annotations

import pytest

from server import main as main_mod
from server.messages_db import COMMUNITY_EDITION_ID
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "GRD"


class _FakeConn:
    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    from server.room import delete_room
    delete_room(CODE)


def _patch_cards(monkeypatch, real_white_count: int, real_black_count: int = 5):
    """Make get_cards_for_editions() return canned cards without touching a DB."""
    monkeypatch.setattr(main_mod, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        main_mod,
        "get_cards_for_editions",
        lambda conn, edition_ids: (make_black_cards(real_black_count), make_white_cards(real_white_count)),
    )


@pytest.mark.asyncio
async def test_start_game_blocked_when_white_cards_below_ten_per_player(monkeypatch):
    room, sids = make_room(CODE, n_players=3)
    room.edition_ids = [1]
    _patch_cards(monkeypatch, real_white_count=29)  # need 10 * 3 = 30
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("sid_0", CODE, {"event": "start_game"})

    assert room.started is False
    last = mgr.sent_to(CODE, "sid_0")[-1]
    assert last["event"] == "error"
    assert "válassz még egy paklit" in last["message"].lower()


@pytest.mark.asyncio
async def test_start_game_allowed_when_white_cards_meet_ten_per_player(monkeypatch):
    room, sids = make_room(CODE, n_players=3)
    room.edition_ids = [1]
    _patch_cards(monkeypatch, real_white_count=30)  # exactly 10 * 3
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("sid_0", CODE, {"event": "start_game"})

    assert room.started is True


@pytest.mark.asyncio
async def test_start_game_counts_community_cards_toward_the_threshold(monkeypatch):
    """The community edition can now be combined with a small deck as long as
    the *combined* white-card count clears the threshold — no more special
    "can't be played alone" rule."""
    room, sids = make_room(CODE, n_players=2)
    room.edition_ids = [1, COMMUNITY_EDITION_ID]
    _patch_cards(monkeypatch, real_white_count=4)  # far below 10 * 2 = 20 on its own
    monkeypatch.setattr(main_mod, "get_msg_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        main_mod,
        "get_community_cards",
        lambda conn: (make_black_cards(5), make_white_cards(20)),
    )
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("sid_0", CODE, {"event": "start_game"})

    assert room.started is True


@pytest.mark.asyncio
async def test_start_game_blocked_when_only_community_selected_and_too_small(monkeypatch):
    """The community edition alone is fine now *if* it clears the threshold,
    but still blocked when it doesn't — the guard is about card counts, not
    about which edition is selected."""
    room, sids = make_room(CODE, n_players=2)
    room.edition_ids = [COMMUNITY_EDITION_ID]
    _patch_cards(monkeypatch, real_white_count=0)
    monkeypatch.setattr(main_mod, "get_msg_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        main_mod,
        "get_community_cards",
        lambda conn: (make_black_cards(5), make_white_cards(10)),  # below 10 * 2 = 20
    )
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("sid_0", CODE, {"event": "start_game"})

    assert room.started is False
    last = mgr.sent_to(CODE, "sid_0")[-1]
    assert last["event"] == "error"


@pytest.mark.asyncio
async def test_start_game_only_community_selected_and_large_enough_now_allowed(monkeypatch):
    """Previously this exact scenario (only the community edition selected)
    was always rejected regardless of card count. Now it's allowed once the
    universal white-card guard is satisfied."""
    room, sids = make_room(CODE, n_players=1)
    room.edition_ids = [COMMUNITY_EDITION_ID]
    _patch_cards(monkeypatch, real_white_count=0)
    monkeypatch.setattr(main_mod, "get_msg_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        main_mod,
        "get_community_cards",
        lambda conn: (make_black_cards(5), make_white_cards(10)),  # exactly 10 * 1
    )
    mgr = FakeManager()
    monkeypatch.setattr(main_mod, "manager", mgr)

    await main_mod._handle_message("sid_0", CODE, {"event": "start_game"})

    assert room.started is True
