"""End-to-end fixtures: a real uvicorn server on the sample cards, driven by headless Chromium.

The server runs from the repository with EEK_DATA_DIR pointing at a temp directory that holds a fresh
sample card database, so the run gets its own databases and never touches the working copy's (or a real
card database next to it).

When Playwright or its Chromium is missing these tests are skipped, so a plain `pytest` still works on a
machine without browsers. The CI e2e job sets EEK_E2E_REQUIRED=1, which turns that skip into a failure.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REQUIRED = os.environ.get("EEK_E2E_REQUIRED") == "1"


def _unavailable(reason: str) -> None:
    if REQUIRED:
        pytest.fail(f"EEK_E2E_REQUIRED=1 but {reason}")
    pytest.skip(reason)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_url(tmp_path_factory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("data")
    subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "make_sample_db.py"),
         "--out", str(data_dir / "database" / "card_master.db")],
        check=True, capture_output=True,
    )

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log_path = data_dir / "uvicorn.log"
    # No privacy/TURN/admin secrets: IP-based features, voice and /admin/stats stay off, as in a fresh clone.
    env = {k: v for k, v in os.environ.items()
           if k not in ("EEK_PRIVACY_HMAC_SECRET", "TURN_SECRET", "EEK_ADMIN_STATS_KEY")}
    env["EEK_DATA_DIR"] = str(data_dir)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(url + "/", timeout=2) as res:
                    if res.status == 200:
                        break
            except OSError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"server did not start:\n{log_path.read_text()}")
            time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (pip install -r requirements-dev.txt)")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Error as e:
            _unavailable(f"Chromium can't start (python -m playwright install --with-deps chromium): {e}")
        yield b
        b.close()


@pytest.fixture
def new_player(browser, server_url):
    """Factory for independent players: each gets its own browser context, so its own localStorage
    session. Requests to any other host (the live analytics script) are blocked, so a test run never
    reaches the internet or shows up in the real statistics. Uncaught page errors fail the test."""
    contexts, page_errors = [], []
    server_host = urlparse(server_url).netloc

    def make(name: str):
        ctx = browser.new_context(base_url=server_url, viewport={"width": 390, "height": 844})  # a phone
        ctx.route("**/*", lambda route: route.continue_()
                  if urlparse(route.request.url).netloc == server_host else route.abort())
        page = ctx.new_page()
        page.set_default_timeout(10_000)
        page.on("pageerror", lambda exc: page_errors.append(f"{name}: {exc}"))
        contexts.append(ctx)
        return page

    yield make
    for ctx in contexts:
        ctx.close()
    assert page_errors == [], "uncaught JavaScript errors"
