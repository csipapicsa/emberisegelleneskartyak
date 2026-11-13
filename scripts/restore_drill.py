#!/usr/bin/env python3
"""Restore drill: prove that the newest production backup can be restored and served.

1. Takes the newest backup set from the backup folder (all files with the same timestamp).
2. Restores it into a temporary folder with production's layout (``database__card_master--<ts>.db``
   becomes ``database/card_master.db``).
3. Runs ``PRAGMA integrity_check`` on every database.
4. Starts the production image on the restored copy (rootless podman, a free local port, a throwaway privacy
   secret, so production's secrets are never needed) and compares its ``/api/editions`` with production's.
5. Removes the container and the temporary folder, and prints a summary. Exit code 0 only if every step passed.

Runs as ``eek`` (the backups are readable only by that user), on the host's Python 3.9:

    sudo -u eek python3 /opt/emberisegellenes_kartyak/scripts/restore_drill.py

The runbook: docs/runbooks/deploy.md, "Backup restore drill".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DEFAULT_BACKUP_DIR = Path("/srv/eek/backups/prod")
DEFAULT_IMAGE = "ghcr.io/csipapicsa/emberisegelleneskartyak:prod"
DEFAULT_PORT = 8012  # free in SERVER_PORTS.md; only used while the drill runs
DEFAULT_LIVE_URL = "http://127.0.0.1:8010"
CONTAINER_NAME = "eek-restore-drill"
# The databases production can't start without (the app's /healthz needs the card database).
REQUIRED = ("database/card_master.db",)

_BACKUP_RE = re.compile(r"^(?P<stem>.+)--(?P<ts>\d{8}T\d{6}Z)\.db$")


def newest_backup_set(backup_dir: Path) -> tuple[str, dict[str, Path]]:
    """The newest timestamp and its files, keyed by the path they restore to (``database/card_master.db``)."""
    sets: dict[str, dict[str, Path]] = {}
    for path in backup_dir.glob("*.db"):
        match = _BACKUP_RE.match(path.name)
        if match:
            target = match["stem"].replace("__", "/") + ".db"
            sets.setdefault(match["ts"], {})[target] = path
    if not sets:
        raise SystemExit(f"no backups in {backup_dir}")
    newest = max(sets)
    return newest, sets[newest]


def restore(files: dict[str, Path], dest: Path) -> list[Path]:
    """Copy a backup set into ``dest`` with production's layout; returns the restored paths."""
    restored = []
    for target, source in sorted(files.items()):
        path = dest / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)
        restored.append(path)
    return restored


def integrity(path: Path) -> str:
    """SQLite's own consistency check: ``ok`` or the first problems it found."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    return "; ".join(str(r[0]) for r in rows[:5])


def table_rows(path: Path) -> int:
    """Total rows across the database's tables (a rough "is there data in it" signal for the summary)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return sum(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
    finally:
        conn.close()


def comparable_editions(editions: list[dict]) -> dict[str, tuple[int, int]]:
    """Edition name → (black, white) counts, without the community edition (its cards can grow after the
    backup was taken)."""
    return {e["name"]: (e["black_count"], e["white_count"]) for e in editions if not e.get("community")}


def _get_json(url: str, timeout: float = 5) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _wait_healthy(url: str, deadline_s: float) -> dict | None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            return _get_json(url + "/healthz", timeout=2)  # 200 only when the card database is usable
        except Exception:
            time.sleep(1)
    return None


def _podman(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return subprocess.run(["podman", *args], cwd="/", env=env, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def serve_and_compare(data_dir: Path, image: str, port: int, live_url: str) -> list[str]:
    """Start the image on the restored copy and compare its editions with production's; returns problems."""
    problems: list[str] = []
    _podman("rm", "--force", CONTAINER_NAME, check=False)
    run = _podman(
        "run", "--detach", "--name", CONTAINER_NAME,
        "--publish", f"127.0.0.1:{port}:8010",
        "--userns", "keep-id:uid=10001,gid=10001",
        "--volume", f"{data_dir}:/data:Z",
        "--env", f"EEK_PRIVACY_HMAC_SECRET={secrets.token_hex(32)}",
        "--read-only", "--cap-drop", "all",
        image, check=False)
    if run.returncode != 0:
        return [f"podman run failed: {run.stdout.strip()}"]
    try:
        url = f"http://127.0.0.1:{port}"
        health = _wait_healthy(url, deadline_s=90)
        if health is None:
            logs = _podman("logs", "--tail", "20", CONTAINER_NAME, check=False).stdout
            return [f"the restored app never became healthy; last log lines:\n{logs}"]
        print(f"  app on the restored data: healthy, version {health.get('version')}")
        restored = comparable_editions(_get_json(url + "/api/editions"))
        live = comparable_editions(_get_json(live_url + "/api/editions"))
        if restored != live:
            missing = sorted(set(live) - set(restored))
            differ = sorted(n for n in set(live) & set(restored) if live[n] != restored[n])
            problems.append(f"editions differ from production: missing {missing}, other counts {differ}")
        else:
            print(f"  editions: the same {len(live)} as production (community edition not compared)")
    finally:
        _podman("rm", "--force", CONTAINER_NAME, check=False)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--live-url", default=DEFAULT_LIVE_URL, help="production, to compare the editions with")
    parser.add_argument("--no-app", action="store_true", help="only restore and check the files")
    args = parser.parse_args(argv)

    started = time.monotonic()
    ts, files = newest_backup_set(args.backup_dir)
    print(f"Backup set {ts}: {len(files)} databases")
    problems: list[str] = [f"{name} missing from the backup set" for name in REQUIRED if name not in files]

    work = Path(tempfile.mkdtemp(prefix="eek-restore-drill-", dir="/var/tmp"))
    try:
        for path in restore(files, work):
            name = str(path.relative_to(work))
            result = integrity(path)
            print(f"  {name}: integrity {result}, {table_rows(path)} rows")
            if result != "ok":
                problems.append(f"{name}: {result}")
        if not args.no_app and not problems:
            problems += serve_and_compare(work, args.image, args.port, args.live_url)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    took = time.monotonic() - started
    if problems:
        print(f"RESTORE DRILL FAILED after {took:.0f} s:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"RESTORE DRILL PASSED in {took:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
