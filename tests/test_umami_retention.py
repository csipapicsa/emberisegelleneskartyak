"""scripts/umami_retention.py must enforce the Umami retention period that
static/adatkezeles.html promises."""
from __future__ import annotations

import os

from scripts.umami_retention import EVENT_TABLES, RETENTION, build_sql

ROOT = os.path.dirname(os.path.dirname(__file__))


def test_deletes_every_event_table_then_orphaned_sessions():
    sql = build_sql()
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    for table in EVENT_TABLES:
        assert f"DELETE FROM {table} WHERE created_at < now() - interval '2 years';" in sql
    # sessions only after their events are gone, and only orphaned ones
    assert sql.index("DELETE FROM session ") > sql.index("DELETE FROM website_event ")
    assert "NOT EXISTS (SELECT 1 FROM website_event e WHERE e.session_id = session.session_id)" in sql


def test_dry_run_never_deletes():
    sql = build_sql(dry_run=True)
    assert "DELETE" not in sql and "BEGIN" not in sql
    assert sql.count("SELECT '") == len(EVENT_TABLES) + 1


def test_privacy_notice_matches_retention():
    assert RETENTION == "2 years"
    with open(os.path.join(ROOT, "static", "adatkezeles.html"), encoding="utf-8") as f:
        html = f.read()
    assert "Umami" in html
    assert "Megőrzési idő: legfeljebb 2 év" in html
