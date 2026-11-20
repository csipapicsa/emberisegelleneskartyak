"""Card popularity: how often each white card was played when it was in a hand, keyed by the card's text.

The popularity baseline (evaluate.popularity_probs) and the `popwhite` model (ml/train.py) both use it. A
model that takes popularity as an input carries its table with it: the counts are logged as the artifact
`popularity.json` of the model's run, and the model service and the promotion step read them from there, so
a card's rate is the one the model was trained with.

The rate is smoothed towards 1 in 10 (a card seen once and played once isn't a sure thing):

    rate = (played + strength * prior) / (shown + strength),  prior = 1 / hand size, strength = 10

and a card nobody has seen gets the prior. Models get log(rate), so a softmax over log rates alone is exactly
the popularity baseline.

Keyed by text, not text_id: text IDs are local to one gameplay.db, while the model service gets card texts
from the game (server/bot.py), and the log stores each text exactly as it was shown.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ml.evaluate import HAND_SIZE, Example

ARTIFACT = "popularity.json"
# Version tag of a model whose input carries log popularity as a last channel: (n, 11, dim + 1).
INPUTS_TAG = "inputs"
INPUTS = "embedding+log_popularity"
PRIOR = 1 / HAND_SIZE
STRENGTH = 10.0


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Popularity:
    shown: dict[str, int] = field(default_factory=dict)    # text key -> times in a hand
    played: dict[str, int] = field(default_factory=dict)   # text key -> times played
    strength: float = STRENGTH
    prior: float = PRIOR

    @classmethod
    def from_examples(cls, examples: Sequence[Example], texts: dict[int, str]) -> Popularity:
        """Counts from human picks (source "pick"); vote examples don't say what a player chose from a hand."""
        pop = cls()
        for e in examples:
            if e.source != "pick":
                continue
            for card in e.hand:
                key = text_key(texts[card])
                pop.shown[key] = pop.shown.get(key, 0) + 1
            key = text_key(texts[e.hand[e.target]])
            pop.played[key] = pop.played.get(key, 0) + 1
        return pop

    def log_rate(self, key: str, own_pick: bool | None = None) -> float:
        """log of the smoothed rate. own_pick (leave-one-out): the counts include one hand of the example
        being scored, and own_pick says whether the card was played in it; that hand is taken out."""
        n, k = self.shown.get(key, 0), self.played.get(key, 0)
        if own_pick is not None:
            n, k = n - 1, k - int(own_pick)
        if n <= 0:
            return math.log(self.prior)
        return math.log((k + self.strength * self.prior) / (n + self.strength))

    def log_rates(self, texts: Sequence[str]) -> np.ndarray:
        return np.array([self.log_rate(text_key(t)) for t in texts], dtype=np.float32)

    def matrix(self, examples: Sequence[Example], texts: dict[int, str], leave_one_out: bool = False) -> np.ndarray:
        """(n, HAND_SIZE) log rates in hand order; a shorter hand is padded by repeating its last card, as
        train.to_tensors pads the embeddings. leave_one_out: for the examples the counts came from (the
        training picks), so a pick isn't counted in its own card's rate; otherwise the model learns to
        trust popularity more than it deserves on games it hasn't seen."""
        out = np.empty((len(examples), HAND_SIZE), dtype=np.float32)
        for row, e in enumerate(examples):
            hand = [*e.hand, *[e.hand[-1]] * (HAND_SIZE - len(e.hand))]
            loo = leave_one_out and e.source == "pick"
            for col, card in enumerate(hand):
                out[row, col] = self.log_rate(text_key(texts[card]), (col == e.target) if loo else None)
        return out

    def to_dict(self) -> dict:
        return {"format": 1, "strength": self.strength, "prior": self.prior, "shown": self.shown, "played": self.played}

    @classmethod
    def from_dict(cls, data: dict) -> Popularity:
        if data.get("format") != 1:
            raise ValueError(f"unknown popularity format {data.get('format')!r}")
        return cls(dict(data["shown"]), dict(data["played"]), float(data["strength"]), float(data["prior"]))

    @classmethod
    def load(cls, path: str | Path) -> Popularity:
        return cls.from_dict(json.loads(Path(path).read_text()))


def with_popularity(x: np.ndarray, log_rates: np.ndarray) -> np.ndarray:
    """(n, 1 + hand, dim) embeddings + (n, hand) log rates -> (n, 1 + hand, dim + 1): the log rate as a last
    channel, 0 for the black card. One tensor, so the logged model keeps a single-input signature."""
    x = np.asarray(x, dtype=np.float32)
    extra = np.zeros((*x.shape[:2], 1), dtype=np.float32)
    extra[:, 1:, 0] = log_rates
    return np.concatenate([x, extra], axis=-1)


def for_version(version) -> Popularity | None:
    """The popularity table of a registered model version, or None if its model takes no popularity."""
    if version.tags.get(INPUTS_TAG) != INPUTS:
        return None
    import mlflow
    return Popularity.load(mlflow.artifacts.download_artifacts(run_id=version.run_id, artifact_path=ARTIFACT))
