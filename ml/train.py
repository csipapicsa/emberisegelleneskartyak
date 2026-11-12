"""One training cycle: 2 model structures x 3 embeddings x 3 data variants + 2 baselines, logged to MLflow.

    python -m ml.train                                    # latest validated snapshot
    python -m ml.train --embeddings e5 --models linear --variants A --no-register

Data variants (ml/evaluate.py): A trains on human picks from games without a computer player, B adds the
human picks from games with one, C adds the rounds' vote winners. All are evaluated on the same validation
and test picks; the challenger comes from A, and B and C are reported as a difference to A with a 95%
interval (paired bootstrap over games), so a variant becomes the default only on evidence.

The task and the metrics are in ml/evaluate.py. Both structures score every (black card, hand card) pair
from the two embeddings, their element-wise product and their absolute difference, and a softmax over the
hand turns the ten scores into probabilities:

- linear: one linear layer on those features (logistic regression over the hand, a "conditional logit");
- mlp: a small hidden layer (ReLU, dropout) in between, so it can learn interactions the linear one can't.

Both are trained the same way: full-batch Adam on the cross-entropy of the played card, with early stopping
on the validation games. The baselines need no training: random, and card popularity.

MLflow (experiment "pick-model"): a parent run per cycle ("cycle <date> · data to <snapshot>": a snapshot
is named after its newest game day, so the training date is in the name too; with the snapshot as its
dataset and its files as artifacts) and one nested run per model with its parameters, train/val/test metrics, learning curves and,
for the trained ones, the model. The trained model with the best validation MRR is registered as
"pick-model" and gets the alias "challenger"; moving it to "champion" is a separate, deliberate step
(milestone 4). Test metrics are reported, never used to choose.

The logged model returns raw scores, one per hand card (the softmax is left to whoever uses it), so a score
means the same in every hand. The computer player (ml/serve.py) needs that for its new-hand rule: it asks
for new cards when the best card in its hand scores lower than the best card in most training hands. Those
cut-offs (REFRESH_PERCENTILES of the best score per training hand) are registered with the model as the
tag "best_score_percentiles"; the tag "output" = "scores" tells them apart from older versions, which
returned probabilities.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ml import evaluate, features, lower_priority
from ml.evaluate import HAND_SIZE, VARIANTS, Example
from ml.export import DEFAULT_OUT, latest_snapshot, read_snapshot
from ml.export import _git_commit as git_commit
from ml.validate import require_valid

EXPERIMENT = "pick-model"
# Percentiles of the best card's score per training hand, registered with the model (see the docstring).
REFRESH_PERCENTILES = (5, 10, 20, 30, 50)
REGISTERED_MODEL = "pick-model"
MODEL_TYPES = ("linear", "mlp")


@dataclass(frozen=True)
class TrainConfig:
    lr: float
    weight_decay: float
    hidden: int = 0        # 0: the linear model
    dropout: float = 0.0
    max_epochs: int = 300
    patience: int = 25     # epochs without a better validation loss before stopping
    seed: int = 0


CONFIGS = {
    # 600 epochs: linear e5 kept improving until the old 300-epoch cap in every cycle up to 2026-11-11 (best
    # epoch 297-299). The other models stop early long before either cap.
    "linear": TrainConfig(lr=1e-2, weight_decay=1e-3, max_epochs=600),
    "mlp": TrainConfig(lr=3e-3, weight_decay=1e-4, hidden=64, dropout=0.2),
}


def _torch():
    import torch
    torch.set_num_threads(1)  # the data is tiny; one thread keeps runs reproducible and light on the server
    return torch


def make_scorer(dim: int, config: TrainConfig):
    torch = _torch()
    nn = torch.nn

    class PairScorer(nn.Module):
        """Input (batch, 1 + hand, dim): the black card's embedding, then the hand's. Output (batch, hand)
        scores; a softmax over them gives the probabilities."""

        def __init__(self) -> None:
            super().__init__()
            n_in = 4 * dim
            if config.hidden:
                self.head = nn.Sequential(nn.Linear(n_in, config.hidden), nn.ReLU(), nn.Dropout(config.dropout),
                                          nn.Linear(config.hidden, 1))
            else:
                self.head = nn.Linear(n_in, 1)

        @staticmethod
        def pair_features(x):
            """(batch, hand, 4 * dim): black, hand card, their product and absolute difference. Has no
            parameters, so training computes it once instead of every epoch."""
            hand = x[:, 1:, :]
            b = x[:, :1, :].expand_as(hand)
            return torch.cat([b, hand, b * hand, (b - hand).abs()], dim=-1)

        def score(self, pairs):
            return self.head(pairs).squeeze(-1)

        def forward(self, x):
            return self.score(self.pair_features(x))

    return PairScorer()


def to_tensors(examples: Sequence[Example], row: dict[int, int], vectors: np.ndarray):
    """(inputs, targets): inputs (n, 1 + hand, dim), the black card's embedding first; targets the played
    card's position in the hand. A shorter hand (a vote example) is padded by repeating its last card; see
    hand_mask for which positions are real."""
    torch = _torch()
    rows = [[row[e.black], *(row[c] for c in e.hand), *[row[e.hand[-1]]] * (HAND_SIZE - len(e.hand))]
            for e in examples]
    x = torch.from_numpy(vectors[rows])
    target = torch.tensor([e.target for e in examples], dtype=torch.long)
    return x, target


def hand_mask(examples: Sequence[Example]):
    """(n, hand) True for real cards, False for padding; None when every hand is full (picks only)."""
    if all(len(e.hand) == HAND_SIZE for e in examples):
        return None
    torch = _torch()
    return torch.tensor([[i < len(e.hand) for i in range(HAND_SIZE)] for e in examples])


def fit(train, val, dim: int, config: TrainConfig):
    """Train on (inputs, targets[, mask]) tensors; returns the model at its best validation loss and the
    learning curve [(epoch, train_loss, val_loss)]."""
    _torch().manual_seed(config.seed)  # before the weights are initialised, so runs are reproducible
    model = make_scorer(dim, config)
    train_pairs, val_pairs = model.pair_features(train[0]), model.pair_features(val[0])
    curve, best_epoch = train_loop(model, (train_pairs,), train[1], (val_pairs,), val[1], config, score=model.score,
                                   train_mask=train[2] if len(train) > 2 else None,
                                   val_mask=val[2] if len(val) > 2 else None)
    return model, curve, best_epoch


def train_loop(model, train_inputs: tuple, train_targets, val_inputs: tuple, val_targets, config: TrainConfig,
               score=None, train_mask=None, val_mask=None) -> tuple[list[tuple[int, float, float]], int]:
    """Full-batch Adam on the cross-entropy of the played card, with early stopping on the validation loss.

    Works for any torch module: score(*inputs) (default: the module itself) must return (n, hand) scores.
    Leaves the model at its best validation epoch, in eval mode. Returns the learning curve
    [(epoch, train_loss, val_loss)] and the best epoch. Seed torch (config.seed) before building the model:
    its initial weights come from the random generator. The experiment notebooks train their models with it
    too, so they compare with the pipeline's models on equal terms. A mask (n, hand) leaves padded positions
    of shorter hands out of the softmax.
    """
    torch = _torch()
    raw_score = score or model

    def masked(mask):
        if mask is None:
            return raw_score
        return lambda *inputs: raw_score(*inputs).masked_fill(~mask, float("-inf"))
    train_score, val_score = masked(train_mask), masked(val_mask)
    optimiser = torch.optim.Adam(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
    loss_fn = torch.nn.CrossEntropyLoss()
    best_loss, best_state, best_epoch, curve = float("inf"), None, 0, []
    for epoch in range(1, config.max_epochs + 1):
        model.train()
        optimiser.zero_grad()
        loss = loss_fn(train_score(*train_inputs), train_targets)
        loss.backward()
        optimiser.step()
        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(val_score(*val_inputs), val_targets).item()
        curve.append((epoch, loss.item(), val_loss))
        if val_loss < best_loss - 1e-6:
            best_loss, best_epoch = val_loss, epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        elif epoch - best_epoch >= config.patience:
            break
    model.load_state_dict(best_state)
    model.eval()
    return curve, best_epoch


def predict_probs(model, x, mask=None) -> np.ndarray:
    """Probabilities per hand card; 0 for the padding of a shorter hand (mask)."""
    torch = _torch()
    with torch.no_grad():
        scores = model(x)
        if mask is not None:
            scores = scores.masked_fill(~mask, float("-inf"))
        return torch.softmax(scores, dim=-1).numpy()


def best_score_percentiles(model, x) -> dict[str, float]:
    """{"10": s, ...}: s is the score that the best card of 10% of these hands falls below."""
    torch = _torch()
    with torch.no_grad():
        best = model(x).max(dim=-1).values.numpy()
    return {str(q): float(np.percentile(best, q)) for q in REFRESH_PERCENTILES}


def _log_metrics(prefix_metrics: dict[str, dict[str, float]]) -> None:
    import mlflow
    mlflow.log_metrics({f"{split}_{name}": value for split, m in prefix_metrics.items() for name, value in m.items()})


def run_cycle(snapshot: Path, embeddings: Sequence[str] = tuple(features.EMBEDDING_MODELS),
              model_types: Sequence[str] = MODEL_TYPES, experiment: str = EXPERIMENT, register: bool = True,
              cache_dir: Path = features.DEFAULT_CACHE,
              encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder,
              variants: Sequence[str] = VARIANTS, register_variant: str = "A") -> dict:
    """Train and log one cycle: every embedding x structure x data variant (ml/evaluate.py). Only the
    register_variant's models are logged as models (the others log metrics, to keep the store small), and
    the challenger comes from them; the other variants are compared with it on the same validation and
    test picks (paired bootstrap over games). Returns {"parent_run_id", "runs": [...], "challenger": ...}."""
    import mlflow
    from mlflow.data.dataset_source_registry import resolve_dataset_source
    from mlflow.data.meta_dataset import MetaDataset

    variants = [register_variant, *[v for v in variants if v != register_variant]]
    manifest = require_valid(snapshot)
    _, tables = read_snapshot(snapshot)
    splits_of = {v: evaluate.make_splits(tables, v) for v in variants}
    splits = splits_of[register_variant]
    targets = {s: np.array([e.target for e in splits[s]]) for s in evaluate.SPLITS}
    games = {s: [e.game_id for e in splits[s]] for s in ("val", "test")}
    texts = {c["text_id"]: c["text"] for c in tables["card_texts"]}
    split_sizes = {f"n_{s}_picks": len(splits[s]) for s in evaluate.SPLITS}
    split_sizes |= {f"n_{s}_games": len({e.game_id for e in splits[s]}) for s in evaluate.SPLITS}
    split_sizes["n_human_picks"] = len(evaluate.pick_examples(tables))  # all of them: the pipeline's trigger
    for v in variants:
        train_v = splits_of[v]["train"]
        split_sizes |= {f"n_train_examples_{v}": len(train_v),
                        f"n_train_bot_game_picks_{v}": sum(e.bot_game and e.source == "pick" for e in train_v),
                        f"n_train_vote_examples_{v}": sum(e.source == "vote" for e in train_v)}

    mlflow.set_experiment(experiment)
    # Commit + data hash + image = everything needed to rebuild a run. EEK_ML_IMAGE is set by the training
    # image (Dockerfile.ml); runs from venv-ml on the server have none.
    tags = {"snapshot": snapshot.name, "snapshot_hash": manifest["content_hash"], "git_commit": git_commit() or "unknown",
            "image": os.environ.get("EEK_ML_IMAGE") or "none (venv-ml)"}
    if os.environ.get("CLOUD_RUN_EXECUTION"):  # set by Cloud Run in every job execution
        tags["cloud_run_execution"] = os.environ["CLOUD_RUN_EXECUTION"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "can be interpreted in multiple ways": it is a local folder
        dataset = MetaDataset(resolve_dataset_source(str(snapshot)), name=snapshot.name,
                              digest=manifest["content_hash"][:16])
    runs = []
    trained_on = time.strftime("%Y-%m-%d")
    with mlflow.start_run(run_name=f"cycle {trained_on} · data to {snapshot.name}") as parent:
        mlflow.set_tags({**tags, "kind": "cycle"})
        mlflow.log_params({**split_sizes, "split": "by game, time order; val/test: games without a computer player",
                           "task": "one-blank picks, humans only", "variants": ",".join(variants),
                           "register_variant": register_variant})
        mlflow.log_input(dataset, context="training")
        # The data itself (~150 KB), so the run can be rebuilt even if ml-data/ is gone.
        mlflow.log_artifacts(str(snapshot), artifact_path="snapshot")

        for name, probs_fn in (("random", lambda s: evaluate.random_probs(splits[s])),
                               ("popularity", lambda s: evaluate.popularity_probs(splits["train"], splits[s]))):
            with mlflow.start_run(run_name=f"baseline {name}", nested=True) as child:
                mlflow.set_tags({**tags, "kind": "baseline"})
                mlflow.log_params({"model_type": f"baseline-{name}"})
                scores = {s: evaluate.metrics(probs_fn(s), targets[s]) for s in evaluate.SPLITS}
                _log_metrics(scores)
            runs.append({"name": f"baseline {name}", "run_id": child.info.run_id, **{s: scores[s] for s in scores}})

        for key in embeddings:
            embedding = features.EMBEDDING_MODELS[key]
            ids, vectors = features.embed(texts, embedding, cache_dir, encoder_factory)
            row = {int(t): i for i, t in enumerate(ids)}
            reference: dict[str, dict[str, np.ndarray]] = {}  # model_type -> split -> reciprocal ranks
            for variant in variants:
                split_v = splits_of[variant]
                data = {s: (*to_tensors(split_v[s], row, vectors), hand_mask(split_v[s])) for s in evaluate.SPLITS}
                for model_type in model_types:
                    config = CONFIGS[model_type]
                    model, curve, best_epoch = fit(data["train"], data["val"], embedding.dim, config)
                    probs = {s: predict_probs(model, data[s][0], data[s][2]) for s in evaluate.SPLITS}
                    scores = {s: evaluate.metrics(probs[s], data[s][1].numpy()) for s in evaluate.SPLITS}
                    ranks = {s: evaluate.reciprocal_ranks(probs[s], targets[s]) for s in ("val", "test")}
                    registrable = variant == register_variant
                    run_name = f"{model_type} {key}" if registrable else f"{model_type} {key} [{variant}]"
                    with mlflow.start_run(run_name=run_name, nested=True) as child:
                        mlflow.set_tags({**tags, "kind": "model", "data_variant": variant})
                        mlflow.log_params({"model_type": model_type, "embedding": key,
                                           "embedding_hf_id": embedding.hf_id, "embedding_revision": embedding.revision,
                                           **asdict(config), "data_variant": variant,
                                           "n_train_examples": len(split_v["train"]), "best_epoch": best_epoch,
                                           "epochs_run": len(curve),
                                           "parameters": sum(p.numel() for p in model.parameters())})
                        _log_metrics(scores)
                        for epoch, train_loss, val_loss in curve:
                            mlflow.log_metrics({"curve_train_loss": train_loss, "curve_val_loss": val_loss}, step=epoch)
                        entry = {"name": run_name, "run_id": child.info.run_id, "model_type": model_type,
                                 "embedding": key, "variant": variant, **scores}
                        if registrable:
                            reference[model_type] = ranks
                            percentiles = best_score_percentiles(model, data["train"][0])
                            mlflow.log_metrics({f"train_best_score_p{q}": v for q, v in percentiles.items()})
                            entry |= {"model_uri": _log_model(model, data["train"]),
                                      "best_score_percentiles": percentiles}
                        elif model_type in reference:
                            entry["vs_reference"] = {s: evaluate.paired_bootstrap(reference[model_type][s], ranks[s],
                                                                                  games[s]) for s in ("val", "test")}
                            mlflow.log_metrics({f"{s}_mrr_vs_{register_variant}_{k}": v
                                                for s, b in entry["vs_reference"].items()
                                                for k, v in b.items() if k != "games"})
                    runs.append(entry)

        challenger = _register_best(runs, tags) if register else None
        if challenger:
            mlflow.set_tag("challenger", f"{challenger['name']} (version {challenger['version']})")
    return {"parent_run_id": parent.info.run_id, "runs": runs, "challenger": challenger}


def _log_model(model, train) -> str:
    """The scorer: input (n, 11, 384), the black card's embedding then the hand's; output (n, 10) raw
    scores (apply a softmax for probabilities). Each card's score depends only on the black card and that
    card, so a hand of fewer cards can be padded. Saved in MLflow's "pt2" format, a traced graph
    (torch.export) that loads without this module. The example input (two training picks) also gives the
    model its signature."""
    import mlflow.pytorch
    torch = _torch()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        info = mlflow.pytorch.log_model(torch.nn.Sequential(model).eval(), name="model",
                                        input_example=train[0][:2].numpy(), pip_requirements=["torch==2.14.1"])
    return info.model_uri


def _register_best(runs: list[dict], tags: dict) -> dict | None:
    """Register the trained model with the best validation MRR (then lowest validation log loss) and point
    the "challenger" alias at it."""
    import mlflow
    trained = [r for r in runs if "model_uri" in r]
    if not trained:
        return None
    best = max(trained, key=lambda r: (r["val"]["mrr"], -r["val"]["log_loss"]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        version = mlflow.register_model(best["model_uri"], REGISTERED_MODEL,
                                        tags={**tags, "model_type": best["model_type"], "embedding": best["embedding"],
                                              "output": "scores",
                                              "best_score_percentiles": json.dumps(best["best_score_percentiles"])})
    client = mlflow.MlflowClient()
    client.set_registered_model_alias(REGISTERED_MODEL, "challenger", version.version)
    return {"name": best["name"], "run_id": best["run_id"], "version": version.version}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=None, help="snapshot folder (default: the latest)")
    parser.add_argument("--embeddings", default=",".join(features.EMBEDDING_MODELS),
                        help=f"comma-separated, from: {', '.join(features.EMBEDDING_MODELS)}")
    parser.add_argument("--models", default=",".join(MODEL_TYPES), help=f"comma-separated, from: {', '.join(MODEL_TYPES)}")
    parser.add_argument("--experiment", default=EXPERIMENT, help=f"MLflow experiment (default: {EXPERIMENT})")
    parser.add_argument("--no-register", action="store_true", help="don't register the best model as challenger")
    parser.add_argument("--variants", default=",".join(VARIANTS), help=f"comma-separated, from: {', '.join(VARIANTS)}")
    args = parser.parse_args(argv)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    embeddings = [e.strip() for e in args.embeddings.split(",") if e.strip()]
    model_types = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [e for e in embeddings if e not in features.EMBEDDING_MODELS] + [m for m in model_types if m not in CONFIGS]
    unknown += [v for v in variants if v not in VARIANTS]
    if unknown or not embeddings or not model_types or "A" not in variants:
        print(f"Unknown or missing: {', '.join(unknown) or 'embeddings/models'}", file=sys.stderr)
        return 2
    priority = lower_priority()
    try:
        snapshot = args.snapshot or latest_snapshot(DEFAULT_OUT)
        result = run_cycle(snapshot, embeddings, model_types, args.experiment, register=not args.no_register,
                           variants=variants)
    except (OSError, ValueError) as exc:
        print(f"Training failed: {exc}", file=sys.stderr)
        return 1
    print(f"Snapshot {snapshot.name}, CPU priority: {priority}")
    print(f"{'run':<22} {'test top1':>9} {'top3':>6} {'MRR':>6} {'log loss':>9} {'val MRR':>8}  val MRR vs A [95%]")
    for r in result["runs"]:
        t, cmp = r["test"], r.get("vs_reference", {}).get("val")
        vs = f"  {cmp['delta']:+.3f} [{cmp['lo']:+.3f}, {cmp['hi']:+.3f}]" if cmp else ""
        print(f"{r['name']:<22} {t['top1']:9.3f} {t['top3']:6.3f} {t['mrr']:6.3f} {t['log_loss']:9.3f} "
              f"{r['val']['mrr']:8.3f}{vs}")
    n = result["runs"][0]
    print(f"(picks: {int(n['train']['n'])} train, {int(n['val']['n'])} validation, {int(n['test']['n'])} test)")
    if result["challenger"]:
        c = result["challenger"]
        print(f"Challenger: {c['name']} -> {REGISTERED_MODEL} version {c['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
