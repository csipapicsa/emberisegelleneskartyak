"""GET /api/voice/credentials: TURN credentials only for a player in an open room, rate limited per IP."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from server import main as main_mod
from server.room import Player, create_room, delete_room

URL = "/api/voice/credentials"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TURN_SECRET", "test-secret")
    monkeypatch.setattr(main_mod, "_voice_credential_requests", {})
    return TestClient(main_mod.app)


@pytest.fixture
def room():
    code = "VOICE1"
    admin = str(uuid.uuid4())
    r = create_room(code, admin, "Admin")
    yield r, admin
    delete_room(code)


def get(client, sid, ip="203.0.113.7"):
    return client.get(URL, params={"session_id": sid}, headers={"X-Real-IP": ip})


def test_player_in_an_open_room_gets_credentials(client, room):
    _, sid = room
    res = get(client, sid)
    assert res.status_code == 200
    body = res.json()
    assert body["username"].endswith(f":{sid}") and body["credential"]


def test_a_made_up_session_gets_nothing(client, room):
    # The hole this closes: any well-formed UUID used to get 24-hour TURN credentials.
    assert get(client, str(uuid.uuid4())).status_code == 403


def test_a_pending_player_gets_nothing(client, room):
    r, _ = room
    pending = str(uuid.uuid4())
    r.pending[pending] = Player(session_id=pending, username="Waiting")
    assert get(client, pending).status_code == 403


def test_a_closed_room_gives_nothing(client, room):
    r, sid = room
    r.closed = True
    assert get(client, sid).status_code == 403


def test_malformed_session_is_rejected(client):
    assert get(client, "not-a-uuid").status_code == 400


def test_each_ip_is_rate_limited_but_others_are_not(client, room):
    _, sid = room
    for _ in range(main_mod.VOICE_CREDENTIALS_RATE_LIMIT):
        assert get(client, sid).status_code == 200
    assert get(client, sid).status_code == 429
    assert get(client, sid, ip="198.51.100.9").status_code == 200


def test_unknown_sessions_count_towards_the_limit(client, room):
    # So the endpoint can't be used to probe session IDs at full speed.
    for _ in range(main_mod.VOICE_CREDENTIALS_RATE_LIMIT):
        get(client, str(uuid.uuid4()))
    _, sid = room
    assert get(client, sid).status_code == 429


def test_credentials_expire_within_hours_not_a_day(client, room):
    import time
    _, sid = room
    expiry = int(get(client, sid).json()["username"].split(":")[0])
    assert expiry - time.time() <= 6 * 3600 + 5
