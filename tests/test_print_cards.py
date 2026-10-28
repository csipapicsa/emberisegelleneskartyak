# tests/test_print_cards.py
"""Printable card PDFs (server/print_cards.py): which cards go in, text
layout, file naming/reuse and the 24-hour cleanup."""
from __future__ import annotations
import os
import re
import sqlite3
import stat
import time

import pytest
from reportlab.pdfbase import pdfmetrics

from server import messages_db, print_cards
from server.messages_db import COMMUNITY_EDITION_ID, COMMUNITY_ID_OFFSET
from tests.test_editions import make_master

WIDTH = print_cards.CARD_W - 2 * print_cards.PADDING


@pytest.fixture
def master(monkeypatch, tmp_path):
    """make_master card_master.db (Piros = 1, Main Deck = 2 incomplete,
    Kék = 4) plus a temp messages.db with community cards."""
    monkeypatch.setattr(print_cards.db, "get_conn", make_master)
    monkeypatch.setattr(messages_db, "DB_PATH", str(tmp_path / "messages.db"))
    conn = messages_db.get_conn()
    try:
        messages_db.ensure_tables(conn)
        conn.executemany(
            "INSERT INTO card_ideas (card_type, text, approved, created_at) VALUES (?, ?, ?, datetime('now'))",
            [("white", "Közösségi kutya", 1),
             ("white", "Közös fehér", 1),          # same text as card 3
             ("white", "Jóvá nem hagyott", 0)],
        )
        conn.commit()
    finally:
        conn.close()


def _ids(cards):
    return [c["id"] for c in cards]


def _pages(path) -> int:
    return len(re.findall(rb"/Type /Page\b", path.read_bytes()))


# ── which cards ───────────────────────────────────────────────────────────────

def test_overlapping_packs_print_each_card_once(master):
    black, white = print_cards.collect_cards([1, 4])
    # Card 3 is in Piros and Kék.
    assert _ids(black) == [1, 2]
    assert _ids(white) == [3, 4, 8]


def test_incomplete_packs_print_nothing(master):
    assert print_cards.collect_cards([2]) == ([], [])


def test_community_cards_only_when_selected_and_only_approved(master):
    _black, white = print_cards.collect_cards([COMMUNITY_EDITION_ID])
    assert [c["text"] for c in white] == ["Közösségi kutya", "Közös fehér"]
    assert all(c["id"] > COMMUNITY_ID_OFFSET for c in white)
    assert "Közösségi kutya" not in [c["text"] for c in print_cards.collect_cards([1])[1]]


def test_community_card_with_a_pack_cards_text_is_dropped(master):
    _black, white = print_cards.collect_cards([1, COMMUNITY_EDITION_ID])
    texts = [c["text"] for c in white]
    assert texts.count("Közös fehér") == 1
    assert "Közösségi kutya" in texts


# ── text layout ───────────────────────────────────────────────────────────────

def _width(line, size):
    return pdfmetrics.stringWidth(line, print_cards.FONT_NAME, size)


def test_wrap_keeps_every_line_inside_the_card():
    text = "Mi az, amitől a nagyi kiborulna, mégis elbűvölőnek találná? " * 3
    lines, split_word = print_cards.wrap(text, 14, WIDTH)
    assert len(lines) > 3
    assert all(_width(line, 14) <= WIDTH for line in lines)
    assert not split_word
    assert " ".join(lines).split() == text.split()


def test_wrap_keeps_the_cards_own_line_breaks():
    lines, _ = print_cards.wrap("Első sor\nMásodik sor", 14, WIDTH)
    assert lines == ["Első sor", "Második sor"]


def test_wrap_breaks_after_a_hyphen_instead_of_mid_word():
    lines, split_word = print_cards.wrap("Milyen szuper-képességem van?", 14, WIDTH)
    assert "szuper-" in lines[0] or lines[1].startswith("képességem")
    assert not split_word
    assert "".join(lines).replace(" ", "") == "Milyenszuper-képességemvan?"


def test_wrap_never_leaves_a_lone_letter_after_a_hyphen():
    lines, _ = print_cards.wrap("Mivel motiválják Borsodban a 3H-s diákokat a tanulásra?", 14, WIDTH)
    assert any("3H-s" in line for line in lines)
    assert not any(line.startswith("s ") for line in lines)


def test_wrap_flags_a_word_too_wide_for_any_line():
    lines, split_word = print_cards.wrap("A" * 80, 14, WIDTH)
    assert split_word
    assert "".join(lines) == "A" * 80
    assert all(_width(line, 14) <= WIDTH for line in lines)


def test_fit_text_shrinks_until_no_word_is_cut():
    # One long word that doesn't fit a line at 14pt.
    word = "Megszentségteleníthetetlenségeskedéseitekért"
    size, lines = print_cards.fit_text(word, WIDTH, 100 * print_cards.mm)
    assert size < print_cards.MAX_FONT_SIZE
    assert size == print_cards.MIN_FONT_SIZE or lines == [word]


def test_fit_text_short_card_uses_the_largest_size():
    size, lines = print_cards.fit_text("Lepra", WIDTH, 60 * print_cards.mm)
    assert size == print_cards.MAX_FONT_SIZE
    assert lines == ["Lepra"]


def test_fit_text_long_card_fits_the_box():
    text = "Ez egy nagyon hosszú kártya, amin rengeteg szó van, " * 8
    height = 40 * print_cards.mm
    size, lines = print_cards.fit_text(text, WIDTH, height)
    assert size < print_cards.MAX_FONT_SIZE
    assert len(lines) * size * print_cards.LEADING <= height


# ── files ─────────────────────────────────────────────────────────────────────

def test_render_print_set_writes_three_pdfs_with_hashed_names(master, tmp_path):
    out = tmp_path / "print"
    result = print_cards.render_print_set([1, 4], out)
    assert set(result) == {"white", "black", "black_on_white"}
    for variant, path in result.items():
        prefix = print_cards.VARIANTS[variant][0]
        assert re.fullmatch(rf"{prefix}-[0-9a-f]{{16}}\.pdf", path.name)
        assert path.read_bytes().startswith(b"%PDF")
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert len({p.name for p in result.values()}) == 3
    assert not list(out.glob("*.tmp"))


def test_pdf_has_one_page_per_nine_cards(tmp_path):
    many = [{"id": i, "text": f"Kártya {i}", "pick": 1} for i in range(19)]
    path = tmp_path / "x.pdf"
    print_cards.render_pdf(many, path, "white")
    assert _pages(path) == 3


def test_same_selection_reuses_the_file_and_restarts_its_clock(master, tmp_path):
    out = tmp_path / "print"
    first = print_cards.render_print_set([1, 4], out)["white"]
    old = time.time() - 3600
    os.utime(first, (old, old))
    second = print_cards.render_print_set([4, 1], out)["white"]  # order doesn't matter
    assert second == first
    assert second.stat().st_mtime > old + 3000


def test_different_selection_gets_a_different_file(master, tmp_path):
    out = tmp_path / "print"
    both = print_cards.render_print_set([1, 4], out)["white"]
    kek = print_cards.render_print_set([4], out)["white"]
    assert both != kek
    assert len(list(out.glob("feher-kartyak-*.pdf"))) == 2


def test_white_only_selection_has_no_black_pdfs(master, tmp_path):
    result = print_cards.render_print_set([4], tmp_path)
    assert result["black"] is None and result["black_on_white"] is None
    assert result["white"].exists()


def test_file_name_changes_with_text_pick_and_layout(monkeypatch):
    cards = [{"id": 1, "text": "Valami ________.", "pick": 1}]
    name = print_cards.file_name("black", cards)
    assert print_cards.file_name("black", [{**cards[0], "text": "Valami más ________."}]) != name
    assert print_cards.file_name("black", [{**cards[0], "pick": 2}]) != name
    # Same cards, other variant: a different hash, not just another prefix.
    assert print_cards.file_name("black_on_white", cards)[-20:] != name[-20:]
    monkeypatch.setattr(print_cards, "LAYOUT_VERSION", print_cards.LAYOUT_VERSION + 1)
    assert print_cards.file_name("black", cards) != name


def test_failed_render_leaves_no_file_behind(master, tmp_path, monkeypatch):
    def boom(cards, path, variant):
        path.write_bytes(b"half")
        raise RuntimeError("render failed")
    monkeypatch.setattr(print_cards, "render_pdf", boom)
    out = tmp_path / "print"
    with pytest.raises(RuntimeError):
        print_cards.render_print_set([1], out)
    assert list(out.iterdir()) == []


# ── cleanup ───────────────────────────────────────────────────────────────────

def test_cleanup_deletes_only_old_pdfs(tmp_path):
    now = time.time()
    old_pdf, new_pdf, old_tmp, other = (tmp_path / n for n in
                                        ("a.pdf", "b.pdf", "c.pdf.tmp", "notes.txt"))
    for path in (old_pdf, new_pdf, old_tmp, other):
        path.write_bytes(b"x")
    for path in (old_pdf, old_tmp, other):
        os.utime(path, (now - 25 * 3600, now - 25 * 3600))
    os.utime(new_pdf, (now - 23 * 3600, now - 23 * 3600))

    removed = print_cards.cleanup(tmp_path, now=now)

    assert sorted(p.name for p in removed) == ["a.pdf", "c.pdf.tmp"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.pdf", "notes.txt"]


def test_cleanup_without_the_folder_does_nothing(tmp_path):
    assert print_cards.cleanup(tmp_path / "missing") == []


def test_cleanup_script_uses_the_print_folder(tmp_path, monkeypatch, capsys):
    from scripts import cleanup_print_files
    monkeypatch.setattr(print_cards, "OUTPUT_DIR", tmp_path)
    (tmp_path / "old.pdf").write_bytes(b"x")
    os.utime(tmp_path / "old.pdf", (0, 0))
    assert cleanup_print_files.main() == 0
    assert not (tmp_path / "old.pdf").exists()
    assert "removed 1" in capsys.readouterr().out
