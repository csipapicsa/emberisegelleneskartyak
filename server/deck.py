from __future__ import annotations
import random


def build_deck(black_cards: list[dict], white_cards: list[dict]) -> tuple[list[dict], list[dict]]:
    black = list(black_cards)
    white = list(white_cards)
    random.shuffle(black)
    random.shuffle(white)
    return black, white


def deal_hand(white_deck: list[dict], size: int) -> list[dict]:
    hand = white_deck[:size]
    del white_deck[:size]
    return hand


def draw_card(white_deck: list[dict]) -> dict | None:
    if not white_deck:
        return None
    return white_deck.pop(0)


def reshuffle_into_deck(white_deck: list[dict], discarded: list[dict]) -> None:
    random.shuffle(discarded)
    white_deck.extend(discarded)


def draw_black(black_deck: list[dict]) -> dict | None:
    if not black_deck:
        return None
    return black_deck.pop(0)
