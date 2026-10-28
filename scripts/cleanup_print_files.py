#!/usr/bin/env python3
"""Delete printable card PDFs older than 24 hours from generated/print/.

Run hourly by systemd/emberisegellenes-print-cleanup.timer:

    venv/bin/python -m scripts.cleanup_print_files
"""
from __future__ import annotations
import sys

from server import print_cards


def main() -> int:
    removed = print_cards.cleanup()
    print(f"removed {len(removed)} file(s) from {print_cards.OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
