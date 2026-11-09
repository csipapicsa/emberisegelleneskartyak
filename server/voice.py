# server/voice.py

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import os
import re
import time
from collections import defaultdict

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# ICE candidate rate limiter: max 30/min per session to prevent flood
_ICE_RATE: dict[str, list[float]] = defaultdict(list)
_ICE_LIMIT = 30
_ICE_WINDOW = 60.0


def generate_turn_credentials(session_id: str) -> dict | None:
    """HMAC temporal credentials for coturn. Requires TURN_SECRET env var."""
    secret = os.environ.get("TURN_SECRET")
    if not secret:
        return None
    ttl = 6 * 3600  # the game page fetches fresh credentials on every load
    expiry = int(time.time()) + ttl
    username = f"{expiry}:{session_id}"
    h = _hmac.new(secret.encode(), username.encode(), hashlib.sha1)
    credential = base64.b64encode(h.digest()).decode()
    turn_host = os.environ.get("TURN_HOST", "109.199.104.168")
    turn_port = os.environ.get("TURN_PORT", "3478")
    return {
        "username": username,
        "credential": credential,
        "ttl": ttl,
        "uris": [
            f"stun:{turn_host}:{turn_port}",
            f"turn:{turn_host}:{turn_port}?transport=udp",
            f"turn:{turn_host}:{turn_port}?transport=tcp",
        ],
    }


def validate_signal(event: str, msg: dict) -> bool:
    """Validate incoming signaling message fields and sizes."""
    to = str(msg.get("to", ""))
    if not _UUID_RE.match(to):
        return False
    if event in ("voice_offer", "voice_answer"):
        sdp = msg.get("sdp")
        return isinstance(sdp, str) and 0 < len(sdp) <= 8192
    if event == "voice_ice":
        c = msg.get("candidate")
        mid = msg.get("sdpMid")
        idx = msg.get("sdpMLineIndex")
        return (
            isinstance(c, str) and 0 < len(c) <= 1024
            and isinstance(mid, str) and len(mid) <= 32
            and isinstance(idx, int) and 0 <= idx <= 16
        )
    return False


def check_ice_rate(session_id: str) -> bool:
    """Returns False if the session exceeds the ICE candidate rate limit."""
    now = time.time()
    _ICE_RATE[session_id] = [t for t in _ICE_RATE[session_id] if now - t < _ICE_WINDOW]
    if len(_ICE_RATE[session_id]) >= _ICE_LIMIT:
        return False
    _ICE_RATE[session_id].append(now)
    return True
