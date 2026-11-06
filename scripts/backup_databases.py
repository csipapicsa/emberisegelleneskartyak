#!/usr/bin/env python3
"""Create consistent SQLite backups of every database in this project.

Backups are written below ``backups/`` and files older than the configured
retention window are removed.  SQLite's backup API is used rather than a raw
file copy, so a live database can be backed up safely while the site runs.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

RETENTION_DAYS = 30
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Subdirectories whose databases are backed up too, e.g. the uploaded
# database/card_master.db (the game's card source).
DATABASE_SUBDIRS = ("database",)


def sqlite_databases(project_root: Path) -> list[Path]:
    """Return every top-level SQLite database plus those in DATABASE_SUBDIRS,
    excluding the backup directory."""
    paths = list(project_root.glob("*.db"))
    for subdir in DATABASE_SUBDIRS:
        paths.extend((project_root / subdir).glob("*.db"))
    return sorted(path for path in paths if path.is_file())


def backup_name(project_root: Path, source_path: Path) -> str:
    """Backup file stem: the path relative to the project root, with ``/``
    turned into ``__`` so database/x.db can't collide with a top-level x.db."""
    return "__".join(source_path.relative_to(project_root).with_suffix("").parts)


def backup_database(source_path: Path, destination_path: Path) -> None:
    """Copy one SQLite database using SQLite's online backup interface."""
    source_uri = f"file:{source_path.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(destination_path) as target:
        source.backup(target)
    os.chmod(destination_path, 0o600)


def prune_old_backups(backup_dir: Path, now: datetime) -> list[Path]:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    deleted: list[Path] = []
    for path in backup_dir.glob("*.db"):
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified_at < cutoff:
            path.unlink()
            deleted.append(path)
    return deleted


def run(project_root: Path, now: datetime | None = None) -> tuple[list[Path], list[Path]]:
    """Create one timestamped backup per database and purge expired files."""
    now = now or datetime.now(UTC)
    backup_dir = project_root / "backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    lock_path = backup_dir / ".backup.lock"
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A database backup is already running.") from exc

        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        created: list[Path] = []
        for source_path in sqlite_databases(project_root):
            destination_path = backup_dir / f"{backup_name(project_root, source_path)}--{timestamp}.db"
            backup_database(source_path, destination_path)
            created.append(destination_path)
        deleted = prune_old_backups(backup_dir, now)
    return created, deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project directory containing the .db files.",
    )
    args = parser.parse_args(argv)
    try:
        created, deleted = run(args.project_root)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"Database backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created {len(created)} backup(s); removed {len(deleted)} expired backup(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
