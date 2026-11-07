"""Freeze gameplay.db into a versioned snapshot: Parquet files plus a manifest with a content hash.

    python -m ml.export                                  # gameplay.db from the data dir -> ml-data/snapshots/
    python -m ml.export --db sample-data/gameplay.db --out /tmp/snapshots

Every training run reads a snapshot, never the live database, and logs the snapshot's content hash, so a
run can always be traced back to exactly the data it saw (and rebuilt from it).

What goes in:
- every game with at least two players; solo games (one game_player row) are left out;
- rounds up to a game's closed round (outcome 3); later rounds are left out: until 2026-11-07 a deleted
  room kept playing its remaining rounds to nobody (fixed in server/game.py, close_room);
- the card catalogue (card_text), the games, rounds, decisions, hand changes and votes, with the ID-list
  BLOBs decoded into list columns;
- model players' rows too, flagged (decisions.is_model, votes.voter_is_model, games.model_seats): training
  leaves them out, so no model learns from an older model, but monitoring compares the bot with humans.

The content hash is computed from the rows, not from the Parquet bytes, so the same data gives the same
hash whatever the pyarrow version. The snapshot folder is named after the last game day and the hash
(e.g. 2026-11-07_3f2a9c1b0d4e); exporting unchanged data again finds the existing snapshot and writes
nothing, which is how the retraining trigger can tell there's nothing new.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ml import DATA_DIR
from server.gameplay_log import CLOSED, DB_PATH, unpack_ids

SNAPSHOT_FORMAT = 1
DEFAULT_OUT = DATA_DIR / "snapshots"
MANIFEST = "manifest.json"

_IDS = pa.list_(pa.int32())
# Table name -> Parquet schema. The order of the tables and columns is part of the content hash.
SCHEMAS: dict[str, pa.Schema] = {
    "card_texts": pa.schema([("text_id", pa.int32()), ("card_id", pa.int64()), ("black", pa.bool_()),
                             ("pick", pa.int8()), ("text", pa.string())]),
    "games": pa.schema([("game_id", pa.int32()), ("day", pa.int32()), ("vote_mode", pa.bool_()),
                        ("self_vote", pa.bool_()), ("rounds", pa.int16()), ("timer", pa.int16()),
                        ("editions", pa.string()), ("app_version", pa.string()), ("players", pa.int8()),
                        ("model_seats", _IDS)]),
    "rounds": pa.schema([("game_id", pa.int32()), ("round_no", pa.int16()), ("t", pa.int32()),
                         ("black", pa.int32()), ("czar", pa.int8()), ("outcome", pa.int8()),
                         ("eligible", pa.int8()), ("shown", _IDS)]),
    "decisions": pa.schema([("game_id", pa.int32()), ("round_no", pa.int16()), ("seat", pa.int8()),
                            ("is_model", pa.bool_()), ("hand", _IDS), ("picks", _IDS)]),
    "hand_changes": pa.schema([("game_id", pa.int32()), ("round_no", pa.int16()), ("seat", pa.int8()),
                               ("seq", pa.int16()), ("removed", _IDS), ("added", _IDS)]),
    "votes": pa.schema([("game_id", pa.int32()), ("round_no", pa.int16()), ("voter", pa.int8()),
                        ("voter_is_model", pa.bool_()), ("target", pa.int8())]),
}


def _ids(blob: bytes | None) -> list[int] | None:
    return None if blob is None else unpack_ids(blob)


def _consistent_copy(db_path: Path, dest: Path) -> None:
    """SQLite's backup API: a consistent copy even while the game is writing."""
    source = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def read_tables(db_path: Path) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """The snapshot's rows, sorted by primary key, and counts of what was left out."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "gameplay.db"
        _consistent_copy(db_path, copy)
        conn = sqlite3.connect(copy)
        try:
            return _read(conn)
        finally:
            conn.close()


def _read(conn: sqlite3.Connection) -> tuple[dict[str, list[dict]], dict[str, int]]:
    q = conn.execute
    seats: dict[int, int] = dict(q("SELECT game_id, COUNT(*) FROM game_player GROUP BY game_id"))
    model_seats: dict[int, list[int]] = {}
    for game_id, seat in q("SELECT game_id, seat FROM game_player WHERE model_id IS NOT NULL ORDER BY 1, 2"):
        model_seats.setdefault(game_id, []).append(seat)
    kept = {game_id for game_id, n in seats.items() if n >= 2}
    closed_at: dict[int, int] = dict(q("SELECT game_id, MIN(round_no) FROM round WHERE outcome = ? GROUP BY game_id",
                                       (CLOSED,)))

    def keep(game_id: int, round_no: int | None = None) -> bool:
        return game_id in kept and (round_no is None or round_no <= closed_at.get(game_id, round_no))

    tables: dict[str, list[dict]] = {name: [] for name in SCHEMAS}
    for text_id, card_id, black, pick, text in q("SELECT * FROM card_text ORDER BY text_id"):
        tables["card_texts"].append({"text_id": text_id, "card_id": card_id, "black": bool(black),
                                     "pick": pick, "text": text})
    for row in q("SELECT * FROM game ORDER BY game_id"):
        game_id, day, vote_mode, self_vote, rounds, timer, editions, app_version = row
        if keep(game_id):
            tables["games"].append({"game_id": game_id, "day": day, "vote_mode": bool(vote_mode),
                                    "self_vote": bool(self_vote), "rounds": rounds, "timer": timer,
                                    "editions": editions, "app_version": app_version, "players": seats[game_id],
                                    "model_seats": model_seats.get(game_id, [])})
    for game_id, round_no, t, black, czar, outcome, eligible, shown in q("SELECT * FROM round ORDER BY 1, 2"):
        if keep(game_id, round_no):
            tables["rounds"].append({"game_id": game_id, "round_no": round_no, "t": t, "black": black,
                                     "czar": czar, "outcome": outcome, "eligible": eligible, "shown": _ids(shown)})
    for game_id, round_no, seat, hand, picks, _p, _probs in q("SELECT * FROM decision ORDER BY 1, 2, 3"):
        if keep(game_id, round_no):
            tables["decisions"].append({"game_id": game_id, "round_no": round_no, "seat": seat,
                                        "is_model": seat in model_seats.get(game_id, ()),
                                        "hand": _ids(hand), "picks": _ids(picks)})
    for game_id, round_no, seat, seq, removed, added in q("SELECT * FROM hand_change ORDER BY 1, 2, 3, 4"):
        if keep(game_id, round_no):
            tables["hand_changes"].append({"game_id": game_id, "round_no": round_no, "seat": seat, "seq": seq,
                                           "removed": _ids(removed), "added": _ids(added)})
    for game_id, round_no, voter, target in q("SELECT * FROM vote ORDER BY 1, 2, 3"):
        if keep(game_id, round_no):
            tables["votes"].append({"game_id": game_id, "round_no": round_no, "voter": voter,
                                    "voter_is_model": voter in model_seats.get(game_id, ()), "target": target})

    left_out = {"solo_games": sum(1 for n in seats.values() if n < 2),
                "rounds_after_close": sum(q("SELECT COUNT(*) FROM round WHERE game_id = ? AND round_no > ?",
                                            (g, r)).fetchone()[0] for g, r in closed_at.items() if g in kept),
                "games_without_players": q("SELECT COUNT(*) FROM game WHERE game_id NOT IN "
                                           "(SELECT game_id FROM game_player)").fetchone()[0]}
    return tables, left_out


def content_hash(tables: dict[str, list[dict]]) -> str:
    """SHA-256 over every row in a canonical form (JSON, keys in schema order). Independent of Parquet."""
    digest = hashlib.sha256()
    for name, schema in SCHEMAS.items():
        digest.update(f"table:{name}\n".encode())
        for row in tables[name]:
            values = [row[field.name] for field in schema]
            digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def _git_commit() -> str | None:
    """The code's commit: from git, or inside the training image (no git, no .git) from EEK_VERSION,
    which the image build sets to the commit it was built from."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, check=True, timeout=5)
        return out.stdout.strip() or os.environ.get("EEK_VERSION")
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("EEK_VERSION")


def _day_to_date(day: int) -> str:
    return (date(1970, 1, 1) + timedelta(days=day)).isoformat()


def export(db_path: str | os.PathLike = DB_PATH, out_root: str | os.PathLike = DEFAULT_OUT) -> tuple[Path, bool]:
    """Write a snapshot of db_path below out_root. Returns its folder and whether it was newly written
    (False: a snapshot with the same content already existed)."""
    db_path, out_root = Path(db_path), Path(out_root)
    if not db_path.is_file():
        raise FileNotFoundError(f"no gameplay database at {db_path}")
    tables, left_out = read_tables(db_path)
    if not tables["games"]:
        raise ValueError(f"{db_path} has no games with at least two players")
    digest = content_hash(tables)
    days = [g["day"] for g in tables["games"]]
    folder = out_root / f"{_day_to_date(max(days))}_{digest[:12]}"
    if (folder / MANIFEST).is_file():
        return folder, False

    out_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=out_root, prefix=".tmp-"))
    try:
        for name, schema in SCHEMAS.items():
            pq.write_table(pa.Table.from_pylist(tables[name], schema=schema), tmp / f"{name}.parquet")
        manifest = {
            "format": SNAPSHOT_FORMAT,
            "content_hash": digest,
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": str(db_path.resolve()),
            "git_commit": _git_commit(),
            "days": [_day_to_date(min(days)), _day_to_date(max(days))],
            "rows": {name: len(rows) for name, rows in tables.items()},
            "left_out": left_out,
            "model_rows": {"decisions": sum(d["is_model"] for d in tables["decisions"]),
                           "votes": sum(v["voter_is_model"] for v in tables["votes"])},
        }
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        tmp.rename(folder)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return folder, True


def read_snapshot(folder: str | os.PathLike) -> tuple[dict, dict[str, list[dict]]]:
    """The manifest and every table of a snapshot, as lists of dicts."""
    folder = Path(folder)
    manifest = json.loads((folder / MANIFEST).read_text())
    if manifest.get("format") != SNAPSHOT_FORMAT:
        raise ValueError(f"{folder}: snapshot format {manifest.get('format')}, expected {SNAPSHOT_FORMAT}")
    tables = {name: pq.read_table(folder / f"{name}.parquet").to_pylist() for name in SCHEMAS}
    return manifest, tables


def latest_snapshot(out_root: str | os.PathLike = DEFAULT_OUT) -> Path:
    """The most recently created snapshot below out_root."""
    manifests = [p for p in Path(out_root).glob(f"*/{MANIFEST}")]
    if not manifests:
        raise FileNotFoundError(f"no snapshots in {out_root}; run python -m ml.export first")
    return max(manifests, key=lambda p: (json.loads(p.read_text())["created"], p.parent.name)).parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"gameplay database (default: {DB_PATH})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"snapshot folder (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)
    try:
        folder, new = export(args.db, args.out)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(f"{'New snapshot' if new else 'Unchanged, existing snapshot'}: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
