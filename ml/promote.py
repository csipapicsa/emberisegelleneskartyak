"""Promote the challenger to champion after comparing both on the same, newest test picks; or roll back.

    python -m ml.promote                 # compare, then ask before moving the alias
    python -m ml.promote --dry-run       # compare only
    python -m ml.promote --yes           # no question (scripts)
    python -m ml.promote --auto          # promote only a significantly better challenger (the nightly pipeline)
    python -m ml.promote --rollback      # champion <-> previous-champion

Production's computer player plays the version behind the "champion" alias (ml/serve.py), staging's the
"challenger", which every nightly cycle moves (ml/train.py). This is the step in between. The nightly pipeline
runs it automatically (`[promote] auto` in ml/pipeline.toml): every version has the same architecture and
speed, so the only question is accuracy, and a challenger is promoted only when it is significantly better.
"Higher MRR" alone isn't enough: nightly versions differ by far less than the test set's noise, so promoting
every slightly higher number would swap models at random.

The comparison: both versions score the test picks of the newest validated snapshot (human picks from games
without a computer player, ml/evaluate.py). The newest games are in nobody's training data, and both models
see exactly the same examples, so the difference is a paired comparison: MRR delta with a 95% interval from
resampling whole games. A challenger that is significantly worse is refused (--force overrides).

Promoting moves "champion" to the challenger's version and "previous-champion" to the old champion, and tags
the new champion with the comparison. Rolling back swaps the two aliases. The model service follows the
alias within minutes; nothing restarts.
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ml import evaluate, features, lower_priority, train
from ml.export import DEFAULT_OUT, latest_snapshot, read_snapshot
from ml.validate import require_valid

MODEL = train.REGISTERED_MODEL
CHALLENGER, CHAMPION, PREVIOUS = "challenger", "champion", "previous-champion"


@dataclass
class Scored:
    version: str
    metrics: dict[str, float]
    ranks: np.ndarray       # reciprocal rank per test pick
    embedding: str


def _version(client, alias: str):
    try:
        return client.get_model_version_by_alias(MODEL, alias)
    except Exception:  # noqa: BLE001 - MLflow raises different errors for "no such alias"
        return None


def score_version(version, examples: list[evaluate.Example], texts: dict[int, str], cache_dir: Path,
                  encoder_factory: features.EncoderFactory) -> Scored:
    """A registered version's probabilities for the test picks: raw scores (output=scores) go through a
    softmax; older versions returned probabilities already."""
    import mlflow
    embedding = features.EMBEDDING_MODELS[version.tags["embedding"]]
    ids, vectors = features.embed(texts, embedding, cache_dir, encoder_factory)
    row = {int(t): i for i, t in enumerate(ids)}
    x, y = train.to_tensors(examples, row, vectors)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = mlflow.pyfunc.load_model(f"models:/{MODEL}/{version.version}")
    out = np.asarray(model.predict(x.numpy()), dtype=np.float64)
    if version.tags.get("output") == "scores":
        out = np.exp(out - out.max(axis=1, keepdims=True))
        out /= out.sum(axis=1, keepdims=True)
    targets = y.numpy()
    return Scored(str(version.version), evaluate.metrics(out, targets), evaluate.reciprocal_ranks(out, targets),
                  embedding.key)


def compare(snapshot: Path | None = None, cache_dir: Path = features.DEFAULT_CACHE,
            encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder) -> dict:
    """{"snapshot", "challenger": Scored, "champion": Scored | None, "bootstrap": {...} | None, "verdict"}."""
    import mlflow
    client = mlflow.MlflowClient()
    challenger, champion = _version(client, CHALLENGER), _version(client, CHAMPION)
    if challenger is None:
        raise ValueError(f"{MODEL} has no {CHALLENGER!r} version; run a training cycle first")
    folder = snapshot or latest_snapshot(DEFAULT_OUT)
    require_valid(folder)
    _, tables = read_snapshot(folder)
    test = evaluate.make_splits(tables, "A")["test"]
    texts = {c["text_id"]: c["text"] for c in tables["card_texts"]}
    result = {"snapshot": folder.name, "n_test": len(test), "n_games": len({e.game_id for e in test}),
              "challenger": score_version(challenger, test, texts, cache_dir, encoder_factory),
              "champion": None, "bootstrap": None}
    if champion is None:
        result["verdict"] = "first champion"
    elif str(champion.version) == str(challenger.version):
        result["verdict"] = "already champion"
    else:
        result["champion"] = score_version(champion, test, texts, cache_dir, encoder_factory)
        b = evaluate.paired_bootstrap(result["champion"].ranks, result["challenger"].ranks,
                                      [e.game_id for e in test])
        result["bootstrap"] = b
        result["verdict"] = ("significantly worse" if b["hi"] < 0 else
                             "significantly better" if b["lo"] > 0 else "no significant difference")
    return result


# The verdicts that promote without a person: the first model ever, or one that is better beyond the noise.
AUTO_VERDICTS = ("first champion", "significantly better")


def should_auto_promote(result: dict) -> bool:
    return result["verdict"] in AUTO_VERDICTS


def promote(result: dict) -> dict:
    """Move the aliases as compare() found them; returns {"champion": new, "previous": old or None}."""
    import mlflow
    client = mlflow.MlflowClient()
    new = result["challenger"].version
    old = result["champion"].version if result["champion"] else None
    if old is None:
        current = _version(client, CHAMPION)
        old = str(current.version) if current is not None else None
    if old is not None and old != new:
        client.set_registered_model_alias(MODEL, PREVIOUS, old)
    client.set_registered_model_alias(MODEL, CHAMPION, new)
    tags = {"promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "promoted_from": old or "none",
            "promotion_snapshot": result["snapshot"], "promotion_verdict": result["verdict"],
            "promotion_test_mrr": f"{result['challenger'].metrics['mrr']:.4f}"}
    if result["bootstrap"]:
        b = result["bootstrap"]
        tags["promotion_test_mrr_delta"] = f"{b['delta']:+.4f} [{b['lo']:+.4f}, {b['hi']:+.4f}]"
    for k, v in tags.items():
        client.set_model_version_tag(MODEL, new, k, v)
    return {"champion": new, "previous": old}


def rollback() -> dict:
    """Swap champion and previous-champion."""
    import mlflow
    client = mlflow.MlflowClient()
    champion, previous = _version(client, CHAMPION), _version(client, PREVIOUS)
    if previous is None:
        raise ValueError(f"{MODEL} has no {PREVIOUS!r} version to roll back to")
    client.set_registered_model_alias(MODEL, CHAMPION, str(previous.version))
    if champion is not None:
        client.set_registered_model_alias(MODEL, PREVIOUS, str(champion.version))
    client.set_model_version_tag(MODEL, str(previous.version), "rolled_back_at",
                                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return {"champion": str(previous.version), "previous": str(champion.version) if champion else None}


def report(result: dict) -> str:
    def line(label: str, s) -> str:
        m = s.metrics
        return f"  {label:<11} v{s.version:<4} {s.embedding:<8} MRR {m['mrr']:.3f}  top1 {m['top1']:.3f}  top3 {m['top3']:.3f}"
    lines = [f"Test picks: {result['n_test']} from {result['n_games']} games (snapshot {result['snapshot']})",
             line("challenger", result["challenger"])]
    if result["champion"]:
        lines.append(line("champion", result["champion"]))
        b = result["bootstrap"]
        lines.append(f"  MRR difference {b['delta']:+.3f}, 95% interval [{b['lo']:+.3f}, {b['hi']:+.3f}]")
    lines.append(f"Verdict: {result['verdict']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=None, help="snapshot folder (default: the latest)")
    parser.add_argument("--dry-run", action="store_true", help="compare only, move nothing")
    parser.add_argument("--yes", action="store_true", help="promote without asking")
    parser.add_argument("--force", action="store_true", help="promote even a significantly worse challenger")
    parser.add_argument("--rollback", action="store_true", help="swap champion and previous-champion")
    parser.add_argument("--auto", action="store_true",
                        help="promote only a significantly better challenger (or the first model), no question")
    args = parser.parse_args(argv)
    lower_priority()
    try:
        if args.rollback:
            moved = rollback()
            print(f"Rolled back: champion is version {moved['champion']} (previous-champion: {moved['previous']})")
            return 0
        result = compare(args.snapshot)
    except (OSError, ValueError) as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    print(report(result))
    if args.dry_run or result["verdict"] == "already champion":
        return 0
    if args.auto:
        if not should_auto_promote(result):
            print(f"Champion kept: {result['verdict']}.")
            return 0
        moved = promote(result)
        print(f"Champion: version {moved['champion']} (previous-champion: {moved['previous'] or 'none'})")
        return 0
    if result["verdict"] == "significantly worse" and not args.force:
        print("Not promoted: the challenger is significantly worse (use --force to promote anyway).")
        return 1
    if not args.yes:
        answer = input(f"Make version {result['challenger'].version} the champion? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "i", "igen"):
            print("Not promoted.")
            return 0
    moved = promote(result)
    print(f"Champion: version {moved['champion']} (previous-champion: {moved['previous'] or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
