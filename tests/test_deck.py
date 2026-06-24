# tests/test_deck.py
"""Unit tests for deck operations."""
import pytest
from server.deck import build_deck, deal_hand, draw_card, draw_black, reshuffle_into_deck
from tests.helpers import make_white_cards, make_black_cards


def test_build_deck_preserves_all_cards():
    black = make_black_cards(10)
    white = make_white_cards(40)
    b, w = build_deck(black, white)
    assert len(b) == 10
    assert len(w) == 40


def test_deal_hand_removes_from_deck():
    white = make_white_cards(20)
    hand = deal_hand(white, 10)
    assert len(hand) == 10
    assert len(white) == 10


def test_deal_hand_gives_unique_cards():
    white = make_white_cards(20)
    hand = deal_hand(white, 10)
    ids = [c["id"] for c in hand]
    assert len(ids) == len(set(ids))


def test_draw_card_removes_from_deck():
    white = make_white_cards(5)
    card = draw_card(white)
    assert card is not None
    assert len(white) == 4


def test_draw_card_empty_deck_returns_none():
    assert draw_card([]) is None


def test_draw_black_removes_from_deck():
    black = make_black_cards(3)
    card = draw_black(black)
    assert card is not None
    assert len(black) == 2


def test_draw_black_empty_deck_returns_none():
    assert draw_black([]) is None


def test_reshuffle_extends_deck():
    white = make_white_cards(5)
    discarded = make_white_cards(3)
    original_len = len(white) + len(discarded)
    reshuffle_into_deck(white, discarded)
    assert len(white) == original_len


def test_reshuffle_does_not_duplicate():
    white = [{"id": 1}]
    discarded = [{"id": 2}, {"id": 3}]
    reshuffle_into_deck(white, discarded)
    ids = [c["id"] for c in white]
    assert sorted(ids) == [1, 2, 3]
