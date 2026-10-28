#!/usr/bin/env python3
"""Render the printable card PDFs for a selection of editions.

    venv/bin/python -m scripts.render_print_pdfs 107 106      # Magyar Alap + Vegyes
    venv/bin/python -m scripts.render_print_pdfs -1           # Ti küldtétek
    venv/bin/python -m scripts.render_print_pdfs --list       # edition ids

Prints the path of each PDF (see server/print_cards.py). Until the website
offers this, it's the way to try it out.
"""
from __future__ import annotations
import argparse
import sys
import time

from server import db, edition_display, print_cards


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("edition_ids", nargs="*", type=int)
    parser.add_argument("--list", action="store_true", help="list the printable editions")
    args = parser.parse_args(argv)
    if args.list or not args.edition_ids:
        list_editions()
        return 0
    started = time.monotonic()
    result = print_cards.render_print_set(args.edition_ids)
    for variant, path in result.items():
        print(f"{variant:>15}: {path or '(nincs ilyen kártya)'}")
    print(f"{time.monotonic() - started:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
