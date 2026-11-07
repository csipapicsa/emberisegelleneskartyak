"""ml/evaluate.py: examples, the time-based split, metrics with fair ties, the popularity baseline."""
from __future__ import annotations

import math

import numpy as np
import pytest

from ml import evaluate
from ml.evaluate import Example

HAND = list(range(101, 111))


def tables(decisions, rounds=None, cards=None):
    return {"card_texts": cards or [{"text_id": 1, "black": True, "pick": 1}, {"text_id": 2, "black": True, "pick": 2}],
            "rounds": rounds or [{"game_id": 1, "round_no": 1, "black": 1}, {"game_id": 1, "round_no": 2, "black": 2}],
            "decisions": decisions}


def decision(round_no=1, seat=0, picks=(105,), is_model=False):
    return {"game_id": 1, "round_no": round_no, "seat": seat, "is_model": is_model, "hand": list(HAND),
            "picks": None if picks is None else list(picks)}


def test_pick_examples_keeps_human_one_blank_submissions_only():
    examples = evaluate.pick_examples(tables([
        decision(seat=1), decision(seat=0, picks=(103,)),
        decision(seat=2, is_model=True), decision(seat=3, picks=None), decision(round_no=2, picks=(101, 102)),
    ]))
    assert [(e.seat, e.target, e.black) for e in examples] == [(0, 2, 1), (1, 4, 1)]
    assert examples[0].hand == tuple(HAND)


def ex(game_id: int) -> Example:
    return Example(game_id, 1, 0, 1, tuple(HAND), 0)


def test_split_by_game_follows_time_and_never_shares_a_game():
    games = [{"game_id": g, "day": day} for g, day in ((1, 5), (2, 3), (3, 4), (4, 6), (5, 7), (6, 8), (7, 9),
                                                         (8, 10), (9, 11), (10, 12))]
    examples = [ex(g) for g in range(1, 11) for _ in range(3)]
    splits = evaluate.split_by_game(examples, games)
    by_split = {s: sorted({e.game_id for e in splits[s]}) for s in evaluate.SPLITS}
    assert by_split == {"train": [1, 2, 3, 4, 5, 6], "val": [7, 8], "test": [9, 10]}
    assert sum(len(v) for v in splits.values()) == len(examples)


def test_split_needs_three_games():
    games = [{"game_id": g, "day": 1} for g in (1, 2)]
    with pytest.raises(ValueError, match="at least 3"):
        evaluate.split_by_game([ex(1), ex(2)], games)
    splits = evaluate.split_by_game([ex(1), ex(2), ex(3)], games + [{"game_id": 3, "day": 1}])
    assert [len(splits[s]) for s in evaluate.SPLITS] == [1, 1, 1]


def test_metrics_of_a_perfect_and_a_random_model():
    targets = np.array([0, 3, 9])
    perfect = np.full((3, 10), 0.01)
    perfect[np.arange(3), targets] = 0.91
    m = evaluate.metrics(perfect, targets)
    assert (m["top1"], m["top3"], m["mrr"], m["n"]) == (1, 1, 1, 3)
    assert m["log_loss"] == pytest.approx(-math.log(0.91))

    uniform = evaluate.metrics(evaluate.random_probs([ex(1)] * 3), targets)
    assert uniform["top1"] == pytest.approx(0.1) and uniform["top3"] == pytest.approx(0.3)
    assert uniform["mrr"] == pytest.approx(sum(1 / r for r in range(1, 11)) / 10)
    assert uniform["log_loss"] == pytest.approx(math.log(10))


def test_ties_count_as_a_fair_tie_break():
    # Played card (position 0) tied with one other card behind a single better card: rank 2 or 3.
    probs = np.array([[0.2, 0.2, 0.3, 0.3 / 7 * 0 + 0.3 / 7, *([0.3 / 7] * 6)]])
    m = evaluate.metrics(probs, np.array([0]))
    assert m["top1"] == 0 and m["top3"] == 1
    assert m["mrr"] == pytest.approx((1 / 2 + 1 / 3) / 2)
    # Same scores, other card order: same result.
    assert evaluate.metrics(probs[:, ::-1], np.array([9]))["mrr"] == pytest.approx(m["mrr"])


def test_metrics_refuses_nothing():
    with pytest.raises(ValueError):
        evaluate.metrics(np.zeros((0, 10)), np.zeros(0, dtype=int))


def test_popularity_prefers_cards_played_before():
    train = [Example(1, r, 0, 1, tuple(HAND), 4) for r in range(5)]  # card 105 played every time
    probs = evaluate.popularity_probs(train, [Example(2, 1, 0, 1, tuple(HAND), 0),
                                              Example(2, 2, 0, 1, (105, 201, 202, 203, 204, 205, 206, 207, 208, 209), 0)])
    assert probs.shape == (2, 10) and np.allclose(probs.sum(axis=1), 1)
    assert probs[0].argmax() == 4 and probs[1].argmax() == 0
    assert len(set(np.round(probs[1, 1:], 9))) == 1, "unseen cards share the prior"
    assert probs[0, 4] < 0.5, "smoothed: five plays aren't certainty"
