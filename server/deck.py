from __future__ import annotations
import random

from server import card_seen


def build_deck(black_cards: list[dict], white_cards: list[dict]) -> tuple[list[dict], list[dict]]:
    black = list(black_cards)
    white = list(white_cards)
    random.shuffle(black)
    random.shuffle(white)
    return black, white


def deal_hand(white_deck: list[dict], size: int) -> list[dict]:
    hand = white_deck[:size]
    del white_deck[:size]
    card_seen.record(hand)
    return hand


def draw_card(white_deck: list[dict]) -> dict | None:
    if not white_deck:
        return None
    card = white_deck.pop(0)
    card_seen.record([card])
    return card


def reshuffle_into_deck(white_deck: list[dict], discarded: list[dict]) -> None:
    random.shuffle(discarded)
    white_deck.extend(discarded)


def draw_black(black_deck: list[dict]) -> dict | None:
    if not black_deck:
        return None
    card = black_deck.pop(0)
    card_seen.record([card])
    return card
