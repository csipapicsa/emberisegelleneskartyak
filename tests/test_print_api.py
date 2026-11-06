# tests/test_print_api.py
"""The /nyomtatas page's API: POST /api/print renders the printable PDFs for
the picked editions, GET /api/print/<name> downloads one."""
from __future__ import annotations

import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from server import main as main_mod
from server import messages_db, print_cards
from tests.test_editions import make_master


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(print_cards.db, "get_conn", make_master)
    monkeypatch.setattr(messages_db, "DB_PATH", str(tmp_path / "messages.db"))
    conn = messages_db.get_conn()
    try:
        messages_db.ensure_tables(conn)
    finally:
        conn.close()
    monkeypatch.setattr(print_cards, "OUTPUT_DIR", tmp_path / "print")
    monkeypatch.setattr(main_mod, "_print_requests", {})
    return TestClient(main_mod.app)


def test_print_returns_download_links_with_counts(client):
    res = client.post("/api/print", json={"edition_ids": [1, 4], "per_page": 9})
    assert res.status_code == 200
    files = res.json()["files"]
    assert [f["variant"] for f in files] == ["white", "black", "black_on_white"]
    assert {f["variant"]: f["cards"] for f in files} == {"white": 3, "black": 2, "black_on_white": 2}
    assert all(f["pages"] == 1 for f in files)
    assert all(f["label"] == print_cards.VARIANT_LABELS[f["variant"]] for f in files)
    for f in files:
        assert f["url"].startswith("/api/print/")
        download = client.get(f["url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert "attachment" in download.headers["content-disposition"]
        assert download.content.startswith(b"%PDF")


def test_print_eighteen_per_page(client):
    files = client.post("/api/print", json={"edition_ids": [1], "per_page": 18}).json()["files"]
    assert all("-18-" in f["url"] for f in files)


def test_white_only_pack_gets_only_the_white_pdf(client):
    files = client.post("/api/print", json={"edition_ids": [4]}).json()["files"]
    assert [f["variant"] for f in files] == ["white"]


def test_selection_without_printable_cards_is_refused(client):
    # Main Deck (2) is incomplete, 999 doesn't exist.
    res = client.post("/api/print", json={"edition_ids": [2, 999]})
    assert res.status_code == 400
    assert "nincs nyomtatható" in res.json()["detail"]


@pytest.mark.parametrize("body", [
    {},
    {"edition_ids": []},
    {"edition_ids": "1"},
    {"edition_ids": [1, "4"]},
    {"edition_ids": [True]},
    {"edition_ids": list(range(201))},
    {"edition_ids": [1], "per_page": 12},
    {"edition_ids": [1], "per_page": "9"},
    [1, 4],
])
def test_bad_requests_are_refused(client, body):
    assert client.post("/api/print", json=body).status_code == 400
    assert not (print_cards.OUTPUT_DIR.exists() and any(print_cards.OUTPUT_DIR.iterdir()))


def test_oversized_body_is_refused(client):
    res = client.post("/api/print", content=b'{"edition_ids": [' + b"1," * 3000 + b'1]}',
                      headers={"content-type": "application/json"})
    assert res.status_code == 413


def test_rate_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(main_mod, "PRINT_RATE_LIMIT", 2)
    headers = {"x-real-ip": "203.0.113.5"}
    for _ in range(2):
        assert client.post("/api/print", json={"edition_ids": [1]}, headers=headers).status_code == 200
    assert client.post("/api/print", json={"edition_ids": [1]}, headers=headers).status_code == 429
    other = {"x-real-ip": "203.0.113.6"}
    assert client.post("/api/print", json={"edition_ids": [1]}, headers=other).status_code == 200


def test_generating_deletes_expired_files(client):
    print_cards.OUTPUT_DIR.mkdir(parents=True)
    old = print_cards.OUTPUT_DIR / "feher-kartyak-9-0000000000000000.pdf"
    old.write_bytes(b"%PDF old")
    os.utime(old, (0, 0))
    client.post("/api/print", json={"edition_ids": [1]})
    assert not old.exists()


@pytest.mark.parametrize("name", [
    "..%2Fmessages.db",
    "messages.db",
    "feher-kartyak-12-0123456789abcdef.pdf",
    "feher-kartyak-9-0123456789ABCDEF.pdf",
    "proba-feher-kartyak-9.pdf",
])
def test_download_serves_only_generated_names(client, name):
    print_cards.OUTPUT_DIR.mkdir(parents=True)
    (print_cards.OUTPUT_DIR / "proba-feher-kartyak-9.pdf").write_bytes(b"%PDF")
    assert client.get(f"/api/print/{name}").status_code == 404


def test_expired_download_says_so(client):
    res = client.get("/api/print/feher-kartyak-9-0123456789abcdef.pdf")
    assert res.status_code == 404
    assert "lejárt" in res.json()["detail"]


def test_print_page_is_served_and_linked_from_the_main_page():
    client = TestClient(main_mod.app)
    res = client.get("/nyomtatas")
    assert res.status_code == 200
    assert "generateBtn" in res.text
    index = (pathlib.Path(main_mod.BASE_DIR) / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="/nyomtatas"' in index


def test_print_page_is_set_up_for_search_engines():
    static = pathlib.Path(main_mod.BASE_DIR) / "static"
    html = (static / "nyomtatas.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="index, follow">' in html
    assert '<link rel="canonical" href="https://emberisegelleneskartyak.hu/nyomtatas">' in html
    assert '<meta name="description" content="' in html
    assert "<h1" in html and "PDF" in html.split("<title>")[1].split("</title>")[0]
    assert "<loc>https://emberisegelleneskartyak.hu/nyomtatas</loc>" in (static / "sitemap.xml").read_text(encoding="utf-8")
    # The generated PDFs are temporary: crawlers stay out of /api/.
    assert "Disallow: /api/" in (static / "robots.txt").read_text(encoding="utf-8")
