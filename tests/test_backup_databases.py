from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

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
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    created, deleted = backup_databases.run(tmp_path, now=datetime.now(timezone.utc))

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
