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


# --- Vote winners, data variants, paired bootstrap ---

ONE_BLANK = [{"text_id": 1, "black": True, "pick": 1}]


def vote_round(game_id=1, round_no=1, shown=(0, 1, 2), outcome=0):
    return {"game_id": game_id, "round_no": round_no, "black": 1, "outcome": outcome, "shown": list(shown)}


def submission(game_id, round_no, seat, card, is_model=False):
    hand = [card, *[c for c in HAND if c != card]][:10]
    return {"game_id": game_id, "round_no": round_no, "seat": seat, "is_model": is_model, "hand": hand,
            "picks": [card]}


def vote(game_id, round_no, voter, target, voter_is_model=False):
    return {"game_id": game_id, "round_no": round_no, "voter": voter, "target": target,
            "voter_is_model": voter_is_model}


def vote_tables(votes, rounds=None, model_seats=None):
    rounds = rounds or [vote_round()]
    decisions = [submission(r["game_id"], r["round_no"], seat, 200 + seat, is_model=seat in (model_seats or []))
                 for r in rounds for seat in r["shown"]]
    games = [{"game_id": g, "day": 1, "model_seats": list(model_seats or [])} for g in {r["game_id"] for r in rounds}]
    return {"card_texts": ONE_BLANK, "rounds": rounds, "decisions": decisions, "votes": votes, "games": games}


def test_the_human_vote_winner_is_the_target():
    (e,) = evaluate.vote_examples(vote_tables([vote(1, 1, 0, 2), vote(1, 1, 1, 2), vote(1, 1, 2, 0)]))
    assert e.source == "vote" and e.hand == (200, 201, 202) and e.target == 2


def test_the_computer_players_vote_doesnt_count_but_its_card_may_win():
    # Seat 2 is the computer player. Its own vote (for itself) would make a tie; without it seat 2 wins on
    # the two human votes: humans chose the model's card, which is a human judgment.
    t = vote_tables([vote(1, 1, 0, 2), vote(1, 1, 1, 2), vote(1, 1, 2, 0, voter_is_model=True)], model_seats=[2])
    (e,) = evaluate.vote_examples(t)
    assert e.target == 2 and e.bot_game


def test_ties_undecided_rounds_and_rounds_without_a_human_vote_are_skipped():
    assert evaluate.vote_examples(vote_tables([vote(1, 1, 0, 1), vote(1, 1, 1, 0)])) == [], "a tie"
    assert evaluate.vote_examples(vote_tables([vote(1, 1, 0, 1)], rounds=[vote_round(outcome=2)])) == []
    bot_czar = vote_tables([vote(1, 1, 2, 0, voter_is_model=True)], model_seats=[2])
    assert evaluate.vote_examples(bot_czar) == [], "the computer player was the czar: no human judged"
    assert evaluate.vote_examples(vote_tables([vote(1, 1, 0, 1)], rounds=[vote_round(shown=(1,))])) == []


def split_tables():
    """Ten games in time order; games 3 and 9 had a computer player (seat 9); every game has picks and a
    decided vote round."""
    rounds, decisions, votes, games = [], [], [], []
    for g in range(1, 11):
        bot = g in (3, 9)
        games.append({"game_id": g, "day": g, "model_seats": [9] if bot else []})
        rounds.append({"game_id": g, "round_no": 1, "black": 1, "outcome": 0, "shown": [0, 1]})
        decisions += [submission(g, 1, 0, 105), submission(g, 1, 1, 106)]
        votes.append(vote(g, 1, 0, 1))
    return {"card_texts": ONE_BLANK, "rounds": rounds, "decisions": decisions, "votes": votes, "games": games}


def test_variants_share_validation_and_test_without_computer_player_games():
    t = split_tables()
    by = {v: evaluate.make_splits(t, v) for v in evaluate.VARIANTS}
    for s in ("val", "test"):
        assert by["A"][s] == by["B"][s] == by["C"][s], "every variant is measured on the same picks"
        assert by["A"][s] and not any(e.bot_game for e in by["A"][s])
    assert {e.game_id for e in by["A"]["test"]} == {10}, "game 9 (newest but one, with a bot) is dropped"
    assert not any(e.bot_game for e in by["A"]["train"])
    assert {e.game_id for e in by["B"]["train"] if e.bot_game} == {3}, "B adds human picks from bot games"
    votes_c = [e for e in by["C"]["train"] if e.source == "vote"]
    assert {e.game_id for e in votes_c} == {e.game_id for e in by["B"]["train"]}, "votes from training games only"
    assert len(by["C"]["train"]) == len(by["B"]["train"]) + len(votes_c)
    with pytest.raises(ValueError, match="unknown data variant"):
        evaluate.make_splits(t, "D")


def test_short_hands_get_zero_probability_on_the_padding():
    short = Example(1, 1, -1, 1, (101, 102, 103), 0, source="vote")
    full = Example(1, 1, 0, 1, tuple(HAND), 0)
    r = evaluate.random_probs([short, full])
    assert r[0].tolist() == [1 / 3] * 3 + [0.0] * 7 and r[1].sum() == pytest.approx(1)
    p = evaluate.popularity_probs([full], [short, full])
    assert p[0, 3:].sum() == 0 and p[0].sum() == pytest.approx(1)
    assert evaluate.metrics(r[:1], np.array([0]))["mrr"] == pytest.approx(np.mean([1, 1 / 2, 1 / 3]))


def test_reciprocal_ranks_average_to_the_mrr():
    rng = np.random.default_rng(1)
    probs = rng.dirichlet(np.ones(10), size=50)
    targets = rng.integers(0, 10, 50)
    assert evaluate.reciprocal_ranks(probs, targets).mean() == pytest.approx(evaluate.metrics(probs, targets)["mrr"])


def test_paired_bootstrap_by_game():
    rng = np.random.default_rng(0)
    games = np.repeat(np.arange(30), 20)
    a = rng.random(600)
    same = evaluate.paired_bootstrap(a, a.copy(), games)
    assert same["delta"] == 0 and same["lo"] == same["hi"] == 0
    better = evaluate.paired_bootstrap(a, a + 0.05 + rng.normal(0, 0.01, 600), games)
    assert better["lo"] > 0 and better["p_not_better"] == 0 and better["games"] == 30
    noisy = evaluate.paired_bootstrap(a, a + rng.normal(0, 0.3, 600), games)
    assert noisy["lo"] < noisy["delta"] < noisy["hi"] and noisy["lo"] < 0 < noisy["hi"]
    # Picks in one game move together: the interval over games is wider than over single picks would be.
    shared = np.repeat(rng.normal(0, 0.2, 30), 20)
    by_game = evaluate.paired_bootstrap(a, a + shared, games)
    by_pick = evaluate.paired_bootstrap(a, a + shared, np.arange(600))
    assert (by_game["hi"] - by_game["lo"]) > 2 * (by_pick["hi"] - by_pick["lo"])
