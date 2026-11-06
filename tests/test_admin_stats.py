from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import analytics
from server import main as main_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "analytics.db")
    analytics.init_analytics()
    return TestClient(main_mod.app)


def test_correct_key_opens_the_stats_page(client, monkeypatch):
    monkeypatch.setenv("EEK_ADMIN_STATS_KEY", "test-admin-key")
    res = client.get("/admin/stats", params={"key": "test-admin-key"})
    assert res.status_code == 200
    assert "Statisztika" in res.text


def test_page_never_contains_the_key(client, monkeypatch):
    monkeypatch.setenv("EEK_ADMIN_STATS_KEY", "test-admin-key")
    res = client.get("/admin/stats", params={"key": "test-admin-key"})
    assert "test-admin-key" not in res.text


@pytest.mark.parametrize("key", ["", "wrong", "csipapicsa", "test-admin-key "])
def test_wrong_key_is_rejected(client, monkeypatch, key):
    monkeypatch.setenv("EEK_ADMIN_STATS_KEY", "test-admin-key")
    assert client.get("/admin/stats", params={"key": key}).status_code == 403


@pytest.mark.parametrize("configured", [None, ""])
@pytest.mark.parametrize("key", ["", "csipapicsa"])
def test_unconfigured_key_fails_closed(client, monkeypatch, configured, key):
    """A server without the env var must not open the page, even for an empty key."""
    if configured is None:
        monkeypatch.delenv("EEK_ADMIN_STATS_KEY", raising=False)
    else:
        monkeypatch.setenv("EEK_ADMIN_STATS_KEY", configured)
    assert client.get("/admin/stats", params={"key": key}).status_code == 403
