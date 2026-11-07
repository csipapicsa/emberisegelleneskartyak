"""Train in GCP: upload a snapshot with its embeddings, run the Cloud Run Job on it, find its MLflow runs.

    python -m ml.cloud                                     # latest snapshot
    python -m ml.cloud --snapshot ml-data/snapshots/<name>
    python -m ml.cloud --register                          # also move pick-model@challenger
    python -m ml.cloud --no-wait                           # start the job and return

Steps (setup: docs/ml/gcp-setup.md):

1. the snapshot must have passed ml.validate; its embeddings are computed here, on the server, for every
   model in ml/pipeline.toml [train] (usually already cached), so the cloud job never loads a model;
2. upload to gs://<bucket>/cycles/<snapshot>/{snapshot,embeddings}/. Uploads are create-only (the server's
   service account may add objects but not overwrite them); a file that's already there is skipped;
3. run the Cloud Run Job with this snapshot (args and EEK_ML_DATA_DIR overridden for this execution). The
   job mounts the bucket read-only at /gcs and logs to MLflow like the nightly run;
4. wait for it, then print its MLflow runs next to the local runs on the same snapshot.

Credentials: the eek-uploader service account key (pipeline.toml [cloud] key_file). Only google-auth is
used (already locked through MLflow); Cloud Storage and Cloud Run are called through their REST APIs.
"""
from __future__ import annotations

import argparse
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ml import features, train
from ml.export import DEFAULT_OUT, latest_snapshot, read_snapshot
from ml.validate import require_valid

DEFAULT_CONFIG = Path(__file__).with_name("pipeline.toml")
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
RUN_API = "https://run.googleapis.com/v2"
MOUNT = "/gcs"  # where the job mounts the bucket (gcloud run jobs create ... --add-volume-mount)


@dataclass
class CloudConfig:
    project: str
    region: str
    bucket: str
    job: str
    key_file: Path
    mlflow_url: str
    embeddings: list[str]
    models: list[str]
    experiment: str


def load_config(path: Path = DEFAULT_CONFIG) -> CloudConfig:
    raw = tomllib.loads(Path(path).read_text())
    cloud, tr = raw.get("cloud", {}), raw.get("train", {})
    missing = [k for k in ("project", "region", "bucket", "job", "key_file", "mlflow_url") if not cloud.get(k)]
    if missing:
        raise ValueError(f"{path} [cloud]: missing {', '.join(missing)}")
    return CloudConfig(project=cloud["project"], region=cloud["region"], bucket=cloud["bucket"], job=cloud["job"],
                       key_file=Path(cloud["key_file"]).expanduser(), mlflow_url=cloud["mlflow_url"].rstrip("/"),
                       embeddings=list(tr.get("embeddings", features.EMBEDDING_MODELS)),
                       models=list(tr.get("models", train.MODEL_TYPES)),
                       experiment=tr.get("experiment", train.EXPERIMENT))


def session(key_file: Path):
    """An HTTP session that signs every request as the service account in key_file."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    credentials = service_account.Credentials.from_service_account_file(str(key_file), scopes=SCOPES)
    return AuthorizedSession(credentials)


def stage(snapshot: Path, embeddings: Sequence[str], cache_dir: Path = features.DEFAULT_CACHE,
          encoder_factory: features.EncoderFactory = features.SentenceTransformerEncoder) -> dict[str, Path]:
    """{object name: local file} for one cycle: the snapshot's files and, per embedding model, a cache file
    holding every card text of the snapshot. Missing embeddings are computed first."""
    _, tables = read_snapshot(snapshot)
    texts = {c["text_id"]: c["text"] for c in tables["card_texts"]}
    prefix = f"cycles/{snapshot.name}"
    objects = {f"{prefix}/snapshot/{p.relative_to(snapshot).as_posix()}": p
               for p in sorted(snapshot.rglob("*")) if p.is_file()}
    for key in embeddings:
        model = features.EMBEDDING_MODELS[key]
        features.embed(texts, model, cache_dir, encoder_factory)  # fills the cache if anything is missing
        path = features.cache_path(model, cache_dir)
        objects[f"{prefix}/embeddings/{path.relative_to(cache_dir).as_posix()}"] = path
    return objects


def upload(http, bucket: str, objects: dict[str, Path]) -> dict[str, list[str]]:
    """Create each object unless it exists (ifGenerationMatch=0: create-only, so the upload never needs
    overwrite rights). Returns {"uploaded": [...], "existing": [...]}."""
    done: dict[str, list[str]] = {"uploaded": [], "existing": []}
    for name, path in objects.items():
        response = http.post(GCS_UPLOAD.format(bucket=bucket),
                             params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
                             data=path.read_bytes(), headers={"Content-Type": "application/octet-stream"})
        if response.status_code == 412:  # precondition failed: the object already exists
            done["existing"].append(name)
        elif response.ok:
            done["uploaded"].append(name)
        else:
            raise RuntimeError(f"upload of {name} failed: HTTP {response.status_code} {response.text[:300]}")
    return done


def job_overrides(snapshot_name: str, config: CloudConfig, register: bool) -> dict:
    """This execution's container args and environment: train on the uploaded snapshot."""
    data_dir = f"{MOUNT}/cycles/{snapshot_name}"
    args = ["-m", "ml.train", "--snapshot", f"{data_dir}/snapshot", "--embeddings", ",".join(config.embeddings),
            "--models", ",".join(config.models), "--experiment", config.experiment]
    if not register:
        args.append("--no-register")
    return {"containerOverrides": [{"args": args, "env": [{"name": "EEK_ML_DATA_DIR", "value": data_dir}]}]}


def run_job(http, config: CloudConfig, overrides: dict) -> str:
    """Start one execution of the job; returns its full resource name."""
    url = f"{RUN_API}/projects/{config.project}/locations/{config.region}/jobs/{config.job}:run"
    response = http.post(url, json={"overrides": overrides})
    if not response.ok:
        raise RuntimeError(f"starting {config.job} failed: HTTP {response.status_code} {response.text[:300]}")
    execution = response.json().get("metadata", {}).get("name")
    if not execution:
        raise RuntimeError(f"no execution in the response: {response.text[:300]}")
    return execution


def wait_for(http, execution: str, poll_seconds: float = 15, timeout: float = 45 * 60,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll the execution until it has finished; returns it. RuntimeError if it failed or took too long."""
    waited = 0.0
    while True:
        response = http.get(f"{RUN_API}/{execution}")
        if not response.ok:
            raise RuntimeError(f"reading {execution} failed: HTTP {response.status_code} {response.text[:300]}")
        state = response.json()
        if state.get("completionTime"):
            if state.get("failedCount") or state.get("cancelledCount") or not state.get("succeededCount"):
                raise RuntimeError(f"{execution.rsplit('/', 1)[-1]} failed; logs: {state.get('logUri', '?')}")
            return state
        if waited >= timeout:
            raise RuntimeError(f"{execution} still running after {timeout / 60:.0f} min; logs: {state.get('logUri')}")
        sleep(poll_seconds)
        waited += poll_seconds


def compare(snapshot_hash: str, experiment: str) -> list[dict]:
    """Every model run on this snapshot, cloud and local: [{name, where, image, test_mrr, run_id, experiment_id}]."""
    import mlflow
    runs = mlflow.search_runs(experiment_names=[experiment], output_format="list",
                              filter_string=f"tags.snapshot_hash = '{snapshot_hash}' and tags.kind = 'model'",
                              order_by=["attributes.start_time ASC"])
    return [{"name": r.info.run_name, "where": "cloud" if r.data.tags.get("cloud_run_execution") else "server",
             "execution": r.data.tags.get("cloud_run_execution", ""), "image": r.data.tags.get("image", ""),
             "test_mrr": r.data.metrics.get("test_mrr"), "run_id": r.info.run_id,
             "experiment_id": r.info.experiment_id} for r in runs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=None, help="snapshot folder (default: the latest)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=f"settings (default: {DEFAULT_CONFIG})")
    parser.add_argument("--register", action="store_true", help="register the best model as challenger")
    parser.add_argument("--no-wait", action="store_true", help="start the job and return")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        snapshot = args.snapshot or latest_snapshot(DEFAULT_OUT)
        manifest = require_valid(snapshot)
        objects = stage(snapshot, config.embeddings)
        http = session(config.key_file)
        done = upload(http, config.bucket, objects)
        print(f"Snapshot {snapshot.name}: {len(done['uploaded'])} files uploaded, "
              f"{len(done['existing'])} already in gs://{config.bucket}")
        execution = run_job(http, config, job_overrides(snapshot.name, config, args.register))
        short = execution.rsplit("/", 1)[-1]
        print(f"Started {short}")
        if args.no_wait:
            return 0
        state = wait_for(http, execution)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Cloud training failed: {exc}", file=sys.stderr)
        return 1
    print(f"Finished {short}; logs: {state.get('logUri', '?')}")
    rows = compare(manifest["content_hash"], config.experiment)
    print(f"\nModel runs on this snapshot ({config.experiment}):")
    print(f"{'run':<16} {'where':<6} {'test MRR':>8}  link")
    for r in rows:
        mrr = f"{r['test_mrr']:.4f}" if r["test_mrr"] is not None else "-"
        link = f"{config.mlflow_url}/#/experiments/{r['experiment_id']}/runs/{r['run_id']}"
        print(f"{r['name']:<16} {r['where']:<6} {mrr:>8}  {link}" + ("  <- this run" if r["execution"] == short else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
