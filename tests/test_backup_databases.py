from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import backup_databases

SCRIPT = Path(backup_databases.__file__)


def make_database(path, value: str) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    con.execute("INSERT INTO values_table VALUES (?)", (value,))
    con.commit()
    con.close()


def test_run_backs_up_every_top_level_database_and_prunes_expired(tmp_path):
    make_database(tmp_path / "one.db", "first")
    make_database(tmp_path / "two.db", "second")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    expired = backup_dir / "old--20200101T000000Z.db"
    make_database(expired, "old")
    old_timestamp = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    created, deleted = backup_databases.run(tmp_path, now=datetime.now(UTC))

    assert len(created) == 2
    assert deleted == [expired]
    assert not expired.exists()
    values = []
    for backup in created:
        con = sqlite3.connect(backup)
        values.append(con.execute("SELECT value FROM values_table").fetchone()[0])
        con.close()
        assert backup.stat().st_mode & 0o777 == 0o600
    assert sorted(values) == ["first", "second"]


def test_run_backs_up_databases_in_the_database_subdir(tmp_path):
    # database/card_master.db is the uploaded card source; it must be backed
    # up too, without clashing with a top-level database of the same name.
    make_database(tmp_path / "card_master.db", "top")
    (tmp_path / "database").mkdir()
    make_database(tmp_path / "database" / "card_master.db", "uploaded")
    now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    created, _ = backup_databases.run(tmp_path, now=now)

    assert sorted(path.name for path in created) == [
        "card_master--20261025T120000Z.db",
        "database__card_master--20261025T120000Z.db",
    ]
    con = sqlite3.connect(tmp_path / "backups" / "database__card_master--20261025T120000Z.db")
    assert con.execute("SELECT value FROM values_table").fetchone()[0] == "uploaded"
    con.close()


def test_gameplay_log_is_backed_up(tmp_path):
    """The ML training data (server/gameplay_log.py) sits in the data directory root (tests/test_paths.py),
    so the daily backup picks it up with the other databases."""
    from server import gameplay_log

    gameplay_log.init(tmp_path / "gameplay.db")
    gameplay_log.shutdown()
    assert tmp_path / "gameplay.db" in backup_databases.sqlite_databases(tmp_path)


def test_backup_dir_outside_the_project_root(tmp_path):
    """The MLflow backup (deploy/systemd/eek-mlflow-backup.service) reads /srv/eek/mlflow and writes to
    /srv/eek/backups/mlflow, so the backups don't sit in the folder MLflow's container mounts."""
    data = tmp_path / "mlflow"
    data.mkdir()
    make_database(data / "mlflow.db", "runs")
    make_database(data / "basic_auth.db", "users")
    backup_dir = tmp_path / "backups" / "mlflow"  # parent doesn't exist yet
    now = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)

    created, _ = backup_databases.run(data, now=now, backup_dir=backup_dir)

    assert sorted(path.name for path in created) == [
        "basic_auth--20261107T030000Z.db",
        "mlflow--20261107T030000Z.db",
    ]
    assert all(path.parent == backup_dir for path in created)
    assert not (data / "backups").exists()
    assert backup_dir.stat().st_mode & 0o777 == 0o700


def test_main_accepts_backup_dir(tmp_path, capsys):
    make_database(tmp_path / "one.db", "x")
    out = tmp_path / "elsewhere"

    assert backup_databases.main(["--project-root", str(tmp_path), "--backup-dir", str(out)]) == 0

    assert len(list(out.glob("one--*.db"))) == 1
    assert "Created 1 backup(s)" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("python3.9") is None, reason="python3.9 not installed")
def test_script_runs_on_the_hosts_python_39(tmp_path):
    """The systemd units run this script with the server's /usr/bin/python3 (3.9), not the app's 3.11.
    On 2026-11-07 a lint fix (datetime.UTC, 3.11+) made the nightly backup crash on import."""
    make_database(tmp_path / "one.db", "x")

    result = subprocess.run(
        ["python3.9", str(SCRIPT), "--project-root", str(tmp_path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "backups").glob("one--*.db"))) == 1
