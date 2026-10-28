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

NINE = print_cards.LAYOUTS[9]
EIGHTEEN = print_cards.LAYOUTS[18]
WIDTH = NINE.card_w - 2 * NINE.padding


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


def test_wrap_keeps_a_blank_together_with_its_suffix():
    text = "Az arckifejezése, amikor ________-val leped meg, „________-kihívásként” ________-hoz/-hez"
    for width in (WIDTH, EIGHTEEN.card_w - 2 * EIGHTEEN.padding):
        lines, _ = print_cards.wrap(text, 14, width)
        assert not any(line.endswith("________-") for line in lines), lines
        assert not any(line.startswith(("val", "kihívásként", "hoz")) for line in lines), lines


def test_wrap_flags_a_word_too_wide_for_any_line():
    # Digits have no hyphenation point, so it can only be cut.
    token = "1234567890" * 8
    lines, split_word = print_cards.wrap(token, 14, WIDTH)
    assert split_word
    assert "".join(lines) == token
    assert all(_width(line, 14) <= WIDTH for line in lines)


def test_long_word_is_hyphenated_at_full_size():
    for layout in (NINE, EIGHTEEN):
        width = layout.card_w - 2 * layout.padding
        for text in ("Gyűlöletbűncselekményt színlelni", "Jövedelemegyenlőtlenség",
                     "Jövedelemegyenlőtlenségeitekért"):
            size, lines, fits = print_cards.fit_text(text, width, 50 * print_cards.mm, layout)
            assert fits and size == layout.max_font, (layout.per_page, text, size)
            assert lines[0].endswith("-")
            # Removing the inserted hyphens gives the text back.
            assert "".join(l[:-1] if l.endswith("-") else l + " " for l in lines).strip() == text


def test_hyphenation_points_are_hungarian_syllable_breaks():
    import pyphen
    valid = set(pyphen.Pyphen(lang="hu_HU", left=3, right=3).positions("gyűlöletbűncselekményt"))
    lines, split_word = print_cards.wrap("Gyűlöletbűncselekményt", 14, WIDTH)
    assert not split_word and len(lines) == 2
    assert len(lines[0]) - 1 in valid          # break position, without the "-"


def test_hyphenation_handles_the_hosszu_special_case():
    # Hungarian: "hosszú" is hyphenated "hosz-szú", not "hossz-ú".
    width = pdfmetrics.stringWidth("hosz-", print_cards.FONT_NAME, 14) + 1
    lines, _ = print_cards.wrap("hosszú", 14, width)
    assert lines == ["hosz-", "szú"]


def test_word_that_fits_a_line_moves_down_instead_of_being_hyphenated():
    lines, _ = print_cards.wrap("A következő elővarázsolom", 14, WIDTH)
    assert not any(line.endswith("-") for line in lines)


def test_blanks_numbers_and_file_names_are_not_hyphenated():
    for token in ("„________-kihívásként”", "anya_apa_szexvideo.avi", "________.com/________."):
        assert print_cards._hyphenate(token, lambda first: True) is None
    assert print_cards._hyphenate("„Gyűlöletbűncselekményt”,", lambda first: True)[1].endswith("”,")


def test_line_may_break_after_an_en_dash():
    lines, split_word = print_cards.wrap("A Budapest–Belgrád vasútvonal", 14,
                                         EIGHTEEN.card_w - 2 * EIGHTEEN.padding)
    assert not split_word
    assert any(line.endswith("Budapest–") for line in lines)


def test_fit_text_reports_text_that_cannot_fit():
    size, _lines, fits = print_cards.fit_text("szó " * 400, WIDTH, 30 * print_cards.mm, NINE)
    assert size == NINE.min_font
    assert not fits


def test_fit_text_short_card_uses_the_largest_size():
    size, lines, fits = print_cards.fit_text("Lepra", WIDTH, 60 * print_cards.mm, NINE)
    assert fits and size == NINE.max_font
    assert lines == ["Lepra"]


def test_fit_text_long_card_fits_the_box():
    text = "Ez egy nagyon hosszú kártya, amin rengeteg szó van, " * 8
    height = 40 * print_cards.mm
    size, lines, fits = print_cards.fit_text(text, WIDTH, height, NINE)
    assert fits and size < NINE.max_font
    assert len(lines) * size * print_cards.LEADING <= height


# ── files ─────────────────────────────────────────────────────────────────────

def test_render_print_set_writes_three_pdfs_with_hashed_names(master, tmp_path):
    out = tmp_path / "print"
    result = print_cards.render_print_set([1, 4], out)
    assert set(result) == {"white", "black", "black_on_white"}
    for variant, path in result.items():
        prefix = print_cards.VARIANTS[variant][0]
        assert re.fullmatch(rf"{prefix}-9-[0-9a-f]{{16}}\.pdf", path.name)
        assert path.read_bytes().startswith(b"%PDF")
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert len({p.name for p in result.values()}) == 3
    assert not list(out.glob("*.tmp"))


@pytest.mark.parametrize("per_page, pages", [(9, 3), (18, 2)])
def test_pdf_page_count_follows_cards_per_page(tmp_path, per_page, pages):
    many = [{"id": i, "text": f"Kártya {i}", "pick": 1} for i in range(19)]
    path = tmp_path / "x.pdf"
    print_cards.render_pdf(many, path, "white", per_page)
    assert _pages(path) == pages


@pytest.mark.parametrize("per_page", [9, 18])
def test_card_grid_fits_the_page_without_overlap(per_page):
    layout = print_cards.LAYOUTS[per_page]
    page_w, page_h = layout.pagesize
    boxes = []
    for slot in range(layout.per_page):
        x, y = print_cards._card_origin(slot, layout)
        # Printers can't print the outer ~4 mm.
        assert x >= 4 * print_cards.mm and y >= 4 * print_cards.mm
        assert x + layout.card_w <= page_w - 4 * print_cards.mm
        assert y + layout.card_h <= page_h - 4 * print_cards.mm
        boxes.append((x, y))
    for i, (x1, y1) in enumerate(boxes):
        for x2, y2 in boxes[i + 1:]:
            apart_x = abs(x1 - x2) >= layout.card_w + layout.gap - 0.01
            apart_y = abs(y1 - y2) >= layout.card_h + layout.gap - 0.01
            assert apart_x or apart_y


def test_eighteen_per_page_is_landscape_with_smaller_cards():
    assert EIGHTEEN.pagesize[0] > EIGHTEEN.pagesize[1]
    assert NINE.pagesize[0] < NINE.pagesize[1]
    # Same card shape, 70 % of the size.
    assert abs(EIGHTEEN.card_w / EIGHTEEN.card_h - NINE.card_w / NINE.card_h) < 1e-9
    assert EIGHTEEN.card_w < NINE.card_w


def test_cards_carry_only_their_text_and_pick_label(monkeypatch, tmp_path):
    drawn = []
    from reportlab.pdfgen.canvas import Canvas
    monkeypatch.setattr(Canvas, "drawString", lambda self, x, y, text, *a, **k: drawn.append(text))
    monkeypatch.setattr(Canvas, "drawRightString", lambda self, x, y, text, *a, **k: drawn.append(text))
    cards = [{"id": 1, "text": "Mi ez ________?", "pick": 1},
             {"id": 2, "text": "________ és ________.", "pick": 2}]
    print_cards.render_pdf(cards, tmp_path / "x.pdf", "black", 9)
    assert drawn[-1] == "Játssz ki 2 kártyát"
    assert " ".join(drawn[:-1]).split() == "Mi ez ________? ________ és ________.".split()
    assert not any("Emberiség" in text for text in drawn)


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


def test_nine_and_eighteen_per_page_are_separate_files(master, tmp_path):
    nine = print_cards.render_print_set([1], tmp_path, per_page=9)["black"]
    eighteen = print_cards.render_print_set([1], tmp_path, per_page=18)["black"]
    assert nine != eighteen
    assert re.fullmatch(r"fekete-kartyak-18-[0-9a-f]{16}\.pdf", eighteen.name)
    assert _pages(eighteen) == 1


def test_unsupported_cards_per_page_is_refused(master, tmp_path):
    with pytest.raises(ValueError):
        print_cards.render_print_set([1], tmp_path, per_page=12)


def test_white_only_selection_has_no_black_pdfs(master, tmp_path):
    result = print_cards.render_print_set([4], tmp_path)
    assert result["black"] is None and result["black_on_white"] is None
    assert result["white"].exists()


def test_file_name_changes_with_text_pick_and_layout(monkeypatch):
    cards = [{"id": 1, "text": "Valami ________.", "pick": 1}]
    name = print_cards.file_name("black", cards)
    assert print_cards.file_name("black", [{**cards[0], "text": "Valami más ________."}]) != name
    assert print_cards.file_name("black", [{**cards[0], "pick": 2}]) != name
    assert print_cards.file_name("black", cards, 18)[-20:] != name[-20:]
    # Same cards, other variant: a different hash, not just another prefix.
    assert print_cards.file_name("black_on_white", cards)[-20:] != name[-20:]
    monkeypatch.setattr(print_cards, "LAYOUT_VERSION", print_cards.LAYOUT_VERSION + 1)
    assert print_cards.file_name("black", cards) != name


def test_failed_render_leaves_no_file_behind(master, tmp_path, monkeypatch):
    def boom(cards, path, variant, per_page):
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


# ── every real card ───────────────────────────────────────────────────────────

def test_every_real_card_fits_both_sizes_without_cutting_a_word():
    """Runs on the real database/card_master.db, so a new upload with a card
    too long for a printed card fails here (see also
    scripts/render_print_pdfs.py --stress)."""
    from server import db
    conn = db.get_conn()
    try:
        cards = db.get_playable_cards(conn)
    finally:
        conn.close()
    assert len(cards) > 1000
    too_long = []
    for layout in print_cards.LAYOUTS.values():
        for card in cards:
            frames = (False, True) if card["type"] == "Black" else (False,)
            for frame in frames:
                size, lines, fits = print_cards.layout_card(card, layout, frame)
                if not fits:
                    too_long.append((layout.per_page, frame, card["id"], card["text"]))
    assert too_long == []


@pytest.mark.parametrize("per_page", [9, 18])
def test_long_text_stays_above_the_pick_label(monkeypatch, tmp_path, per_page):
    texts, labels = [], []
    from reportlab.pdfgen.canvas import Canvas
    monkeypatch.setattr(Canvas, "drawString", lambda self, x, y, text, *a, **k: texts.append(y))
    monkeypatch.setattr(Canvas, "drawRightString", lambda self, x, y, text, *a, **k: labels.append(y))
    long_pick = {"id": 1, "pick": 3, "text": "Srácok, tegnap este láttam ezt az őrült filmet. " * 4}
    print_cards.render_pdf([long_pick], tmp_path / "x.pdf", "black", per_page)
    layout = print_cards.LAYOUTS[per_page]
    [label_y] = labels
    # The last text line's descenders stay clear of the label's top.
    assert min(texts) - 0.25 * layout.max_font > label_y + layout.pick_font
