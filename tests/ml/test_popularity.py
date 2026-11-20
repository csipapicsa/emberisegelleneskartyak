"""ml/popularity.py: play counts keyed by text, smoothing, leave-one-out, the extra input channel."""
from __future__ import annotations

import math

import numpy as np
import pytest

from ml import evaluate
from ml.evaluate import HAND_SIZE, Example
from ml.popularity import PRIOR, STRENGTH, Popularity, text_key, with_popularity

TEXTS = {i: f"Card {i}." for i in range(30)}


def pick(hand, target, game=1, source="pick"):
    return Example(game, 1, 0, 100, tuple(hand), target, source=source)


@pytest.fixture
def examples():
    return [pick(range(0, 10), 0), pick(range(0, 10), 0, game=2), pick(range(5, 15), 9, game=3)]


def test_counts_by_text_and_smoothing(examples):
    pop = Popularity.from_examples(examples, TEXTS)
    key0 = text_key("Card 0.")
    assert pop.shown[key0] == 2 and pop.played[key0] == 2
    assert pop.shown[text_key("Card 5.")] == 3 and text_key("Card 5.") not in pop.played
    assert pop.log_rate(key0) == pytest.approx(math.log((2 + STRENGTH * PRIOR) / (2 + STRENGTH)))
    assert pop.log_rate(text_key("never dealt")) == pytest.approx(math.log(PRIOR))


def test_vote_examples_dont_count(examples):
    pop = Popularity.from_examples([*examples, pick(range(20, 25), 1, source="vote")], TEXTS)
    assert text_key("Card 20.") not in pop.shown


def test_leave_one_out_removes_the_examples_own_hand(examples):
    pop = Popularity.from_examples(examples, TEXTS)
    loo = pop.matrix(examples[:1], TEXTS, leave_one_out=True)[0]
    # Card 0: shown 2, played 2; without this hand: shown 1, played 1.
    assert loo[0] == pytest.approx(math.log((1 + STRENGTH * PRIOR) / (1 + STRENGTH)))
    # Card 1: shown 2, played 0; without this hand: shown 1, played 0.
    assert loo[1] == pytest.approx(math.log((STRENGTH * PRIOR) / (1 + STRENGTH)))
    alone = Popularity.from_examples(examples[:1], TEXTS)
    assert np.allclose(alone.matrix(examples[:1], TEXTS, leave_one_out=True), math.log(PRIOR)), \
        "a card seen only in its own hand falls back to the prior"


def test_softmax_of_log_rates_is_the_popularity_baseline(examples):
    pop = Popularity.from_examples(examples, TEXTS)
    test = [pick(range(3, 13), 2, game=9), pick(range(8, 18), 0, game=9)]
    rates = pop.matrix(test, TEXTS)
    probs = np.exp(rates) / np.exp(rates).sum(axis=1, keepdims=True)
    assert np.allclose(probs, evaluate.popularity_probs(examples, test), atol=1e-6)


def test_short_hands_repeat_the_last_card(examples):
    pop = Popularity.from_examples(examples, TEXTS)
    rates = pop.matrix([pick([0, 5, 14], 1, source="vote")], TEXTS)[0]
    assert rates.shape == (HAND_SIZE,) and np.all(rates[3:] == rates[2])


def test_round_trip_and_log_rates_by_text(examples, tmp_path):
    pop = Popularity.from_examples(examples, TEXTS)
    path = tmp_path / "popularity.json"
    import json
    path.write_text(json.dumps(pop.to_dict()))
    loaded = Popularity.load(path)
    assert loaded == pop
    assert np.allclose(loaded.log_rates(["Card 0.", "unknown"]), [pop.log_rate(text_key("Card 0.")), math.log(PRIOR)])
    with pytest.raises(ValueError, match="format"):
        Popularity.from_dict({"format": 99})


def test_with_popularity_adds_a_last_channel():
    x = np.ones((2, 1 + HAND_SIZE, 4), dtype=np.float32)
    rates = np.arange(20, dtype=np.float32).reshape(2, 10)
    out = with_popularity(x, rates)
    assert out.shape == (2, 11, 5) and out.dtype == np.float32
    assert np.all(out[:, 0, 4] == 0), "the black card has no popularity"
    assert np.array_equal(out[:, 1:, 4], rates) and np.all(out[..., :4] == 1)
