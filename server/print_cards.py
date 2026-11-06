# server/print_cards.py
"""Printable PDFs of the cards in one or more packs, to print at home and cut.

For a selection of editions three PDFs are made:

- white cards (black text on white),
- black cards (white text on black),
- black cards on white with a thick black frame and a tiny black card in
  the bottom-left corner, to save ink. Printed on plain paper every card
  has a white back, so the frame and the mark keep them apart from white
  cards in a shuffled deck.

Two sizes (LAYOUTS): 9 cards of 63 × 88 mm (standard playing-card size, so
sleeves fit) on a portrait A4 page, or 18 cards of 44 × 62 mm (the same shape
at 70 %) on a landscape one. The cards touch, with a thin grey line on every
edge: one straight cut separates two cards, so a page needs only a few cuts
along the grid lines. That's why the corners are square — a corner rounder
can round them after cutting. Each card is printed once: packs overlap, so
the cards are de-duplicated by id and by text.

Files go to OUTPUT_DIR and are named after a hash of what's on them, e.g.
``feher-kartyak-9-3f9c0a1b2c3d4e5f.pdf``. The same selection on the same
card_master.db gets the same name, so a file that's already there is reused
instead of rendered again; any change in the cards gives a new name.
cleanup() deletes files older than MAX_AGE_SECONDS
(scripts/cleanup_print_files.py, run by a systemd timer).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pyphen
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from server import card_search, db, messages_db

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "generated" / "print"
MAX_AGE_SECONDS = 24 * 60 * 60

# Bump when the look changes, so files rendered with the old layout get new
# names instead of being reused.
LAYOUT_VERSION = 6

FONT_NAME = "CardFont"
FONT_PATH = Path(__file__).resolve().parent / "fonts" / "DejaVuSans-Bold.ttf"
LEADING = 1.22


@dataclass(frozen=True)
class PrintFile:
    path: Path
    cards: int
    pages: int


@dataclass(frozen=True)
class Layout:
    """Page grid and card measurements, all in points."""
    per_page: int
    pagesize: tuple[float, float]
    columns: int
    rows: int
    card_w: float
    card_h: float
    padding: float
    frame_w: float          # black frame on the ink-saving black cards
    mark_w: float           # ...and the tiny black card bottom-left
    mark_h: float
    mark_radius: float
    max_font: float
    min_font: float
    pick_font: float        # "Játssz ki 2 kártyát" at the bottom


def _layout(per_page: int, pagesize, columns: int, rows: int, scale: float) -> Layout:
    """A layout whose card is the 63 × 88 mm one scaled by `scale`."""
    return Layout(
        per_page=per_page, pagesize=pagesize, columns=columns, rows=rows,
        card_w=63 * mm * scale, card_h=88 * mm * scale,
        padding=6 * mm * scale, frame_w=2 * mm * scale,
        # Upright, in a card's own 63:88 shape, so it reads as "a black card".
        mark_w=3.6 * mm * scale, mark_h=3.6 * mm * 88 / 63 * scale, mark_radius=0.6 * mm * scale,
        max_font=round(14 * scale * 2) / 2, min_font=max(5.0, round(6 * scale * 2) / 2),
        pick_font=round(8 * scale * 2) / 2,
    )


LAYOUTS = {
    9: _layout(9, A4, 3, 3, 1.0),
    18: _layout(18, landscape(A4), 6, 3, 0.7),
}
DEFAULT_PER_PAGE = 9

# variant → (file name prefix, which cards, card fill, text colour, frame)
VARIANTS = {
    "white":          ("feher-kartyak",                "White", (1, 1, 1), (0, 0, 0), False),
    "black":          ("fekete-kartyak",               "Black", (0, 0, 0), (1, 1, 1), False),
    "black_on_white": ("fekete-kartyak-tintatakarekos", "Black", (1, 1, 1), (0, 0, 0), True),
}
# What the /nyomtatas page calls each file.
VARIANT_LABELS = {
    "white": "Fehér kártyák",
    "black": "Fekete kártyák – fekete háttérrel",
    "black_on_white": "Fekete kártyák – tintatakarékos (fehér háttér, fekete keret)",
}
CUT_LINE_GREY = (0.6, 0.6, 0.6)   # visible on white and on black
CUT_LINE_W = 0.5
PDF_TITLE = "Emberiség Ellenes Kártyák – nyomtatható kártyák"


def _register_font() -> None:
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))


# ── which cards ───────────────────────────────────────────────────────────────

def collect_cards(edition_ids: list[int]) -> tuple[list[dict], list[dict]]:
    """(black, white) cards of the selected packs, each printed once.

    Same rules as dealing a game: only complete card_master.db packs count,
    and COMMUNITY_EDITION_ID adds the approved community cards. Cards are
    unique by id already; dropping repeated texts too catches a community
    card that says the same as a pack card. Sorted by id for a stable order.
    """
    real_ids = [e for e in edition_ids if e != messages_db.COMMUNITY_EDITION_ID]
    black, white = [], []
    if real_ids:
        conn = db.get_conn()
        try:
            black, white = db.get_cards_for_editions(conn, real_ids)
        finally:
            conn.close()
    if messages_db.COMMUNITY_EDITION_ID in edition_ids:
        conn = messages_db.get_conn()
        try:
            c_black, c_white = messages_db.get_community_cards(conn)
        finally:
            conn.close()
        black, white = black + c_black, white + c_white
    return _unique(black), _unique(white)


def _unique(cards: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for card in sorted(cards, key=lambda c: c["id"]):
        key = card_search.fold(card["text"])
        if key and key not in seen:
            seen.add(key)
            out.append(card)
    return out


# ── text layout ───────────────────────────────────────────────────────────────

# A line may break after a hyphen or en dash with 2+ letters before it and
# 3+ after ("szuper-" / "képességem", "Budapest–" / "Belgrád"), never "3H-" /
# "s", and never between a blank and its suffix ("________-val"). [^\W_] is
# a letter or digit, not "_".
_AFTER_HYPHEN = re.compile(r"(?<=[^\W_]{2}[-–])(?=[^\W_]{3})")

# Hungarian hyphenation (LibreOffice's hu_HU patterns): "Gyűlöletbűn-" /
# "cselekményt", and the special cases too ("hosszú" → "hosz-" / "szú").
# At least 3 letters on each side.
_HYPHENATOR = pyphen.Pyphen(lang="hu_HU", left=3, right=3)
# Only plain words are hyphenated, with any punctuation around them kept:
# not blanks ("________-val"), numbers, "3H-s" or file names.
_PLAIN_WORD = re.compile(r"^(\W*)([^\W\d_]+)(\W*)$")


def _hyphenate(piece: str, fits_first) -> tuple[str, str] | None:
    """Split piece at the latest Hungarian hyphenation point whose first
    part (with its "-") passes fits_first; None when there is no such point."""
    match = _PLAIN_WORD.match(piece)
    if not match:
        return None
    lead, word, trail = match.groups()
    for first, rest in _HYPHENATOR.iterate(word):   # longest first part first
        if fits_first(f"{lead}{first}-"):
            return f"{lead}{first}-", f"{rest}{trail}"
    return None


def wrap(text: str, size: float, width: float) -> tuple[list[str], bool]:
    """Break text into lines no wider than width at this font size.

    Keeps the card's own line breaks and may break after a hyphen
    ("szuper-" / "képességem"). A word too wide for a line of its own is
    hyphenated by Hungarian rules, filling the current line first; shorter
    words just move to the next line. Returns (lines, split_word):
    split_word is True when a word had to be cut where no hyphenation point
    fits — fit_text() then tries a smaller size first.
    """
    _register_font()

    def fits(s: str) -> bool:
        return pdfmetrics.stringWidth(s, FONT_NAME, size) <= width

    lines: list[str] = []
    split_word = False
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            for i, piece in enumerate(_AFTER_HYPHEN.split(word)):
                joiner = "" if i else " "
                candidate = f"{line}{joiner}{piece}" if line else piece
                if fits(candidate):
                    line = candidate
                    continue
                if fits(piece):
                    if line:
                        lines.append(line)
                    line = piece
                    continue
                head = f"{line}{joiner}" if line else ""
                while not fits(head + piece):
                    split = _hyphenate(piece, lambda first: fits(head + first))
                    if split is None:
                        if head:  # no point fits after the line's words: start a fresh line
                            lines.append(line)
                            head = ""
                            continue
                        split_word = True
                        cut = len(piece) - 1
                        while cut > 1 and not fits(piece[:cut]):
                            cut -= 1
                        split = (piece[:cut], piece[cut:])
                    first, piece = split
                    lines.append(head + first)
                    head = ""
                line = head + piece
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines, split_word


def fit_text(text: str, width: float, height: float, layout: Layout) -> tuple[float, list[str], bool]:
    """Largest font size (in half points) at which the text fits the box
    without cutting a word. Returns (size, lines, fits); when nothing down
    to layout.min_font fits, that size is used anyway and fits is False."""
    size = layout.max_font
    while True:
        lines, split_word = wrap(text, size, width)
        fits = len(lines) * size * LEADING <= height and not split_word
        if fits or size <= layout.min_font:
            return size, lines, fits
        size -= 0.5


def _pick_label(card: dict) -> str:
    pick = card.get("pick") or 1
    return f"Játssz ki {pick} kártyát" if pick > 1 else ""


def text_box(card: dict, layout: Layout, frame: bool) -> tuple[float, float, float]:
    """(inset, width, height) of the area the card's text may fill. The
    bottom line is kept free for the pick label and, on framed cards, the
    black mark."""
    inset = layout.padding + (layout.frame_w if frame else 0)
    reserved = layout.pick_font * 2 if _pick_label(card) else 0
    if frame:
        reserved = max(reserved, layout.mark_h + layout.pick_font)
    bottom = inset + reserved
    return inset, layout.card_w - 2 * inset, layout.card_h - inset - bottom


def layout_card(card: dict, layout: Layout, frame: bool = False) -> tuple[float, list[str], bool]:
    """Font size, lines and whether the card's text fits on this layout."""
    _inset, width, height = text_box(card, layout, frame)
    return fit_text(card["text"], width, height, layout)


# ── drawing ───────────────────────────────────────────────────────────────────

def _card_origin(slot: int, layout: Layout) -> tuple[float, float]:
    page_w, page_h = layout.pagesize
    left = (page_w - layout.columns * layout.card_w) / 2
    top = (page_h + layout.rows * layout.card_h) / 2
    col, row = slot % layout.columns, slot // layout.columns
    return left + col * layout.card_w, top - (row + 1) * layout.card_h


def _draw_card(c: canvas.Canvas, card: dict, x: float, y: float, layout: Layout, fill, ink, frame: bool) -> None:
    w, h = layout.card_w, layout.card_h
    c.setFillColorRGB(*fill)
    if frame:
        # Stroke centred on a path inset by half its width, so the frame's
        # outer edge is exactly the cut line. Two touching cards make a
        # double-width band with the cut line in its middle, which forgives
        # a slightly crooked cut.
        half = layout.frame_w / 2
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(layout.frame_w)
        c.rect(x + half, y + half, w - layout.frame_w, h - layout.frame_w, stroke=1, fill=1)
    else:
        c.rect(x, y, w, h, stroke=0, fill=1)

    inset, _width, _height = text_box(card, layout, frame)
    size, lines, _fits = layout_card(card, layout, frame)
    c.setFillColorRGB(*ink)
    c.setFont(FONT_NAME, size)
    baseline = y + h - inset - size
    for line in lines:
        c.drawString(x + inset, baseline, line)
        baseline -= size * LEADING

    label = _pick_label(card)
    if label:
        c.setFont(FONT_NAME, layout.pick_font)
        c.drawRightString(x + w - inset, y + inset, label)
    if frame:
        c.setFillColorRGB(0, 0, 0)
        c.roundRect(x + inset, y + inset, layout.mark_w, layout.mark_h, layout.mark_radius, stroke=0, fill=1)

    # Cut line last, on top of the fill and frame. Touching cards share it.
    c.setStrokeColorRGB(*CUT_LINE_GREY)
    c.setLineWidth(CUT_LINE_W)
    c.rect(x, y, w, h, stroke=1, fill=0)


def render_pdf(cards: list[dict], path: Path, variant: str, per_page: int = DEFAULT_PER_PAGE) -> None:
    """Write cards to path as an A4 PDF, per_page cards per page."""
    _register_font()
    layout = LAYOUTS[per_page]
    _prefix, _type, fill, ink, frame = VARIANTS[variant]
    c = canvas.Canvas(str(path), pagesize=layout.pagesize)
    c.setTitle(PDF_TITLE)
    for page in range(math.ceil(len(cards) / per_page)):
        for slot, card in enumerate(cards[page * per_page:(page + 1) * per_page]):
            _draw_card(c, card, *_card_origin(slot, layout), layout, fill, ink, frame)
        c.showPage()
    c.save()


# ── files ─────────────────────────────────────────────────────────────────────

def file_name(variant: str, cards: list[dict], per_page: int = DEFAULT_PER_PAGE) -> str:
    """<prefix>-<per page>-<hash>.pdf, the hash covering everything printed."""
    prefix = VARIANTS[variant][0]
    payload = json.dumps(
        [LAYOUT_VERSION, variant, per_page, [[c["text"], c.get("pick") or 1] for c in cards]],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{per_page}-{digest}.pdf"


# Exactly the names file_name() makes, so a download URL can't name any
# other file.
FILE_NAME_RE = re.compile(
    "(?:%s)-(?:%s)-[0-9a-f]{16}\\.pdf" % (
        "|".join(re.escape(prefix) for prefix, *_ in VARIANTS.values()),
        "|".join(str(n) for n in LAYOUTS),
    )
)


def render_print_set(
    edition_ids: list[int], output_dir: Path | None = None, per_page: int = DEFAULT_PER_PAGE,
) -> dict[str, PrintFile | None]:
    """Render (or reuse) the three PDFs for a selection of editions, with
    per_page (9 or 18) cards on a page.

    Returns variant → PrintFile, or None for a variant with no cards (a
    white-only pack has no black PDFs). Synchronous and CPU-bound: call it
    from a worker thread, not the event loop.
    """
    if per_page not in LAYOUTS:
        raise ValueError(f"per_page must be one of {sorted(LAYOUTS)}")
    output_dir = Path(output_dir or OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    black, white = collect_cards(edition_ids)
    by_type = {"Black": black, "White": white}
    result: dict[str, PrintFile | None] = {}
    for variant, (_prefix, card_type, *_rest) in VARIANTS.items():
        cards = by_type[card_type]
        if not cards:
            result[variant] = None
            continue
        path = output_dir / file_name(variant, cards, per_page)
        if path.exists():
            os.utime(path)  # served again: restart its 24 hours
        else:
            # Render to a temp file and rename, so two requests for the same
            # selection never see a half-written PDF.
            fd, tmp = tempfile.mkstemp(dir=output_dir, suffix=".pdf.tmp")
            os.close(fd)
            try:
                render_pdf(cards, Path(tmp), variant, per_page)
                os.chmod(tmp, 0o644)  # mkstemp makes it owner-only
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        result[variant] = PrintFile(path, len(cards), math.ceil(len(cards) / per_page))
    return result


def cleanup(output_dir: Path | None = None, max_age: float = MAX_AGE_SECONDS, now: float | None = None) -> list[Path]:
    """Delete PDFs (and leftover temp files) older than max_age seconds.
    Returns what was deleted."""
    output_dir = Path(output_dir or OUTPUT_DIR)
    if not output_dir.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - max_age
    removed = []
    for path in output_dir.iterdir():
        if not path.is_file() or not path.name.endswith((".pdf", ".pdf.tmp")):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except FileNotFoundError:
            pass  # deleted meanwhile, e.g. by another cleanup run
    return removed
