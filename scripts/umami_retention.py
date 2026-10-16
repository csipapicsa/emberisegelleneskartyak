"""Delete Umami analytics data older than the retention period promised in
static/adatkezeles.html (2 years).

Umami itself never deletes anything, so this runs daily from a systemd --user
timer (systemd/user/umami-retention.timer). It talks to the rootless podman
Postgres container directly — no extra Python dependencies.

    python3 scripts/umami_retention.py            # delete
    python3 scripts/umami_retention.py --dry-run  # only print what would go
"""
from __future__ import annotations

import argparse
import subprocess
import sys

RETENTION = "2 years"
CONTAINER = "umami-db"
DB_USER = "umami"
DB_NAME = "umami"

# Every Umami table that holds per-visit data. Config tables (website, user,
# team, report, board, ...) are left alone.
EVENT_TABLES = (
    "website_event",
    "event_data",
    "session_data",
    "revenue",
    "heatmap_event",
    "session_replay",
)


def build_sql(retention: str = RETENTION, dry_run: bool = False) -> str:
    cutoff = f"now() - interval '{retention}'"
    stmts = []
    for table in EVENT_TABLES:
        where = f"created_at < {cutoff}"
        if dry_run:
            stmts.append(f"SELECT '{table}', count(*) FROM {table} WHERE {where};")
        else:
            stmts.append(f"DELETE FROM {table} WHERE {where};")
    # A session goes once it is past the cutoff and has no events left.
    session_where = (
        f"created_at < {cutoff} AND NOT EXISTS "
        "(SELECT 1 FROM website_event e WHERE e.session_id = session.session_id)"
    )
    if dry_run:
        stmts.append(f"SELECT 'session', count(*) FROM session WHERE {session_where};")
        return "\n".join(stmts)
    stmts.append(f"DELETE FROM session WHERE {session_where};")
    return "BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cmd = [
        "podman", "exec", "-i", CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-v", "ON_ERROR_STOP=1", "-At", "-F", ": ",
    ]
    result = subprocess.run(cmd, input=build_sql(dry_run=args.dry_run),
                            capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
