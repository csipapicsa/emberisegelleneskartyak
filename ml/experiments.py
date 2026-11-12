"""Shared code for the experiment notebooks (notebooks/): load a snapshot, embed, train, compare, log.

The notebooks try model ideas the training cycle (ml/train.py) doesn't have yet. They use the same split,
metrics, baselines and training loop as the cycle, so their numbers compare directly with the pipeline's
models. An idea that wins on validation moves into ml/train.py.

    from ml import experiments as ex
    data = ex.load()                       # latest validated snapshot
    inputs, targets = data.tensors("e5")   # per split: ((n, 11, 384),), (n,)
    results = ex.baselines(data) + [ex.fit("my model", build, inputs, targets, config)]
    ex.table(results); ex.log("01_bilinear", data, results)

MLflow: experiment "pick-model-experiments", never "pick-model": the pipeline reads its state and the
challenger from that one. One parent run per notebook run (tag kind=experiment), one nested run per variant.
"""
from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ml import DATA_DIR, evaluate, features, train
from ml.evaluate import HAND_SIZE, Example
from ml.export import DEFAULT_OUT, latest_snapshot, read_snapshot
from ml.export import _git_commit as git_commit
from ml.validate import require_valid

EXPERIMENT = "pick-model-experiments"
# Filled-in sentences and other models' embeddings: tens of thousands of texts. Kept apart from the
# pipeline's cache, which every training cycle loads in full.
EXPERIMENT_CACHE = DATA_DIR / "embeddings-experiments"
_BLANK = re.compile(r"_{2,}")


@dataclass(frozen=True)
class Data:
    snapshot: Path
    manifest: dict
    tables: dict[str, list[dict]]
    splits: dict[str, list[Example]]
    texts: dict[int, str]   # text_id -> card text

    @property
    def targets(self) -> dict[str, np.ndarray]:
        return {s: np.array([e.target for e in self.splits[s]]) for s in evaluate.SPLITS}

    def sizes(self) -> dict[str, int]:
        return {f"n_{s}_picks": len(self.splits[s]) for s in evaluate.SPLITS} | {
            f"n_{s}_games": len({e.game_id for e in self.splits[s]}) for s in evaluate.SPLITS}

    def tensors(self, black_model: str | features.EmbeddingModel, white_model=None,
                cache_dir: Path | None = None, encoder_factory: features.EncoderFactory | None = None):
        """Per split: (inputs, targets) as ml/train.py builds them; inputs is a 1-tuple holding
        (n, 1 + hand, dim), the black card's embedding first. white_model (default: the same model) embeds
        the hand, e.g. e5 with "query: " for the black card and "passage: " for the answers."""
        black_model, white_model = _model(black_model), _model(white_model or black_model)
        if black_model.dim != white_model.dim:
            raise ValueError(f"black and white embeddings differ in size: {black_model.dim} vs {white_model.dim}")
        black_ids = {e.black for split in self.splits.values() for e in split}
        white_ids = {c for split in self.splits.values() for e in split for c in e.hand}
        black = _lookup(self.texts, black_ids, black_model, cache_dir, encoder_factory)
        white = _lookup(self.texts, white_ids, white_model, cache_dir, encoder_factory)
        torch = train._torch()
        inputs, targets = {}, {}
        for s in evaluate.SPLITS:
            rows = [np.stack([black[e.black], *(white[c] for c in e.hand)]) for e in self.splits[s]]
            inputs[s] = (torch.from_numpy(np.stack(rows) if rows else np.zeros((0, 1 + HAND_SIZE, black_model.dim),
                                                                                 dtype=np.float32)),)
            targets[s] = torch.tensor(self.targets[s], dtype=torch.long)
        return inputs, targets

    def filled_tensors(self, model: str | features.EmbeddingModel, cache_dir: Path = EXPERIMENT_CACHE,
                       encoder_factory: features.EncoderFactory | None = None, chunk: int = 2000):
        """Per split: (inputs, targets) where inputs is a 1-tuple holding (n, hand, dim): the embedding of
        the black card with each hand card filled into its blank (fill_blank). Encoded in chunks, each saved
        to the cache, so an interrupted run resumes where it stopped."""
        model = _model(model)
        pairs = sorted({(e.black, c) for split in self.splits.values() for e in split for c in e.hand})
        sentences = {i: fill_blank(self.texts[b], self.texts[w]) for i, (b, w) in enumerate(pairs)}
        vectors: dict[tuple[int, int], np.ndarray] = {}
        for start in range(0, len(pairs), chunk):
            part = {i: sentences[i] for i in range(start, min(start + chunk, len(pairs)))}
            ids, matrix = features.embed(part, model, cache_dir, encoder_factory or features.SentenceTransformerEncoder)
            vectors.update((pairs[int(i)], v) for i, v in zip(ids, matrix, strict=True))
        torch = train._torch()
        inputs, targets = {}, {}
        for s in evaluate.SPLITS:
            rows = [np.stack([vectors[(e.black, c)] for c in e.hand]) for e in self.splits[s]]
            inputs[s] = (torch.from_numpy(np.stack(rows) if rows else np.zeros((0, HAND_SIZE, model.dim),
                                                                                 dtype=np.float32)),)
            targets[s] = torch.tensor(self.targets[s], dtype=torch.long)
        return inputs, targets


def load(snapshot: str | Path | None = None) -> Data:
    """A validated snapshot (default: the latest), split by game in time order as the training cycle does."""
    folder = Path(snapshot) if snapshot else latest_snapshot(DEFAULT_OUT)
    manifest = require_valid(folder)
    _, tables = read_snapshot(folder)
    splits = evaluate.split_by_game(evaluate.pick_examples(tables), tables["games"])
    return Data(folder, manifest, tables, splits, {c["text_id"]: c["text"] for c in tables["card_texts"]})


def custom_model(hf_id: str, dim: int, prefix: str = "", revision: str | None = None,
                 key: str | None = None) -> features.EmbeddingModel:
    """An embedding model that isn't one of the pipeline's three. Without a revision, the current commit
    on Hugging Face is looked up and pinned, so the run records exactly which weights it used."""
    if revision is None:
        from huggingface_hub import HfApi
        revision = HfApi().model_info(hf_id).sha
    key = key or re.sub(r"[^a-z0-9]+", "-", hf_id.lower()).strip("-")
    return features.EmbeddingModel(key, hf_id, revision, prefix=prefix, dim=dim)


def fill_blank(black: str, white: str) -> str:
    """The black card with the white card in its blank: "Mi ragad? ____." + "A méz." -> "Mi ragad? A méz."
    The answer's closing full stop goes when it lands mid-sentence. A black card with no blank (a question)
    gets the answer after it."""
    answer = white.strip()
    if not _BLANK.search(black):
        return f"{black.strip()} {answer}"
    if answer.endswith(".") and not answer.endswith(".."):
        answer = answer[:-1]
    return _BLANK.sub(lambda _: answer, black, count=1)


def popularity_log_rates(train_examples: Sequence[Example], examples: Sequence[Example], strength: float = 10.0,
                         leave_one_out: bool = False) -> np.ndarray:
    """(n, hand) log of each hand card's smoothed play rate, as the popularity baseline computes it
    (evaluate.popularity_probs: the softmax of these is exactly its probabilities).

    leave_one_out: leave each example's own pick out of its rates. Use it for the training examples
    themselves: otherwise a card's rate already contains the pick the model is learning to predict, and the
    model learns to trust popularity more than it deserves on unseen games."""
    prior = 1 / HAND_SIZE
    shown: dict[int, int] = {}
    played: dict[int, int] = {}
    for e in train_examples:
        for card in e.hand:
            shown[card] = shown.get(card, 0) + 1
        played[e.hand[e.target]] = played.get(e.hand[e.target], 0) + 1
    out = np.empty((len(examples), HAND_SIZE))
    for row, e in enumerate(examples):
        for col, card in enumerate(e.hand):
            n, k = shown.get(card, 0), played.get(card, 0)
            if leave_one_out:
                n, k = n - 1, k - (col == e.target)
            out[row, col] = np.log((k + strength * prior) / (n + strength)) if n > 0 else np.log(prior)
    return out


def center(inputs: dict[str, tuple]) -> dict[str, tuple]:
    """Subtract the training split's mean vector from every split's first input tensor (the embeddings).
    e5's vectors share a large common part (cosine similarity ~0.8 between unrelated texts); without it
    the differences that matter are small numbers, and a linear model learns them slowly."""
    mean = inputs["train"][0].reshape(-1, inputs["train"][0].shape[-1]).mean(dim=0)
    return {s: (x[0] - mean, *x[1:]) for s, x in inputs.items()}


@dataclass
class Result:
    name: str
    scores: dict[str, dict[str, float]]          # split -> metrics (evaluate.metrics)
    params: dict = field(default_factory=dict)
    curve: list[tuple[int, float, float]] = field(default_factory=list)


def baselines(data: Data) -> list[Result]:
    """Random and popularity, scored on every split: the bars every notebook compares against."""
    random = {s: evaluate.metrics(evaluate.random_probs(data.splits[s]), data.targets[s]) for s in evaluate.SPLITS}
    popular = {s: evaluate.metrics(evaluate.popularity_probs(data.splits["train"], data.splits[s]), data.targets[s])
               for s in evaluate.SPLITS}
    return [Result("baseline random", random, {"model_type": "baseline-random"}),
            Result("baseline popularity", popular, {"model_type": "baseline-popularity"})]


def fit(name: str, build: Callable[[], object], inputs: dict[str, tuple], targets: dict, config: train.TrainConfig,
        params: dict | None = None) -> Result:
    """Seed, build the model, train it with the pipeline's loop (train.train_loop) and score every split.
    The model takes the split's inputs as arguments (model(*inputs[split])) and returns (n, hand) scores."""
    torch = train._torch()
    torch.manual_seed(config.seed)
    model = build()
    curve, best_epoch = train.train_loop(model, inputs["train"], targets["train"], inputs["val"], targets["val"],
                                         config)
    with torch.no_grad():
        scores = {s: evaluate.metrics(torch.softmax(model(*inputs[s]), dim=-1).numpy(), targets[s].numpy())
                  for s in evaluate.SPLITS}
    run_params = {**(params or {}), **asdict(config), "best_epoch": best_epoch, "epochs_run": len(curve),
                  "parameters": sum(p.numel() for p in model.parameters())}
    result = Result(name, scores, run_params, curve)
    result.model = model  # kept for a closer look in the notebook; not logged
    if best_epoch == config.max_epochs:
        warnings.warn(f"{name}: still improving at max_epochs={config.max_epochs}; raise it", stacklevel=2)
    return result


def table(results: Sequence[Result]):
    """A pandas DataFrame, best validation MRR first (choose by validation; test is only reported), with
    each row's test MRR against the popularity baseline."""
    import pandas as pd
    popularity = next((r.scores["test"]["mrr"] for r in results if r.name == "baseline popularity"), None)
    rows = []
    for r in results:
        row = {"name": r.name, "val MRR": r.scores["val"]["mrr"], "test MRR": r.scores["test"]["mrr"],
               "test top1": r.scores["test"]["top1"], "test top3": r.scores["test"]["top3"],
               "test log loss": r.scores["test"]["log_loss"], "best epoch": r.params.get("best_epoch"),
               "epochs": r.params.get("epochs_run")}
        if popularity is not None:
            row["test MRR - popularity"] = r.scores["test"]["mrr"] - popularity
        rows.append(row)
    return pd.DataFrame(rows).sort_values("val MRR", ascending=False).set_index("name").round(4)


def log(notebook: str, data: Data, results: Sequence[Result], params: dict | None = None,
        experiment: str = EXPERIMENT) -> str:
    """Log one notebook run to MLflow: a parent run with the snapshot and the notebook's parameters, one
    nested run per result with its parameters, metrics and learning curve. Returns the parent run ID."""
    import mlflow
    tags = {"kind": "experiment", "notebook": notebook, "snapshot": data.snapshot.name,
            "snapshot_hash": data.manifest["content_hash"], "git_commit": git_commit() or "unknown"}
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=f"{notebook} {data.snapshot.name}") as parent:
        mlflow.set_tags(tags)
        mlflow.log_params({**data.sizes(), **(params or {})})
        for r in results:
            with mlflow.start_run(run_name=r.name, nested=True):
                mlflow.set_tags({**tags, "kind": "experiment-variant"})
                mlflow.log_params(r.params)
                mlflow.log_metrics({f"{s}_{k}": v for s, m in r.scores.items() for k, v in m.items()})
                for epoch, train_loss, val_loss in r.curve:
                    mlflow.log_metrics({"curve_train_loss": train_loss, "curve_val_loss": val_loss}, step=epoch)
    return parent.info.run_id


def _model(model: str | features.EmbeddingModel) -> features.EmbeddingModel:
    return features.EMBEDDING_MODELS[model] if isinstance(model, str) else model


def _lookup(texts: dict[int, str], ids: set[int], model: features.EmbeddingModel, cache_dir: Path | None,
            encoder_factory: features.EncoderFactory | None) -> dict[int, np.ndarray]:
    """text_id -> vector. The pipeline's three models share its cache; other models get the experiments'."""
    if cache_dir is None:
        cache_dir = features.DEFAULT_CACHE if features.EMBEDDING_MODELS.get(model.key) == model else EXPERIMENT_CACHE
    found, matrix = features.embed({i: texts[i] for i in ids}, model, cache_dir,
                                   encoder_factory or features.SentenceTransformerEncoder)
    return {int(i): v for i, v in zip(found, matrix, strict=True)}
