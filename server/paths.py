"""Where the code is and where the runtime data lives.

All SQLite databases (and generated PDFs) live under DATA_DIR, keeping the same layout as before:
analytics.db, chat.db and messages.db at the top, the uploaded card database and the dealt-card counts
in database/, printable PDFs in generated/print/.

DATA_DIR defaults to the repository root, which is how the app has always run. A container sets
EEK_DATA_DIR to a mounted volume, so the data survives replacing the image.
"""
from __future__ import annotations

import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("EEK_DATA_DIR") or CODE_DIR).resolve()
