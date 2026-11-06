# server/session.py

from __future__ import annotations

import uuid


def create_or_get_session(session_id: str | None) -> str:
    if session_id and len(session_id) == 36:
        return session_id
    return str(uuid.uuid4())
