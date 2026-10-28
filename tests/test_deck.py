# tests/test_deck.py
"""Unit tests for deck operations."""
import pytest
from server.deck import build_deck, deal_hand, draw_card, draw_black, least_seen_shuffle, reshuffle_into_deck
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


def _fresh_in_top(cards: list[dict], seen: dict[int, int], fresh: set[int], top: int, trials: int = 400) -> float:
    """Average number of `fresh` cards among the first `top` after a shuffle."""
    total = 0
    for _ in range(trials):
        total += sum(c["id"] in fresh for c in least_seen_shuffle(cards, seen)[:top])
    return total / trials


def test_least_seen_shuffle_keeps_every_card_once():
    white = make_white_cards(50)
    seen = {i: i % 7 for i in range(50)}
    shuffled = least_seen_shuffle(white, seen)
    assert sorted(c["id"] for c in shuffled) == list(range(50))


def test_least_seen_shuffle_without_counts_is_a_plain_shuffle():
    white = make_white_cards(30)
    shuffled = least_seen_shuffle(white, {})
    assert sorted(c["id"] for c in shuffled) == list(range(30))
    assert white == make_white_cards(30)  # input left untouched


def test_least_seen_cards_tend_to_come_first():
    # 10 unseen cards among 40 seen 20 times each: a uniform shuffle puts
    # 2 of them in the first 10 on average; the weighting should beat that
    # clearly.
    white = make_white_cards(50)
    seen = {i: 20 for i in range(10, 50)}
    assert _fresh_in_top(white, seen, set(range(10)), top=10) > 4


def test_least_seen_shuffle_is_still_random_not_a_sort():
    # A well-seen card must still be able to come first.
    white = make_white_cards(20)
    seen = {i: 20 for i in range(10, 20)}
    firsts = {least_seen_shuffle(white, seen)[0]["id"] for _ in range(300)}
    assert firsts & set(range(10, 20))


def test_fresh_pack_does_not_take_over_when_counts_are_huge():
    # Weights are relative to the deck's average, so 0 vs 1000 pulls no
    # harder than 0 vs 10 — a new pack gets dealt more, not exclusively.
    white = make_white_cards(200)
    fresh = set(range(20))
    small = {i: 10 for i in range(20, 200)}
    huge = {i: 1000 for i in range(20, 200)}
    for seen in (small, huge):
        assert 4 < _fresh_in_top(white, seen, fresh, top=20) < 12


def test_build_deck_weights_both_decks():
    black = make_black_cards(20)
    white = make_white_cards(20)
    seen = {c["id"]: 50 for c in black[5:] + white[5:]}
    fresh_black = {c["id"] for c in black[:5]}
    hits_black = hits_white = 0
    for _ in range(300):
        b, w = build_deck(black, white, seen)
        hits_black += b[0]["id"] in fresh_black
        hits_white += w[0]["id"] in range(5)
    # Uniform would put a fresh card first 25% of the time.
    assert hits_black / 300 > 0.45
    assert hits_white / 300 > 0.45
