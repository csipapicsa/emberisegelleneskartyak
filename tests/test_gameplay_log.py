"""Anonymous gameplay log (server/gameplay_log.py, spec: docs/ml/gameplay-logging.md).

The rounds are played through the real WebSocket handler (main._handle_message) and game logic, with
a fake connection manager, and the result is read back from gameplay.db.
"""
from __future__ import annotations

import sqlite3

import pytest

from server import game as game_logic
from server import gameplay_log
from server import main as main_mod
from server.gameplay_log import pack_ids, unpack_ids
from server.room import delete_room
from tests.helpers import FakeManager, make_room

CODE = "GLOG"


def black_cards(n: int = 10, pick: int = 1) -> list[dict]:
    return [{"id": 5000 + i, "text": f"fekete {i} ____", "type": "Black", "pick": pick} for i in range(n)]


def white_cards(n: int = 120) -> list[dict]:
    return [{"id": 1 + i, "text": f"fehér {i}", "type": "White", "pick": 1} for i in range(n)]


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    delete_room(CODE)


@pytest.fixture
def glog(tmp_path):
    """Logging on, writing to a temp gameplay.db. Yields a query helper that waits for the writer."""
    path = tmp_path / "gameplay.db"
    gameplay_log.init(path)
    assert gameplay_log.enabled()

    def query(sql: str, params: tuple = ()) -> list[tuple]:
        gameplay_log._writer.drain()
        conn = sqlite3.connect(path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    query.path = path
    yield query
    gameplay_log.shutdown()


@pytest.fixture
def game(monkeypatch):
    """Start a game; returns (room, sids, manager)."""
    async def start(n_players=3, voting_mode="czar", pick=1, round_total=3, prevent_self_vote=True):
        room, sids = make_room(CODE, n_players=n_players, voting_mode=voting_mode, round_total=round_total,
                               prevent_self_vote=prevent_self_vote)
        room.edition_ids = [4, 1]
        mgr = FakeManager()
        monkeypatch.setattr(main_mod, "manager", mgr)
        await game_logic.start_game(room, black_cards(pick=pick), white_cards(), mgr)
        return room, sids, mgr
    return start


async def submit(room, sid, n=1):
    """Submit the player's first n cards through the real handler; returns the hand and picks."""
    hand = list(room.players[sid].hand)
    picks = hand[:n]
    await main_mod._handle_message(sid, CODE, {"event": "submit_card", "card_ids": [c["id"] for c in picks]})
    return hand, picks


def text_ids(query, cards) -> list[int]:
    return [query("SELECT text_id FROM card_text WHERE card_id = ? AND text = ?", (c["id"], c["text"]))[0][0]
            for c in cards]


# ── varint encoding ──────────────────────────────────────────────────────────

def test_pack_round_trip():
    ids = [0, 1, 127, 128, 255, 16383, 16384, 10571, 2**31]
    assert unpack_ids(pack_ids(ids)) == ids
    assert unpack_ids(b"") == []


def test_pack_is_compact():
    assert len(pack_ids([127])) == 1
    assert len(pack_ids([16383])) == 2
    assert len(pack_ids(range(1000, 1010))) == 20  # a hand of ten realistic IDs


def test_pack_rejects_negative_and_unpack_rejects_truncated():
    with pytest.raises(ValueError):
        pack_ids([-1])
    with pytest.raises(ValueError):
        unpack_ids(b"\x80")


# ── a full czar round ────────────────────────────────────────────────────────

async def test_czar_round_is_logged_completely(glog, game):
    room, sids, mgr = await game()
    czar = room.czar_session_id
    submitted = {sid: await submit(room, sid) for sid in sids}
    shown_sids = [s["session_id"] for s in room._last_submissions]
    winner = next(sid for sid in sids if sid != czar)
    await main_mod._handle_message(czar, CODE, {"event": "czar_pick", "winner_session_id": winner})

    seat = {sid: i for i, sid in enumerate(room.join_order)}
    (game_row,) = glog("SELECT vote_mode, self_vote, rounds, timer, editions FROM game")
    assert game_row == (0, 0, 3, 0, "1,4")
    assert glog("SELECT seat, model_id FROM game_player ORDER BY seat") == [(0, None), (1, None), (2, None)]

    (rnd,) = glog("SELECT round_no, black, czar, outcome, eligible, shown FROM round")
    round_no, black, czar_seat, outcome, eligible, shown = rnd
    assert (round_no, czar_seat, outcome, eligible) == (1, seat[czar], gameplay_log.DECIDED, 1)
    assert glog("SELECT card_id, black, pick FROM card_text WHERE text_id = ?", (black,))[0][1:] == (1, 1)
    assert unpack_ids(shown) == [seat[sid] for sid in shown_sids]

    decisions = {s: (unpack_ids(h), unpack_ids(p)) for s, h, p in glog("SELECT seat, hand, picks FROM decision")}
    for sid, (hand, picks) in submitted.items():
        assert decisions[seat[sid]] == (text_ids(glog, hand), text_ids(glog, picks))
        assert len(decisions[seat[sid]][0]) == 10  # the hand as shown, picked card included

    assert glog("SELECT voter, target FROM vote") == [(seat[czar], seat[winner])]


async def test_card_texts_are_stored_once_and_exactly(glog, game):
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    hand = room.players[sids[1]].hand
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": sids[1]})
    rows = glog("SELECT card_id, text, black FROM card_text")
    assert len(rows) == len({r[0] for r in rows})
    assert (hand[0]["id"], hand[0]["text"], 0) in rows


async def test_edited_card_text_gets_a_new_text_id(glog, game):
    room, sids, mgr = await game()
    card = room.players[sids[0]].hand[0]
    log = room.gameplay
    old = gameplay_log._text_id(log, card)
    new = gameplay_log._text_id(log, {**card, "text": card["text"] + " (javítva)"})
    assert new != old
    assert gameplay_log._text_id(log, dict(card)) == old


# ── vote mode ────────────────────────────────────────────────────────────────

async def test_vote_mode_logs_every_vote_and_eligible_voters(glog, game):
    room, sids, mgr = await game(voting_mode="vote")
    for sid in sids:
        await submit(room, sid)
    # A tie: 0 and 1 vote for each other, 2 votes for 0.
    for voter, target in [(sids[0], sids[1]), (sids[1], sids[0]), (sids[2], sids[0])]:
        await main_mod._handle_message(voter, CODE, {"event": "cast_vote", "winner_session_id": target})

    assert glog("SELECT czar, outcome, eligible FROM round") == [(None, gameplay_log.DECIDED, 3)]
    assert sorted(glog("SELECT voter, target FROM vote")) == [(0, 1), (1, 0), (2, 0)]
    assert glog("SELECT vote_mode FROM game") == [(1,)]


async def test_vote_round_without_votes_is_logged_and_refills_hands(glog, game):
    room, sids, mgr = await game(voting_mode="vote")
    for sid in sids:
        await submit(room, sid)
    await game_logic.tally_votes(room, mgr)  # the vote timer ran out, nobody voted

    assert glog("SELECT outcome FROM round") == [(gameplay_log.NO_DECISION,)]
    assert glog("SELECT COUNT(*) FROM vote") == [(0,)]
    assert all(len(room.players[sid].hand) == 10 for sid in sids)  # used to start the next round with 9
    assert room.round_num == 2


# ── hand changes, multi-pick, withdrawal ─────────────────────────────────────

async def test_swap_and_new_hand_are_logged_in_order(glog, game):
    room, sids, mgr = await game()
    sid = sids[0]
    out_card = room.players[sid].hand[3]
    await main_mod._handle_message(sid, CODE, {"event": "swap_card", "card_id": out_card["id"]})
    in_card = room.players[sid].hand[-1]
    old_hand = list(room.players[sid].hand)
    await main_mod._handle_message(sid, CODE, {"event": "request_new_hand"})
    new_hand = list(room.players[sid].hand)
    for s in sids:
        await submit(room, s)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})

    changes = glog("SELECT seat, seq, removed, added FROM hand_change ORDER BY seq")
    assert [(s, q) for s, q, _, _ in changes] == [(0, 0), (0, 1)]
    assert unpack_ids(changes[0][2]) == text_ids(glog, [out_card])
    assert unpack_ids(changes[0][3]) == text_ids(glog, [in_card])
    assert unpack_ids(changes[1][2]) == text_ids(glog, old_hand)
    assert unpack_ids(changes[1][3]) == text_ids(glog, new_hand)


async def test_multi_pick_keeps_blank_order(glog, game):
    room, sids, mgr = await game(pick=2)
    hand = list(room.players[sids[0]].hand)
    order = [hand[5], hand[2]]  # the second card fills the first blank
    await main_mod._handle_message(sids[0], CODE, {"event": "submit_card", "card_ids": [c["id"] for c in order]})
    for sid in sids[1:]:
        await submit(room, sid, n=2)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})

    (picks,) = glog("SELECT picks FROM decision WHERE seat = 0")[0]
    assert unpack_ids(picks) == text_ids(glog, order)
    assert glog("SELECT pick FROM card_text WHERE black = 1") == [(2,)]


async def test_withdrawn_then_resubmitted_logs_the_final_choice(glog, game):
    room, sids, mgr = await game()
    sid = sids[0]
    first = room.players[sid].hand[0]
    await main_mod._handle_message(sid, CODE, {"event": "submit_card", "card_ids": [first["id"]]})
    await main_mod._handle_message(sid, CODE, {"event": "withdraw_submission"})
    hand_now = list(room.players[sid].hand)  # the withdrawn card is back, at the end
    second = hand_now[0]
    await main_mod._handle_message(sid, CODE, {"event": "submit_card", "card_ids": [second["id"]]})
    for s in sids[1:]:
        await submit(room, s)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})

    (hand, picks) = glog("SELECT hand, picks FROM decision WHERE seat = 0")[0]
    assert unpack_ids(picks) == text_ids(glog, [second])
    assert unpack_ids(hand) == text_ids(glog, hand_now)


async def test_withdrawn_and_not_resubmitted_counts_as_no_submission(glog, game):
    room, sids, mgr = await game(n_players=4)
    idle = sids[3]  # not the czar (seat 0), so the round still gets decided
    hand = list(room.players[idle].hand)
    await submit(room, idle)
    await main_mod._handle_message(idle, CODE, {"event": "withdraw_submission"})
    for s in sids[:3]:
        await submit(room, s)
    await game_logic.reveal_cards(room, mgr)  # timer ran out: the idle player is kicked
    assert idle not in room.players
    await main_mod._handle_message(room.czar_session_id, CODE, {"event": "czar_pick", "winner_session_id": sids[1]})

    (row,) = glog("SELECT hand, picks FROM decision WHERE seat = 3")
    assert row[1] is None
    assert sorted(unpack_ids(row[0])) == sorted(text_ids(glog, hand))  # kicked, but the hand is logged


# ── rounds that end without a decision ───────────────────────────────────────

async def test_nobody_submitted(glog, game):
    room, sids, mgr = await game()
    await game_logic.reveal_cards(room, mgr)  # timer ran out
    assert glog("SELECT outcome FROM round") == [(gameplay_log.NO_SUBMISSIONS,)]
    rows = glog("SELECT seat, hand, picks FROM decision ORDER BY seat")
    assert [(s, p) for s, _, p in rows] == [(0, None), (1, None), (2, None)]
    assert all(len(unpack_ids(h)) == 10 for _, h, _ in rows)


async def test_czar_did_not_pick(glog, game):
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    await game_logic.skip_revealed_round(room, mgr)  # czar timer ran out
    assert glog("SELECT outcome FROM round") == [(gameplay_log.NO_DECISION,)]
    assert glog("SELECT COUNT(*) FROM decision WHERE picks IS NOT NULL") == [(3,)]


async def test_room_closed_mid_round(glog, game):
    room, sids, mgr = await game()
    await submit(room, sids[0])
    main_mod._delete_room_and_chat(CODE)
    assert glog("SELECT outcome FROM round") == [(gameplay_log.CLOSED,)]
    assert glog("SELECT COUNT(*) FROM decision WHERE picks IS NOT NULL") == [(1,)]


async def test_finished_game_logs_no_extra_round_on_close(glog, game):
    room, sids, mgr = await game(round_total=1)
    for sid in sids:
        await submit(room, sid)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    assert room.finished
    main_mod._delete_room_and_chat(CODE)
    assert glog("SELECT round_no, outcome FROM round") == [(1, gameplay_log.DECIDED)]


async def test_each_round_is_logged_once(glog, game, monkeypatch):
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    batches = []
    real_submit = gameplay_log._writer.submit
    monkeypatch.setattr(gameplay_log._writer, "submit", lambda b: (batches.append(b), real_submit(b)))
    gameplay_log.round_ended(room, gameplay_log.NO_DECISION)
    gameplay_log.round_ended(room, gameplay_log.CLOSED)
    assert len(batches) == 1
    assert glog("SELECT outcome FROM round") == [(gameplay_log.NO_DECISION,)]


async def test_several_rounds_and_games_get_their_own_ids(glog, game):
    for _ in range(2):
        room, sids, mgr = await game(round_total=2)
        for _round in range(2):
            for sid in sids:
                await submit(room, sid)
            await main_mod._handle_message(room.czar_session_id, CODE,
                                           {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
        delete_room(CODE)
    assert glog("SELECT game_id, round_no FROM round ORDER BY 1, 2") == [(1, 1), (1, 2), (2, 1), (2, 2)]


# ── anonymity ────────────────────────────────────────────────────────────────

async def test_no_session_ids_or_usernames_are_stored(glog, game):
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    gameplay_log._writer.drain()
    raw = glog.path.read_bytes()
    for sid, player in room.players.items():
        assert sid.encode() not in raw
        assert player.username.encode() not in raw


# ── robustness ───────────────────────────────────────────────────────────────

def test_app_startup_turns_logging_on_and_shutdown_off(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EEK_DISABLE_ROOM_CLEANUP", "1")  # its loop would spin with the instant asyncio.sleep

    assert not gameplay_log.enabled()
    with TestClient(main_mod.app):
        assert gameplay_log.enabled()
        assert gameplay_log.DB_PATH == tmp_path / "gameplay.db"  # the test fixture's path, not the data dir
        assert gameplay_log.DB_PATH.exists()
    assert not gameplay_log.enabled()


async def test_off_by_default_and_with_the_kill_switch(tmp_path, monkeypatch, game):
    assert not gameplay_log.enabled()  # init() only runs at app startup
    monkeypatch.setenv("EEK_GAMEPLAY_LOG", "0")
    gameplay_log.init(tmp_path / "off.db")
    assert not gameplay_log.enabled()
    assert not (tmp_path / "off.db").exists()
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    assert room.phase == "revealed"


async def test_a_failing_write_loses_one_round_not_the_game(glog, game, monkeypatch):
    room, sids, mgr = await game()
    real_write = gameplay_log._Writer._write
    calls = []

    def flaky(self, conn, batch):
        calls.append(1)
        if len(calls) == 1:  # fail mid-transaction, after the card texts went in: all of it rolls back
            batch = {**batch, "votes": [("not", "enough", "columns")]}
        real_write(self, conn, batch)

    monkeypatch.setattr(gameplay_log._Writer, "_write", flaky)
    for _round in range(2):
        for sid in sids:
            await submit(room, sid)
        await main_mod._handle_message(room.czar_session_id, CODE,
                                       {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    assert room.round_num == 3  # the game went on
    assert glog("SELECT round_no FROM round") == [(2,)]
    # Round 2's batch brought the game, seats and every card text it needs, although round 1's were lost.
    assert glog("SELECT COUNT(*) FROM game") == [(1,)]
    known = {r[0] for r in glog("SELECT text_id FROM card_text")}
    used = {glog("SELECT black FROM round")[0][0]}
    for hand, picks in glog("SELECT hand, picks FROM decision"):
        used.update(unpack_ids(hand), unpack_ids(picks))
    assert used <= known


async def test_a_failing_hook_never_reaches_the_game(glog, game, monkeypatch):
    room, sids, mgr = await game()

    def broken(*args, **kwargs):
        raise RuntimeError("bug in the logger")

    monkeypatch.setattr(gameplay_log, "pack_ids", broken)
    for sid in sids:
        await submit(room, sid)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    assert room.round_num == 2


async def test_ids_continue_after_a_restart(glog, game):
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    texts_before = glog("SELECT COUNT(*) FROM card_text")[0][0]
    gameplay_log.shutdown()
    delete_room(CODE)

    gameplay_log.init(glog.path)  # the app restarts
    room, sids, mgr = await game()
    for sid in sids:
        await submit(room, sid)
    await main_mod._handle_message(room.czar_session_id, CODE,
                                   {"event": "czar_pick", "winner_session_id": next(s for s in sids if s != room.czar_session_id)})
    assert glog("SELECT game_id FROM game ORDER BY 1") == [(1,), (2,)]
    rows = glog("SELECT card_id, text FROM card_text")
    assert len(rows) == len(set(rows)) >= texts_before  # known texts kept their IDs, no duplicates
