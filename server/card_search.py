# server/card_search.py
"""Find cards by typed text, for the card feedback page (/visszajelzes).

Players type a few words they remember from a card. Matching ignores case,
Hungarian accents ("oles" finds "ölés"), punctuation and the blank lines on
black cards, and every typed word has to appear somewhere in the card. Cards containing
the typed words as one phrase rank first, then by where it starts, then
shorter cards — so typing the start of a card puts it on top.
"""
from __future__ import annotations

import re
import unicodedata

MIN_QUERY_LENGTH = 2
MAX_RESULTS = 20

_BLANK = re.compile(r"_{2,}")
_NON_WORD = re.compile(r"[^\w]+")


def fold(text: str) -> str:
    """Lowercase, accent- and punctuation-free, single-spaced text with
    blanks removed."""
    text = unicodedata.normalize("NFKD", _BLANK.sub(" ", text).casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_NON_WORD.sub(" ", text).split())


def search(cards: list[dict], query: str, limit: int = MAX_RESULTS) -> list[dict]:
    phrase = fold(query)
    if len(phrase.replace(" ", "")) < MIN_QUERY_LENGTH:
        return []
    words = phrase.split()
    hits = []
    for card in cards:
        text = fold(card["text"])
        if all(word in text for word in words):
            position = text.find(phrase)
            hits.append(((position < 0, max(position, 0), len(text)), card))
    hits.sort(key=lambda hit: hit[0])
    return [card for _, card in hits[:limit]]
