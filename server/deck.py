from __future__ import annotations

import random

from server import card_seen


def build_deck(
    black_cards: list[dict], white_cards: list[dict], seen: dict[int, int] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Shuffle both decks, putting cards players have seen less often near
    the top. seen maps card id → global seen count (card_seen.current_counts());
    without it the shuffle is uniform."""
    seen = seen or {}
    return least_seen_shuffle(black_cards, seen), least_seen_shuffle(white_cards, seen)


def least_seen_shuffle(cards: list[dict], seen: dict[int, int]) -> list[dict]:
    """Random order where each card's odds of coming early are weighted by
    1 / (1 + seen / average)², the average taken over this deck.

    It's still a shuffle, not a sort: any card can come first, but an unseen
    card is 4× as likely to be picked as an average one and 16× as likely as
    one seen 3× the average. Measuring against the deck's own average keeps
    the pull the same strength whether counts are in the tens or thousands,
    so a fresh pack gets dealt more but doesn't take over every hand.

    Weighted sampling without replacement (Efraimidis–Spirakis): each card
    draws an exponential key with rate = its weight, and the smallest keys go
    first.
    """
    counts = [seen.get(card.get("id"), 0) for card in cards]
    average = sum(counts) / len(counts) if counts else 0
    if not average:
        shuffled = list(cards)
        random.shuffle(shuffled)
        return shuffled
    keys = [random.expovariate(1 / (1 + n / average) ** 2) for n in counts]
    order = sorted(range(len(cards)), key=keys.__getitem__)
    return [cards[i] for i in order]


def deal_hand(white_deck: list[dict], size: int, record: bool = True) -> list[dict]:
    """record=False: the cards go to a computer player, who doesn't count as having seen them."""
    hand = white_deck[:size]
    del white_deck[:size]
    if record:
        card_seen.record(hand)
    return hand


def draw_card(white_deck: list[dict], record: bool = True) -> dict | None:
    if not white_deck:
        return None
    card = white_deck.pop(0)
    if record:
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
