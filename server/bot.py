"""Computer players: a seat in the room that the model service (ml/serve.py) plays.

Off unless EEK_MODEL_URL points at the service (staging only). The room admin adds computer players in the
lobby, up to config.json's "max_bots" (server/main.py).

Each round the computer player:
- submits: it sends the black card and its hand to the service and plays the best-scored card (for a
  2- or 3-blank black card, its best 2 or 3 in score order). If even its best card scores below the model's
  cut-off ("refresh_below": worse than the best card of 90% of training hands), it asks for a new hand
  first, up to MAX_NEW_HANDS times a round. Cards dealt to it don't count as seen (card_seen).
- judges: in vote mode it votes for the best-scored submission, in czar mode it picks one when it's the
  czar; never its own when self-votes are off. A multi-card submission scores the mean of its cards.

It acts as soon as the model answers. It never waits past its deadline: DEADLINE_MAX seconds, or the round
timer minus SAFETY_MARGIN if that's sooner. A request that would end after it is cut off, and any failure
(service down, timeout, malformed answer) makes it play at random, so a slow or broken service can't stall a
round. With the shortest timer (15 s) the deadline is 10 s; the service needs well under a second.

It only acts while a human is still in the room, so a room left with computer players alone stops and is
cleaned up like an empty one. Its decisions go to the gameplay log as a model seat (training leaves those
out) with the model's version and probabilities.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import secrets
import time
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from server import gameplay_log

if TYPE_CHECKING:
    from server.main import ConnectionManager
    from server.room import Player, Room

logger = logging.getLogger(__name__)

MODEL_URL_ENV = "EEK_MODEL_URL"
NAME = "Gép"
SESSION_PREFIX = "bot-"
MAX_NEW_HANDS = 3
DEADLINE_MAX = 10.0     # seconds after the phase starts
SAFETY_MARGIN = 3.0     # seconds left on the round timer at the latest deadline
MIN_REQUEST_TIME = 0.5  # don't start a request (or a new hand) with less time than this left

_tasks: set[asyncio.Task] = set()


def model_url() -> str | None:
    return os.environ.get(MODEL_URL_ENV) or None


def enabled() -> bool:
    return model_url() is not None


def new_player(room: Room) -> Player:
    """A computer player for this room, with a unique name ("Gép", "Gép 2", ...) and an unguessable session
    ID, so nobody can take its seat by connecting with it."""
    from server.room import Player
    taken = {p.username.casefold() for p in room.players.values()} | {p.username.casefold() for p in room.pending.values()}
    name = next(n for n in (NAME if i == 1 else f"{NAME} {i}" for i in range(1, 100)) if n.casefold() not in taken)
    return Player(session_id=SESSION_PREFIX + secrets.token_urlsafe(16), username=name, is_bot=True)


# --- The model service ---

@dataclass(frozen=True)
class Answer:
    model: str
    version: str
    scores: list[float]
    probs: list[float]
    refresh_below: float | None


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(f"{url.rstrip('/')}/score", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


async def ask(black: str, cards: list[str], timeout: float) -> Answer | None:
    """The service's scores for the cards, or None if it can't answer correctly within timeout seconds."""
    url = model_url()
    if not url or timeout < MIN_REQUEST_TIME or not cards:
        return None
    try:
        data = await asyncio.wait_for(asyncio.to_thread(_post, url, {"black": black, "cards": cards}, timeout),
                                      timeout)
        scores, probs = [float(x) for x in data["scores"]], [float(x) for x in data["probs"]]
        if len(scores) != len(cards) or len(probs) != len(cards) or not all(map(math.isfinite, scores + probs)):
            raise ValueError(f"{len(cards)} cards, got {len(scores)} scores / {len(probs)} probs")
        cutoff = data.get("refresh_below")
        return Answer(str(data["model"]), str(data["version"]), scores, probs,
                      float(cutoff) if cutoff is not None else None)
    except Exception as exc:  # noqa: BLE001 - any failure means "play at random"
        logger.warning("model service: %s: %s", type(exc).__name__, exc)
        return None


def deadline(room: Room, now: float | None = None) -> float:
    """time.monotonic() by which the computer player must have acted in the phase that starts now."""
    now = time.monotonic() if now is None else now
    budget = DEADLINE_MAX if room.timer_seconds <= 0 else min(DEADLINE_MAX, room.timer_seconds - SAFETY_MARGIN)
    return now + max(budget, 0.0)


def best_first(scores: list[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: -scores[i])


# --- Turns ---

def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_idle() -> None:
    """Wait until no computer player is mid-turn (tests). Turns can start new turns (a round ends, the next
    begins), so keep going until none are left."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def start_turns(room: Room, manager: ConnectionManager) -> None:
    """A round started: every computer player that hasn't submitted plays its turn."""
    if room.phase != "submitting":
        return
    for sid, player in list(room.players.items()):
        if player.is_bot and not player.submitted_cards:
            _spawn(_submit_turn(room, sid, room.round_num, manager))


def start_judging(room: Room, manager: ConnectionManager) -> None:
    """Submissions are revealed: computer players vote, or pick if one is the czar."""
    if room.phase != "revealed":
        return
    for sid, player in list(room.players.items()):
        if not player.is_bot:
            continue
        if room.voting_mode == "vote" and sid not in room.votes:
            _spawn(_judge_turn(room, sid, room.round_num, manager))
        elif room.voting_mode == "czar" and sid == room.czar_session_id:
            _spawn(_judge_turn(room, sid, room.round_num, manager))


def resume(room: Room, manager: ConnectionManager) -> None:
    """A human came back: computer players that stopped because the room was empty catch up."""
    if not room.started or room.finished:
        return
    if room.phase == "submitting":
        start_turns(room, manager)
    elif room.phase == "revealed":
        start_judging(room, manager)


async def _submit_turn(room: Room, sid: str, round_no: int, manager: ConnectionManager) -> None:
    from server import game

    def still_on() -> bool:
        player = room.players.get(sid)
        return (not room.closed and not room.finished and room.phase == "submitting" and room.round_num == round_no
                and player is not None and not player.submitted_cards and room.humans_connected())

    if not still_on():
        return
    until = deadline(room)
    player = room.players[sid]
    black = room.current_black or {}
    pick = max(1, int(black.get("pick", 1) or 1))
    answer, new_hands = None, 0
    try:
        while True:
            texts = [c["text"] for c in player.hand]
            answer = await ask(black.get("text", ""), texts, until - time.monotonic())
            if not still_on():
                return
            if (answer is None or answer.refresh_below is None or max(answer.scores) >= answer.refresh_below
                    or new_hands >= MAX_NEW_HANDS or until - time.monotonic() < 2 * MIN_REQUEST_TIME):
                break
            game.deal_new_hand(room, player)  # every card in it is worse than the cut-off: try ten new ones
            new_hands += 1
            await manager.send(room.code, sid, {"event": "hand_update", "hand": player.hand})
    except Exception:  # noqa: BLE001 - never leave the round without a submission
        logger.exception("computer player %s: turn failed, playing at random", sid)
        answer = None
    if not still_on() or len(player.hand) < pick:
        return
    if answer is not None and len(answer.scores) == len(player.hand):
        gameplay_log.model_answered(room, sid, answer.model, answer.version)
        cards, probs = [player.hand[i] for i in best_first(answer.scores)[:pick]], answer.probs
    else:
        cards, probs = random.sample(player.hand, pick), None
    await game.submit_cards(room, sid, cards, manager, probs=probs)


async def _judge_turn(room: Room, sid: str, round_no: int, manager: ConnectionManager) -> None:
    from server import game

    def still_on() -> bool:
        if room.closed or room.finished or room.phase != "revealed" or room.round_num != round_no:
            return False
        if sid not in room.players or not room.humans_connected():
            return False
        return sid not in room.votes if room.voting_mode == "vote" else sid == room.czar_session_id

    if not still_on():
        return
    until = deadline(room)
    submissions = getattr(room, "_last_submissions", None) or []
    self_vote_off = room.prevent_self_vote and len(room.players) > 1
    candidates = [s for s in submissions if s["cards"] and not (self_vote_off and s["session_id"] == sid)]
    if not candidates:
        return
    winner = random.choice(candidates)["session_id"]
    try:
        flat = [(i, c["text"]) for i, s in enumerate(candidates) for c in s["cards"]]
        answer = await ask((room.current_black or {}).get("text", ""), [t for _, t in flat],
                           until - time.monotonic())
        if answer is not None:
            gameplay_log.model_answered(room, sid, answer.model, answer.version)
            totals: dict[int, list[float]] = {}
            for (i, _), score in zip(flat, answer.scores, strict=True):
                totals.setdefault(i, []).append(score)
            best = max(totals, key=lambda i: sum(totals[i]) / len(totals[i]))
            winner = candidates[best]["session_id"]
    except Exception:  # noqa: BLE001 - fall back to the random choice
        logger.exception("computer player %s: judging failed, choosing at random", sid)
    if not still_on():
        return
    if room.voting_mode == "vote":
        await game.cast_vote(room, sid, winner, manager)
    else:
        await game.czar_pick(room, winner, manager)
