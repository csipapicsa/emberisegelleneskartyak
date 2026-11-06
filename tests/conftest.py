# tests/conftest.py
from unittest.mock import AsyncMock, patch

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "real_sleep: disable the instant_sleep autouse fixture")


@pytest.fixture(autouse=True)
def instant_sleep(request):
    """Patch asyncio.sleep in game.py to return immediately.
    Tests marked with @pytest.mark.real_sleep opt out."""
    if request.node.get_closest_marker("real_sleep"):
        yield
        return
    with patch("server.game.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep


@pytest.fixture(autouse=True)
def isolated_card_seen(tmp_path, monkeypatch):
    """Every dealt card is counted in server.card_seen; keep tests from
    writing those counts into the real database/card_seen.db."""
    from collections import Counter

    from server import card_seen
    monkeypatch.setattr(card_seen, "DB_PATH", str(tmp_path / "card_seen.db"))
    monkeypatch.setattr(card_seen, "_pending", Counter())
    yield card_seen


@pytest.fixture(autouse=True)
def isolated_runtime_data(tmp_path, monkeypatch):
    """Point every database the app writes (and the PDF output folder) at the test's temp directory.

    Without this, tests that start the app wrote chat.db and messages.db into the data directory, which
    on the server is the live site's data. A test can still patch a path itself; its setattr runs later
    and wins. The card database is opened read-only and stays where it is.
    """
    from server import analytics, chat_db, gameplay_log, messages_db, print_cards
    monkeypatch.setattr(messages_db, "DB_PATH", str(tmp_path / "messages.db"))
    monkeypatch.setattr(gameplay_log, "DB_PATH", tmp_path / "gameplay.db")
    monkeypatch.setattr(chat_db, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "analytics.db")
    monkeypatch.setattr(print_cards, "OUTPUT_DIR", tmp_path / "generated" / "print")
