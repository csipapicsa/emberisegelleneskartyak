"""The whole pipeline in one command: export -> validate -> train, when there is enough new data.

    python -m ml.pipeline                    # settings from ml/pipeline.toml
    python -m ml.pipeline --force            # train even without new data
    python -m ml.pipeline --config other.toml

Run nightly by deploy/systemd/eek-ml-train.timer. Steps:

1. export gameplay.db to a snapshot (unchanged data finds the existing one);
2. validate it; any failed check stops the pipeline with exit code 1;
3. ask MLflow for the last training cycle: if this snapshot was already trained on, or it has fewer than
   `min_new_picks` new picks, stop here (exit code 0: nothing to do is not an error);
4. train one cycle (ml/train.py), which logs to MLflow and registers the challenger.

MLflow is the only state: the last cycle is the newest run tagged kind=cycle in the experiment, and its
parameters say how many picks it trained on.
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ml import evaluate, features, lower_priority, train
from ml.export import DEFAULT_OUT, export, read_snapshot
from ml.validate import FIRST_LOG_DAY, validate_snapshot
from server.gameplay_log import DB_PATH

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("pipeline.toml")


@dataclass
class Config:
    db: Path = DB_PATH
    snapshots: Path = DEFAULT_OUT
    first_day: int = FIRST_LOG_DAY  # days since 1970-01-01
    embeddings: list[str] = field(default_factory=lambda: list(features.EMBEDDING_MODELS))
    models: list[str] = field(default_factory=lambda: list(train.MODEL_TYPES))
    experiment: str = train.EXPERIMENT
    register: bool = True
    min_new_picks: int = 20


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    exp, val, tr, trig = (raw.get(k, {}) for k in ("export", "validate", "train", "trigger"))
    config = Config()
    if exp.get("db"):
        config.db = ROOT / exp["db"]
    if exp.get("snapshots"):
        config.snapshots = ROOT / exp["snapshots"]
    if val.get("first_day"):
        config.first_day = (date.fromisoformat(val["first_day"]) - date(1970, 1, 1)).days
    config.embeddings = list(tr.get("embeddings", config.embeddings))
    config.models = list(tr.get("models", config.models))
    config.experiment = tr.get("experiment", config.experiment)
    config.register = bool(tr.get("register", config.register))
    config.min_new_picks = int(trig.get("min_new_picks", config.min_new_picks))
    unknown = [e for e in config.embeddings if e not in features.EMBEDDING_MODELS]
    unknown += [m for m in config.models if m not in train.CONFIGS]
    if unknown or not config.embeddings or not config.models:
        raise ValueError(f"{path}: unknown or missing embeddings/models: {', '.join(unknown) or '(empty list)'}")
    return config


def last_cycle(experiment: str) -> dict | None:
    """The newest training cycle in MLflow: {"snapshot_hash", "picks", "run_id"}, or None."""
    import mlflow
    found = mlflow.search_runs(experiment_names=[experiment], filter_string="tags.kind = 'cycle'",
                               order_by=["attributes.start_time DESC"], max_results=1, output_format="list")
    if not found:
        return None
    run = found[0]
    picks = sum(int(run.data.params.get(f"n_{s}_picks", 0)) for s in evaluate.SPLITS)
    return {"snapshot_hash": run.data.tags.get("snapshot_hash"), "picks": picks, "run_id": run.info.run_id}


def run(config: Config, force: bool = False,
        encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder) -> dict:
    """One pipeline run. Returns {"status": "trained" | "skipped" | "invalid", "reason", "snapshot", ...}."""
    folder, new = export(config.db, config.snapshots)
    results = validate_snapshot(folder, first_day=config.first_day)
    failed = [r.name for r in results if not r.ok]
    if failed:
        return {"status": "invalid", "snapshot": folder, "reason": f"failed checks: {', '.join(failed)}"}

    manifest, tables = read_snapshot(folder)
    picks = len(evaluate.pick_examples(tables))
    previous = last_cycle(config.experiment)
    if not force and previous:
        if previous["snapshot_hash"] == manifest["content_hash"]:
            return {"status": "skipped", "snapshot": folder, "reason": "this snapshot was already trained on"}
        if picks - previous["picks"] < config.min_new_picks:
            return {"status": "skipped", "snapshot": folder,
                    "reason": f"{picks - previous['picks']} new picks, fewer than {config.min_new_picks}"}

    result = train.run_cycle(folder, config.embeddings, config.models, config.experiment, register=config.register,
                             encoder_factory=encoder_factory)
    return {"status": "trained", "snapshot": folder, "new_snapshot": new, "picks": picks, **result,
            "reason": "forced" if force else ("first cycle" if previous is None else
                                              f"{picks - previous['picks']} new picks")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"settings (default: {DEFAULT_CONFIG})")
    parser.add_argument("--force", action="store_true", help="train even if there is no new data")
    args = parser.parse_args(argv)
    priority = lower_priority()
    try:
        config = load_config(args.config)
        outcome = run(config, force=args.force)
    except (OSError, ValueError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(f"Snapshot {outcome['snapshot'].name} (CPU priority: {priority})")
    if outcome["status"] == "invalid":
        print(f"Stopped: {outcome['reason']}; see {outcome['snapshot'] / 'validation.json'}", file=sys.stderr)
        return 1
    if outcome["status"] == "skipped":
        print(f"Nothing to train: {outcome['reason']}.")
        return 0
    best = max((r for r in outcome["runs"] if "model_type" in r), key=lambda r: r["test"]["mrr"])
    print(f"Trained ({outcome['reason']}): {len(outcome['runs'])} runs, parent {outcome['parent_run_id']}; "
          f"best on test: {best['name']} (MRR {best['test']['mrr']:.3f})")
    if outcome["challenger"]:
        print(f"Challenger: {outcome['challenger']['name']} -> {train.REGISTERED_MODEL} "
              f"version {outcome['challenger']['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
