#!/usr/bin/env python3
"""Render the printable card PDFs for a selection of editions.

    venv/bin/python -m scripts.render_print_pdfs 107 106          # Magyar Alap + Vegyes, 9/page
    venv/bin/python -m scripts.render_print_pdfs 107 --per-page 18
    venv/bin/python -m scripts.render_print_pdfs -1               # Ti küldtétek
    venv/bin/python -m scripts.render_print_pdfs --list           # edition ids
    venv/bin/python -m scripts.render_print_pdfs --stress         # longest cards, both sizes

Prints the path of each PDF (see server/print_cards.py). Until the website
offers this, it's the way to try it out.

--stress takes the longest texts and the cards with the longest words (of
the given editions, or of every printable one), renders them in every
size and colour as proba-*.pdf, and lists any card whose text doesn't fit
without cutting a word. Run it after uploading a new card_master.db.
"""
from __future__ import annotations
import argparse
import sys
import time

from server import db, edition_display, print_cards
from server.messages_db import COMMUNITY_EDITION_ID


def printable_edition_ids() -> list[int]:
    conn = db.get_conn()
    try:
        return [e["id"] for e in db.get_editions(conn)] + [COMMUNITY_EDITION_ID]
    finally:
        conn.close()


def list_editions() -> None:
    conn = db.get_conn()
    try:
        editions = db.get_editions(conn)
    finally:
        conn.close()
    print("  -1  Ti küldtétek")
    for e in editions:
        print(f"{e['id']:>4}  {edition_display.label_for(e['name'])}  "
              f"({e['black_count']} fekete, {e['white_count']} fehér)")


def longest_word(card: dict) -> int:
    return max((len(word) for word in card["text"].split()), default=0)


def stress_cards(cards: list[dict], count: int) -> list[dict]:
    """The `count` longest texts plus the `count` cards with the longest
    word, without repeats."""
    picked = sorted(cards, key=lambda c: -len(c["text"]))[:count]
    picked += [c for c in sorted(cards, key=lambda c: -longest_word(c)) if c not in picked][:count]
    return picked


def stress(edition_ids: list[int], count: int) -> int:
    black, white = print_cards.collect_cards(edition_ids)
    everything = {"Black": black, "White": white}
    problems = 0
    for per_page, layout in print_cards.LAYOUTS.items():
        for variant, (prefix, card_type, _fill, _ink, frame) in print_cards.VARIANTS.items():
            cards = everything[card_type]
            if not cards:
                continue
            sizes = []
            for card in cards:
                size, _lines, fits = print_cards.layout_card(card, layout, frame)
                sizes.append(size)
                if not fits:
                    problems += 1
                    print(f"NEM FÉR EL  {per_page}/oldal {variant}: #{card['id']} {card['text']!r}")
            path = print_cards.OUTPUT_DIR / f"proba-{prefix}-{per_page}.pdf"
            print_cards.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            print_cards.render_pdf(stress_cards(cards, count), path, variant, per_page)
            print(f"{per_page:>2}/oldal {variant:>15}: {len(cards)} kártya, "
                  f"betűméret {min(sizes)}–{max(sizes)} pt  → {path}")
    print("minden kártya elfér" if not problems else f"{problems} kártya nem fér el", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("edition_ids", nargs="*", type=int)
    parser.add_argument("--list", action="store_true", help="list the printable editions")
    parser.add_argument("--per-page", type=int, choices=sorted(print_cards.LAYOUTS),
                        default=print_cards.DEFAULT_PER_PAGE, help="cards per page (default: 9)")
    parser.add_argument("--stress", action="store_true", help="render the longest cards as proba-*.pdf")
    parser.add_argument("--count", type=int, default=9, help="--stress: how many of each kind (default: 9)")
    args = parser.parse_args(argv)
    if args.stress:
        return stress(args.edition_ids or printable_edition_ids(), args.count)
    if args.list or not args.edition_ids:
        list_editions()
        return 0
    started = time.monotonic()
    result = print_cards.render_print_set(args.edition_ids, per_page=args.per_page)
    for variant, path in result.items():
        print(f"{variant:>15}: {path or '(nincs ilyen kártya)'}")
    print(f"{time.monotonic() - started:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
