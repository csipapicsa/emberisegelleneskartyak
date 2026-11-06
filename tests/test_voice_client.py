# tests/test_voice_client.py
"""Runs the client-side voice tests (static/js/voice.js) under Node.

The voice chat bug that made a player's first room silent lived entirely in the
browser, so the server-side tests in test_voice.py could not have caught it.
These exercise voice.js directly against stubbed DOM/WebRTC objects; this
wrapper keeps them in the same `pytest` run as everything else.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_TEST_DIR = os.path.join(REPO_ROOT, "tests", "js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_voice_client_suite():
    result = subprocess.run(
        ["node", "--test", JS_TEST_DIR],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.fail(
            "client-side voice tests failed\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
