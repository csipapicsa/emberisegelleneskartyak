"""Guard rails for the single bottom ad banner (static/js/ads.js): it may only
exist on /, /join and /create, and no ad-network code may sneak into any page."""
from __future__ import annotations

import os
import re

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
AD_PAGES = {"index.html", "join.html", "create.html"}
AD_NETWORK = re.compile(r"adsbygoogle|googlesyndication|doubleclick|googletagservices|fundingchoices", re.I)


def _read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def _html_pages():
    return sorted(f for f in os.listdir(STATIC) if f.endswith(".html"))


def test_ad_slot_only_on_whitelisted_pages():
    for page in _html_pages():
        html = _read(page)
        has_slot = 'id="adSlot"' in html or "ads.js" in html
        assert has_slot == (page in AD_PAGES), page


def test_ad_slot_starts_hidden():
    for page in AD_PAGES:
        assert re.search(r'<div class="ad-slot" id="adSlot" hidden', _read(page)), page


def test_ads_disabled_and_whitelist_unchanged():
    src = _read("js/ads.js")
    assert "const ADS_ENABLED = false;" in src
    assert "const AD_ALLOWED_PATHS = ['/', '/join', '/create'];" in src


def test_no_ad_network_code_anywhere():
    files = _html_pages() + [os.path.join("js", f) for f in os.listdir(os.path.join(STATIC, "js"))]
    for rel in files:
        assert not AD_NETWORK.search(_read(rel)), rel
