"""ml/export.py: gameplay.db -> a versioned Parquet snapshot. Runs on the made-up sample data."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from ml import export
from scripts import make_sample_gameplay_db as sample
from server.gameplay_log import CLOSED, pack_ids


@pytest.fixture(scope="module")
def sample_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "gameplay.db"
    sample.generate(path, games=30, seed=2)
    return path


@pytest.fixture(scope="module")
def snapshot(sample_db, tmp_path_factory):
    folder, new = export.export(sample_db, tmp_path_factory.mktemp("snapshots"))
    assert new
    return folder


def counts(db, sql, *params):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def test_snapshot_files_and_manifest(snapshot, sample_db):
    manifest = json.loads((snapshot / export.MANIFEST).read_text())
    assert sorted(p.name for p in snapshot.iterdir()) == sorted(
        [export.MANIFEST] + [f"{name}.parquet" for name in export.SCHEMAS])
    assert manifest["format"] == export.SNAPSHOT_FORMAT
    assert snapshot.name == f"{manifest['days'][1]}_{manifest['content_hash'][:12]}"
    assert manifest["source"] == str(sample_db.resolve())
    assert manifest["rows"]["card_texts"] == counts(sample_db, "SELECT COUNT(*) FROM card_text")


def test_solo_games_are_left_out(snapshot, sample_db):
    manifest, tables = export.read_snapshot(snapshot)
    solo = counts(sample_db, "SELECT COUNT(*) FROM (SELECT game_id FROM game_player GROUP BY game_id HAVING COUNT(*) = 1)")
    assert solo > 0
    assert manifest["left_out"]["solo_games"] == solo
    assert len(tables["games"]) == counts(sample_db, "SELECT COUNT(*) FROM game") - solo
    assert all(g["players"] >= 2 for g in tables["games"])
    kept = {g["game_id"] for g in tables["games"]}
    for name in ("rounds", "decisions", "hand_changes", "votes"):
        assert {row["game_id"] for row in tables[name]} <= kept


def test_model_rows_are_kept_and_flagged(snapshot):
    manifest, tables = export.read_snapshot(snapshot)
    model_seats = {(g["game_id"], s) for g in tables["games"] for s in g["model_seats"]}
    assert model_seats
    for d in tables["decisions"]:
        assert d["is_model"] == ((d["game_id"], d["seat"]) in model_seats)
    for v in tables["votes"]:
        assert v["voter_is_model"] == ((v["game_id"], v["voter"]) in model_seats)
    assert manifest["model_rows"]["decisions"] == sum(d["is_model"] for d in tables["decisions"]) > 0


def test_id_lists_are_decoded(snapshot):
    _, tables = export.read_snapshot(snapshot)
    d = next(d for d in tables["decisions"] if d["picks"] is not None)
    assert isinstance(d["hand"], list) and len(d["hand"]) == sample.HAND_SIZE
    assert set(d["picks"]) <= set(d["hand"])
    assert any(r["shown"] is None for r in tables["rounds"])


def test_content_hash_is_reproducible_from_the_parquet_files(snapshot):
    manifest, tables = export.read_snapshot(snapshot)
    assert export.content_hash(tables) == manifest["content_hash"]


def test_unchanged_data_finds_the_existing_snapshot(snapshot, sample_db):
    folder, new = export.export(sample_db, snapshot.parent)
    assert (folder, new) == (snapshot, False)
    assert len([p for p in snapshot.parent.iterdir()]) == 1


def test_new_data_gives_a_new_snapshot(sample_db, tmp_path):
    db = tmp_path / "gameplay.db"
    shutil.copy(sample_db, db)
    first, _ = export.export(db, tmp_path / "out")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE game SET timer = timer + 1 WHERE game_id = (SELECT MAX(game_id) FROM game)")
    conn.commit()
    conn.close()
    second, new = export.export(db, tmp_path / "out")
    assert new and second != first


def test_rounds_after_a_closed_round_are_left_out(sample_db, tmp_path):
    """Until 2026-11-07 a deleted room kept playing to nobody, leaving empty rounds after its closed round."""
    db = tmp_path / "gameplay.db"
    shutil.copy(sample_db, db)
    conn = sqlite3.connect(db)
    game_id, round_no = conn.execute(
        "SELECT r.game_id, r.round_no FROM round r JOIN game_player gp USING (game_id) WHERE r.outcome = ? "
        "GROUP BY r.game_id HAVING COUNT(DISTINCT gp.seat) > 1 LIMIT 1", (CLOSED,)).fetchone()
    hand = conn.execute("SELECT hand FROM decision WHERE game_id = ? AND round_no = ? AND seat = 0",
                        (game_id, round_no)).fetchone()[0]
    black = conn.execute("SELECT black FROM round WHERE game_id = ? AND round_no = ?", (game_id, round_no)).fetchone()[0]
    for extra in (1, 2):
        conn.execute("INSERT INTO round VALUES (?, ?, 0, ?, NULL, 1, 1, NULL)", (game_id, round_no + extra, black))
        conn.execute("INSERT INTO decision (game_id, round_no, seat, hand) VALUES (?, ?, 0, ?)",
                     (game_id, round_no + extra, hand))
    conn.commit()
    conn.close()

    folder, _ = export.export(db, tmp_path / "out")
    manifest, tables = export.read_snapshot(folder)
    assert manifest["left_out"]["rounds_after_close"] == 2
    assert max(r["round_no"] for r in tables["rounds"] if r["game_id"] == game_id) == round_no
    assert not [d for d in tables["decisions"] if d["game_id"] == game_id and d["round_no"] > round_no]


def test_missing_database_and_no_multiplayer_games(tmp_path):
    with pytest.raises(FileNotFoundError):
        export.export(tmp_path / "nope.db", tmp_path / "out")
    db = tmp_path / "solo.db"
    conn = sqlite3.connect(db)
    from server.gameplay_log import SCHEMA
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO game VALUES (1, 20763, 0, 0, 10, 60, '1', NULL)")
    conn.execute("INSERT INTO game_player (game_id, seat) VALUES (1, 0)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="at least two players"):
        export.export(db, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_unknown_snapshot_format_is_refused(snapshot, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(snapshot, copy)
    manifest = json.loads((copy / export.MANIFEST).read_text())
    manifest["format"] = 99
    (copy / export.MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="format 99"):
        export.read_snapshot(copy)


def test_main(sample_db, tmp_path, capsys):
    out = tmp_path / "out"
    assert export.main(["--db", str(sample_db), "--out", str(out)]) == 0
    assert "New snapshot" in capsys.readouterr().out
    assert export.main(["--db", str(sample_db), "--out", str(out)]) == 0
    assert "Unchanged" in capsys.readouterr().out
    assert export.main(["--db", str(tmp_path / "missing.db"), "--out", str(out)]) == 1
    assert "Export failed" in capsys.readouterr().err


def test_pack_ids_matches_what_the_export_decodes():
    assert export._ids(pack_ids([1, 300, 20000])) == [1, 300, 20000]
    assert export._ids(None) is None
