"""server/paths.py: runtime data goes under EEK_DATA_DIR (a container volume) and defaults to the repo
root. Paths are fixed at import time, so each case runs in a fresh interpreter."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRINT_PATHS = """
import json
from scripts import backup_databases
from server import analytics, card_seen, chat_db, db, gameplay_log, messages_db, print_cards
print(json.dumps({
    "analytics": str(analytics.DB_PATH),
    "card_seen": str(card_seen.DB_PATH),
    "chat": str(chat_db.DB_PATH),
    "gameplay": str(gameplay_log.DB_PATH),
    "card_master": str(db.DB_PATH),
    "messages": str(messages_db.DB_PATH),
    "print_output": str(print_cards.OUTPUT_DIR),
    "backup_root": str(backup_databases.PROJECT_ROOT),
}))
"""

EXPECTED = {  # relative to the data directory: the same layout the live server has always used
    "analytics": "analytics.db",
    "card_seen": "database/card_seen.db",
    "chat": "chat.db",
    "gameplay": "gameplay.db",
    "card_master": "database/card_master.db",
    "messages": "messages.db",
    "print_output": "generated/print",
    "backup_root": ".",
}


def paths_with(env_value: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "EEK_DATA_DIR"}
    if env_value is not None:
        env["EEK_DATA_DIR"] = env_value
    out = subprocess.run([sys.executable, "-c", PRINT_PATHS], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, check=True).stdout
    return {k: os.path.realpath(v) for k, v in json.loads(out).items()}


def test_default_keeps_every_database_in_the_repo_root():
    """No EEK_DATA_DIR (the server today): nothing moves."""
    root = os.path.realpath(REPO_ROOT)
    assert paths_with(None) == {k: os.path.realpath(os.path.join(root, v)) for k, v in EXPECTED.items()}


def test_empty_value_counts_as_unset():
    assert paths_with("") == paths_with(None)


def test_eek_data_dir_moves_all_runtime_data(tmp_path):
    data = os.path.realpath(tmp_path)
    assert paths_with(str(tmp_path)) == {k: os.path.realpath(os.path.join(data, v)) for k, v in EXPECTED.items()}
