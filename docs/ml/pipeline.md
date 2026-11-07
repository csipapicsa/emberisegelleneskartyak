# Running the ML pipeline

The steps so far, run on the server from the repository root. Plan and status:
[implementation-plan.md](implementation-plan.md).

## Setup (once)

The pipeline has its own virtualenv, separate from the game's `venv/` (which production runs from):

```bash
python3.11 -m venv venv-ml
venv-ml/bin/pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements-dev.txt -r requirements-ml.txt
```

PyTorch comes from its CPU-only index: the PyPI build bundles GPU libraries worth several GB.
MLflow's address and login come from `MLFLOW_TRACKING_URI` (in `~/.bashrc`) and `~/.mlflow/credentials`
([deploy/containers/README.md](../../deploy/containers/README.md#mlflow)).

## One command

```bash
venv-ml/bin/python -m ml.pipeline           # export -> validate -> train, if there's enough new data
venv-ml/bin/python -m ml.pipeline --force   # train anyway
```

Settings are in [ml/pipeline.toml](../../ml/pipeline.toml): which embeddings and models, the MLflow experiment,
and the trigger (`min_new_picks`, 20 by default). The pipeline asks MLflow for the last training cycle and
skips if the snapshot was already trained on or has too few new picks; that isn't an error (exit code 0).
A failed validation check stops it with exit code 1.

Each cycle stores its snapshot (about 150 KB) as artifacts of its parent run, so a run carries the exact
data it was trained on. A cycle adds about 4 MB of models.

### Nightly run (as root)

At 04:00, after the database backups, at idle CPU and disk priority with a 3 GB memory ceiling
([deploy/systemd/eek-ml-train.service](../../deploy/systemd/eek-ml-train.service)):

```bash
install -m 644 deploy/systemd/eek-ml-train.service deploy/systemd/eek-ml-train.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now eek-ml-train.timer
systemctl start eek-ml-train.service            # a first run by hand
journalctl -u eek-ml-train.service -n 20        # what it did: trained, or why it skipped
```

## Single steps

```bash
venv-ml/bin/python -m ml.export                 # gameplay.db -> ml-data/snapshots/<day>_<hash>/
venv-ml/bin/python -m ml.validate ml-data/snapshots/<day>_<hash>
venv-ml/bin/python -m ml.embedding_benchmark    # latest snapshot, MLflow experiment "embedding-benchmark"
venv-ml/bin/python -m ml.train                  # latest snapshot, MLflow experiment "pick-model"
```

| Step | Reads | Writes |
|---|---|---|
| `ml.export` | `gameplay.db` (a consistent copy) | Parquet tables + `manifest.json` with the content hash; nothing if the data is unchanged |
| `ml.validate` | a snapshot | `validation.json`; exit 1 if any of the 13 checks fails |
| `ml.embedding_benchmark` | a validated snapshot | one MLflow run per embedding model; the embeddings cache in `ml-data/embeddings/` |
| `ml.train` | a validated snapshot, the embeddings | a parent run with 2 baselines + 6 models nested; the best on validation registered as `pick-model@challenger` |

Every ML process lowers its own CPU priority to the minimum (`ml.lower_priority()`: SCHED_IDLE, nice 19),
so the live game always comes first.

## Training image

The same pipeline as a container: [Dockerfile.ml](../../Dockerfile.ml), published by
[.github/workflows/ml-image.yml](../../.github/workflows/ml-image.yml) as
`ghcr.io/csipapicsa/eek-ml-train:sha-<commit>` (and `:main`). This is what the GCP training job runs
(milestone 3). Data lives in its `/data` volume: `gameplay.db`, `snapshots/`, `embeddings/` and the
Hugging Face downloads. The embedding models aren't baked in; with the embeddings cache next to the
snapshot, a job never loads them.

```bash
podman run --rm -v ./data:/data:Z --userns=keep-id:uid=10001,gid=10001 \
    -e MLFLOW_TRACKING_URI -e MLFLOW_TRACKING_USERNAME -e MLFLOW_TRACKING_PASSWORD \
    ghcr.io/csipapicsa/eek-ml-train:main python -m ml.train --snapshot /data/snapshots/<name>
```

Every MLflow run gets the tags `git_commit`, `snapshot_hash` and `image`, which are enough to rebuild it.
Local builds: `podman build -f Dockerfile.ml --ignorefile Dockerfile.ml.dockerignore --target test .`
(podman reuses cached layers even when `GIT_SHA` changes; CI uses docker, which doesn't).

## Training in GCP

The same cycle as a Cloud Run Job ([ml/cloud.py](../../ml/cloud.py); setup: [gcp-setup.md](gcp-setup.md)):

```bash
venv-ml/bin/python -m ml.cloud                                  # latest snapshot
venv-ml/bin/python -m ml.cloud --snapshot ml-data/snapshots/<name> --register
```

It computes any missing embeddings here, uploads the snapshot and its embeddings to
`gs://emberisegelleneskartya-ml/cycles/<snapshot>/` (create-only), runs the job on them, waits, and prints
the job's runs next to the server's runs on the same snapshot, with MLflow links. Cloud runs carry the tag
`cloud_run_execution`. Without `--register` the job doesn't touch `pick-model@challenger`; the nightly run
on the server stays the one that registers. Settings: `[cloud]` in [ml/pipeline.toml](../../ml/pipeline.toml).

## Sample data

Everything runs on made-up games too, which is what the tests and CI use:

```bash
venv-ml/bin/python scripts/make_sample_gameplay_db.py
venv-ml/bin/python -m ml.export --db sample-data/gameplay.db --out /tmp/snapshots
venv-ml/bin/python -m ml.validate --first-day 2025-01-01 /tmp/snapshots/<name>
```

The tests (`venv-ml/bin/python -m pytest tests/ml`) use a small stand-in encoder instead of the real
embedding models and a throwaway local MLflow store, so they need no download and no server.
