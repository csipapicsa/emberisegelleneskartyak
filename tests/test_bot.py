"""Computer players (server/bot.py): picking, new hands, judging, the deadline and the random fallback, the
lobby events, and the gameplay log. The model service is faked: either bot._post is replaced, or a tiny HTTP
server answers on a real port."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server import bot, gameplay_log
from server import game as game_logic
from server import main as main_mod
from server.gameplay_log import unpack_ids, unpack_probs
from server.room import delete_room
from tests.helpers import FakeManager, make_room

CODE = "BOT1"
URL = "http://model.test"


def black_cards(n: int = 10, pick: int = 1) -> list[dict]:
    return [{"id": 5000 + i, "text": f"fekete {i} ____", "pick": pick} for i in range(n)]


def white_cards(n: int = 200) -> list[dict]:
    return [{"id": 1 + i, "text": f"fehér {1 + i}", "pick": 1} for i in range(n)]


def card_no(text: str) -> int:
    return int(text.split()[-1])


class FakeService:
    """Stands in for bot._post: scores a card by its number (higher is better) unless told otherwise."""

    def __init__(self, score=None, refresh_below=None, delay=0.0, fail=False, version="7"):
        self.score = score or (lambda black, text: float(card_no(text)))
        self.refresh_below, self.delay, self.fail, self.version = refresh_below, delay, fail, version
        self.calls: list[dict] = []

    def __call__(self, url, body, timeout):
        self.calls.append(body)
        if self.delay:
            time.sleep(min(self.delay, timeout + 0.2))
        if self.fail:
            raise ConnectionRefusedError("service down")
        scores = [self.score(body["black"], t) for t in body["cards"]]
        total = sum(2.718 ** (s - max(scores)) for s in scores)
        cutoff = self.refresh_below(body) if callable(self.refresh_below) else self.refresh_below
        return {"model": "pick-model", "version": self.version, "scores": scores,
                "probs": [2.718 ** (s - max(scores)) / total for s in scores], "refresh_below": cutoff}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(bot.MODEL_URL_ENV, URL)
    yield
    for code in (CODE, *(f"BP{i}" for i in range(5))):
        delete_room(code)


@pytest.fixture
def service(monkeypatch):
    def install(**kwargs) -> FakeService:
        fake = FakeService(**kwargs)
        monkeypatch.setattr(bot, "_post", fake)
        return fake
    return install


@pytest.fixture
def glog(tmp_path):
    path = tmp_path / "gameplay.db"
    gameplay_log.init(path)

    def query(sql, params=()):
        gameplay_log._writer.drain()
        conn = sqlite3.connect(path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    yield query
    gameplay_log.shutdown()


def room_with_bot(code=CODE, humans=1, voting_mode="czar", prevent_self_vote=True, timer_seconds=0, round_total=3):
    room, sids = make_room(code, n_players=humans, voting_mode=voting_mode, prevent_self_vote=prevent_self_vote,
                           timer_seconds=timer_seconds, round_total=round_total)
    computer = bot.new_player(room)
    room.players[computer.session_id] = computer
    room.join_order.append(computer.session_id)
    return room, sids, computer.session_id


async def start(room, mgr, pick=1, whites=None):
    await game_logic.start_game(room, black_cards(pick=pick), whites or white_cards(), mgr)
    await bot.wait_idle()


# --- Basics ---

def test_new_players_have_unique_names_and_unguessable_ids():
    room, _ = make_room(CODE, n_players=1)
    first = bot.new_player(room)
    room.players[first.session_id] = first
    second = bot.new_player(room)
    assert (first.username, second.username) == ("Gép", "Gép 2")
    assert first.is_bot and first.session_id.startswith(bot.SESSION_PREFIX) and len(first.session_id) > 20
    assert first.session_id != second.session_id


def test_enabled_only_with_a_service_url(monkeypatch):
    assert bot.enabled()
    monkeypatch.delenv(bot.MODEL_URL_ENV)
    assert not bot.enabled()


@pytest.mark.parametrize(("timer", "budget"), [(0, 10.0), (15, 10.0), (30, 10.0), (60, 10.0)])
def test_deadline_is_within_every_timer(timer, budget):
    room, _ = make_room(CODE, n_players=1, timer_seconds=timer)
    assert bot.deadline(room, now=100.0) == pytest.approx(100.0 + budget)
    if timer:
        assert budget <= timer - bot.SAFETY_MARGIN


async def test_ask_rejects_malformed_answers(monkeypatch):
    def answer(data):
        monkeypatch.setattr(bot, "_post", lambda url, body, timeout: data)
    answer({"model": "m", "version": 1, "scores": [1.0], "probs": [1.0], "refresh_below": None})
    assert (await bot.ask("b", ["c"], 5)).version == "1"
    for bad in ({"scores": [1.0, 2.0], "probs": [0.5, 0.5], "model": "m", "version": "1"},  # wrong length
                {"scores": [float("nan")], "probs": [1.0], "model": "m", "version": "1"},
                {"oops": True}):
        answer(bad)
        assert await bot.ask("b", ["c"], 5) is None
    assert await bot.ask("b", ["c"], 0.1) is None, "no request with less than MIN_REQUEST_TIME left"


# --- Submitting ---

async def test_plays_its_best_card_and_logs_the_probabilities(service, glog):
    fake = service()
    room, (human,), sid = room_with_bot()
    mgr = FakeManager()
    await start(room, mgr)
    me = room.players[sid]
    assert len(me.submitted_cards) == 1
    shown = me.hand + me.submitted_cards
    assert card_no(me.submitted_cards[0]["text"]) == max(card_no(c["text"]) for c in shown)
    assert fake.calls[0]["black"] == room.current_black["text"]
    hand, picks, probs = room.gameplay.round.decisions[room.gameplay.seat(sid)]
    assert len(probs) == 10 and probs[hand.index(picks[0])] == max(probs)
    assert room.gameplay.models[room.gameplay.seat(sid)] == ("pick-model", "7")
    assert "player_submitted" in mgr.events(CODE)


async def test_multi_blank_plays_its_best_cards_in_score_order(service):
    service()
    room, _, sid = room_with_bot()
    await start(room, FakeManager(), pick=2)
    numbers = [card_no(c["text"]) for c in room.players[sid].submitted_cards]
    assert len(numbers) == 2 and numbers[0] > numbers[1]
    assert all(numbers[1] > card_no(c["text"]) for c in room.players[sid].hand)


async def test_asks_for_new_hands_up_to_three_times_and_they_are_not_counted_as_seen(service, glog, isolated_card_seen):
    fake = service(refresh_below=10_000)  # every hand is "bad"
    room, (human,), sid = room_with_bot()
    await start(room, FakeManager())
    assert len(fake.calls) == 1 + bot.MAX_NEW_HANDS
    assert len(room.gameplay.round.changes) == bot.MAX_NEW_HANDS
    assert len(room.players[sid].submitted_cards) == 1, "plays its best card after the last new hand"
    seen_ids = set(isolated_card_seen._pending)
    bot_cards = room.players[sid].hand + room.players[sid].submitted_cards
    assert not seen_ids & {c["id"] for c in bot_cards}, "the computer player's cards aren't counted as seen"
    assert {c["id"] for c in room.players[human].hand} <= seen_ids


async def test_stops_asking_once_a_hand_is_good_enough(service, glog):
    calls = []
    # The first hand is bad (the cut-off is above every card), the second one is good.
    fake = service(refresh_below=lambda body: (calls.append(1), 10_000.0 if len(calls) == 1 else -1.0)[1])
    room, (human,), sid = room_with_bot()
    await start(room, FakeManager())
    assert len(fake.calls) == 2 and len(room.gameplay.round.changes) == 1


async def test_no_new_hand_without_a_cutoff(service):
    fake = service(refresh_below=None)
    room, _, sid = room_with_bot()
    await start(room, FakeManager())
    assert len(fake.calls) == 1


async def test_service_down_means_a_random_card_and_the_seat_stays_a_model_seat(service, glog):
    service(fail=True)
    room, _, sid = room_with_bot()
    await start(room, FakeManager())
    me = room.players[sid]
    assert len(me.submitted_cards) == 1
    hand, picks, probs = room.gameplay.round.decisions[room.gameplay.seat(sid)]
    assert probs is None and picks[0] in hand
    assert room.gameplay.models[room.gameplay.seat(sid)] == gameplay_log.UNKNOWN_MODEL


@pytest.mark.real_sleep
async def test_a_hanging_service_cannot_break_the_deadline(service, monkeypatch):
    monkeypatch.setattr(bot, "DEADLINE_MAX", 1.0)
    service(delay=30)
    room, _, sid = room_with_bot(timer_seconds=15)
    started = time.monotonic()
    await game_logic.start_game(room, black_cards(), white_cards(), FakeManager())
    await bot.wait_idle()
    elapsed = time.monotonic() - started
    assert len(room.players[sid].submitted_cards) == 1, "played at random instead"
    assert elapsed < 1.5


async def test_waits_while_no_human_is_in_the_room_and_resumes(service):
    service()
    room, (human,), sid = room_with_bot()
    room.players[human].connected = False
    mgr = FakeManager()
    await start(room, mgr)
    assert not room.players[sid].submitted_cards
    room.players[human].connected = True
    bot.resume(room, mgr)
    await bot.wait_idle()
    assert room.players[sid].submitted_cards


async def test_its_submission_alone_never_reveals_the_round(service):
    """Between the lobby and the game page every human is briefly disconnected. If the computer player
    submits then, the round must still wait for the humans (found by the end-to-end test)."""
    service()
    room, (human,), sid = room_with_bot()
    mgr = FakeManager()
    await game_logic.start_game(room, black_cards(), white_cards(), mgr)  # the bot's turn is queued
    room.players[human].connected = False          # the human's lobby socket closes
    await game_logic.handle_disconnect(room, human, mgr)
    room.players[human].connected = True           # ...and the game page connects
    bot.resume(room, mgr)
    await bot.wait_idle()
    assert room.players[sid].submitted_cards
    assert room.phase == "submitting", "still waiting for the human"
    room.players[human].connected = False
    await game_logic.handle_disconnect(room, human, mgr)
    assert room.phase == "submitting", "the human leaving doesn't reveal a round the bot alone played"
    room.players[human].connected = True
    await game_logic.submit_cards(room, human, [room.players[human].hand[0]], mgr)
    await bot.wait_idle()
    assert room.phase != "submitting"


async def test_never_kicked_as_inactive_and_never_the_admin(service):
    service(fail=True)
    room, (human, other), sid = room_with_bot(humans=2)
    room.players[sid].submitted_cards = []
    kicked = await game_logic._kick_inactive_players(room, FakeManager())
    assert sid not in kicked
    room.admin_session_id = human
    game_logic.remove_player(room, human)
    room.players.pop(human, None)
    assert room.promote_next_admin() != sid


def test_a_room_with_only_computer_players_left_is_empty():
    room, (human,), sid = room_with_bot()
    assert room.humans_connected()
    room.players[human].connected = False
    assert not room.humans_connected() and room.players[sid].connected


# --- Judging ---

async def reveal_with(room, mgr, cards_by_sid):
    for s, texts in cards_by_sid.items():
        player = room.players[s]
        player.submitted_cards = [{"id": 9000 + card_no(t), "text": t, "pick": 1} for t in texts]
    await game_logic.reveal_cards(room, mgr)
    await bot.wait_idle()


async def test_votes_for_the_best_submission_but_never_its_own(service):
    service()
    room, (h1, h2), sid = room_with_bot(humans=2, voting_mode="vote", prevent_self_vote=True)
    mgr = FakeManager()
    await start(room, mgr)
    await reveal_with(room, mgr, {h1: ["fehér 50"], h2: ["fehér 70"], sid: ["fehér 99"]})
    assert room.votes[sid] == h2


async def test_votes_for_itself_when_self_votes_are_allowed(service):
    service()
    room, (h1, h2), sid = room_with_bot(humans=2, voting_mode="vote", prevent_self_vote=False)
    mgr = FakeManager()
    await start(room, mgr)
    await reveal_with(room, mgr, {h1: ["fehér 50"], h2: ["fehér 70"], sid: ["fehér 99"]})
    assert room.votes[sid] == sid


async def test_multi_card_submissions_score_their_mean(service):
    service()
    room, (h1, h2), sid = room_with_bot(humans=2, voting_mode="vote")
    mgr = FakeManager()
    await start(room, mgr, pick=2)
    await reveal_with(room, mgr, {h1: ["fehér 90", "fehér 10"], h2: ["fehér 60", "fehér 60"], sid: ["fehér 1", "fehér 2"]})
    assert room.votes[sid] == h2, "mean 60 beats mean 50"


async def test_picks_the_best_submission_as_czar(service):
    service()
    room, (h1, h2), sid = room_with_bot(humans=2, voting_mode="czar")
    room.czar_index = room.join_order.index(sid)
    mgr = FakeManager()
    await game_logic.start_game(room, black_cards(), white_cards(), mgr)
    room.czar_index = room.join_order.index(sid)
    await bot.wait_idle()
    await reveal_with(room, mgr, {h1: ["fehér 40"], h2: ["fehér 80"], sid: ["fehér 99"]})
    assert room.players[h2].score == 1.0 and room.players[sid].score == 0.0


async def test_judges_at_random_when_the_service_is_down(service):
    service(fail=True)
    room, (h1, h2), sid = room_with_bot(humans=2, voting_mode="vote")
    mgr = FakeManager()
    await start(room, mgr)
    await reveal_with(room, mgr, {h1: ["fehér 50"], h2: ["fehér 70"], sid: ["fehér 99"]})
    assert room.votes[sid] in (h1, h2)


# --- Several games at once ---

@pytest.mark.real_sleep
async def test_five_games_at_once_with_a_realistic_service(service, monkeypatch):
    """Requests run in threads, so five computer players waiting 0.4 s each finish in about 0.4 s, not 2 s,
    and every one submits within its deadline."""
    service(delay=0.4)
    rooms = [room_with_bot(code=f"BP{i}", timer_seconds=15) for i in range(5)]
    started = time.monotonic()
    await asyncio.gather(*(game_logic.start_game(r, black_cards(), white_cards(), FakeManager()) for r, _, _ in rooms))
    await bot.wait_idle()
    elapsed = time.monotonic() - started
    assert all(r.players[sid].submitted_cards for r, _, sid in rooms)
    assert elapsed < 1.5


# --- A real HTTP server on a real port ---

class _Handler(BaseHTTPRequestHandler):
    delay = 0.0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        time.sleep(self.delay)
        scores = [float(card_no(t)) for t in body["cards"]]
        out = json.dumps({"model": "pick-model", "version": "3", "scores": scores,
                          "probs": [1 / len(scores)] * len(scores), "refresh_below": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_service(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(bot.MODEL_URL_ENV, f"http://127.0.0.1:{server.server_address[1]}")
    yield _Handler
    _Handler.delay = 0.0
    server.shutdown()


@pytest.mark.real_sleep
async def test_over_real_http(http_service):
    answer = await bot.ask("fekete ____", ["fehér 3", "fehér 9"], 5)
    assert answer.version == "3" and answer.scores == [3.0, 9.0]
    http_service.delay = 2.0
    started = time.monotonic()
    assert await bot.ask("fekete ____", ["fehér 3"], 0.8) is None
    assert time.monotonic() - started < 1.2


async def test_unreachable_service_over_real_http(monkeypatch):
    monkeypatch.setenv(bot.MODEL_URL_ENV, "http://127.0.0.1:9")  # nothing listens on the discard port
    assert await bot.ask("fekete ____", ["fehér 3"], 2) is None


# --- Lobby events (server/main.py) ---

def snapshot_players(room):
    return main_mod._room_snapshot(room)["players"]


async def test_admin_adds_one_computer_player_and_can_remove_it(monkeypatch):
    room, (admin,) = make_room(CODE, n_players=1)
    await main_mod._handle_message(admin, CODE, {"event": "add_bot"})
    bots = [p for p in room.players.values() if p.is_bot]
    assert len(bots) == 1 and bots[0].session_id in room.join_order
    assert any(p["is_bot"] for p in snapshot_players(room))
    assert main_mod._room_snapshot(room)["bots"] == {"enabled": True, "max": 1}
    await main_mod._handle_message(admin, CODE, {"event": "add_bot"})
    assert room.bot_count() == 1, "max_bots is 1"
    await main_mod._handle_message(admin, CODE, {"event": "remove_bot", "session_id": bots[0].session_id})
    assert room.bot_count() == 0 and bots[0].session_id not in room.join_order


async def test_max_bots_comes_from_the_config(monkeypatch):
    monkeypatch.setitem(main_mod.CONFIG, "max_bots", 2)
    room, (admin,) = make_room(CODE, n_players=1)
    for _ in range(3):
        await main_mod._handle_message(admin, CODE, {"event": "add_bot"})
    assert sorted(p.username for p in room.players.values() if p.is_bot) == ["Gép", "Gép 2"]


async def test_only_the_admin_only_in_the_lobby_only_when_enabled(monkeypatch):
    room, (admin, other) = make_room(CODE, n_players=2)
    await main_mod._handle_message(other, CODE, {"event": "add_bot"})
    assert room.bot_count() == 0
    await main_mod._handle_message(admin, CODE, {"event": "remove_bot", "session_id": other})
    assert other in room.players, "remove_bot can't remove a human"
    room.started = True
    await main_mod._handle_message(admin, CODE, {"event": "add_bot"})
    assert room.bot_count() == 0
    room.started = False
    monkeypatch.delenv(bot.MODEL_URL_ENV)
    await main_mod._handle_message(admin, CODE, {"event": "add_bot"})
    assert room.bot_count() == 0
    assert main_mod._room_snapshot(room)["bots"]["enabled"] is False


def test_nobody_can_connect_as_the_computer_player():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    room, (admin,), sid = room_with_bot()
    client = TestClient(main_mod.app)  # no lifespan: the app's background loops aren't needed here
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/ws/{CODE}?session_id={sid}") as ws:
            ws.receive_json()
    assert closed.value.code == 4001
    assert room.players[sid].connected and room.players[sid].is_bot, "the seat is untouched"


# --- A whole game, written to gameplay.db ---

async def test_a_whole_vote_game_against_the_computer_player(service, glog):
    """One human and the computer player, vote mode, self-votes allowed so a vote decides: the computer
    player submits and votes every round, and the log marks its seat as a model with its probabilities."""
    service()
    room, (human,), sid = room_with_bot(voting_mode="vote", prevent_self_vote=False, round_total=3)
    mgr = FakeManager()
    await start(room, mgr)
    for _ in range(3):
        assert room.phase == "submitting" and room.players[sid].submitted_cards
        await game_logic.submit_cards(room, human, [room.players[human].hand[0]], mgr)
        await bot.wait_idle()
        assert room.phase == "revealed" and sid in room.votes
        number = {s: card_no(room.players[s].submitted_cards[0]["text"]) for s in (human, sid)}
        assert room.votes[sid] == max(number, key=number.get), "it votes for the better-scored card"
        await game_logic.cast_vote(room, human, sid, mgr)
        await bot.wait_idle()
    assert room.finished and "game_end" in mgr.events(CODE)
    assert room.players[sid].score >= 1.5, "at least half a point every round: the human always voted for it"

    (model_id,) = glog("SELECT model_id FROM game_player WHERE seat = ?", (room.gameplay.seat(sid),))[0]
    assert glog("SELECT name, version FROM model WHERE model_id = ?", (model_id,)) == [("pick-model", "7")]
    assert glog("SELECT model_id FROM game_player WHERE seat = ?", (room.gameplay.seat(human),)) == [(None,)]
    rows = glog("SELECT hand, picks, p_chosen, probs FROM decision WHERE seat = ?", (room.gameplay.seat(sid),))
    assert len(rows) == 3
    for hand, picks, p_chosen, probs in rows:
        probs = unpack_probs(probs)
        assert len(probs) == 10 and p_chosen == pytest.approx(max(probs), abs=1e-3)
        assert unpack_ids(picks)[0] in unpack_ids(hand)
    human_rows = glog("SELECT p_chosen, probs FROM decision WHERE seat = ?", (room.gameplay.seat(human),))
    assert human_rows == [(None, None)] * 3
    votes = glog("SELECT voter, target FROM vote WHERE voter = ?", (room.gameplay.seat(sid),))
    assert len(votes) == 3
