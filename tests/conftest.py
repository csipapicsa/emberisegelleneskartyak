# tests/conftest.py
import pytest
from unittest.mock import patch, AsyncMock


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
