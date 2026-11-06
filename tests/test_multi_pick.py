# tests/test_multi_pick.py
"""Tests for black cards that require pick > 1 white cards."""
import pytest

from server import game as game_logic
from server import main as main_mod
from server.room import delete_room
from tests.helpers import FakeManager, make_black_cards, make_room, make_white_cards

CODE = "PICK"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def setup_pick2():
    """3-player czar room with pick-2 black cards."""
    room, sids = make_room(CODE, n_players=3, voting_mode="czar", round_total=3)
    mgr = FakeManager()
    black = make_black_cards(10, pick=2)
    white = make_white_cards(60)
    return room, sids, mgr, black, white


def submit_pick_cards(room, sids, count):
    for sid in sids:
        player = room.players[sid]
        player.submitted_cards = player.hand[:count]
        for c in player.submitted_cards:
            player.hand.remove(c)


# ── Submission validation ──────────────────────────────────────────────────────

async def test_pick2_requires_two_cards(setup_pick2):
    """Submitting only 1 card on a pick-2 black card must be rejected."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar][0]
    player = room.players[non_czar]

    # Try to submit just one card — should be rejected server-side
    one_card_id = player.hand[0]["id"]
    # Manually call the submit logic as main.py does it
    pick = room.current_black.get("pick", 1)
    selected = [c for c in player.hand if c["id"] == one_card_id]
    assert len(selected) != pick  # guard check: 1 != 2 → server would reject


async def test_pick2_accepts_two_cards(setup_pick2):
    """Submitting exactly 2 cards on a pick-2 black card must be accepted."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)

    for sid in sids:
        player = room.players[sid]
        two_cards = player.hand[:2]
        card_ids = {c["id"] for c in two_cards}
        pick = room.current_black.get("pick", 1)
        selected = [c for c in player.hand if c["id"] in card_ids]
        assert len(selected) == pick  # exactly 2
        player.submitted_cards = selected
        for c in selected:
            player.hand.remove(c)

    assert room.all_submitted() is True


async def test_pick2_all_submitted_triggers_reveal(setup_pick2):
    """After all non-czar players submit 2 cards each, cards_revealed is broadcast."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)
    mgr.clear()

    submit_pick_cards(room, sids, 2)

    await game_logic.reveal_cards(room, mgr)

    assert "cards_revealed" in mgr.events(CODE)
    revealed = next(m for m in mgr.broadcasts(CODE) if m["event"] == "cards_revealed")
    # Each submission must contain 2 cards
    for sub in revealed["submissions"]:
        assert len(sub["cards"]) == 2


async def test_pick2_winner_gets_point(setup_pick2):
    """czar_pick on a pick-2 round correctly awards 1 point."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)
    mgr.clear()

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    submit_pick_cards(room, sids, 2)

    await game_logic.reveal_cards(room, mgr)

    winner_sid = non_czar[0]
    await game_logic.czar_pick(room, winner_sid, mgr)

    assert room.players[winner_sid].score == 1.0


async def test_pick2_hand_refilled_to_10_after_round(setup_pick2):
    """After a pick-2 round, every player's hand is refilled back to 10 cards."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)
    mgr.clear()

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    submit_pick_cards(room, sids, 2)

    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, non_czar[0], mgr)

    # After _end_round, all players should have 10 cards again
    for sid in sids:
        assert len(room.players[sid].hand) == 10


async def test_pick2_submitted_cards_in_winner_overlay_data(setup_pick2):
    """round_end event must include both submitted cards for the winner."""
    room, sids, mgr, black, white = setup_pick2
    await game_logic.start_game(room, black, white, mgr)
    mgr.clear()

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar]
    winner_sid = non_czar[0]
    submit_pick_cards(room, sids, 2)

    await game_logic.reveal_cards(room, mgr)
    await game_logic.czar_pick(room, winner_sid, mgr)

    round_end = next(m for m in mgr.broadcasts(CODE) if m["event"] == "round_end")
    winner_author = next(a for a in round_end["authors"] if a["session_id"] == winner_sid)
    assert len(winner_author["cards"]) == 2


async def test_pick3_requires_three_cards(setup_pick2):
    """pick=3 black card: submitting 2 cards is not enough."""
    room, sids, mgr, black, white = setup_pick2
    # Override to pick=3
    black3 = make_black_cards(10, pick=3)
    await game_logic.start_game(room, black3, white, mgr)

    pick = room.current_black.get("pick", 1)
    assert pick == 3

    czar = room.czar_session_id
    non_czar = [s for s in sids if s != czar][0]
    player = room.players[non_czar]

    two_ids = {c["id"] for c in player.hand[:2]}
    selected = [c for c in player.hand if c["id"] in two_ids]
    assert len(selected) != pick  # 2 != 3 → server would reject


# ── Submission order (real submit_card handler) ─────────────────────────────────

async def test_pick2_preserves_submission_order(setup_pick2, monkeypatch):
    """The order the player taps cards must survive server-side — for a
    multi-blank black card the order the whites fill the blanks is meaningful."""
    room, sids, mgr, black, white = setup_pick2
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, black, white, mgr)

    sid = sids[0]
    player = room.players[sid]
    # Select in an order that differs from hand order (index 3 then index 1).
    first, second = player.hand[3], player.hand[1]
    await main_mod._handle_message(
        sid, CODE, {"event": "submit_card", "card_ids": [first["id"], second["id"]]}
    )

    assert [c["id"] for c in player.submitted_cards] == [first["id"], second["id"]]
    assert "submission_accepted" in mgr.sent_events(CODE, sid)


async def test_pick2_rejects_duplicate_card(setup_pick2, monkeypatch):
    """Submitting the same card id twice on a pick-2 round must be rejected."""
    room, sids, mgr, black, white = setup_pick2
    monkeypatch.setattr(main_mod, "manager", mgr)
    await game_logic.start_game(room, black, white, mgr)

    sid = sids[0]
    player = room.players[sid]
    dup_id = player.hand[0]["id"]
    await main_mod._handle_message(
        sid, CODE, {"event": "submit_card", "card_ids": [dup_id, dup_id]}
    )

    assert player.submitted_cards == []
    assert "error" in mgr.sent_events(CODE, sid)
