"""MLflow experiment `embedding-benchmark`: the three embedding models on this server's CPU, one run each.

    python -m ml.embedding_benchmark                       # latest snapshot, all three models
    python -m ml.embedding_benchmark --models e5,granite --snapshot ml-data/snapshots/<name>

Per model it logs:
- cost: model load time, encoding time and texts per second for every card text in the snapshot, and the
  peak memory of the process (each model runs in its own process, so the numbers don't mix);
- a zero-training signal check on the snapshot's human picks (rounds with one blank): how often the card a
  player picked is the one most similar to the black card (random: 1 in 10), and how much more similar
  picked cards are than the rest of the hand. Not a model: a first hint whether an embedding carries
  anything useful for this game.

Runs at the lowest CPU priority (ml.lower_priority), so the live game always comes first; that makes
the timings an upper bound when the server is busy. The snapshot must have passed ml.validate. The embeddings are cached (ml/features.py) for training.
MLflow server and login come from MLFLOW_TRACKING_URI and ~/.mlflow/credentials.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import resource
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ml import features, lower_priority
from ml.export import DEFAULT_OUT, latest_snapshot, read_snapshot
from ml.export import _git_commit as git_commit
from ml.validate import require_valid

EXPERIMENT = "embedding-benchmark"


def similarity_signal(tables: dict[str, list[dict]], ids: np.ndarray, vectors: np.ndarray) -> dict[str, float]:
    """For human submissions in one-blank rounds: share where the pick is the hand card most similar to the
    black card, the mean similarity of picked and of unpicked cards, and the number of picks used."""
    row = {int(text_id): i for i, text_id in enumerate(ids)}
    black_of = {(r["game_id"], r["round_no"]): r["black"] for r in tables["rounds"]}
    one_blank = {c["text_id"] for c in tables["card_texts"] if c["black"] and c["pick"] == 1}
    top1 = picked_sum = unpicked_sum = 0.0
    n = unpicked_n = 0
    for d in tables["decisions"]:
        black = black_of.get((d["game_id"], d["round_no"]))
        if d["is_model"] or d["picks"] is None or black not in one_blank:
            continue
        sims = vectors[[row[c] for c in d["hand"]]] @ vectors[row[black]]
        picked = d["hand"].index(d["picks"][0])
        top1 += float(np.argmax(sims) == picked)
        picked_sum += float(sims[picked])
        unpicked_sum += float(sims.sum() - sims[picked])
        unpicked_n += len(sims) - 1
        n += 1
    if n == 0:
        return {"signal_picks": 0.0}
    return {"signal_picks": float(n), "signal_top1": top1 / n, "signal_random_top1": 1 / 10,
            "signal_sim_picked": picked_sum / n, "signal_sim_unpicked": unpicked_sum / unpicked_n,
            "signal_sim_gap": picked_sum / n - unpicked_sum / unpicked_n}


def cpu_priority() -> str:
    """How this process is scheduled, for the run's parameters (the timings depend on it)."""
    try:
        policy = {os.SCHED_IDLE: "idle", os.SCHED_OTHER: "normal"}.get(os.sched_getscheduler(0), "other")
    except (AttributeError, OSError):  # pragma: no cover - not Linux
        policy = "unknown"
    return f"{policy}, nice {os.nice(0)}"


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


def measure(model: features.EmbeddingModel, texts: Sequence[str],
            encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder) -> dict:
    """Load the model and encode every text once, without the cache. Returns timings, peak memory of this
    process, torch's thread count and the vectors."""
    start = time.perf_counter()
    encoder = encoder_factory(model)
    load_seconds = time.perf_counter() - start
    inputs = [model.prefix + t for t in texts]
    encoder.encode(inputs[:8])  # warm-up: the first call pays one-off setup costs
    start = time.perf_counter()
    vectors = encoder.encode(inputs)
    encode_seconds = time.perf_counter() - start
    try:
        import torch
        threads = torch.get_num_threads()
    except ImportError:  # pragma: no cover - torch is in requirements-ml.txt
        threads = 0
    return {"load_seconds": load_seconds, "encode_seconds": encode_seconds,
            "texts_per_second": len(texts) / encode_seconds if encode_seconds else 0.0,
            "peak_memory_mb": _peak_rss_mb(), "threads": threads, "vectors": vectors}


def _measure_in_child(key: str, texts: list[str]) -> dict:
    lower_priority()  # inherited from the parent anyway; explicit in case the child is started differently
    return measure(features.EMBEDDING_MODELS[key], texts)


class _Precomputed:
    """Hands vectors measured in the child process to features.embed, which caches them."""

    def __init__(self, model: features.EmbeddingModel, texts: Sequence[str], vectors: np.ndarray) -> None:
        self._by_input = {model.prefix + t: v for t, v in zip(texts, vectors, strict=True)}

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._by_input[t] for t in texts])


def run(snapshot: Path, models: Sequence[str] = tuple(features.EMBEDDING_MODELS),
        cache_dir: Path = features.DEFAULT_CACHE, experiment: str = EXPERIMENT,
        encoder_factory: features.EncoderFactory | None = None) -> list[dict]:
    """Benchmark each model on the snapshot and log one MLflow run per model. encoder_factory (tests)
    measures in this process instead of a fresh one per model."""
    import mlflow

    manifest = require_valid(snapshot)
    _, tables = read_snapshot(snapshot)
    by_id = {c["text_id"]: c["text"] for c in tables["card_texts"]}
    texts = [by_id[i] for i in sorted(by_id)]
    mlflow.set_experiment(experiment)
    results = []
    for key in models:
        model = features.EMBEDDING_MODELS[key]
        if encoder_factory is None:
            with multiprocessing.get_context("spawn").Pool(1) as pool:
                measured = pool.apply(_measure_in_child, (key, texts))
        else:
            measured = measure(model, texts, encoder_factory)
        vectors = measured.pop("vectors")
        ids, matrix = features.embed(by_id, model, cache_dir,
                                     encoder_factory=lambda m, v=vectors: _Precomputed(m, texts, v))
        metrics = {**measured, **similarity_signal(tables, ids, matrix), "texts": float(len(texts))}
        with mlflow.start_run(run_name=key) as active:
            mlflow.set_tags({"snapshot": snapshot.name, "snapshot_hash": manifest["content_hash"],
                             "git_commit": git_commit() or "unknown"})
            mlflow.log_params({"model_key": model.key, "hf_id": model.hf_id, "revision": model.revision,
                               "prefix": model.prefix or "(none)", "dim": model.dim,
                               "cpu_priority": cpu_priority()})
            mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
        results.append({"model": key, "run_id": active.info.run_id, **metrics})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=None, help="snapshot folder (default: the latest)")
    parser.add_argument("--models", default=",".join(features.EMBEDDING_MODELS),
                        help=f"comma-separated, from: {', '.join(features.EMBEDDING_MODELS)}")
    parser.add_argument("--experiment", default=EXPERIMENT, help=f"MLflow experiment (default: {EXPERIMENT})")
    args = parser.parse_args(argv)
    priority = lower_priority()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in features.EMBEDDING_MODELS]
    if unknown or not models:
        print(f"Unknown model(s): {', '.join(unknown) or '(none given)'}", file=sys.stderr)
        return 2
    try:
        snapshot = args.snapshot or latest_snapshot(DEFAULT_OUT)
        results = run(snapshot, models, experiment=args.experiment)
        print(f"CPU priority: {priority}")
    except (OSError, ValueError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(f"{'model':<8} {'load s':>7} {'texts/s':>8} {'peak MB':>8} {'top-1':>6} {'sim gap':>8}")
    for r in results:
        print(f"{r['model']:<8} {r['load_seconds']:7.1f} {r['texts_per_second']:8.1f} {r['peak_memory_mb']:8.0f} "
              f"{r.get('signal_top1', float('nan')):6.3f} {r.get('signal_sim_gap', float('nan')):8.4f}")
    print(f"(random top-1: 0.100; {int(results[0].get('signal_picks', 0))} picks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
