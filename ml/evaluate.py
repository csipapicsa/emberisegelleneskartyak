"""Training examples, the time-based split and the metrics every model and baseline is scored with.

The task: a player holds a hand of cards and a one-blank black card is on the table; which card do they
play? A model gives every card in the hand a probability.

- Examples: human submissions in one-blank rounds (model players are left out, so no model learns from an
  older model). Rounds with two or three blanks come later.
- Split: by game, in time order (game day, then game ID, which grows with time): the oldest games train,
  the next ones validate (model choice, early stopping), the newest ones test. No game is in two splits,
  so a model is always tested on games it has never seen.
- Metrics: top-1 and top-3 accuracy (is the played card the model's first / among its first three?), MRR
  (mean of 1 / rank of the played card) and log loss (how much probability the played card got). With a
  hand of 10, random guessing gives top-1 0.1, top-3 0.3, MRR 0.293 and log loss 2.303.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

HAND_SIZE = 10
SPLITS = ("train", "val", "test")


@dataclass(frozen=True)
class Example:
    game_id: int
    round_no: int
    seat: int
    black: int              # text_id of the black card
    hand: tuple[int, ...]   # text_ids, in display order
    target: int             # position of the played card in hand


def pick_examples(tables: dict[str, list[dict]]) -> list[Example]:
    """Human submissions in one-blank rounds, in (game, round, seat) order."""
    black_of = {(r["game_id"], r["round_no"]): r["black"] for r in tables["rounds"]}
    one_blank = {c["text_id"] for c in tables["card_texts"] if c["black"] and c["pick"] == 1}
    examples = []
    for d in tables["decisions"]:
        black = black_of.get((d["game_id"], d["round_no"]))
        if d["is_model"] or d["picks"] is None or black not in one_blank or len(d["hand"]) != HAND_SIZE:
            continue
        examples.append(Example(d["game_id"], d["round_no"], d["seat"], black, tuple(d["hand"]),
                                d["hand"].index(d["picks"][0])))
    return sorted(examples, key=lambda e: (e.game_id, e.round_no, e.seat))


def split_by_game(examples: Sequence[Example], games: Sequence[dict], val_share: float = 0.2,
                  test_share: float = 0.2) -> dict[str, list[Example]]:
    """Oldest games train, newer validate, newest test; at least one game each."""
    day = {g["game_id"]: g["day"] for g in games}
    ordered = sorted({e.game_id for e in examples}, key=lambda g: (day[g], g))
    n_test = max(1, round(len(ordered) * test_share))
    n_val = max(1, round(len(ordered) * val_share))
    if len(ordered) - n_test - n_val < 1:
        raise ValueError(f"{len(ordered)} games with picks; need at least 3 (train, validation, test)")
    split_of = {g: "train" for g in ordered[: len(ordered) - n_test - n_val]}
    split_of.update({g: "val" for g in ordered[len(ordered) - n_test - n_val: len(ordered) - n_test]})
    split_of.update({g: "test" for g in ordered[len(ordered) - n_test:]})
    out: dict[str, list[Example]] = {s: [] for s in SPLITS}
    for e in examples:
        out[split_of[e.game_id]].append(e)
    return out


def metrics(probs: np.ndarray, targets: np.ndarray) -> dict[str, float]:
    """top1, top3, mrr, log_loss and n for probabilities (examples x hand) and the played positions.

    Ties count as a fair random tie-break: if the played card shares its probability with others, its rank
    is equally likely to be any of the tied places, and the metrics take the expected value. (Otherwise a
    baseline that scores many cards equally would look better or worse depending on card order.)
    """
    probs = np.asarray(probs, dtype=np.float64)
    targets = np.asarray(targets)
    if len(probs) == 0:
        raise ValueError("no examples to score")
    p_target = probs[np.arange(len(probs)), targets]
    greater = (probs > p_target[:, None]).sum(axis=1)
    tied = (probs == p_target[:, None]).sum(axis=1)  # includes the played card itself

    def top_k(k: int) -> np.ndarray:
        return np.clip((k - greater) / tied, 0, 1)

    reciprocal = np.array([np.mean(1 / np.arange(g + 1, g + t + 1)) for g, t in zip(greater, tied, strict=True)])
    return {"top1": float(top_k(1).mean()), "top3": float(top_k(3).mean()), "mrr": float(reciprocal.mean()),
            "log_loss": float(-np.log(np.clip(p_target, 1e-12, 1)).mean()), "n": float(len(probs))}


def random_probs(examples: Sequence[Example]) -> np.ndarray:
    return np.full((len(examples), HAND_SIZE), 1 / HAND_SIZE)


def popularity_probs(train: Sequence[Example], examples: Sequence[Example], strength: float = 10.0) -> np.ndarray:
    """The popularity baseline: each card's share of being played when it was in a training hand,
    smoothed towards 1 in 10 (a card seen once and played once isn't a sure thing), normalised per hand.
    Cards never seen in training get the prior."""
    prior = 1 / HAND_SIZE
    shown: dict[int, int] = {}
    played: dict[int, int] = {}
    for e in train:
        for card in e.hand:
            shown[card] = shown.get(card, 0) + 1
        played[e.hand[e.target]] = played.get(e.hand[e.target], 0) + 1
    rate = {c: (played.get(c, 0) + strength * prior) / (n + strength) for c, n in shown.items()}
    scores = np.array([[rate.get(c, prior) for c in e.hand] for e in examples])
    return scores / scores.sum(axis=1, keepdims=True)
