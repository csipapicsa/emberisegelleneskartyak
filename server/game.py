# servers/game.py

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from typing import TYPE_CHECKING

from server import card_seen, gameplay_log
from server.analytics import log_event
from server.deck import build_deck, deal_hand, draw_black, draw_card, reshuffle_into_deck
from server.room import Room

if TYPE_CHECKING:
    from server.main import ConnectionManager


def close_room(room: Room) -> None:
    """The room is being deleted: stop its round loop for good. The loop runs in background tasks that
    hold their own reference to the room, so without this a running game kept playing its remaining
    rounds to nobody after the room was gone (seen in gameplay.db by ml/validate.py, 2026-11-07)."""
    room.closed = True
    _cancel_timer(room)


def _cancel_timer(room: Room) -> None:
    if room.timer_task and not room.timer_task.done():
        current = asyncio.current_task()
        if room.timer_task is not current:
            room.timer_task.cancel()
    room.timer_task = None


def _remove_player(room: Room, session_id: str) -> None:
    """Drop a player from the room (players map, join_order, votes).
    Adjusts czar_index so the rotation stays consistent."""
    if session_id not in room.players:
        return
    try:
        idx = room.join_order.index(session_id)
    except ValueError:
        idx = -1
    room.players.pop(session_id, None)
    if idx >= 0:
        room.join_order.pop(idx)
        # Keep czar_index pointing at the same logical "next" position
        if idx < room.czar_index:
            room.czar_index -= 1
        if room.join_order:
            room.czar_index %= len(room.join_order)
        else:
            room.czar_index = 0
    room.votes.pop(session_id, None)
    for vsid, winner in list(room.votes.items()):
        if winner == session_id:
            room.votes.pop(vsid, None)


async def _kick_inactive_players(room: Room, manager: ConnectionManager) -> list[str]:
    """Kick connected players who didn't submit this round.
    Returns the list of session_ids that were kicked. If the room becomes
    unplayable, broadcasts game_aborted and sets room.finished.

    Standalone (1-player) rooms are exempt — a solo player should be able to
    browse the deck at their own pace."""
    if len(room.players) <= 1:
        return []
    kicked: list[str] = []
    for sid, player in list(room.players.items()):
        if not player.connected:
            continue
        if player.submitted_cards:
            continue
        # Czar in czar-mode is special: czar doesn't have to submit if the
        # engine treats czar as non-submitter. In current code czar DOES submit,
        # so we kick czars too if they go idle.
        player.missed_rounds += 1
        if player.missed_rounds >= 1:
            kicked.append(sid)

    for sid in kicked:
        username = room.players[sid].username if sid in room.players else ""
        was_admin = (sid == room.admin_session_id)
        await manager.send(room.code, sid, {"event": "kicked", "reason": "inactive"})
        _remove_player(room, sid)
        await manager.broadcast(room.code, {
            "event": "player_kicked",
            "session_id": sid,
            "username": username,
            "reason": "inactive",
        })
        if was_admin:
            new_admin = room.promote_next_admin()
            if new_admin:
                await manager.broadcast(room.code, {"event": "admin_promoted", "session_id": new_admin})

    if kicked and len(room.players) < 1:
        room.phase = "finished"
        room.finished = True
        await manager.broadcast(room.code, {"event": "game_aborted", "reason": "no_players"})
        return kicked

    return kicked


async def start_game(room: Room, black_cards: list[dict], white_cards: list[dict], manager: ConnectionManager) -> None:
    room.started = True
    room.round_num = 0
    room.czar_index = 0
    room.phase = "lobby"

    black_deck, white_deck = build_deck(black_cards, white_cards, card_seen.current_counts())
    room.black_deck = black_deck
    room.white_deck = white_deck

    for player in room.players.values():
        player.score = 0.0
        player.swaps_remaining = room.round_total
        player.hand = deal_hand(room.white_deck, 10)

    gameplay_log.game_started(room)
    await manager.broadcast(room.code, {"event": "game_started"})
    await start_round(room, manager)


async def start_round(room: Room, manager: ConnectionManager) -> None:
    if room.finished or room.closed:
        return

    _cancel_timer(room)
    room.round_num += 1
    room.votes = {}
    room.phase = "submitting"

    if room.round_num > room.round_total or not room.black_deck:
        room.phase = "finished"
        room.finished = True
        await manager.broadcast(room.code, {"event": "game_end", "final_scores": _scores(room)})
        log_event("game_ended", room_code=room.code, player_count=len(room.players), extra={"rounds": room.round_total})
        return

    room.current_black = draw_black(room.black_deck)
    room.round_start_time = time.time()
    gameplay_log.round_started(room)

    if room.voting_mode == "czar":
        connected = {sid for sid, player in room.players.items() if player.connected}
        while room.czar_session_id and room.czar_session_id not in connected and connected:
            room.next_czar()

    for player in room.players.values():
        player.submitted_cards = []
        player.hand_refresh_used = False

    czar_sid = room.czar_session_id
    czar_name = room.players[czar_sid].username if czar_sid and czar_sid in room.players else ""

    await manager.broadcast(room.code, {
        "event": "round_start",
        "black_card": room.current_black,
        "czar": czar_sid,
        "czar_username": czar_name,
        "round_num": room.round_num,
        "round_total": room.round_total,
        "timer_seconds": room.timer_seconds,
        "voting_mode": room.voting_mode,
        "prevent_self_vote": room.prevent_self_vote,
        "scores": _scores(room),
    })

    for sid, player in room.players.items():
        await manager.send(room.code, sid, {"event": "hand_update", "hand": player.hand})

    room.cards_revealed = False
    if room.timer_seconds > 0:
        room.timer_task = asyncio.create_task(_timer_task(room, room.round_num, manager))


async def _timer_task(room: Room, round_num: int, manager: ConnectionManager) -> None:
    remaining = room.timer_seconds
    while remaining > 0:
        await asyncio.sleep(1)
        if room.closed or room.round_num != round_num or room.phase not in ("submitting", "revealed"):
            return
        remaining -= 1
        await manager.broadcast(room.code, {"event": "timer_tick", "seconds_remaining": remaining})

    if room.round_num != round_num or room.phase not in ("submitting", "revealed"):
        return
    if room.phase == "revealed" and room.voting_mode == "vote":
        await tally_votes(room, manager)
    elif room.phase == "revealed" and room.voting_mode == "czar":
        await skip_revealed_round(room, manager)
    elif room.phase == "submitting":
        await reveal_cards(room, manager)


async def reveal_cards(room: Room, manager: ConnectionManager) -> None:
    if room.phase != "submitting":
        return

    original_czar_sid = room.czar_session_id

    # If nobody submitted at all, skip the round without kicking anyone.
    # This happens when the timer runs out and all players were idle.
    any_submitted = any(p.submitted_cards for p in room.players.values())
    if not any_submitted:
        await skip_revealed_round(room, manager)
        return

    # Inactivity kick: any connected player who failed to submit this round is
    # kicked. Disconnected players are left alone (they may reconnect).
    gameplay_log.submissions_closed(room)
    kicked = await _kick_inactive_players(room, manager)

    # Aborted by inactivity sweep (not enough players left to continue).
    if room.finished:
        return

    if room.voting_mode == "czar" and original_czar_sid in kicked:
        await skip_revealed_round(room, manager)
        return

    submissions = [
        {"session_id": sid, "username": player.username, "cards": player.submitted_cards}
        for sid, player in room.players.items()
        if player.submitted_cards
    ]

    # Reset missed_rounds for everyone who submitted.
    for player in room.players.values():
        if player.submitted_cards:
            player.missed_rounds = 0

    # No submissions at all — skip round
    if not submissions:
        gameplay_log.round_ended(room, gameplay_log.NO_SUBMISSIONS)
        await manager.broadcast(room.code, {"event": "round_skipped"})
        room.next_czar()
        await start_round(room, manager)
        return

    random.shuffle(submissions)
    room._last_submissions = submissions
    gameplay_log.revealed(room, [s["session_id"] for s in submissions])
    room.cards_revealed = True
    room.phase = "revealed"

    # Strip usernames — revealed only after pick/vote
    anon = [{"session_id": s["session_id"], "cards": s["cards"]} for s in submissions]
    await manager.broadcast(room.code, {
        "event": "cards_revealed",
        "submissions": anon,
        "timer_seconds": room.timer_seconds,
        "prevent_self_vote": room.prevent_self_vote,
        "player_count": len(room.players),
    })

    # Revealed phase gets a fresh timer for voting or czar selection.
    if room.timer_seconds > 0:
        _cancel_timer(room)
        room.timer_task = asyncio.create_task(_timer_task(room, room.round_num, manager))


async def czar_pick(room: Room, winner_session_id: str, manager: ConnectionManager) -> None:
    if room.voting_mode != "czar" or room.phase != "revealed":
        return
    if room.prevent_self_vote and len(room.players) > 1 and room.czar_session_id == winner_session_id:
        return
    winner = room.players.get(winner_session_id)
    if winner:
        winner.score += 1.0
    gameplay_log.round_ended(room, gameplay_log.DECIDED, {room.czar_session_id: winner_session_id})
    await _end_round(room, [winner_session_id], manager)


async def cast_vote(room: Room, voter_sid: str, winner_sid: str, manager: ConnectionManager) -> None:
    if room.voting_mode != "vote" or room.phase != "revealed":
        return
    if voter_sid in room.votes:
        return  # already voted
    if winner_sid not in room.players:
        return
    if room.prevent_self_vote and len(room.players) > 1 and voter_sid == winner_sid:
        return
    room.votes[voter_sid] = winner_sid

    votes_in = len(room.votes)
    total = sum(1 for p in room.players.values() if p.connected)
    await manager.broadcast(room.code, {"event": "vote_cast", "votes_in": votes_in, "total": total})

    if room.all_voted():
        await tally_votes(room, manager)


async def tally_votes(room: Room, manager: ConnectionManager) -> None:
    if room.phase != "revealed" or room.finished:
        return
    if not room.votes:
        # Like a czar who doesn't pick: no point, and the hands are refilled for the next round.
        await skip_revealed_round(room, manager)
        return

    gameplay_log.round_ended(room, gameplay_log.DECIDED, dict(room.votes))
    counts = Counter(room.votes.values())
    max_votes = max(counts.values())
    winners = [sid for sid, c in counts.items() if c == max_votes]
    points_each = 1.0 / len(winners)
    for sid in winners:
        p = room.players.get(sid)
        if p:
            p.score += points_each

    await _end_round(room, winners, manager)


async def skip_revealed_round(room: Room, manager: ConnectionManager) -> None:
    """Finish a revealed round without awarding a point."""
    if room.phase not in ("submitting", "revealed") or room.finished:
        return
    room.phase = "round_ending"
    _cancel_timer(room)
    any_submitted = any(p.submitted_cards for p in room.players.values())
    gameplay_log.round_ended(room, gameplay_log.NO_DECISION if any_submitted else gameplay_log.NO_SUBMISSIONS)

    for player in room.players.values():
        room.discard_pile.extend(player.submitted_cards)
        player.submitted_cards = []

    for player in room.players.values():
        while len(player.hand) < 10:
            if not room.white_deck:
                reshuffle_into_deck(room.white_deck, room.discard_pile)
                room.discard_pile = []
                if not room.white_deck:
                    break
            card = draw_card(room.white_deck)
            if card:
                player.hand.append(card)

    await manager.broadcast(room.code, {"event": "round_skipped"})
    for sid, player in room.players.items():
        await manager.send(room.code, sid, {"event": "hand_update", "hand": player.hand})

    room.next_czar()
    await start_round(room, manager)


async def _end_round(room: Room, winners: list[str], manager: ConnectionManager) -> None:
    if room.phase not in ("revealed", "submitting") or room.finished:
        return
    room.phase = "round_ending"
    _cancel_timer(room)

    for player in room.players.values():
        room.discard_pile.extend(player.submitted_cards)
        player.submitted_cards = []

    for player in room.players.values():
        while len(player.hand) < 10:
            if not room.white_deck:
                reshuffle_into_deck(room.white_deck, room.discard_pile)
                room.discard_pile = []
                if not room.white_deck:
                    break
            card = draw_card(room.white_deck)
            if card:
                player.hand.append(card)

    await manager.broadcast(room.code, {
        "event": "round_end",
        "winner_session_id": winners[0],
        "winners": winners,
        "scores": _scores(room),
        "authors": getattr(room, "_last_submissions", []),
    })

    for sid, player in room.players.items():
        await manager.send(room.code, sid, {"event": "hand_update", "hand": player.hand})

    await asyncio.sleep(3)
    if room.closed:  # deleted during the pause; _cancel_timer above couldn't stop the task we run in
        return
    room.next_czar()
    await start_round(room, manager)


async def handle_disconnect(room: Room, session_id: str, manager: ConnectionManager) -> None:
    player = room.players.get(session_id)
    if not player:
        return

    player.connected = False
    await manager.broadcast(room.code, {"event": "player_disconnected", "session_id": session_id})

    if session_id == room.admin_session_id and not room.started:
        new_admin = room.promote_next_admin()
        if new_admin:
            await manager.broadcast(room.code, {"event": "admin_promoted", "session_id": new_admin})

    if room.started and not room.finished:
        if room.phase == "submitting" and room.all_submitted():
            await reveal_cards(room, manager)
        elif (
            room.phase == "revealed"
            and room.voting_mode == "czar"
            and session_id == room.czar_session_id
        ):
            await skip_revealed_round(room, manager)
        elif room.phase == "revealed" and room.voting_mode == "vote":
            connected = [p for p in room.players.values() if p.connected]
            if not connected or room.all_voted():
                await tally_votes(room, manager)


def _scores(room: Room) -> list[dict]:
    return [
        {"session_id": sid, "username": p.username, "score": p.score}
        for sid, p in room.players.items()
    ]
