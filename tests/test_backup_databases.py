from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

from scripts import backup_databases


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
