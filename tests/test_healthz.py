"""/healthz: what the container runtime and deploy checks look at."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from server import main as main_mod
from tests.test_editions import make_master


def _client(monkeypatch, get_conn):
    monkeypatch.setattr(main_mod, "get_conn", get_conn)
    monkeypatch.setenv("EEK_DISABLE_ROOM_CLEANUP", "1")
    return TestClient(main_mod.app)


def test_ok_with_playable_packs_and_reports_the_version(monkeypatch):
    monkeypatch.setattr(main_mod, "VERSION", "abc1234")
    res = _client(monkeypatch, make_master).get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "version": "abc1234"}


def test_head_works_too(monkeypatch):
    assert _client(monkeypatch, make_master).head("/healthz").status_code == 200


def test_missing_card_database_is_unhealthy(monkeypatch, tmp_path):
    """A container started without its data volume must fail its health check, so a deploy rolls back."""
    def missing():
        return sqlite3.connect(f"file:{tmp_path / 'nope.db'}?mode=ro", uri=True)
    res = _client(monkeypatch, missing).get("/healthz")
    assert res.status_code == 503
    assert res.json()["status"] == "error"


def test_no_playable_pack_is_unhealthy(monkeypatch):
    def no_complete_pack():
        conn = make_master()
        conn.execute("UPDATE cards SET validated = 0")
        return conn
    assert _client(monkeypatch, no_complete_pack).get("/healthz").status_code == 503


def test_error_details_are_not_exposed(monkeypatch):
    def broken():
        raise sqlite3.OperationalError("unable to open database file /secret/path")
    res = _client(monkeypatch, broken).get("/healthz")
    assert res.status_code == 503
    assert "secret" not in res.text
