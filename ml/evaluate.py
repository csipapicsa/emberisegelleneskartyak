"""Training examples, the time-based split and the metrics every model and baseline is scored with.

The task: a player holds a hand of cards and a one-blank black card is on the table; which card do they
play? A model gives every card in the hand a probability.

- Examples: human submissions in one-blank rounds (model players are left out, so no model learns from an
  older model). Rounds with two or three blanks come later.
- Split: by game, in time order (game day, then game ID, which grows with time): the oldest games train,
  the next ones validate (model choice, early stopping), the newest ones test. No game is in two splits,
  so a model is always tested on games it has never seen.
- Random split (split="random", make_splits): games are shuffled instead (fixed seed), still whole. A few
  weeks of data have no real "future": players come and go, so time order mostly measures who played last
  weekend. Games with a computer player always train (validation and test take none), so B and C use them.
- Data variants (make_splits): validation and test are always human picks from games *without* a computer
  player, so every variant is measured on the same, undisturbed human behaviour. Only training differs:
    A  human picks from games without a computer player (the default, and what gets registered);
    B  A + the human picks from games with one (people may play differently against a bot);
    C  B + vote winners: in a decided round, the submissions are a "hand" and the one human votes chose is
       the target. The computer player's card may be among them and may win: the label still comes from
       humans. Its own vote is left out, ties and rounds it judged as czar are skipped.
  Whether B or C help is measured, not assumed: paired_bootstrap compares them with A every cycle.
- Metrics: top-1 and top-3 accuracy (is the played card the model's first / among its first three?), MRR
  (mean of 1 / rank of the played card) and log loss (how much probability the played card got). With a
  hand of 10, random guessing gives top-1 0.1, top-3 0.3, MRR 0.293 and log loss 2.303.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

HAND_SIZE = 10
SPLITS = ("train", "val", "test")
VARIANTS = ("A", "B", "C")
SPLIT_METHODS = ("time", "random")


@dataclass(frozen=True)
class Example:
    game_id: int
    round_no: int
    seat: int
    black: int              # text_id of the black card
    hand: tuple[int, ...]   # text_ids, in display order (a vote example: the submissions, shown order)
    target: int             # position of the played card in hand (a vote example: the winner)
    source: str = "pick"    # "pick": a human's own submission; "vote": the submission human votes chose
    bot_game: bool = False  # the game had a computer player


def bot_games(tables: dict[str, list[dict]]) -> set[int]:
    """Games with at least one computer player (a model seat)."""
    return {g["game_id"] for g in tables.get("games", []) if g.get("model_seats")}


def pick_examples(tables: dict[str, list[dict]]) -> list[Example]:
    """Human submissions in one-blank rounds, in (game, round, seat) order."""
    black_of = {(r["game_id"], r["round_no"]): r["black"] for r in tables["rounds"]}
    one_blank = {c["text_id"] for c in tables["card_texts"] if c["black"] and c["pick"] == 1}
    with_bot = bot_games(tables)
    examples = []
    for d in tables["decisions"]:
        black = black_of.get((d["game_id"], d["round_no"]))
        if d["is_model"] or d["picks"] is None or black not in one_blank or len(d["hand"]) != HAND_SIZE:
            continue
        examples.append(Example(d["game_id"], d["round_no"], d["seat"], black, tuple(d["hand"]),
                                d["hand"].index(d["picks"][0]), bot_game=d["game_id"] in with_bot))
    return sorted(examples, key=lambda e: (e.game_id, e.round_no, e.seat))


def vote_examples(tables: dict[str, list[dict]]) -> list[Example]:
    """Decided one-blank rounds as examples: the shown submissions are the hand, the target is the one that
    got the most *human* votes (a czar's pick is one vote). Skipped: ties, rounds with fewer than 2 or more
    than HAND_SIZE submissions, rounds without a human vote (e.g. a computer player was the czar)."""
    one_blank = {c["text_id"] for c in tables["card_texts"] if c["black"] and c["pick"] == 1}
    picked = {(d["game_id"], d["round_no"], d["seat"]): d["picks"] for d in tables["decisions"] if d["picks"]}
    votes: dict[tuple[int, int], Counter] = {}
    for v in tables["votes"]:
        if not v["voter_is_model"]:
            votes.setdefault((v["game_id"], v["round_no"]), Counter())[v["target"]] += 1
    with_bot = bot_games(tables)
    examples = []
    for r in tables["rounds"]:
        key = (r["game_id"], r["round_no"])
        shown, counts = r["shown"] or [], votes.get(key)
        if r["outcome"] != 0 or r["black"] not in one_blank or not counts or not 2 <= len(shown) <= HAND_SIZE:
            continue
        cards = [picked.get((*key, seat)) for seat in shown]
        top = max(counts.values())
        winners = [seat for seat, n in counts.items() if n == top]
        if any(c is None for c in cards) or len(winners) != 1 or winners[0] not in shown:
            continue
        examples.append(Example(r["game_id"], r["round_no"], -1, r["black"], tuple(c[0] for c in cards),
                                shown.index(winners[0]), source="vote", bot_game=r["game_id"] in with_bot))
    return examples


def make_splits(tables: dict[str, list[dict]], variant: str = "A", val_share: float = 0.2,
                test_share: float = 0.2, split: str = "time", seed: int = 0) -> dict[str, list[Example]]:
    """The training cycle's split for one data variant (see the module docstring). split="time": games are
    split in time order as by split_by_game, over every game with human picks; validation and test then keep
    only games without a computer player, so they are the same for every variant. split="random": the games
    without a computer player are shuffled (seed) into train, validation and test; the others all train."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown data variant {variant!r}; choose from {', '.join(VARIANTS)}")
    if split not in SPLIT_METHODS:
        raise ValueError(f"unknown split {split!r}; choose from {', '.join(SPLIT_METHODS)}")
    picks = pick_examples(tables)
    if split == "time":
        periods = split_by_game(picks, tables["games"], val_share, test_share)
    else:
        human = split_by_game([e for e in picks if not e.bot_game], tables["games"], val_share, test_share,
                              shuffle_seed=seed)
        periods = {**human, "train": human["train"] + [e for e in picks if e.bot_game]}
    period_of = {e.game_id: s for s in SPLITS for e in periods[s]}
    out = {s: [e for e in periods[s] if not e.bot_game] for s in ("val", "test")}
    out["train"] = [e for e in periods["train"] if variant != "A" or not e.bot_game]
    if variant == "C":
        out["train"] += [e for e in vote_examples(tables) if period_of.get(e.game_id) == "train"]
    if not out["val"] or not out["test"]:
        raise ValueError("validation or test has no game without a computer player")
    return out


def split_by_game(examples: Sequence[Example], games: Sequence[dict], val_share: float = 0.2,
                  test_share: float = 0.2, shuffle_seed: int | None = None) -> dict[str, list[Example]]:
    """Oldest games train, newer validate, newest test; at least one game each. With shuffle_seed the games
    are shuffled first (the same seed gives the same split)."""
    day = {g["game_id"]: g["day"] for g in games}
    ordered = sorted({e.game_id for e in examples}, key=lambda g: (day[g], g))
    if shuffle_seed is not None:
        ordered = [ordered[i] for i in np.random.default_rng(shuffle_seed).permutation(len(ordered))]
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


def reciprocal_ranks(probs: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """1 / rank of the played card per example, ties broken fairly (as in metrics); its mean is the MRR."""
    probs = np.asarray(probs, dtype=np.float64)
    p_target = probs[np.arange(len(probs)), np.asarray(targets)]
    greater = (probs > p_target[:, None]).sum(axis=1)
    tied = (probs == p_target[:, None]).sum(axis=1)
    return np.array([np.mean(1 / np.arange(g + 1, g + t + 1)) for g, t in zip(greater, tied, strict=True)])


def paired_bootstrap(a: np.ndarray, b: np.ndarray, games: Sequence[int], n: int = 4000,
                     seed: int = 0) -> dict[str, float]:
    """How much better b is than a on the same examples (per-example scores, e.g. reciprocal ranks), with a
    95% interval from resampling whole games: picks in one game share players and cards, so they aren't
    independent and resampling single picks would make the interval too narrow. p_not_better: the share
    of resamples where b is no better than a."""
    diff = np.asarray(b, dtype=np.float64) - np.asarray(a, dtype=np.float64)
    ids, group = np.unique(np.asarray(games), return_inverse=True)
    sums, counts = np.bincount(group, weights=diff), np.bincount(group)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(ids), size=(n, len(ids)))
    deltas = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {"delta": float(diff.mean()), "lo": float(lo), "hi": float(hi),
            "p_not_better": float(np.mean(deltas <= 0)), "games": float(len(ids))}


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
    """1 / hand size for every card; 0 for the padding of a shorter hand (a vote example)."""
    out = np.zeros((len(examples), HAND_SIZE))
    for i, e in enumerate(examples):
        out[i, :len(e.hand)] = 1 / len(e.hand)
    return out


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
    scores = np.zeros((len(examples), HAND_SIZE))  # 0 for the padding of a shorter hand
    for i, e in enumerate(examples):
        scores[i, :len(e.hand)] = [rate.get(c, prior) for c in e.hand]
    return scores / scores.sum(axis=1, keepdims=True)
