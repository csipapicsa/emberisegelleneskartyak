# server/main.py

from __future__ import annotations
import asyncio
import ipaddress
import json
import logging
import os
import random
import re
import string
import time
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI, Request, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from server.db import (
    get_conn,
    get_editions,
    get_edition_name,
    get_cards_for_edition,
    get_cards_for_editions,
)
from server import edition_display
from server.messages_db import get_conn as get_msg_conn, ensure_tables as ensure_msg_tables, \
    save_message, save_card_idea, get_chat_messages, post_chat_message, \
    get_community_cards, list_card_ideas, vote_card_idea, purge_expired_data, \
    add_card_feedback, \
    COMMUNITY_EDITION_ID, COMMUNITY_EDITION_NAME
from server import chat_db
from server import card_seen
from server.session import create_or_get_session
from server.room import create_room, get_room, delete_room, Player
from server import game as game_logic
from server.voice import generate_turn_credentials, validate_signal, check_ice_rate
from server.analytics import log_event, get_stats
from server.privacy import PrivacyConfigurationError, pseudonymize_ip

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

with open(os.path.join(BASE_DIR, "config.json")) as f:
    CONFIG = json.load(f)


def _delete_room_and_chat(code: str) -> None:
    """Drop the in-memory room and its chat history together — chat is only
    ever meant to outlive the room it belongs to, never longer. Chat storage
    is a side concern: if it's unavailable, the room still has to go."""
    delete_room(code)
    try:
        conn = chat_db.get_conn()
        try:
            chat_db.delete_room_messages(conn, code)
        finally:
            conn.close()
    except Exception:
        logger.exception("failed to purge chat history for room %s", code)


async def _card_seen_flush_task(stop: asyncio.Event) -> None:
    """Periodically write the in-memory card_seen counts to disk, off the
    event loop. Once stop is set it writes one last batch and returns, so
    shutdown never cuts a write in half."""
    while True:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), card_seen.FLUSH_INTERVAL)
        batch = card_seen.take_pending()
        try:
            await asyncio.to_thread(card_seen.write_counts, batch)
        except Exception:
            card_seen.restore_pending(batch)
            logger.exception("card_seen flush failed; will retry")
        if stop.is_set():
            return


async def _room_cleanup_task() -> None:
    """Periodically remove stale rooms."""
    UNSTARTED_TTL  = 10 * 60   # 10 min — lobby with no activity
    EMPTY_TTL      =  5 * 60   # 5 min  — started but everyone disconnected
    FINISHED_TTL   =  5 * 60   # 5 min  — finished, scoreboard grace period
    HARD_LIMIT     = 20 * 60   # 20 min  — absolute maximum regardless of activity
    CHECK_INTERVAL =  2 * 60   # check every 2 min

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        now = time.time()
        from server.room import _rooms
        stale = []
        for code, room in list(_rooms.items()):
            inactivity = now - room.last_activity
            age        = now - room.created_at

            if age > HARD_LIMIT:
                stale.append(code)
            elif room.finished:
                if inactivity > FINISHED_TTL:
                    stale.append(code)
            elif not room.started:
                all_gone = all(not p.connected for p in room.players.values())
                ttl = EMPTY_TTL if all_gone else UNSTARTED_TTL
                if inactivity > ttl:
                    stale.append(code)
            else:
                all_gone = all(not p.connected for p in room.players.values())
                if all_gone and inactivity > EMPTY_TTL:
                    stale.append(code)

        for code in stale:
            try:
                room = get_room(code)
                if room and not room.started:
                    admin = room.players.get(room.admin_session_id)
                    log_event(
                        "room_abandoned",
                        room_code=code,
                        username=admin.username if admin else None,
                        player_count=len(room.players),
                    )
                _delete_room_and_chat(code)
            except Exception:
                logger.exception("room cleanup failed for room %s; continuing", code)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_conn = get_msg_conn()
    try:
        ensure_msg_tables(startup_conn, migrate_legacy_votes=True)
        purge_expired_data(startup_conn)
        startup_conn.commit()
    finally:
        startup_conn.close()
    from server.analytics import init_analytics
    init_analytics(migrate_privacy=True)

    flush_stop = asyncio.Event()
    flush_task = asyncio.create_task(_card_seen_flush_task(flush_stop))
    try:
        if os.environ.get("EEK_DISABLE_ROOM_CLEANUP") == "1":
            yield
            return

        cleanup_task = asyncio.create_task(_room_cleanup_task())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
    finally:
        flush_stop.set()
        await flush_task


app = FastAPI(lifespan=lifespan)

# Ensure chat.db tables exist. Room chat is scoped to in-memory rooms, which
# are all gone on process start, so any leftover rows are orphaned — wipe them.
# Chat is a side feature: if its storage is broken, the game must still boot.
try:
    _chat_startup_conn = chat_db.get_conn()
    try:
        chat_db.ensure_tables(_chat_startup_conn)
        _chat_startup_conn.execute("DELETE FROM room_chat_messages")
        _chat_startup_conn.commit()
    finally:
        _chat_startup_conn.close()
except Exception:
    logger.exception("chat.db unavailable at startup; chat will be degraded")


@app.get("/api/chat")
async def api_get_chat():
    conn = get_msg_conn()
    try:
        return get_chat_messages(conn)
    finally:
        conn.close()


@app.post("/api/chat")
async def api_post_chat(body: dict):
    username = str(body.get("username", "")).strip()[:24]
    message  = str(body.get("message",  "")).strip()[:500]
    if not username or not message:
        raise HTTPException(400, "Név és üzenet megadása kötelező")
    conn = get_msg_conn()
    try:
        post_chat_message(conn, username, message)
    finally:
        conn.close()
    return {"ok": True}


# ── WebSocket connection manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        # room_code -> session_id -> ws  (room-scoped, no cross-room bleed)
        self._sockets: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, room_code: str, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        room_sockets = self._sockets.setdefault(room_code, {})
        prior = room_sockets.get(session_id)
        if prior is not None and prior is not ws:
            try:
                await prior.close(code=4003)
            except Exception:
                pass
        room_sockets[session_id] = ws

    def disconnect(self, room_code: str, session_id: str) -> None:
        room_sockets = self._sockets.get(room_code)
        if room_sockets:
            room_sockets.pop(session_id, None)
            if not room_sockets:
                self._sockets.pop(room_code, None)

    def evict_from_all_rooms(self, session_id: str, except_room: str) -> None:
        """Disconnect a session from every room except the given one."""
        for code in list(self._sockets.keys()):
            if code != except_room:
                self._sockets.get(code, {}).pop(session_id, None)

    async def send(self, room_code: str, session_id: str, data: dict) -> None:
        ws = self._sockets.get(room_code, {}).get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                pass

    async def broadcast(self, room_code: str, data: dict) -> None:
        room = get_room(room_code)
        if not room:
            return
        for sid in list(room.players.keys()):
            await self.send(room_code, sid, data)

    async def broadcast_all(self, room_code: str, data: dict) -> None:
        """Send to every connected socket in the room, including players still
        waiting on admin approval — used for chat, which isn't gated on that."""
        for ws in list(self._sockets.get(room_code, {}).values()):
            try:
                await ws.send_json(data)
            except Exception:
                pass


manager = ConnectionManager()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _username_taken(room, username: str, exclude_sid: str | None = None) -> bool:
    """Case-insensitive duplicate-username check among approved players."""
    norm = username.strip().casefold()
    if not norm:
        return False
    for sid, p in room.players.items():
        if sid == exclude_sid:
            continue
        if p.username.strip().casefold() == norm:
            return True
    return False


async def _send_chat_history(room_code: str, session_id: str) -> None:
    """Send chat history to a joining/reconnecting player. Chat is a side
    feature layered on top of the game: if its storage is unavailable, the
    player must still be able to join and play, just without history."""
    history: list[dict] = []
    try:
        conn = chat_db.get_conn()
        try:
            history = chat_db.get_messages(conn, room_code)
        finally:
            conn.close()
    except Exception:
        logger.exception("failed to load chat history for room %s", room_code)
    await manager.send(room_code, session_id, {"event": "chat_history", "messages": history})


def _chat_username(room, session_id: str) -> str | None:
    """Username for a chat sender — approved players and players still
    waiting on admin approval can both write, so check both."""
    player = room.players.get(session_id) or room.pending.get(session_id)
    return player.username if player else None


def _generate_room_code() -> str:
    digits = CONFIG.get("room_code_digits", 4)
    while True:
        code = "".join(random.choices(string.digits, k=digits))
        if not get_room(code):
            return code


def _revealed_submissions_snapshot(room) -> list[dict]:
    if room.phase != "revealed":
        return []
    submissions = getattr(room, "_last_submissions", None)
    if not submissions:
        submissions = [
            {"session_id": sid, "cards": player.submitted_cards}
            for sid, player in room.players.items()
            if player.submitted_cards
        ]
    return [
        {"session_id": s["session_id"], "cards": s["cards"]}
        for s in submissions
    ]


def _room_snapshot(room) -> dict:
    snapshot = {
        "event": "room_state",
        "code": room.code,
        "started": room.started,
        "finished": room.finished,
        "round_num": room.round_num,
        "round_total": room.round_total,
        "timer_seconds": room.timer_seconds,
        "voting_mode": room.voting_mode,
        "prevent_self_vote": room.prevent_self_vote,
        "edition_ids": room.edition_ids,
        "czar": room.czar_session_id,
        "admin": room.admin_session_id,
        "phase": room.phase,
        "players": [
            {
                "session_id": sid,
                "username": p.username,
                "score": p.score,
                "connected": p.connected,
                "is_admin": p.is_admin,
                "submitted": bool(p.submitted_cards),
            }
            for sid, p in room.players.items()
        ],
        "pending": [
            {"session_id": sid, "username": p.username}
            for sid, p in room.pending.items()
        ],
        "black_card": room.current_black,
    }
    if room.phase == "revealed":
        snapshot["submissions"] = _revealed_submissions_snapshot(room)
    return snapshot


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@app.post("/api/session")
async def api_session(body: dict):
    sid = create_or_get_session(body.get("session_id"))
    return {"session_id": sid}


async def _close_room_with_notice(code: str, reason: str = "admin_left") -> None:
    """Broadcast room_closed to all sockets in the room, then drop it."""
    room = get_room(code)
    if not room:
        return
    await manager.broadcast(code, {"event": "room_closed", "reason": reason})
    # Force-disconnect each socket so the client navigates away cleanly.
    room_sockets = manager._sockets.get(code, {})
    for sid, ws in list(room_sockets.items()):
        try:
            await ws.close(code=4002)
        except Exception:
            pass
    manager._sockets.pop(code, None)
    _delete_room_and_chat(code)


# Remembers, briefly, which new room a just-closed room's admin moved to.
# Lets a tab that was still loading the *old* room (e.g. a second tab that
# lost the "create room" race) show a specific "you already opened a room
# elsewhere" message with a link to the new code, instead of a bare
# room-not-found bounce to /join. Keyed by the closed room's code.
_ADMIN_REDIRECT_TTL_SECONDS = 120
_admin_room_redirects: dict[str, tuple[str, float]] = {}


def _record_admin_redirect(old_code: str, new_code: str) -> None:
    _admin_room_redirects[old_code] = (new_code, time.time() + _ADMIN_REDIRECT_TTL_SECONDS)


def _get_admin_redirect(old_code: str) -> str | None:
    entry = _admin_room_redirects.get(old_code)
    if not entry:
        return None
    new_code, expires_at = entry
    if time.time() > expires_at:
        _admin_room_redirects.pop(old_code, None)
        return None
    return new_code


async def _close_other_rooms_admined_by(
    session_id: str, except_code: str | None, new_code: str | None = None
) -> None:
    from server.room import _rooms
    for code, room in list(_rooms.items()):
        if code == except_code:
            continue
        if room.admin_session_id == session_id and not room.finished:
            if new_code:
                _record_admin_redirect(code, new_code)
            await _close_room_with_notice(code, reason="admin_opened_new_room")


@app.post("/api/room")
async def api_create_room(request: Request, body: dict):
    session_id = body.get("session_id")
    username = body.get("username", "").strip()
    if not session_id or not username:
        raise HTTPException(400, "Session és név megadása kötelező.")
    code = _generate_room_code()
    await _close_other_rooms_admined_by(session_id, except_code=None, new_code=code)
    create_room(code, session_id, username)
    log_event(
        "room_created",
        room_code=code,
        username=username,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"code": code}


@app.get("/api/room/{code}")
async def api_get_room(code: str, session_id: str | None = None):
    room = get_room(code)
    if not room:
        new_code = _get_admin_redirect(code)
        if new_code:
            raise HTTPException(404, detail={"reason": "admin_opened_new_room", "new_code": new_code})
        raise HTTPException(404, "A szoba nem található.")
    is_player = bool(session_id and session_id in room.players)
    return {
        "code": room.code,
        "started": room.started,
        "is_player": is_player,
        "can_join_game": is_player or not room.started,
        "player_count": len(room.players),
        "edition_ids": room.edition_ids,
        "round_total": room.round_total,
        "timer_seconds": room.timer_seconds,
        "prevent_self_vote": room.prevent_self_vote,
    }


@app.get("/api/rooms")
async def api_list_rooms():
    from server.room import _rooms
    return [
        {
            "code": r.code,
            "player_count": len(r.players),
            "player_names": [p.username for p in r.players.values()],
            "started": r.started,
            "created_at": r.created_at,
        }
        for r in _rooms.values()
        # An unstarted lobby with nobody connected has no admin left to
        # approve a new joiner into — don't advertise it as joinable.
        if not r.finished and (r.started or any(p.connected for p in r.players.values()))
    ]


@app.get("/api/editions")
async def api_editions():
    conn = get_conn()
    try:
        editions = get_editions(conn)
    finally:
        conn.close()
    # Append the virtual community edition (cards live in messages.db, not cards.db)
    msg_conn = get_msg_conn()
    try:
        c_black, c_white = get_community_cards(msg_conn)
    finally:
        msg_conn.close()
    editions.append({
        "id": COMMUNITY_EDITION_ID,
        "name": COMMUNITY_EDITION_NAME,
        "community": True,
        "black_count": len(c_black),
        "white_count": len(c_white),
    })
    editions.sort(key=lambda e: edition_display.sort_key(e["name"], e.pop("sort_order", None)))
    for e in editions:
        e["name"] = edition_display.label_for(e["name"])
    return editions


@app.get("/api/editions/{edition_id}/cards")
async def api_edition_cards(edition_id: int):
    """Return one edition's playable cards for the read-only card browser."""
    if edition_id == COMMUNITY_EDITION_ID:
        conn = get_msg_conn()
        try:
            black_cards, white_cards = get_community_cards(conn)
        finally:
            conn.close()
        name = COMMUNITY_EDITION_NAME
        community = True
    else:
        conn = get_conn()
        try:
            name = get_edition_name(conn, edition_id)
            if name is None:
                raise HTTPException(404, "Ez a kártyacsomag nem létezik.")
            black_cards, white_cards = get_cards_for_edition(conn, edition_id)
        finally:
            conn.close()
        community = False

    def card_payload(card: dict) -> dict:
        return {"text": card["text"], "pick": card["pick"]}

    return {
        "id": edition_id,
        "name": edition_display.label_for(name),
        "community": community,
        "black_cards": [card_payload(card) for card in black_cards],
        "white_cards": [card_payload(card) for card in white_cards],
    }


@app.get("/api/config")
async def api_config():
    return CONFIG


@app.get("/api/voice/credentials")
async def api_voice_credentials(session_id: str = ""):
    if not _UUID_RE.match(session_id):
        raise HTTPException(400, "Érvénytelen session.")
    creds = generate_turn_credentials(session_id)
    if creds is None:
        raise HTTPException(503, "A hangkapcsolat nincs beállítva.")
    return creds


_EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,64}$')
_UUID_RE  = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
CARD_IDEA_MAX_BODY_BYTES = 4096

@app.post("/api/contact")
async def api_contact(body: dict):
    name    = str(body.get("name",    "")).strip()[:100]
    email   = str(body.get("email",   "")).strip()[:200]
    message = str(body.get("message", "")).strip()[:2000]

    if not name or not email or not message:
        raise HTTPException(400, "Minden mező kitöltése kötelező")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Érvénytelen e-mail cím")

    conn = get_msg_conn()
    try:
        save_message(conn, name, email, message)
    finally:
        conn.close()
    return {"ok": True}


def _validate_card_idea_payload(body: object) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise HTTPException(400, "Érvénytelen kérés")

    raw_card_type = body.get("card_type")
    raw_text = body.get("text")
    if not isinstance(raw_card_type, str) or not isinstance(raw_text, str):
        raise HTTPException(400, "Érvénytelen kérés")

    card_type = raw_card_type.strip().lower()
    text = raw_text.strip()

    if card_type not in ("white", "black"):
        raise HTTPException(400, "Érvénytelen kártyatípus")
    if not text:
        raise HTTPException(400, "A kártyaszöveg megadása kötelező")
    return card_type, text


@app.post("/api/card-ideas")
async def api_card_ideas(request: Request):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > CARD_IDEA_MAX_BODY_BYTES:
                raise HTTPException(413, "Túl nagy kérés")
        except ValueError:
            raise HTTPException(400, "Érvénytelen kérés")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Érvénytelen JSON")

    card_type, text = _validate_card_idea_payload(body)

    conn = get_msg_conn()
    try:
        saved = save_card_idea(conn, card_type, text)
    finally:
        conn.close()
    if not saved:
        raise HTTPException(400, "A kártyaszöveg megadása kötelező")
    return {"ok": True}


def _client_ip(request: Request) -> str | None:
    """Read the client IP from nginx's single, sanitised forwarding header."""
    candidate = request.headers.get("x-real-ip")
    if not candidate and request.client:
        candidate = request.client.host
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate.strip()))
    except ValueError:
        return None


@app.get("/api/card-ideas")
async def api_list_card_ideas(request: Request):
    voter_hash = None
    ip = _client_ip(request)
    if ip:
        try:
            voter_hash = pseudonymize_ip(ip, "card-idea-vote")
        except PrivacyConfigurationError:
            logger.error("Card-idea vote identity unavailable: privacy HMAC secret is not configured.")
    conn = get_msg_conn()
    try:
        return list_card_ideas(conn, voter_hash)
    finally:
        conn.close()


@app.post("/api/card-ideas/{idea_id}/vote")
async def api_vote_card_idea(idea_id: int, request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Érvénytelen JSON")
    vote = body.get("vote") if isinstance(body, dict) else None
    if vote not in ("like", "dislike"):
        raise HTTPException(400, "Érvénytelen szavazat")

    ip = _client_ip(request)
    if not ip:
        raise HTTPException(400, "A szavazáshoz nem azonosítható a kapcsolat.")
    try:
        voter_hash = pseudonymize_ip(ip, "card-idea-vote")
    except PrivacyConfigurationError as exc:
        logger.exception("Card-idea voting is unavailable: privacy HMAC secret is not configured.")
        raise HTTPException(503, "A szavazás átmenetileg nem elérhető.") from exc

    conn = get_msg_conn()
    try:
        result = vote_card_idea(conn, idea_id, voter_hash, vote)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(404, "Nincs ilyen kártya")
    return result


# ── WebSocket ─────────────────────────────────────────────────────────────────

async def _evict_from_other_rooms(session_id: str, current_room_code: str) -> None:
    """Remove a session from any room it's in other than the one it's joining."""
    from server.room import _rooms
    for code, room in list(_rooms.items()):
        if code == current_room_code:
            continue
        if session_id in room.pending:
            room.pending.pop(session_id, None)
        if session_id in room.players:
            await game_logic.handle_disconnect(room, session_id, manager)


@app.websocket("/ws/{room_code}")
async def websocket_endpoint(ws: WebSocket, room_code: str):
    session_id = ws.query_params.get("session_id")
    if not session_id:
        await ws.close(code=4001)
        return

    manager.evict_from_all_rooms(session_id, room_code)
    await _evict_from_other_rooms(session_id, room_code)

    room = get_room(room_code)

    # Reconnect path
    if room and session_id in room.players:
        player = room.players[session_id]
        player.connected = True
        room.last_activity = time.time()
        await manager.connect(room_code, session_id, ws)
        await manager.send(room_code, session_id, _room_snapshot(room))
        if room.started and not room.finished:
            await manager.send(room_code, session_id, {"event": "hand_update", "hand": player.hand})
    elif room and not room.started:
        # New player joining — goes to pending
        username = ws.query_params.get("username", "").strip() or f"Játékos{len(room.players)+1}"
        if _username_taken(room, username, exclude_sid=session_id):
            await ws.accept()
            await ws.send_json({"event": "username_taken", "username": username})
            await ws.close(code=4005)
            return
        player = Player(session_id=session_id, username=username)
        room.pending[session_id] = player
        room.last_activity = time.time()
        await manager.connect(room_code, session_id, ws)
        await manager.send(room_code, session_id, {"event": "pending_approval"})
        await manager.send(room_code, room.admin_session_id, _room_snapshot(room))
    elif room:
        await ws.close(code=4006)
        return
    else:
        new_code = _get_admin_redirect(room_code)
        if new_code:
            await ws.accept()
            await ws.send_json({"event": "room_replaced", "new_code": new_code})
        await ws.close(code=4004)
        return

    await _send_chat_history(room_code, session_id)

    try:
        # ws.iter_json() catches WebSocketDisconnect internally and just ends
        # the loop — it never reaches an `except` around this loop. `finally`
        # guarantees the cleanup below runs on every exit path, disconnect or
        # otherwise.
        async for raw in ws.iter_json():
            await _handle_message(session_id, room_code, raw)
    finally:
        manager.disconnect(room_code, session_id)
        room = get_room(room_code)
        if room:
            await game_logic.handle_disconnect(room, session_id, manager)


async def _handle_message(session_id: str, room_code: str, msg: dict) -> None:
    event = msg.get("event")
    room = get_room(room_code)
    if not room:
        return
    room.last_activity = time.time()

    if event == "approve_player":
        if session_id != room.admin_session_id:
            return
        target = msg.get("session_id")
        player = room.pending.pop(target, None)
        if not player:
            return
        if len(room.players) >= CONFIG.get("max_room_players", 20):
            await manager.send(room_code, target, {"event": "error", "message": "A szoba megtelt."})
            return
        if _username_taken(room, player.username, exclude_sid=target):
            await manager.send(room_code, target, {"event": "username_taken", "username": player.username})
            return
        room.players[target] = player
        room.join_order.append(target)
        log_event("player_joined", room_code=room_code, username=player.username, player_count=len(room.players))
        await manager.broadcast(room_code, _room_snapshot(room))

    elif event == "deny_player":
        if session_id != room.admin_session_id:
            return
        target = msg.get("session_id")
        player = room.pending.pop(target, None)
        if not player:
            return
        await manager.send(room_code, target, {"event": "denied", "message": "A belépési kérelmet elutasították."})
        await manager.broadcast(room_code, _room_snapshot(room))

    elif event == "configure_room":
        if session_id != room.admin_session_id or room.started:
            return
        if "edition_ids" in msg:
            room.edition_ids = msg["edition_ids"]
        if "round_total" in msg and msg["round_total"] in CONFIG.get("round_options", []):
            room.round_total = msg["round_total"]
        if "timer_seconds" in msg and msg["timer_seconds"] in CONFIG.get("timer_options_seconds", []):
            room.timer_seconds = msg["timer_seconds"]
        if "voting_mode" in msg and msg["voting_mode"] in ("czar", "vote"):
            room.voting_mode = msg["voting_mode"]
        if "prevent_self_vote" in msg and isinstance(msg["prevent_self_vote"], bool):
            room.prevent_self_vote = msg["prevent_self_vote"]
        await manager.broadcast(room_code, _room_snapshot(room))

    elif event == "start_game":
        if session_id != room.admin_session_id or room.started:
            return
        if room.pending:
            await manager.send(room_code, session_id, {"event": "error", "message": "Előbb bírálj el minden belépési kérelmet."})
            return
        if not room.edition_ids:
            await manager.send(room_code, session_id, {"event": "error", "message": "Válassz legalább egy kártyacsomagot."})
            return
        real_ids = [e for e in room.edition_ids if e != COMMUNITY_EDITION_ID]
        include_community = COMMUNITY_EDITION_ID in room.edition_ids
        if len(room.players) < 1:
            await manager.send(room_code, session_id, {"event": "error", "message": "Legalább 1 játékos szükséges."})
            return
        conn = get_conn()
        try:
            black_cards, white_cards = get_cards_for_editions(conn, real_ids)
        finally:
            conn.close()
        if include_community:
            msg_conn = get_msg_conn()
            try:
                c_black, c_white = get_community_cards(msg_conn)
            finally:
                msg_conn.close()
            black_cards += c_black
            white_cards += c_white
        # Some complete packs have only white cards (Határon túl), and with
        # no black card the game would end before the first round.
        if not black_cards:
            await manager.send(room_code, session_id, {"event": "error", "message": "A kiválasztott csomagokban nincs fekete lap – válassz még egy paklit."})
            return
        # Universal guard: a game needs enough distinct white cards to keep
        # dealing fresh hands round after round, scaled to the table size —
        # replaces the old "community edition can't be played alone" rule.
        min_white_needed = CONFIG.get("min_white_cards_per_player", 10) * len(room.players)
        if len(white_cards) < min_white_needed:
            await manager.send(room_code, session_id, {"event": "error", "message": "Túl kevés fehér lap van a kiválasztott csomagokban ennyi játékoshoz – válassz még egy paklit."})
            return
        log_event("game_started", room_code=room_code, player_count=len(room.players))
        await game_logic.start_game(room, black_cards, white_cards, manager)

    elif event == "submit_card":
        if room.phase != "submitting":
            return
        player = room.players.get(session_id)
        if not player or player.submitted_cards:
            return
        card_ids = msg.get("card_ids", [])
        pick = room.current_black.get("pick", 1) if room.current_black else 1
        by_id = {c["id"]: c for c in player.hand}
        # Preserve the player's chosen order — for multi-blank black cards the
        # order the white cards fill the blanks is meaningful.
        selected = [by_id[cid] for cid in card_ids if cid in by_id]
        if len(selected) != pick or len(set(card_ids)) != len(card_ids):
            await manager.send(room_code, session_id, {"event": "error", "message": f"Pontosan {pick} kártyát kell beküldeni."})
            return
        player.submitted_cards = selected
        for c in selected:
            player.hand.remove(c)
        await manager.send(room_code, session_id, {"event": "hand_update", "hand": player.hand})
        await manager.send(room_code, session_id, {"event": "submission_accepted"})
        await manager.broadcast(room_code, {
            "event": "player_submitted",
            "session_id": session_id,
            "username": player.username,
        })
        if room.all_submitted():
            await game_logic.reveal_cards(room, manager)

    elif event == "withdraw_submission":
        if room.phase != "submitting":
            return
        player = room.players.get(session_id)
        if not player or not player.submitted_cards:
            return
        player.hand.extend(player.submitted_cards)
        player.submitted_cards = []
        await manager.send(room_code, session_id, {"event": "hand_update", "hand": player.hand})
        await manager.broadcast(room_code, {
            "event": "submission_withdrawn",
            "session_id": session_id,
            "username": player.username,
        })

    elif event == "czar_pick":
        if room.phase != "revealed" or room.voting_mode != "czar" or session_id != room.czar_session_id:
            return
        winner_sid = msg.get("winner_session_id")
        if winner_sid not in room.players:
            return
        await game_logic.czar_pick(room, winner_sid, manager)

    elif event == "cast_vote":
        if room.phase != "revealed" or room.voting_mode != "vote":
            return
        winner_sid = msg.get("winner_session_id")
        if winner_sid not in room.players:
            return
        await game_logic.cast_vote(room, session_id, winner_sid, manager)

    elif event == "swap_card":
        player = room.players.get(session_id)
        if not player or player.swaps_remaining <= 0 or room.phase not in ("submitting",) or player.submitted_cards:
            return
        card_id = msg.get("card_id")
        old = next((c for c in player.hand if c["id"] == card_id), None)
        if not old:
            return
        if not room.white_deck:
            from server.deck import reshuffle_into_deck
            reshuffle_into_deck(room.white_deck, room.discard_pile)
            room.discard_pile = []
        if not room.white_deck:
            await manager.send(room_code, session_id, {"event": "error", "message": "Nincs több cserélhető kártya."})
            return
        from server.deck import draw_card
        new_card = draw_card(room.white_deck)
        player.hand.remove(old)
        room.discard_pile.append(old)
        player.hand.append(new_card)
        player.swaps_remaining -= 1
        await manager.send(room_code, session_id, {"event": "hand_update", "hand": player.hand})

    elif event == "request_new_hand":
        player = room.players.get(session_id)
        if not player or room.phase != "submitting" or player.submitted_cards:
            return
        from server.deck import draw_card, reshuffle_into_deck
        room.discard_pile.extend(player.hand)
        player.hand = []
        if not room.white_deck:
            reshuffle_into_deck(room.white_deck, room.discard_pile)
            room.discard_pile = []
        for _ in range(10):
            if not room.white_deck:
                reshuffle_into_deck(room.white_deck, room.discard_pile)
                room.discard_pile = []
            card = draw_card(room.white_deck)
            if card:
                player.hand.append(card)
        await manager.send(room_code, session_id, {"event": "hand_update", "hand": player.hand})
        await manager.send(room_code, session_id, {"event": "new_hand_dealt"})

    elif event == "card_feedback":
        player = room.players.get(session_id)
        if not player:
            return
        card_id = msg.get("card_id")
        liked = msg.get("liked")
        card_type = msg.get("card_type")
        if not isinstance(card_id, int) or not isinstance(liked, bool) or card_type not in ("white", "black"):
            return
        card_text = msg.get("card_text")
        card_text = card_text if isinstance(card_text, str) else ""
        comment = msg.get("comment")
        comment = comment if isinstance(comment, str) else ""
        conn = get_msg_conn()
        try:
            add_card_feedback(conn, card_id, card_text, card_type, session_id, liked, comment)
        finally:
            conn.close()

    elif event == "chat_send":
        text = msg.get("message")
        username = _chat_username(room, session_id)
        if not username or not isinstance(text, str) or not text.strip():
            return
        try:
            conn = chat_db.get_conn()
            try:
                chat_msg = chat_db.post_message(conn, room.code, username, text)
            finally:
                conn.close()
        except Exception:
            logger.exception("failed to store chat message for room %s", room.code)
            return
        if chat_msg:
            await manager.broadcast_all(room.code, {"event": "chat_message", **chat_msg})

    elif event in ("voice_offer", "voice_answer", "voice_ice"):
        # Only fully approved players may signal
        if session_id not in room.players:
            return
        if event == "voice_ice" and not check_ice_rate(session_id):
            return
        if not validate_signal(event, msg):
            return
        target = msg.get("to")
        if target not in room.players:
            return
        forward = {"event": event, "from": session_id}
        if event in ("voice_offer", "voice_answer"):
            forward["sdp"] = msg["sdp"]
        else:
            forward["candidate"]      = msg["candidate"]
            forward["sdpMid"]         = msg["sdpMid"]
            forward["sdpMLineIndex"]  = msg["sdpMLineIndex"]
        await manager.send(room_code, target, forward)

    elif event == "voice_mute_state":
        if session_id not in room.players:
            return
        muted = msg.get("muted")
        if not isinstance(muted, bool):
            return
        await manager.broadcast(room_code, {
            "event":      "voice_mute_state",
            "session_id": session_id,
            "muted":      muted,
        })

    elif event == "voice_ready":
        if session_id not in room.players:
            return
        for target_sid in list(room.players):
            if target_sid != session_id:
                await manager.send(room_code, target_sid, {"event": "voice_ready", "from": session_id})


# ── Admin stats ───────────────────────────────────────────────────────────────

@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(key: str = ""):
    if key != "***REMOVED***":
        raise HTTPException(403, "Forbidden")
    s = get_stats()
    c = s["counts"]

    rooms_created  = c.get("room_created",  0)
    games_started  = c.get("game_started",  0)
    games_ended    = c.get("game_ended",     0)
    rooms_abandoned = c.get("room_abandoned", 0)
    completion_pct = round(games_ended / games_started * 100) if games_started else 0

    # Peak hours bar chart data
    max_cnt = max(s["peak_hours"].values(), default=1)
    hour_bars = ""
    for h in range(24):
        hh = f"{h:02d}"
        cnt = s["peak_hours"].get(hh, 0)
        pct = round(cnt / max_cnt * 100) if max_cnt else 0
        hour_bars += f'<div class="hbar-col"><div class="hbar-fill" style="height:{pct}%" title="{hh}:00 — {cnt} esemény"></div><div class="hbar-label">{hh}</div></div>'

    # Daily table rows
    daily_rows = ""
    for row in s["daily"]:
        daily_rows += (
            f'<tr><td>{row["day"]}</td><td>{row["users"] or 0}</td>'
            f'<td>{row["rooms"]}</td><td>{row["games"]}</td><td>{row["completed"]}</td></tr>'
        )

    # Recent events
    event_labels = {
        "room_created": "🏠 szoba létrehozva",
        "player_joined": "👤 játékos csatlakozott",
        "game_started": "▶ játék indult",
        "game_ended": "✅ játék végzett",
        "room_abandoned": "💨 szoba elhagyva",
    }
    recent_rows = ""
    for e in s["recent"]:
        label = event_labels.get(e["event_type"], e["event_type"])
        ts    = e["ts"][:16].replace("T", " ")
        room  = e["room_code"] or "—"
        user  = e["username"] or "—"
        pc    = e["player_count"] if e["player_count"] is not None else "—"
        recent_rows += f"<tr><td>{ts}</td><td>{label}</td><td>{room}</td><td>{user}</td><td>{pc}</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Statisztika</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{--bg:#0a0a0a;--card:#2a2a2a;--card2:#3d3d3d;--text:#f5f5f5;--muted:#888;--accent:#e8e8e8;--green:#27ae60;--font:'Segoe UI',system-ui,sans-serif}}
  body{{background:var(--bg);color:var(--text);font-family:var(--font);padding:24px 16px;max-width:960px;margin:0 auto}}
  h1{{font-size:1.4rem;font-weight:800;margin-bottom:4px}}
  .subtitle{{color:var(--muted);font-size:.85rem;margin-bottom:28px}}
  .section-label{{font-size:.7rem;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:12px}}
  .stat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:28px}}
  .stat-card{{background:var(--card);border-radius:12px;padding:18px 16px}}
  .stat-num{{font-size:2rem;font-weight:800;line-height:1}}
  .stat-desc{{font-size:.78rem;color:var(--muted);margin-top:6px}}
  .dau-row{{display:flex;gap:10px;margin-bottom:28px}}
  .dau-item{{background:var(--card);border-radius:12px;padding:14px 16px;flex:1;text-align:center}}
  .dau-num{{font-size:1.5rem;font-weight:800}}
  .dau-label{{font-size:.75rem;color:var(--muted);margin-top:4px}}
  .chart-wrap{{background:var(--card);border-radius:12px;padding:20px;margin-bottom:28px}}
  .hbar-grid{{display:flex;align-items:flex-end;gap:3px;height:80px}}
  .hbar-col{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px;height:100%}}
  .hbar-fill{{width:100%;background:var(--accent);border-radius:3px 3px 0 0;transition:height .3s;min-height:2px;cursor:default}}
  .hbar-label{{font-size:.55rem;color:var(--muted)}}
  table{{width:100%;border-collapse:collapse;font-size:.83rem;margin-bottom:28px}}
  th{{text-align:left;padding:8px 12px;color:var(--muted);font-size:.75rem;font-weight:600;border-bottom:1px solid var(--card2)}}
  td{{padding:8px 12px;border-bottom:1px solid var(--card)}}
  tr:last-child td{{border-bottom:none}}
  .tbl-wrap{{background:var(--card);border-radius:12px;overflow:hidden;margin-bottom:28px}}
</style>
</head>
<body>
<h1>Statisztika</h1>
<p class="subtitle">Csak neked — <a href="?key=***REMOVED***" style="color:var(--muted)">frissítés</a></p>

<div class="section-label">Összesített</div>
<div class="stat-grid">
  <div class="stat-card"><div class="stat-num">{rooms_created}</div><div class="stat-desc">Szoba létrehozva</div></div>
  <div class="stat-card"><div class="stat-num">{games_started}</div><div class="stat-desc">Játék elindult</div></div>
  <div class="stat-card"><div class="stat-num">{games_ended}</div><div class="stat-desc">Játék befejezve</div></div>
  <div class="stat-card"><div class="stat-num">{completion_pct}%</div><div class="stat-desc">Befejezési arány</div></div>
  <div class="stat-card"><div class="stat-num">{rooms_abandoned}</div><div class="stat-desc">Szoba elhagyva</div></div>
</div>

<div class="section-label">Egyedi látogatók (IP alapján)</div>
<div class="dau-row">
  <div class="dau-item"><div class="dau-num">{s["dau"]}</div><div class="dau-label">Ma</div></div>
  <div class="dau-item"><div class="dau-num">{s["wau"]}</div><div class="dau-label">7 nap</div></div>
  <div class="dau-item"><div class="dau-num">{s["mau"]}</div><div class="dau-label">30 nap</div></div>
</div>

<div class="section-label">Csúcsidők (utolsó 30 nap, UTC)</div>
<div class="chart-wrap">
  <div class="hbar-grid">{hour_bars}</div>
</div>

<div class="section-label">Napi bontás (utolsó 14 nap)</div>
<div class="tbl-wrap">
<table>
  <thead><tr><th>Dátum</th><th>Látogatók</th><th>Szobák</th><th>Játékok</th><th>Befejezett</th></tr></thead>
  <tbody>{daily_rows or "<tr><td colspan=5 style='color:var(--muted);text-align:center;padding:20px'>Még nincs adat</td></tr>"}</tbody>
</table>
</div>

<div class="section-label">Legutóbbi események</div>
<div class="tbl-wrap">
<table>
  <thead><tr><th>Időpont</th><th>Esemény</th><th>Szoba</th><th>Felhasználó</th><th>Játékosok</th></tr></thead>
  <tbody>{recent_rows or "<tr><td colspan=5 style='color:var(--muted);text-align:center;padding:20px'>Még nincs adat</td></tr>"}</tbody>
</table>
</div>
</body>
</html>"""
    return HTMLResponse(html)


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def robots_txt():
    return FileResponse(os.path.join(BASE_DIR, "static", "robots.txt"), media_type="text/plain")


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
async def sitemap_xml():
    return FileResponse(os.path.join(BASE_DIR, "static", "sitemap.xml"), media_type="application/xml")


@app.api_route("/ads.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def ads_txt():
    return FileResponse(os.path.join(BASE_DIR, "static", "ads.txt"), media_type="text/plain")


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
async def serve_html(full_path: str):
    mapping = {
        "": "index.html",
        "join": "join.html",
        "create": "create.html",
        "kartyaotlet": "kartyaotlet.html",
        "kartyaotlet.html": "kartyaotlet.html",
        "kartyaszavazas": "kartyaszavazas.html",
        "kartyaszavazas.html": "kartyaszavazas.html",
        "kartyak": "cardlist.html",
        "adatkezeles": "adatkezeles.html",
        "adatkezeles.html": "adatkezeles.html",
    }
    filename = mapping.get(full_path.strip("/"))
    if filename:
        return FileResponse(os.path.join(BASE_DIR, "static", filename), headers=_NO_CACHE)

    # /room/<code> and /game/<code> both serve their respective HTML
    parts = full_path.strip("/").split("/")
    if len(parts) == 2 and parts[0] in ("room", "game"):
        return FileResponse(os.path.join(BASE_DIR, "static", f"{parts[0]}.html"), headers=_NO_CACHE)

    raise HTTPException(404)
