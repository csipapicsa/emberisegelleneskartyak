from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

from server.paths import DATA_DIR
from server.privacy import PrivacyConfigurationError, pseudonymize_ip

DB_PATH = DATA_DIR / "analytics.db"
RETENTION_DAYS = 90
logger = logging.getLogger(__name__)

_BOT_PATTERNS = [
    "bot", "crawler", "spider", "crawl", "slurp",
    "python-requests", "curl/", "wget/", "scrapy",
    "headlesschrome", "phantomjs", "selenium",
    "facebookexternalhit", "twitterbot", "linkedinbot",
    "applebot", "ia_archiver", "semrushbot", "ahrefsbot", "mj12bot",
]


def init_analytics(*, migrate_privacy: bool = False) -> None:
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT    NOT NULL,
            ts           TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            room_code    TEXT,
            username     TEXT,
            ip_hash      TEXT,
            player_count INTEGER,
            user_agent   TEXT,
            extra        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ts   ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_type ON events(event_type);
        CREATE TABLE IF NOT EXISTS analytics_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    if migrate_privacy:
        version = con.execute(
            "SELECT value FROM analytics_meta WHERE key = 'privacy_schema_version'"
        ).fetchone()
        if not version or version[0] != "2":
            con.execute(
                "INSERT OR REPLACE INTO analytics_meta (key, value) VALUES ('privacy_schema_version', '2')"
            )
        con.execute("DELETE FROM events WHERE ts < datetime('now', ?)", (f"-{RETENTION_DAYS} days",))
    con.commit()
    con.close()


def _is_bot(ua: str) -> bool:
    ua_l = ua.lower()
    return any(p in ua_l for p in _BOT_PATTERNS)


def log_event(
    event_type: str,
    *,
    room_code: str | None = None,
    username: str | None = None,
    ip: str | None = None,
    player_count: int | None = None,
    user_agent: str | None = None,
    extra: dict | None = None,
) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if user_agent and _is_bot(user_agent):
        return
    visitor_hash = None
    if ip:
        try:
            visitor_hash = pseudonymize_ip(ip, "internal-statistics")
        except PrivacyConfigurationError:
            logger.warning("IP-based unique-visitor counting is disabled: privacy HMAC secret is not configured.")
    init_analytics()
    con = sqlite3.connect(DB_PATH)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        """INSERT INTO events
           (event_type, ts, room_code, username, ip_hash, player_count, user_agent, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_type,
            ts,
            room_code,
            username,
            visitor_hash,
            player_count,
            (user_agent or "")[:200] or None,
            json.dumps(extra) if extra else None,
        ),
    )
    con.execute("DELETE FROM events WHERE ts < datetime('now', ?)", (f"-{RETENTION_DAYS} days",))
    con.commit()
    con.close()


def get_stats() -> dict:
    init_analytics()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    def q(sql: str, params: tuple = ()) -> list:
        return [dict(r) for r in con.execute(sql, params).fetchall()]

    def scalar(sql: str, params: tuple = (), default=0):
        row = con.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default

    # Overall counts per event type
    counts = {r["event_type"]: r["cnt"] for r in q(
        "SELECT event_type, COUNT(*) AS cnt FROM events GROUP BY event_type"
    )}

    # Unique real users (by ip_hash) — day / week / month
    dau = scalar(
        "SELECT COUNT(DISTINCT ip_hash) FROM events "
        "WHERE ts >= date('now','localtime') AND ip_hash IS NOT NULL"
    )
    wau = scalar(
        "SELECT COUNT(DISTINCT ip_hash) FROM events "
        "WHERE ts >= date('now','localtime','-7 days') AND ip_hash IS NOT NULL"
    )
    mau = scalar(
        "SELECT COUNT(DISTINCT ip_hash) FROM events "
        "WHERE ts >= date('now','localtime','-30 days') AND ip_hash IS NOT NULL"
    )

    # Per-day breakdown — last 14 days
    daily = q("""
        SELECT
            date(ts)  AS day,
            COUNT(DISTINCT ip_hash) AS users,
            SUM(CASE WHEN event_type='room_created'  THEN 1 ELSE 0 END) AS rooms,
            SUM(CASE WHEN event_type='game_started'  THEN 1 ELSE 0 END) AS games,
            SUM(CASE WHEN event_type='game_ended'    THEN 1 ELSE 0 END) AS completed
        FROM events
        WHERE ts >= date('now','localtime','-14 days')
        GROUP BY day
        ORDER BY day DESC
    """)

    # Peak hours (last 30 days, 0-padded)
    hour_rows = q("""
        SELECT strftime('%H', ts) AS hour, COUNT(*) AS cnt
        FROM events
        WHERE ts >= date('now','localtime','-30 days')
        GROUP BY hour
        ORDER BY hour
    """)
    peak_hours = {r["hour"]: r["cnt"] for r in hour_rows}

    # Recent 60 events
    recent = q("SELECT * FROM events ORDER BY ts DESC LIMIT 60")

    con.close()
    return {
        "counts": counts,
        "dau": dau, "wau": wau, "mau": mau,
        "daily": daily,
        "peak_hours": peak_hours,
        "recent": recent,
    }
