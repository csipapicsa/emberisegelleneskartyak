"""Every page that loads the Umami tracker must route it through
static/js/umami-normalize.js, so room codes never reach analytics."""
from __future__ import annotations

import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
TRACKER = "https://stats.emberisegelleneskartyak.hu/script.js"


def test_tracker_always_normalized():
    pages = sorted(f for f in os.listdir(STATIC) if f.endswith(".html"))
    assert pages
    for page in pages:
        with open(os.path.join(STATIC, page), encoding="utf-8") as f:
            html = f.read()
        tags = re.findall(r"<script[^>]*" + re.escape(TRACKER) + r"[^>]*>", html)
        assert len(tags) == 1, f"{page}: expected exactly one tracker tag"
        assert 'data-before-send="umamiBeforeSend"' in tags[0], page
        hook = html.find("/static/js/umami-normalize.js")
        assert 0 <= hook < html.find(TRACKER), f"{page}: hook must load before the tracker"
        assert "defer" not in html[html.rfind("<script", 0, hook):hook], f"{page}: hook must not be deferred"
