"""/admin/stats: the key is the HTTP Basic Auth password, never a URL parameter
(URLs end up in the nginx and uvicorn access logs)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server import analytics
from server import main as main_mod

KEY = "test-admin-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "analytics.db")
    analytics.init_analytics()
    monkeypatch.setenv("EEK_ADMIN_STATS_KEY", KEY)
    return TestClient(main_mod.app)


def test_correct_key_as_basic_auth_password_opens_the_page(client):
    res = client.get("/admin/stats", auth=("admin", KEY))
    assert res.status_code == 200
    assert "Statisztika" in res.text


def test_any_username_is_accepted(client):
    assert client.get("/admin/stats", auth=("gergo", KEY)).status_code == 200


def test_page_never_contains_the_key(client):
    assert KEY not in client.get("/admin/stats", auth=("admin", KEY)).text


def test_no_credentials_asks_the_browser_for_a_login(client):
    res = client.get("/admin/stats")
    assert res.status_code == 401
    assert res.headers["www-authenticate"].startswith("Basic ")


@pytest.mark.parametrize("password", ["", "wrong", "csipapicsa", KEY + " "])
def test_wrong_password_is_rejected(client, password):
    assert client.get("/admin/stats", auth=("admin", password)).status_code == 401


def test_key_in_the_url_no_longer_works(client):
    """The old ?key= form must not open the page, or people would keep using it
    and the key would keep landing in the logs."""
    assert client.get("/admin/stats", params={"key": KEY}).status_code == 401


@pytest.mark.parametrize("configured", [None, ""])
@pytest.mark.parametrize("password", ["", "csipapicsa"])
def test_unconfigured_key_fails_closed(client, monkeypatch, configured, password):
    """A server without the env var must not open the page, even for an empty password."""
    if configured is None:
        monkeypatch.delenv("EEK_ADMIN_STATS_KEY", raising=False)
    else:
        monkeypatch.setenv("EEK_ADMIN_STATS_KEY", configured)
    assert client.get("/admin/stats", auth=("admin", password)).status_code == 401
