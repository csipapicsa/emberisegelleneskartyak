# server/print_cards.py
"""Printable PDFs of the cards in one or more packs, to print at home and cut.

For a selection of editions three PDFs are made:

- white cards (black text on white),
- black cards (white text on black),
- black cards on white with a thick black frame, to save ink. The frame keeps
  them apart from white cards once cut.

Cards are 63 × 88 mm (standard playing-card size, so sleeves fit) with rounded
corners, 9 per A4 page, with a gap between them so every card can be cut out
on its own outline. Each card is printed once: packs overlap, so the cards are
de-duplicated by id and by text.

Files go to OUTPUT_DIR and are named after a hash of what's on them, e.g.
``feher-kartyak-3f9c0a1b2c3d4e5f.pdf``. The same selection on the same
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
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from server import card_search
from server import db
from server import messages_db

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "generated" / "print"
MAX_AGE_SECONDS = 24 * 60 * 60

# Bump when the look changes, so files rendered with the old layout get new
# names instead of being reused.
LAYOUT_VERSION = 1

CARD_W = 63 * mm
CARD_H = 88 * mm
CORNER_RADIUS = 3.5 * mm
GAP = 4 * mm
COLUMNS, ROWS = 3, 3
PER_PAGE = COLUMNS * ROWS
PADDING = 6 * mm
FOOTER_H = 9 * mm
FRAME_W = 2 * mm            # black frame on the ink-saving black cards

FONT_NAME = "CardFont"
FONT_PATH = Path(__file__).resolve().parent / "fonts" / "DejaVuSans-Bold.ttf"
MAX_FONT_SIZE = 14
MIN_FONT_SIZE = 6
LEADING = 1.22
FOOTER_FONT_SIZE = 6.5
PICK_FONT_SIZE = 8
FOOTER_TEXT = "Emberiség Ellenes Kártyák"

# variant → (file name prefix, which cards, card fill, text colour, frame)
VARIANTS = {
    "white":          ("feher-kartyak",                "White", (1, 1, 1), (0, 0, 0), False),
    "black":          ("fekete-kartyak",               "Black", (0, 0, 0), (1, 1, 1), False),
    "black_on_white": ("fekete-kartyak-tintatakarekos", "Black", (1, 1, 1), (0, 0, 0), True),
}
CUT_LINE_GREY = (0.6, 0.6, 0.6)
FOOTER_GREY = {(0, 0, 0): (0.45, 0.45, 0.45), (1, 1, 1): (0.7, 0.7, 0.7)}


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

# A line may break after a hyphen with 2+ letters before it and 3+ after
# ("szuper-" / "képességem"), never "3H-" / "s".
_AFTER_HYPHEN = re.compile(r"(?<=\w\w-)(?=\w{3})")


def wrap(text: str, size: float, width: float) -> tuple[list[str], bool]:
    """Break text into lines no wider than width at this font size.

    Keeps the card's own line breaks and may break after a hyphen
    ("szuper-" / "képességem"). Returns (lines, split_word): split_word is
    True when a word had to be cut mid-word because even that piece was too
    wide — fit_text() then tries a smaller size first.
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
                if line:
                    lines.append(line)
                line = piece
                while not fits(line):
                    split_word = True
                    cut = len(line) - 1
                    while cut > 1 and not fits(line[:cut]):
                        cut -= 1
                    lines.append(line[:cut])
                    line = line[cut:]
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines, split_word


def fit_text(text: str, width: float, height: float) -> tuple[float, list[str]]:
    """Largest font size (in half points) at which the text fits the box
    without cutting a word; at MIN_FONT_SIZE it's used as is."""
    size = MAX_FONT_SIZE
    while True:
        lines, split_word = wrap(text, size, width)
        if (len(lines) * size * LEADING <= height and not split_word) or size <= MIN_FONT_SIZE:
            return size, lines
        size -= 0.5


def _pick_label(card: dict) -> str:
    pick = card.get("pick") or 1
    return f"Játssz ki {pick} kártyát" if pick > 1 else ""


# ── drawing ───────────────────────────────────────────────────────────────────

def _card_origin(slot: int) -> tuple[float, float]:
    page_w, page_h = A4
    grid_w = COLUMNS * CARD_W + (COLUMNS - 1) * GAP
    grid_h = ROWS * CARD_H + (ROWS - 1) * GAP
    left = (page_w - grid_w) / 2
    top = (page_h + grid_h) / 2
    col, row = slot % COLUMNS, slot // COLUMNS
    return left + col * (CARD_W + GAP), top - (row + 1) * CARD_H - row * GAP


def _draw_card(c: canvas.Canvas, card: dict, x: float, y: float, fill, ink, frame: bool) -> None:
    c.setFillColorRGB(*fill)
    if frame:
        # Stroke centred on a path inset by half its width, so the frame's
        # outer edge is exactly the cut line.
        inset = FRAME_W / 2
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(FRAME_W)
        c.roundRect(x + inset, y + inset, CARD_W - FRAME_W, CARD_H - FRAME_W,
                    CORNER_RADIUS - inset, stroke=1, fill=1)
    elif fill == (1, 1, 1):
        c.setStrokeColorRGB(*CUT_LINE_GREY)
        c.setLineWidth(0.4)
        c.roundRect(x, y, CARD_W, CARD_H, CORNER_RADIUS, stroke=1, fill=1)
    else:
        c.roundRect(x, y, CARD_W, CARD_H, CORNER_RADIUS, stroke=0, fill=1)

    label = _pick_label(card)
    pad = PADDING + (FRAME_W if frame else 0)
    text_w = CARD_W - 2 * pad
    text_h = CARD_H - pad - FOOTER_H - (FRAME_W if frame else 0) - (PICK_FONT_SIZE * 2 if label else 0)
    size, lines = fit_text(card["text"], text_w, text_h)
    c.setFillColorRGB(*ink)
    c.setFont(FONT_NAME, size)
    baseline = y + CARD_H - pad - size
    for line in lines:
        c.drawString(x + pad, baseline, line)
        baseline -= size * LEADING

    footer_y = y + (FRAME_W if frame else 0) + 4 * mm
    c.setFont(FONT_NAME, FOOTER_FONT_SIZE)
    c.setFillColorRGB(*FOOTER_GREY[ink])
    c.drawString(x + pad, footer_y, FOOTER_TEXT)
    if label:
        # Its own line above the footer, right-aligned, so it can't run into it.
        c.setFont(FONT_NAME, PICK_FONT_SIZE)
        c.setFillColorRGB(*ink)
        c.drawRightString(x + CARD_W - pad, footer_y + FOOTER_FONT_SIZE + 2 * mm, label)


def render_pdf(cards: list[dict], path: Path, variant: str) -> None:
    """Write cards to path as an A4 PDF, PER_PAGE per page."""
    _register_font()
    _prefix, _type, fill, ink, frame = VARIANTS[variant]
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"{FOOTER_TEXT} – nyomtatható kártyák")
    c.setAuthor(FOOTER_TEXT)
    for page in range(math.ceil(len(cards) / PER_PAGE)):
        for slot, card in enumerate(cards[page * PER_PAGE:(page + 1) * PER_PAGE]):
            _draw_card(c, card, *_card_origin(slot), fill, ink, frame)
        c.showPage()
    c.save()


# ── files ─────────────────────────────────────────────────────────────────────

def file_name(variant: str, cards: list[dict]) -> str:
    """<prefix>-<hash>.pdf, the hash covering everything printed on the cards."""
    prefix = VARIANTS[variant][0]
    payload = json.dumps(
        [LAYOUT_VERSION, variant, [[c["text"], c.get("pick") or 1] for c in cards]],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}.pdf"


def render_print_set(edition_ids: list[int], output_dir: Path | None = None) -> dict[str, Path | None]:
    """Render (or reuse) the three PDFs for a selection of editions.

    Returns variant → file path, or None for a variant with no cards (a
    white-only pack has no black PDFs). Synchronous and CPU-bound: call it
    from a worker thread, not the event loop.
    """
    output_dir = Path(output_dir or OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    black, white = collect_cards(edition_ids)
    by_type = {"Black": black, "White": white}
    result: dict[str, Path | None] = {}
    for variant, (_prefix, card_type, *_rest) in VARIANTS.items():
        cards = by_type[card_type]
        if not cards:
            result[variant] = None
            continue
        path = output_dir / file_name(variant, cards)
        if path.exists():
            os.utime(path)  # served again: restart its 24 hours
        else:
            # Render to a temp file and rename, so two requests for the same
            # selection never see a half-written PDF.
            fd, tmp = tempfile.mkstemp(dir=output_dir, suffix=".pdf.tmp")
            os.close(fd)
            try:
                render_pdf(cards, Path(tmp), variant)
                os.chmod(tmp, 0o644)  # mkstemp makes it owner-only
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        result[variant] = path
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
