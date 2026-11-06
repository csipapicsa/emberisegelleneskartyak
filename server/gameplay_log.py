"""Anonymous gameplay log: every decision in a round, as training data for a computer player.

Spec: docs/ml/gameplay-logging.md. No player identifiers are stored; seats link a player's choices
within one game only.

The game calls the hooks below. They only update in-memory state on the room and never raise into the
game. When a round ends, its rows go to a background thread as one self-contained batch (it carries
its own card texts, game and seat rows, written with INSERT OR IGNORE), so a failed write loses that
round and nothing else. Logging is off until init() runs at app startup, and EEK_GAMEPLAY_LOG=0 keeps
it off.
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from server.paths import DATA_DIR

if TYPE_CHECKING:
    from server.room import Room

DB_PATH = DATA_DIR / "gameplay.db"
ENABLED_ENV = "EEK_GAMEPLAY_LOG"
logger = logging.getLogger(__name__)

# round.outcome
DECIDED = 0          # the czar picked or players voted
NO_SUBMISSIONS = 1   # nobody submitted a card
NO_DECISION = 2      # cards were submitted, but nobody picked or voted
CLOSED = 3           # the room closed or the game was aborted mid-round

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_text (
    text_id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL, black INTEGER NOT NULL,
    pick INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS card_text_card ON card_text (card_id);
CREATE TABLE IF NOT EXISTS model (
    model_id INTEGER PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL, UNIQUE (name, version));
CREATE TABLE IF NOT EXISTS game (
    game_id INTEGER PRIMARY KEY, day INTEGER NOT NULL, vote_mode INTEGER NOT NULL,
    self_vote INTEGER NOT NULL, rounds INTEGER NOT NULL, timer INTEGER NOT NULL,
    editions TEXT NOT NULL, app_version TEXT);
CREATE TABLE IF NOT EXISTS game_player (
    game_id INTEGER NOT NULL, seat INTEGER NOT NULL, model_id INTEGER,
    PRIMARY KEY (game_id, seat)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS round (
    game_id INTEGER NOT NULL, round_no INTEGER NOT NULL, t INTEGER NOT NULL, black INTEGER NOT NULL,
    czar INTEGER, outcome INTEGER NOT NULL, eligible INTEGER NOT NULL, shown BLOB,
    PRIMARY KEY (game_id, round_no)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS decision (
    game_id INTEGER NOT NULL, round_no INTEGER NOT NULL, seat INTEGER NOT NULL, hand BLOB NOT NULL,
    picks BLOB, p_chosen REAL, probs BLOB,
    PRIMARY KEY (game_id, round_no, seat)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hand_change (
    game_id INTEGER NOT NULL, round_no INTEGER NOT NULL, seat INTEGER NOT NULL, seq INTEGER NOT NULL,
    removed BLOB NOT NULL, added BLOB NOT NULL,
    PRIMARY KEY (game_id, round_no, seat, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS vote (
    game_id INTEGER NOT NULL, round_no INTEGER NOT NULL, voter INTEGER NOT NULL, target INTEGER NOT NULL,
    PRIMARY KEY (game_id, round_no, voter)) WITHOUT ROWID;
"""


# --- ID lists as unsigned LEB128 varints: 1 byte below 128, 2 bytes below 16,384 ---

def pack_ids(ids: Iterable[int]) -> bytes:
    out = bytearray()
    for n in ids:
        if n < 0:
            raise ValueError(f"negative id: {n}")
        while n >= 0x80:
            out.append((n & 0x7F) | 0x80)
            n >>= 7
        out.append(n)
    return bytes(out)


def unpack_ids(data: bytes) -> list[int]:
    ids: list[int] = []
    n = shift = 0
    for byte in data:
        n |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
        else:
            ids.append(n)
            n = shift = 0
    if shift:
        raise ValueError("truncated varint")
    return ids


# --- Per-game state, kept on the room ---

@dataclass
class _Round:
    no: int
    decisions: dict[int, tuple[list[int], list[int]]] = field(default_factory=dict)  # seat -> hand, picks
    changes: list[tuple[int, int, list[int], list[int]]] = field(default_factory=list)  # seat, seq, out, in
    shown: list[int] | None = None
    idle_hands: dict[int, list[int]] = field(default_factory=dict)  # non-submitters when submitting closed


@dataclass
class GameLog:
    game_id: int
    started: float
    game_row: tuple
    seats: dict[str, int] = field(default_factory=dict)       # session_id -> seat
    texts: dict[int, tuple] = field(default_factory=dict)     # text_id -> card_text row, this game
    round: _Round | None = None

    def seat(self, session_id: str) -> int:
        if session_id not in self.seats:
            self.seats[session_id] = len(self.seats)
        return self.seats[session_id]


# --- Module state (only touched from the event loop thread, except the writer's queue) ---

_writer: _Writer | None = None
_text_ids: dict[tuple[int, str], int] = {}
_next_text_id = 1
_next_game_id = 1


def enabled() -> bool:
    return _writer is not None


def init(path=None) -> None:
    """Create the schema, load the ID counters and start the writer. Called at app startup; if anything
    fails, logging stays off and the game runs as before."""
    global _writer, _next_text_id, _next_game_id
    if os.environ.get(ENABLED_ENV) == "0":
        logger.info("gameplay log disabled by %s=0", ENABLED_ENV)
        return
    path = path or DB_PATH
    try:
        conn = sqlite3.connect(path)
        try:
            conn.executescript(SCHEMA)
            _text_ids.clear()
            _text_ids.update(((card_id, text), text_id)
                             for text_id, card_id, text in conn.execute("SELECT text_id, card_id, text FROM card_text"))
            _next_text_id = (conn.execute("SELECT MAX(text_id) FROM card_text").fetchone()[0] or 0) + 1
            _next_game_id = (conn.execute("SELECT MAX(game_id) FROM game").fetchone()[0] or 0) + 1
        finally:
            conn.close()
        _writer = _Writer(path)
        _writer.start()
    except Exception:
        logger.exception("gameplay log unavailable; games run without it")
        _writer = None


def shutdown() -> None:
    """Write what's queued and stop the writer."""
    global _writer
    writer, _writer = _writer, None
    if writer:
        writer.stop()


def _text_id(log: GameLog, card: dict, black: bool = False) -> int:
    global _next_text_id
    key = (int(card["id"]), str(card["text"]))
    text_id = _text_ids.get(key)
    if text_id is None:
        text_id = _text_ids[key] = _next_text_id
        _next_text_id += 1
    if text_id not in log.texts:
        log.texts[text_id] = (text_id, key[0], int(black), int(card.get("pick") or 1) if black else 0, key[1])
    return text_id


def _ids(log: GameLog, cards: Iterable[dict]) -> list[int]:
    return [_text_id(log, c) for c in cards]


def _current_round(room: Room) -> _Round | None:
    """The round in progress: started (round_started) and not logged yet."""
    log = room.gameplay
    if log is None or log.round is None or log.round.no != room.round_num:
        return None
    return log.round


def _safe(hook):
    """Hooks must never break a game: errors are logged and swallowed."""
    def wrapper(*args, **kwargs):
        if _writer is None:
            return None
        try:
            return hook(*args, **kwargs)
        except Exception:
            logger.exception("gameplay log hook %s failed", hook.__name__)
            return None
    wrapper.__name__ = hook.__name__
    wrapper.__doc__ = hook.__doc__
    return wrapper


# --- Hooks ---

@_safe
def game_started(room: Room) -> None:
    global _next_game_id
    game_id, _next_game_id = _next_game_id, _next_game_id + 1
    now = time.time()
    game_row = (
        game_id, int(now // 86400), int(room.voting_mode == "vote"), int(not room.prevent_self_vote),
        room.round_total, room.timer_seconds, ",".join(str(e) for e in sorted(room.edition_ids)),
        os.environ.get("EEK_VERSION"),
    )
    room.gameplay = GameLog(game_id=game_id, started=now, game_row=game_row)
    for sid in room.join_order:
        room.gameplay.seat(sid)


@_safe
def round_started(room: Room) -> None:
    """After the black card is drawn. A round that is never logged (a missed path) is simply replaced."""
    if room.gameplay is not None:
        room.gameplay.round = _Round(room.round_num)


@_safe
def hand_changed(room: Room, session_id: str, removed: list[dict], added: list[dict]) -> None:
    """A swap (one card) or a new hand (all of them), before submitting."""
    rnd = _current_round(room)
    if rnd is None:
        return
    log = room.gameplay
    seat = log.seat(session_id)
    seq = sum(1 for c in rnd.changes if c[0] == seat)
    rnd.changes.append((seat, seq, _ids(log, removed), _ids(log, added)))


@_safe
def submitted(room: Room, session_id: str, hand: list[dict], picks: list[dict]) -> None:
    """hand: the hand as shown when submitting (picked cards included); picks in blank order."""
    rnd = _current_round(room)
    if rnd is None:
        return
    log = room.gameplay
    rnd.decisions[log.seat(session_id)] = (_ids(log, hand), _ids(log, picks))


@_safe
def withdrawn(room: Room, session_id: str) -> None:
    rnd = _current_round(room)
    if rnd is not None:
        rnd.decisions.pop(room.gameplay.seat(session_id), None)


@_safe
def submissions_closed(room: Room) -> None:
    """Before inactive players are kicked: remember the hands of those who didn't submit, so they still
    get a decision row (picks NULL) after they've left the room."""
    rnd = _current_round(room)
    if rnd is None:
        return
    log = room.gameplay
    for sid, player in room.players.items():
        seat = log.seat(sid)
        if seat not in rnd.decisions:
            rnd.idle_hands[seat] = _ids(log, player.hand)


@_safe
def revealed(room: Room, submission_sids: list[str]) -> None:
    """submission_sids: authors in the order the submissions are shown."""
    rnd = _current_round(room)
    if rnd is not None:
        rnd.shown = [room.gameplay.seat(sid) for sid in submission_sids]


@_safe
def round_ended(room: Room, outcome: int, votes: dict[str, str] | None = None) -> None:
    """Call before the submitted cards are cleared. votes: voter session -> author session (a czar pick
    is the czar's single vote). Each round is logged once; later calls for it are ignored."""
    rnd = _current_round(room)
    if rnd is None or room.current_black is None:
        return
    log = room.gameplay
    log.round = None

    decisions = []
    for sid, player in room.players.items():
        seat = log.seat(sid)
        if seat in rnd.decisions:
            hand, picks = rnd.decisions[seat]
            decisions.append((log.game_id, rnd.no, seat, pack_ids(hand), pack_ids(picks)))
        else:
            decisions.append((log.game_id, rnd.no, seat, pack_ids(_ids(log, player.hand)), None))
    # Players who have left the room since (kicked for inactivity, or disconnected and removed).
    present = {d[2] for d in decisions}
    for seat, (hand, picks) in rnd.decisions.items():
        if seat not in present:
            decisions.append((log.game_id, rnd.no, seat, pack_ids(hand), pack_ids(picks)))
            present.add(seat)
    for seat, hand in rnd.idle_hands.items():
        if seat not in present:
            decisions.append((log.game_id, rnd.no, seat, pack_ids(hand), None))

    czar = log.seat(room.czar_session_id) if room.voting_mode == "czar" and room.czar_session_id else None
    eligible = 1 if room.voting_mode == "czar" else sum(1 for p in room.players.values() if p.connected)
    black = _text_id(log, room.current_black, black=True)
    t = max(0, int((room.round_start_time or log.started) - log.started))

    batch = {
        "texts": list(log.texts.values()),
        "game": log.game_row,
        "seats": [(log.game_id, seat) for seat in log.seats.values()],
        "round": (log.game_id, rnd.no, t, black, czar, outcome, eligible,
                  pack_ids(rnd.shown) if rnd.shown is not None else None),
        "decisions": decisions,
        "changes": [(log.game_id, rnd.no, seat, seq, pack_ids(out), pack_ids(inn))
                    for seat, seq, out, inn in rnd.changes],
        "votes": [(log.game_id, rnd.no, log.seat(voter), log.seat(target))
                  for voter, target in (votes or {}).items()],
    }
    _writer.submit(batch)


@_safe
def room_closed(room: Room) -> None:
    """The room is being deleted. A round still in progress is logged as CLOSED."""
    round_ended(room, CLOSED)


# --- Background writer ---

class _Writer:
    def __init__(self, path) -> None:
        self._path = path
        self._queue: queue.Queue[dict | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="gameplay-log", daemon=True)
        self._confirmed_texts: set[int] = set()

    def start(self) -> None:
        self._thread.start()

    def submit(self, batch: dict) -> None:
        self._queue.put(batch)

    def stop(self, timeout: float = 10) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def drain(self) -> None:
        """Block until everything queued so far is written (tests)."""
        self._queue.join()

    def _run(self) -> None:
        conn = sqlite3.connect(self._path, timeout=10)
        try:
            while True:
                batch = self._queue.get()
                try:
                    if batch is None:
                        return
                    self._write(conn, batch)
                except Exception:
                    logger.exception("gameplay log: a round could not be written")
                finally:
                    self._queue.task_done()
        finally:
            conn.close()

    def _write(self, conn: sqlite3.Connection, batch: dict) -> None:
        new_texts = [row for row in batch["texts"] if row[0] not in self._confirmed_texts]
        with conn:  # one transaction: the whole round or nothing
            conn.executemany("INSERT OR IGNORE INTO card_text VALUES (?,?,?,?,?)", new_texts)
            conn.execute("INSERT OR IGNORE INTO game VALUES (?,?,?,?,?,?,?,?)", batch["game"])
            conn.executemany("INSERT OR IGNORE INTO game_player (game_id, seat) VALUES (?,?)", batch["seats"])
            conn.execute("INSERT OR IGNORE INTO round VALUES (?,?,?,?,?,?,?,?)", batch["round"])
            conn.executemany("INSERT OR IGNORE INTO decision (game_id, round_no, seat, hand, picks) "
                             "VALUES (?,?,?,?,?)", batch["decisions"])
            conn.executemany("INSERT OR IGNORE INTO hand_change VALUES (?,?,?,?,?,?)", batch["changes"])
            conn.executemany("INSERT OR IGNORE INTO vote VALUES (?,?,?,?)", batch["votes"])
        self._confirmed_texts.update(row[0] for row in new_texts)
