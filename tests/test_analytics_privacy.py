from __future__ import annotations

import sqlite3
from datetime import datetime

from server import analytics
from server.privacy import pseudonymize_ip


def test_privacy_migration_removes_unneeded_legacy_event_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr(analytics, "DB_PATH", db_path)
    analytics.init_analytics()
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO events (event_type, ts, room_code, username, ip_hash, user_agent)
           VALUES ('room_created', ?, '1234', 'Feri', 'legacy', 'UA')""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    con.commit()
    con.close()

    analytics.init_analytics(migrate_privacy=True)
    con = sqlite3.connect(db_path)
    assert con.execute(
        "SELECT room_code, username, ip_hash, user_agent FROM events"
    ).fetchone() == ("1234", "Feri", "legacy", "UA")
    con.close()


def test_new_events_store_only_purpose_bound_ip_hmac(tmp_path, monkeypatch):
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr(analytics, "DB_PATH", db_path)
    monkeypatch.setenv("EEK_PRIVACY_HMAC_SECRET", "test-only-privacy-secret")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    analytics.log_event(
        "room_created",
        room_code="1234",
        username="Feri",
        ip="1.2.3.4",
        user_agent="Example Browser",
    )

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT room_code, username, ip_hash, user_agent FROM events"
    ).fetchone()
    con.close()
    assert row == ("1234", "Feri", pseudonymize_ip("1.2.3.4", "internal-statistics"), "Example Browser")
