"""The restore drill's file side: picking the newest backup set, restoring the layout, checking integrity.

The podman part (starting the image on the restored copy) is exercised by running the drill on the server;
here it is switched off with --no-app.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import backup_databases, restore_drill


def make_db(path: Path, rows: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()


def make_set(backup_dir: Path, ts: str, names=("analytics", "database__card_master")) -> None:
    for name in names:
        make_db(backup_dir / f"{name}--{ts}.db")


def test_newest_backup_set_picks_latest_timestamp_and_maps_paths(tmp_path):
    make_set(tmp_path, "20261112T020500Z")
    make_set(tmp_path, "20261113T020500Z")
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "stray.db").write_bytes(b"")  # no timestamp: not part of any set

    ts, files = restore_drill.newest_backup_set(tmp_path)

    assert ts == "20261113T020500Z"
    assert set(files) == {"analytics.db", "database/card_master.db"}
    assert files["database/card_master.db"].name == "database__card_master--20261113T020500Z.db"


def test_newest_backup_set_reads_what_the_backup_script_writes(tmp_path):
    data = tmp_path / "data"
    make_db(data / "gameplay.db")
    make_db(data / "database" / "card_master.db")
    backups = tmp_path / "backups"
    backups.mkdir()
    backup_databases.main(["--project-root", str(data), "--backup-dir", str(backups)])

    _, files = restore_drill.newest_backup_set(backups)

    assert set(files) == {"gameplay.db", "database/card_master.db"}


def test_newest_backup_set_without_backups_stops(tmp_path):
    with pytest.raises(SystemExit):
        restore_drill.newest_backup_set(tmp_path)


def test_restore_recreates_production_layout(tmp_path):
    make_set(tmp_path / "b", "20261113T020500Z")
    _, files = restore_drill.newest_backup_set(tmp_path / "b")

    restored = restore_drill.restore(files, tmp_path / "work")

    assert sorted(p.relative_to(tmp_path / "work").as_posix() for p in restored) == [
        "analytics.db", "database/card_master.db"]
    assert restore_drill.table_rows(tmp_path / "work" / "analytics.db") == 1


def test_integrity_ok_and_corrupt(tmp_path):
    good = tmp_path / "good.db"
    make_db(good, rows=2000)
    assert restore_drill.integrity(good) == "ok"

    bad = tmp_path / "bad.db"
    data = bytearray(good.read_bytes())
    data[4096 * 2:4096 * 2 + 200] = b"\xff" * 200  # overwrite part of a table page
    bad.write_bytes(bytes(data))
    try:
        result = restore_drill.integrity(bad)
    except sqlite3.DatabaseError:  # damage SQLite can't even open past is also a detected failure
        result = "error"
    assert result != "ok"


def test_comparable_editions_ignores_community():
    editions = [
        {"name": "Alap", "black_count": 10, "white_count": 50},
        {"name": "Ti küldtétek", "community": True, "black_count": 3, "white_count": 9},
    ]
    assert restore_drill.comparable_editions(editions) == {"Alap": (10, 50)}


def test_main_files_only_passes_and_fails(tmp_path, capsys):
    make_set(tmp_path / "ok", "20261113T020500Z")
    assert restore_drill.main(["--backup-dir", str(tmp_path / "ok"), "--no-app"]) == 0
    assert "RESTORE DRILL PASSED" in capsys.readouterr().out

    make_set(tmp_path / "nocards", "20261113T020500Z", names=("analytics",))
    assert restore_drill.main(["--backup-dir", str(tmp_path / "nocards"), "--no-app"]) == 1
    assert "database/card_master.db missing" in capsys.readouterr().out
